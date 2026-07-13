import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from urllib.request import urlopen

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _wait_for_health(url: str, process: subprocess.Popen | None = None) -> None:
    for _ in range(80):
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    output = ""
    if process is not None and process.poll() is not None and process.stdout is not None:
        output = process.stdout.read()
    raise AssertionError(f"listener did not become ready: {url}\n{output}")


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _launcher_environment(dev_dir: Path) -> tuple[dict[str, str], int, int]:
    executable_dir = dev_dir / "bin"
    executable_dir.mkdir()
    python = executable_dir / "python"
    python.write_text(
        f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n", encoding="utf-8"
    )
    python.chmod(0o700)
    npm = executable_dir / "npm"
    npm.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
    npm.chmod(0o700)
    manager_port = _unused_port()
    agent_port = _unused_port()
    environment = os.environ | {
        "CONDA_DEFAULT_ENV": "venv312",
        "SKIP_INSTALL": "1",
        "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
        "IC_ENV_GUARD_PORT": str(manager_port),
        "IC_ENV_GUARD_AGENT_PORT": str(agent_port),
        "IC_ENV_GUARD_AGENT_INGEST_PORT": str(_unused_port()),
        "IC_ENV_GUARD_FRONTEND_PORT": str(_unused_port()),
        "PATH": f"{executable_dir}:{os.environ['PATH']}",
    }
    return environment, manager_port, agent_port


def _start_all(environment: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [str(PROJECT_ROOT / "start.sh"), "all"],
        cwd=PROJECT_ROOT,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_all(process: subprocess.Popen) -> str:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        output = process.communicate(timeout=10)[0]
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output = process.communicate(timeout=5)[0]
    return output


def _wait_for_local_agent(
    database: Path, process: subprocess.Popen | None = None
) -> tuple[str, str, str]:
    for _ in range(80):
        try:
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT transport_profile_id, enrollment_method, source "
                    "FROM agents WHERE agent_id = 'local-agent'"
                ).fetchone()
            if row is not None:
                return row
        except sqlite3.Error:
            pass
        time.sleep(0.1)
    output = ""
    if process is not None and process.poll() is not None and process.stdout is not None:
        output = process.stdout.read()
    raise AssertionError(
        f"local-agent was not committed to the Manager Registry\n{output}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("initial_token", ["", " \n"], ids=["zero-byte", "whitespace-only"])
def test_manager_development_config_repairs_blank_token_and_is_restartable(
    tmp_path, initial_token
):
    token_file = tmp_path / "control-plane.token"
    token_file.write_text(initial_token, encoding="utf-8")
    token_file.chmod(0o600)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    (executable_dir / "python").symlink_to(Path(sys.executable))
    environment = os.environ | {
        "CONDA_DEFAULT_ENV": "venv312",
        "SKIP_INSTALL": "1",
        "IC_ENV_GUARD_DEV_DIR": str(tmp_path),
        "PATH": f"{executable_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", "control-plane"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    generated_token = token_file.read_text(encoding="utf-8")
    assert generated_token.strip()
    assert token_file.stat().st_mode & 0o777 == 0o600
    config = yaml.safe_load((tmp_path / "control-plane.yaml").read_text())
    control_plane = config["control_plane"]
    assert config["mode"] == "control-plane"
    assert control_plane["audit_database"] == str(tmp_path / "control-plane.db")
    assert control_plane["credential_directory"] == str(tmp_path / "manager-credentials")
    assert control_plane["allowed_agent_cidrs"] == ["127.0.0.0/8"]
    assert control_plane["transport_profiles"] == [
        {
            "id": "local-loopback-http",
            "type": "trusted_lan_http",
            "allowed_cidrs": ["127.0.0.0/8"],
        }
    ]
    assert control_plane["discovery"] == {"scopes": []}
    assert config["development"] == {
        "allow_insecure_http": True,
        "local_agent_bootstrap": True,
    }
    assert config["enrollment"]["manager_socket_path"] == str(
        tmp_path / "manager-enrollment.sock"
    )
    assert "agents" not in config
    assert "legacy-config-http" not in (tmp_path / "control-plane.yaml").read_text()
    assert "ingest" not in config

    restart = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", "control-plane"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert token_file.read_text(encoding="utf-8") == generated_token


@pytest.mark.integration
def test_start_all_runs_isolated_agent_and_manager_lifecycle(tmp_path):
    dev_dir = Path(mkdtemp(prefix="ieg-all-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    agent_token = dev_dir / "agent.token"
    manager_token = dev_dir / "control-plane.token"
    agent_token.write_text("preserved-agent-login-token\n", encoding="utf-8")
    manager_token.write_text("preserved-manager-login-token\n", encoding="utf-8")
    agent_token.chmod(0o600)
    manager_token.chmod(0o600)
    (dev_dir / "state.db").write_text("old Agent state", encoding="utf-8")
    (dev_dir / "control-plane.db").write_text("old Manager state", encoding="utf-8")
    credentials = dev_dir / "manager-credentials"
    credentials.mkdir()
    (credentials / "old-credential").write_text("old managed secret", encoding="utf-8")
    for name in ("agent-enrollment.sock", "manager-enrollment.sock"):
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(dev_dir / name))
    (dev_dir / "agent.yaml").write_text("mode: stale-agent\n", encoding="utf-8")
    (dev_dir / "control-plane.yaml").write_text(
        "mode: control-plane\n"
        "agents:\n"
        "  - id: local-agent\n"
        "    transport_profile_id: legacy-config-http\n",
        encoding="utf-8",
    )
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    process = _start_all(environment)
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", process)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", process)
        agent = yaml.safe_load((dev_dir / "agent.yaml").read_text())
        manager = yaml.safe_load((dev_dir / "control-plane.yaml").read_text())
        assert agent["server"]["port"] == agent_port
        assert agent["state_database"] == str(dev_dir / "state.db")
        assert manager["server"]["port"] == manager_port
        assert manager["control_plane"]["audit_database"] == str(dev_dir / "control-plane.db")
        assert manager["control_plane"]["credential_directory"] == str(
            dev_dir / "manager-credentials"
        )
        assert agent["auth"]["token_file"] != manager["auth"]["token_file"]
        assert "agents" not in manager
        assert "legacy-config-http" not in (dev_dir / "control-plane.yaml").read_text()
        assert not (dev_dir / "manager-credentials" / "old-credential").exists()
        assert (dev_dir / "state.db").read_bytes() != b"old Agent state"
        assert (dev_dir / "control-plane.db").read_bytes() != b"old Manager state"
        assert agent_token.read_text(encoding="utf-8") == "preserved-agent-login-token\n"
        assert manager_token.read_text(encoding="utf-8") == "preserved-manager-login-token\n"
        assert _wait_for_local_agent(dev_dir / "control-plane.db", process) == (
            "local-loopback-http",
            "local_socket",
            "local_dev_bootstrap",
        )
    finally:
        output = _stop_all(process)
        assert "preserved-agent-login-token" not in output
        assert "preserved-manager-login-token" not in output
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_start_all_stops_recorded_same_user_backend_before_reset():
    dev_dir = Path(mkdtemp(prefix="ieg-owned-process-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    config_path = dev_dir / "agent.yaml"
    recorded = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", str(config_path)]
    )
    (dev_dir / "agent.pid").write_text(f"{recorded.pid}\n", encoding="ascii")
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    process = _start_all(environment)
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", process)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", process)
        recorded.wait(timeout=5)
        assert recorded.returncode is not None
    finally:
        _stop_all(process)
        if recorded.poll() is None:
            recorded.terminate()
            recorded.wait(timeout=5)
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_start_all_rejects_unrelated_recorded_process_without_signaling_it():
    dev_dir = Path(mkdtemp(prefix="ieg-unrelated-process-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    (dev_dir / "agent.pid").write_text(f"{unrelated.pid}\n", encoding="ascii")
    environment, _, _ = _launcher_environment(dev_dir)
    try:
        result = subprocess.run(
            [str(PROJECT_ROOT / "start.sh"), "all"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "development process identity mismatch" in result.stderr
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        rmtree(dev_dir, ignore_errors=True)

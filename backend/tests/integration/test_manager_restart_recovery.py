import os
import signal
import socket
import sqlite3
import stat
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


def _bounded_all(environment: dict[str, str], timeout: float = 3) -> tuple[int | None, str]:
    process = _start_all(environment)
    try:
        output = process.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        output = _stop_all(process)
        return None, output
    return process.returncode, output


def _wait_for_file(path: Path, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            assert process.stdout is not None
            raise AssertionError(process.stdout.read())
        time.sleep(0.02)
    raise AssertionError(f"file did not appear: {path}")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _write_frontend_listener(executable: Path, dev_dir: Path) -> None:
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import socket\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        f"dev_dir = Path({str(dev_dir)!r})\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "counter = dev_dir / 'frontend.count'\n"
        "run = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(run))\n"
        "(dev_dir / f'frontend-run-{run}.pid').write_text(str(os.getpid()))\n"
        "listener = socket.socket()\n"
        "listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "listener.bind(('127.0.0.1', port))\n"
        "listener.listen()\n"
        "while True:\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)


def _write_frontend_with_descendant(executable: Path, dev_dir: Path) -> None:
    executable.write_text(
        f"#!{sys.executable}\n"
        "import subprocess\n"
        "import sys\n"
        f"dev_dir = {str(dev_dir)!r}\n"
        "port = sys.argv[sys.argv.index('--port') + 1]\n"
        "source = (\n"
        "    'import os,socket,sys,time; from pathlib import Path; '\n"
        "    'listener=socket.socket(); listener.bind((\"127.0.0.1\", int(sys.argv[1]))); '\n"
        "    'listener.listen(); Path(sys.argv[2]).write_text(str(os.getpid())); '\n"
        "    'time.sleep(60)'\n"
        ")\n"
        "child = subprocess.Popen([sys.executable, '-c', source, port, "
        "dev_dir + '/frontend-descendant.pid'])\n"
        "(open(dev_dir + '/frontend-parent.pid', 'w')).write(str(child.pid))\n"
        "raise SystemExit(child.wait())\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)


def _fail_frontend_ps_after_marker(executable: Path, marker: Path) -> None:
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        "if marker.exists() and 'pgid=' in sys.argv:\n"
        "    raise SystemExit(1)\n"
        "os.execv('/bin/ps', ['/bin/ps', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)


def _start_foreign_listener(signal_marker: Path, *, health: bool = False):
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,socket,sys; from pathlib import Path; "
            "marker=Path(sys.argv[1]); health=sys.argv[2] == 'health'; "
            "listener=socket.socket(); listener.bind(('127.0.0.1', 0)); "
            "listener.listen(); print(listener.getsockname()[1], flush=True); "
            "signal.signal(signal.SIGTERM, lambda *_: (marker.touch(), sys.exit(0))); "
            "signal.signal(signal.SIGINT, lambda *_: (marker.touch(), sys.exit(0))); "
            "response=(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n' "
            "b'Connection: close\\r\\n\\r\\nOK'); "
            "\nwhile True:\n"
            " connection,_=listener.accept()\n"
            " with connection:\n"
            "  if health:\n"
            "   connection.recv(4096); connection.sendall(response)\n",
            str(signal_marker),
            "health" if health else "raw",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    port = int(process.stdout.readline())
    return process, port


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
        assert agent["terminal"]["exited_retention_minutes"] == 0
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
def test_second_start_all_replaces_the_owned_frontend_and_backend_processes():
    dev_dir = Path(mkdtemp(prefix="ieg-all-restart-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    _write_frontend_listener(dev_dir / "bin" / "npm", dev_dir)
    first = _start_all(environment)
    second: subprocess.Popen | None = None
    first_output = ""
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", first)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", first)
        _wait_for_file(dev_dir / "frontend-run-1.pid", first)
        _wait_for_file(dev_dir / "frontend.pid", first)
        old_pids = {
            "agent": int((dev_dir / "agent.pid").read_text()),
            "manager": int((dev_dir / "control-plane.pid").read_text()),
            "frontend-leader": int((dev_dir / "frontend.pid").read_text().split()[0]),
            "frontend-child": int((dev_dir / "frontend-run-1.pid").read_text()),
        }

        second = _start_all(environment)
        _wait_for_file(dev_dir / "frontend-run-2.pid", second, timeout=20)
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", second)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", second)
        first_output = first.communicate(timeout=10)[0]

        assert second.poll() is None
        assert first.returncode is not None
        assert all(not _pid_exists(pid) for pid in old_pids.values())
        frontend_metadata = (dev_dir / "frontend.pid").read_text().split()
        assert int(frontend_metadata[0]) != old_pids["frontend-leader"]
        assert _pid_exists(int(frontend_metadata[0]))
    finally:
        if first.poll() is None:
            first_output = _stop_all(first)
        second_output = _stop_all(second) if second is not None else ""
        assert "development port already in use" not in first_output + second_output
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_term_launcher_pid_stops_owned_frontend_descendants_and_metadata():
    dev_dir = Path(mkdtemp(prefix="ieg-all-term-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    _write_frontend_with_descendant(dev_dir / "bin" / "npm", dev_dir)
    process = _start_all(environment)
    descendant_pid = 0
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", process)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", process)
        descendant_path = dev_dir / "frontend-descendant.pid"
        _wait_for_file(descendant_path, process)
        descendant_pid = int(descendant_path.read_text())

        started = time.monotonic()
        os.kill(process.pid, signal.SIGTERM)
        output = process.communicate(timeout=10)[0]

        assert time.monotonic() - started < 10
        assert process.returncode == 143, output
        assert not _pid_exists(descendant_pid)
        assert not (dev_dir / "frontend.pid").exists()
        assert not (dev_dir / "agent.pid").exists()
        assert not (dev_dir / "control-plane.pid").exists()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", int(environment["IC_ENV_GUARD_FRONTEND_PORT"])))
    finally:
        if process.poll() is None:
            _stop_all(process)
        if descendant_pid and _pid_exists(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_term_launcher_cleanup_does_not_depend_on_frontend_identity_reprobe():
    dev_dir = Path(mkdtemp(prefix="ieg-all-term-ps-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    _write_frontend_with_descendant(dev_dir / "bin" / "npm", dev_dir)
    ps_failure = dev_dir / "fail-frontend-ps"
    _fail_frontend_ps_after_marker(dev_dir / "bin" / "ps", ps_failure)
    process = _start_all(environment)
    descendant_pid = 0
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", process)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", process)
        descendant_path = dev_dir / "frontend-descendant.pid"
        _wait_for_file(descendant_path, process)
        _wait_for_file(dev_dir / "frontend.pid", process)
        descendant_pid = int(descendant_path.read_text())
        ps_failure.touch()

        os.kill(process.pid, signal.SIGTERM)
        output = process.communicate(timeout=10)[0]

        assert process.returncode == 143, output
        assert not _pid_exists(descendant_pid)
        assert not (dev_dir / "frontend.pid").exists()
    finally:
        if process.poll() is None:
            _stop_all(process)
        if descendant_pid and _pid_exists(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_start_all_does_not_signal_frontend_with_mismatched_identity():
    dev_dir = Path(mkdtemp(prefix="ieg-foreign-frontend-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    metadata = dev_dir / "frontend.pid"
    metadata.write_text(f"{foreign.pid} {'0' * 64}\n", encoding="ascii")
    metadata.chmod(0o600)
    try:
        returncode, output = _bounded_all(environment)

        assert returncode is not None
        assert returncode != 0
        assert "development frontend identity mismatch" in output
        assert foreign.poll() is None
        assert metadata.exists()
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait(timeout=5)
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_start_all_stops_recorded_same_user_backend_before_reset():
    dev_dir = Path(mkdtemp(prefix="ieg-owned-process-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    config_path = dev_dir / "agent.yaml"
    recorded = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            "x" * 2048,
            str(config_path),
        ]
    )
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text(f"{recorded.pid}\n", encoding="ascii")
    pid_file.chmod(0o600)
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
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text(f"{unrelated.pid}\n", encoding="ascii")
    pid_file.chmod(0o600)
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


@pytest.mark.integration
@pytest.mark.parametrize(
    ("port_variable", "serves_health"),
    [
        ("IC_ENV_GUARD_AGENT_PORT", True),
        ("IC_ENV_GUARD_PORT", False),
        ("IC_ENV_GUARD_AGENT_INGEST_PORT", False),
        ("IC_ENV_GUARD_FRONTEND_PORT", False),
    ],
    ids=["agent", "manager", "ingest", "frontend"],
)
def test_start_all_rejects_occupied_port_before_reset_without_signaling_listener(
    port_variable, serves_health
):
    dev_dir = Path(mkdtemp(prefix="ieg-occupied-port-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    preserved_files = {
        "state.db": b"preserved Agent state",
        "control-plane.db": b"preserved Manager state",
        "agent.yaml": b"preserved Agent config",
        "control-plane.yaml": b"preserved Manager config",
        "agent.token": b"preserved Agent token\n",
        "control-plane.token": b"preserved Manager token\n",
    }
    for name, payload in preserved_files.items():
        path = dev_dir / name
        path.write_bytes(payload)
        path.chmod(0o600)
    credentials = dev_dir / "manager-credentials"
    credentials.mkdir(mode=0o700)
    credential = credentials / "managed.credential"
    credential.write_bytes(b"preserved managed credential")
    credential.chmod(0o600)
    enrollment_sockets: list[socket.socket] = []
    socket_identities = {}
    for name in ("agent-enrollment.sock", "manager-enrollment.sock"):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(dev_dir / name))
        listener.listen()
        enrollment_sockets.append(listener)
        metadata = (dev_dir / name).lstat()
        socket_identities[name] = (metadata.st_dev, metadata.st_ino)
    signal_marker = dev_dir / "foreign-listener.signaled"
    foreign, occupied_port = _start_foreign_listener(
        signal_marker, health=serves_health
    )
    environment, _, _ = _launcher_environment(dev_dir)
    environment[port_variable] = str(occupied_port)
    try:
        started = time.monotonic()
        returncode, output = _bounded_all(environment, timeout=5)

        assert time.monotonic() - started < 7
        assert returncode is not None
        assert returncode != 0
        assert "development port already in use" in output
        assert foreign.poll() is None
        assert not signal_marker.exists()
        for name, payload in preserved_files.items():
            assert (dev_dir / name).read_bytes() == payload
        assert credential.read_bytes() == b"preserved managed credential"
        for name, identity in socket_identities.items():
            metadata = (dev_dir / name).lstat()
            assert (metadata.st_dev, metadata.st_ino) == identity
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait(timeout=5)
        for listener in enrollment_sockets:
            listener.close()
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_backend_readiness_rejects_exited_captured_child_despite_foreign_health(
    tmp_path,
):
    marker = tmp_path / "foreign-listener.signaled"
    foreign, port = _start_foreign_listener(marker, health=True)
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
BACKEND_HOST=127.0.0.1
BACKEND_PORT={port}
false &
child=$!
wait "$child" || true
wait_for_backend "$child"
"""
    try:
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )

        assert result.returncode != 0
        assert "backend child exited before readiness" in result.stderr
        assert foreign.poll() is None
        assert not marker.exists()
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait(timeout=5)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_ports", [False, True], ids=["duplicate", "invalid"])
def test_start_all_rejects_invalid_or_duplicate_ports_before_reset(
    tmp_path, invalid_ports
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    state = dev_dir / "state.db"
    state.write_bytes(b"must survive port validation")
    environment, _, _ = _launcher_environment(dev_dir)
    if invalid_ports:
        environment["IC_ENV_GUARD_AGENT_INGEST_PORT"] = "invalid"
    else:
        environment["IC_ENV_GUARD_FRONTEND_PORT"] = environment["IC_ENV_GUARD_PORT"]

    returncode, output = _bounded_all(environment)

    assert returncode is not None
    assert returncode != 0
    assert "development port already in use" in output
    assert state.read_bytes() == b"must survive port validation"


def test_reset_validation_uses_passwd_home_before_any_directory_mutation():
    launcher = (PROJECT_ROOT / "start.sh").read_text(encoding="utf-8")
    validation = launcher.split("prepare_dev_dir_for_reset() {", 1)[1].split(
        "\n}\n", 1
    )[0]

    assert "pwd.getpwuid(os.getuid()).pw_dir" in validation
    assert "Path.home()" not in validation
    assert validation.index("pwd.getpwuid") < validation.index("resolved.mkdir")


@pytest.mark.integration
def test_start_all_rejects_environment_home_before_reset(tmp_path):
    dev_dir = (tmp_path / "environment-home").resolve()
    dev_dir.mkdir(mode=0o700)
    state = dev_dir / "state.db"
    state.write_text("must survive", encoding="utf-8")
    environment, _, _ = _launcher_environment(dev_dir)
    environment["HOME"] = str(dev_dir)

    returncode, output = _bounded_all(environment)

    assert returncode is not None
    assert returncode != 0
    assert "unsafe development directory" in output
    assert state.read_text(encoding="utf-8") == "must survive"


@pytest.mark.integration
@pytest.mark.parametrize(
    "path_component",
    ["unsupported path", "unsupported\npath", "unsupported\x7fpath"],
    ids=["space", "newline", "delete-control"],
)
def test_start_all_rejects_unsupported_development_path_before_reset(
    tmp_path, path_component
):
    dev_dir = (tmp_path / path_component).resolve()
    dev_dir.mkdir(mode=0o700)
    state = dev_dir / "state.db"
    state.write_text("must survive", encoding="utf-8")
    (dev_dir / "agent.pid").write_text("malformed\n", encoding="ascii")
    environment, _, _ = _launcher_environment(dev_dir)

    returncode, output = _bounded_all(environment)

    assert returncode is not None
    assert returncode != 0
    assert "development directory path is unsupported" in output
    assert state.read_text(encoding="utf-8") == "must survive"


@pytest.mark.integration
def test_cleanup_preserves_replaced_enrollment_sockets():
    dev_dir = Path(mkdtemp(prefix="ieg-replaced-socket-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    frontend_exit = dev_dir / "frontend.exit"
    (dev_dir / "bin" / "npm").write_text(
        f"#!/bin/sh\nwhile [ ! -e '{frontend_exit}' ]; do sleep 0.1; done\nexit 1\n",
        encoding="utf-8",
    )
    process = _start_all(environment)
    replacements: list[socket.socket] = []
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", process)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", process)
        _wait_for_local_agent(dev_dir / "control-plane.db", process)
        identities = {}
        for name in ("agent-enrollment.sock", "manager-enrollment.sock"):
            path = dev_dir / name
            path.unlink()
            replacement = socket.socket(socket.AF_UNIX)
            replacement.bind(str(path))
            replacements.append(replacement)
            metadata = path.lstat()
            identities[name] = (metadata.st_dev, metadata.st_ino)

        frontend_exit.touch()
        process.wait(timeout=10)

        for name, identity in identities.items():
            metadata = (dev_dir / name).lstat()
            assert (metadata.st_dev, metadata.st_ino) == identity
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for replacement in replacements:
            replacement.close()
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_cleanup_lock_prevents_new_all_socket_from_compare_unlink_race():
    dev_dir = Path(mkdtemp(prefix="ieg-socket-race-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, manager_port, agent_port = _launcher_environment(dev_dir)
    frontend_count = dev_dir / "frontend.count"
    first_frontend_exit = dev_dir / "first-frontend.exit"
    second_frontend_exit = dev_dir / "second-frontend.exit"
    (dev_dir / "bin" / "npm").write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        "from pathlib import Path\n"
        f"counter = Path({str(frontend_count)!r})\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        f"exit_path = Path({str(first_frontend_exit)!r}) if count == 1 else "
        f"Path({str(second_frontend_exit)!r})\n"
        "while not exit_path.exists():\n"
        "    time.sleep(0.02)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    cleanup_ready = dev_dir / "cleanup-unlink.ready"
    release_cleanup = dev_dir / "cleanup-unlink.release"
    replacement_ready = dev_dir / "replacement-socket.ready"
    race_armed = dev_dir / "socket-race.armed"
    agent_socket = dev_dir / "agent-enrollment.sock"
    python = dev_dir / "bin" / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "sys.path[0] = os.getcwd()\n"
        f"target = {str(agent_socket)!r}\n"
        f"cleanup_ready = Path({str(cleanup_ready)!r})\n"
        f"release_cleanup = Path({str(release_cleanup)!r})\n"
        f"replacement_ready = Path({str(replacement_ready)!r})\n"
        f"race_armed = Path({str(race_armed)!r})\n"
        "if len(sys.argv) > 1 and sys.argv[1] == '-':\n"
        "    source = sys.stdin.read()\n"
        "    sys.argv = sys.argv[1:]\n"
        "    if (\n"
        "        race_armed.exists()\n"
        "        and len(sys.argv) > 1\n"
        "        and sys.argv[1] == target\n"
        "        and 'path, expected = sys.argv[1:]' in source\n"
        "    ):\n"
        "        cleanup_ready.touch()\n"
        "        while not release_cleanup.exists():\n"
        "            time.sleep(0.02)\n"
        "    elif (\n"
        "        race_armed.exists()\n"
        "        and len(sys.argv) > 1\n"
        "        and sys.argv[1] == target\n"
        "        and 'enrollment socket identity mismatch' in source\n"
        "    ):\n"
        "        replacement_ready.touch()\n"
        "    exec(compile(source, '<stdin>', 'exec'))\n"
        "else:\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    first = _start_all(environment)
    second: subprocess.Popen | None = None
    try:
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", first)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", first)
        _wait_for_local_agent(dev_dir / "control-plane.db", first)
        original = agent_socket.lstat()
        original_identity = (original.st_dev, original.st_ino)
        race_armed.touch()
        first_frontend_exit.touch()
        for _ in range(1000):
            if cleanup_ready.exists():
                break
            if first.poll() is not None:
                break
            time.sleep(0.02)
        assert cleanup_ready.exists(), _stop_all(first)

        second = _start_all(environment)
        for _ in range(150):
            if replacement_ready.exists() or second.poll() is not None:
                break
            time.sleep(0.02)
        replaced_before_cleanup_released = replacement_ready.exists()
        release_cleanup.touch()
        first.communicate(timeout=10)

        assert not replaced_before_cleanup_released
        _wait_for_health(f"http://127.0.0.1:{manager_port}/healthz", second)
        _wait_for_health(f"http://127.0.0.1:{agent_port}/healthz", second)
        _wait_for_local_agent(dev_dir / "control-plane.db", second)
        replacement = agent_socket.lstat()
        assert (replacement.st_dev, replacement.st_ino) != original_identity
    finally:
        release_cleanup.touch()
        second_frontend_exit.touch()
        if first.poll() is None:
            _stop_all(first)
        if second is not None and second.poll() is None:
            _stop_all(second)
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_start_all_rejects_symlink_lifecycle_lock_without_following_target(tmp_path):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    target = tmp_path / "must-survive"
    target.write_text("unchanged", encoding="utf-8")
    (dev_dir / ".start-all.lock").symlink_to(target)

    returncode, output = _bounded_all(environment, timeout=3)

    assert returncode is not None
    assert returncode != 0
    assert "development lifecycle lock is unsafe" in output
    assert target.read_text(encoding="utf-8") == "unchanged"


def _run_lifecycle_lock_scenario(
    dev_dir: Path, scenario: str, *, timeout: float = 3
) -> subprocess.CompletedProcess:
    use_original = dev_dir / "use-original-keeper"
    keeper_pid = dev_dir / "keeper.pid"
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
lifecycle_lock_pid=""
lifecycle_lock_io_dir=""
lifecycle_lock_fds_open=0
lifecycle_lock_held=0
lifecycle_lock_acquire_timeout=1
lifecycle_lock_release_timeout=1
lifecycle_lock_cleanup_attempts=10
lifecycle_lock_cleanup_interval=0.01
eval "$(
  declare -f lifecycle_lock_keeper \
    | sed '1s/lifecycle_lock_keeper/original_lifecycle_lock_keeper/'
)"

assert_lock_is_reset() {{
  [[ "$lifecycle_lock_pid" == "" ]]
  [[ "$lifecycle_lock_io_dir" == "" ]]
  [[ "$lifecycle_lock_fds_open" == "0" ]]
  [[ "$lifecycle_lock_held" == "0" ]]
  ! compgen -G "$DEV_DIR/.start-all-lock.*" >/dev/null
}}

retry_lock() {{
  : > {str(use_original)!r}
  acquire_lifecycle_lock
  release_lifecycle_lock
  assert_lock_is_reset
}}

SCENARIO={scenario!r}
KEEPER_PID_FILE={str(keeper_pid)!r}
USE_ORIGINAL={str(use_original)!r}

case "$SCENARIO" in
  peer-open-failure)
    lifecycle_lock_keeper() {{
      if [[ -e "$USE_ORIGINAL" ]]; then
        original_lifecycle_lock_keeper
      else
        return 17
      fi
    }}
    if acquire_lifecycle_lock; then
      echo "peer-open failure unexpectedly acquired the lock" >&2
      exit 1
    fi
    assert_lock_is_reset
    retry_lock
    ;;
  acquire-hang|acquire-crash)
    lifecycle_lock_keeper() {{
      if [[ -e "$USE_ORIGINAL" ]]; then
        original_lifecycle_lock_keeper
        return
      fi
      exec 3<"$lifecycle_lock_io_dir/control"
      exec 4>"$lifecycle_lock_io_dir/status"
      python -c 'import os,sys; open(sys.argv[1], "w").write(str(os.getppid()))' \
        "$KEEPER_PID_FILE"
      if [[ "$SCENARIO" == "acquire-crash" ]]; then
        return 17
      fi
      trap '' TERM
      while :; do sleep 1; done
    }}
    if acquire_lifecycle_lock; then
      echo "broken acquire handshake unexpectedly acquired the lock" >&2
      exit 1
    fi
    failed_pid="$(cat "$KEEPER_PID_FILE")"
    ! kill -0 "$failed_pid" 2>/dev/null
    assert_lock_is_reset
    retry_lock
    ;;
  release-no-ack)
    lifecycle_lock_keeper() {{
      if [[ -e "$USE_ORIGINAL" ]]; then
        original_lifecycle_lock_keeper
        return
      fi
      exec 3<"$lifecycle_lock_io_dir/control"
      exec 4>"$lifecycle_lock_io_dir/status"
      python -c 'import os,sys; open(sys.argv[1], "w").write(str(os.getppid()))' \
        "$KEEPER_PID_FILE"
      printf 'locked\n' >&4
      while IFS= read -r -n 1 <&3; do :; done
      trap '' TERM
      while :; do sleep 1; done
    }}
    acquire_lifecycle_lock
    failed_pid="$lifecycle_lock_pid"
    if release_lifecycle_lock; then
      echo "missing release ACK unexpectedly succeeded" >&2
      exit 1
    fi
    ! kill -0 "$failed_pid" 2>/dev/null
    assert_lock_is_reset
    retry_lock
    ;;
  release-stopped)
    lifecycle_lock_keeper() {{ original_lifecycle_lock_keeper; }}
    acquire_lifecycle_lock
    failed_pid="$lifecycle_lock_pid"
    kill -STOP "$failed_pid"
    if release_lifecycle_lock; then
      echo "stopped keeper unexpectedly released the lock" >&2
      exit 1
    fi
    ! kill -0 "$failed_pid" 2>/dev/null
    assert_lock_is_reset
    retry_lock
    ;;
esac
"""
    process = subprocess.Popen(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=os.environ | {"IC_ENV_GUARD_DEV_DIR": str(dev_dir)},
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=1)
        pytest.fail(
            f"lifecycle lock scenario was not bounded: {scenario}\n{stdout}{stderr}"
        )
    return subprocess.CompletedProcess(
        process.args, process.returncode, stdout=stdout, stderr=stderr
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        "peer-open-failure",
        "acquire-hang",
        "acquire-crash",
        "release-no-ack",
        "release-stopped",
    ],
)
def test_lifecycle_lock_keeper_failures_are_bounded_clean_and_retryable(
    tmp_path, scenario
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)

    result = _run_lifecycle_lock_scenario(dev_dir, scenario)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
@pytest.mark.parametrize(
    ("signal_number", "expected_status"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
    ids=["interrupt", "terminate"],
)
def test_start_all_signal_during_lock_handshake_cleans_and_exits(
    tmp_path, signal_number, expected_status
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    ready = dev_dir / "keeper-ready"
    keeper_pid = dev_dir / "keeper.pid"
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
activate_backend_env() {{ :; }}
prepare_dev_dir_for_reset() {{ :; }}
lifecycle_lock_keeper() {{
  exec python - {str(keeper_pid)!r} {str(ready)!r} <<'PY'
import os
import signal
import sys
import time
from pathlib import Path

signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
Path(sys.argv[2]).touch()
time.sleep(30)
PY
}}
start_all
"""
    environment = os.environ | {
        "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
        "lifecycle_lock_acquire_timeout": "1",
        "lifecycle_lock_release_timeout": "1",
        "lifecycle_lock_cleanup_attempts": "2",
        "lifecycle_lock_cleanup_interval": "0.01",
    }
    launcher = subprocess.Popen(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if ready.exists() or launcher.poll() is not None:
                break
            time.sleep(0.02)
        assert ready.exists(), launcher.communicate(timeout=1)
        os.kill(launcher.pid, signal_number)
        stdout, stderr = launcher.communicate(timeout=5)

        assert launcher.returncode == expected_status, stdout + stderr
        recorded_keeper = int(keeper_pid.read_text(encoding="ascii"))
        with pytest.raises(ProcessLookupError):
            os.kill(recorded_keeper, 0)
        assert not list(dev_dir.glob(".start-all-lock.*"))

        retry = _run_lifecycle_lock_scenario(dev_dir, "peer-open-failure")
        assert retry.returncode == 0, retry.stdout + retry.stderr
    finally:
        if launcher.poll() is None:
            os.killpg(launcher.pid, signal.SIGKILL)
            launcher.communicate(timeout=1)


@pytest.mark.integration
def test_start_all_uses_hard_lifecycle_deadlines_not_environment(tmp_path):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
activate_backend_env() {{ :; }}
prepare_dev_dir_for_reset() {{ :; }}
acquire_lifecycle_lock() {{
  printf '%s\n' \
    "$lifecycle_lock_acquire_timeout" \
    "$lifecycle_lock_release_timeout" \
    "$lifecycle_lock_cleanup_attempts" \
    "$lifecycle_lock_cleanup_interval"
  return 17
}}
start_all
"""
    environment = os.environ | {
        "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
        "lifecycle_lock_acquire_timeout": "999999999",
        "lifecycle_lock_release_timeout": "invalid",
        "lifecycle_lock_cleanup_attempts": "999999999",
        "lifecycle_lock_cleanup_interval": "invalid",
    }

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["121", "2", "20", "0.05"]


@pytest.mark.integration
def test_recorded_process_identity_change_before_kill_fails_closed():
    dev_dir = Path(mkdtemp(prefix="ieg-reused-process-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    ready = dev_dir / "recorded.ready"
    signaled = dev_dir / "recorded.signaled"
    config_path = dev_dir / "agent.yaml"
    recorded = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, lambda *_: Path(sys.argv[2]).touch()); "
            "Path(sys.argv[1]).touch(); time.sleep(30)",
            str(ready),
            str(signaled),
            str(config_path),
        ]
    )
    for _ in range(50):
        if ready.exists():
            break
        time.sleep(0.02)
    assert ready.exists()
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text(f"{recorded.pid}\n", encoding="ascii")
    pid_file.chmod(0o600)
    ps_counter = dev_dir / "ps-counter"
    fake_ps = dev_dir / "bin" / "ps"
    fake_ps.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"counter = Path({str(ps_counter)!r})\n"
        f"uid = {os.getuid()!r}\n"
        f"config = {str(config_path)!r}\n"
        "if 'lstart=' in sys.argv:\n"
        "    count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "    counter.write_text(str(count))\n"
        "    command = f'python runtime {config}' if count == 1 else 'python unrelated'\n"
        "    print(f'{uid} Mon Jan  1 00:00:00 2024 S {command}')\n"
        "elif 'command=' in sys.argv:\n"
        "    print(f'{uid} python runtime {config}')\n"
        "elif 'stat=' in sys.argv:\n"
        "    print('S')\n",
        encoding="utf-8",
    )
    fake_ps.chmod(0o700)
    launcher = _start_all(environment)
    try:
        output = launcher.communicate(timeout=8)[0]

        assert launcher.returncode != 0
        assert "development process identity mismatch" in output
        assert recorded.poll() is None
        assert not signaled.exists()
    except subprocess.TimeoutExpired:
        _stop_all(launcher)
        pytest.fail("launcher did not fail closed after recorded process identity changed")
    finally:
        if launcher.poll() is None:
            _stop_all(launcher)
        if recorded.poll() is None:
            recorded.kill()
        recorded.wait(timeout=5)
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize("post_kill_identity", ["same", "changed"])
def test_post_kill_uncertainty_preserves_recorded_pid_metadata(
    tmp_path, post_kill_identity
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text("99999998\n", encoding="ascii")
    pid_file.chmod(0o600)
    config_path = dev_dir / "agent.yaml"
    killed = dev_dir / "kill-issued"
    environment |= {
        "POST_KILL_IDENTITY": post_kill_identity,
        "KILL_MARKER": str(killed),
    }
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
recorded_process_matches() {{
  if [[ -e "$KILL_MARKER" && "$POST_KILL_IDENTITY" == "changed" ]]; then
    return 1
  fi
  printf 'recorded-identity\n'
}}
wait_for_recorded_process_exit() {{
  if [[ -e "$KILL_MARKER" && "$POST_KILL_IDENTITY" == "changed" ]]; then
    return 2
  fi
  return 1
}}
kill() {{
  if [[ "$1" == "-KILL" ]]; then
    : > "$KILL_MARKER"
  fi
  return 0
}}
sleep() {{ return 0; }}
set +e
stop_recorded_process {str(pid_file)!r} {str(config_path)!r}
exit $?
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode != 0
    expected_error = (
        "development process identity mismatch"
        if post_kill_identity == "changed"
        else "development process did not exit"
    )
    assert expected_error in result.stderr
    assert pid_file.read_text(encoding="ascii") == "99999998\n"


@pytest.mark.integration
def test_captured_child_second_wait_never_calls_unbounded_wait(tmp_path):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    wait_called = dev_dir / "unbounded-wait-called"
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
( while :; do builtin read -r -t 1 || true; done ) &
child=$!
printf '%s\n' "$child" > {str(pid_file)!r}
chmod 0600 {str(pid_file)!r}
trap 'builtin kill -KILL "$child" 2>/dev/null; builtin wait "$child" 2>/dev/null' EXIT
kill() {{ return 0; }}
wait_for_process_exit() {{ return 1; }}
wait() {{ : > {str(wait_called)!r}; return 0; }}
set +e
if terminate_child "$child"; then
  remove_owned_pid_file {str(pid_file)!r} "$child"
  status=0
else
  status=$?
fi
exit "$status"
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode != 0
    assert "development child did not exit" in result.stderr
    assert pid_file.exists()
    assert not wait_called.exists()


@pytest.mark.integration
def test_owned_frontend_cleanup_closes_setsid_race_after_process_classification(
    tmp_path,
):
    ready = tmp_path / "frontend.ready"
    descendant = tmp_path / "frontend.descendant"
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
python - {str(ready)!r} {str(descendant)!r} <<'PY' &
import os
import subprocess
import sys
from pathlib import Path

os.setsid()
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(sys.argv[2]).write_text(str(child.pid), encoding="ascii")
Path(sys.argv[1]).touch()
raise SystemExit(child.wait())
PY
leader=$!
for _ in $(seq 1 100); do
  [[ -e {str(ready)!r} ]] && break
  sleep 0.02
done
[[ -e {str(ready)!r} ]]
owned_frontend_is_group() {{ return 1; }}
terminate_owned_frontend "$leader"
child="$(cat {str(descendant)!r})"
status=0
if kill -0 "$child" 2>/dev/null; then
  status=42
fi
python - "$leader" <<'PY'
import os
import signal
import sys

try:
    os.killpg(int(sys.argv[1]), signal.SIGKILL)
except ProcessLookupError:
    pass
PY
exit "$status"
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=8,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
@pytest.mark.parametrize(
    ("probe_error", "expected_status", "pid_metadata_survives"),
    [("ESRCH", 0, False), ("EPERM", 1, True)],
)
def test_recorded_process_probe_distinguishes_esrch_from_eperm(
    tmp_path, probe_error, expected_status, pid_metadata_survives
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text("99999997\n", encoding="ascii")
    pid_file.chmod(0o600)
    config_path = dev_dir / "agent.yaml"
    (dev_dir / "bin" / "ps").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (dev_dir / "bin" / "ps").chmod(0o700)
    python = dev_dir / "bin" / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import errno\n"
        "import os\n"
        "import sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == '-':\n"
        "    source = sys.stdin.read()\n"
        "    sys.argv = sys.argv[1:]\n"
        "    if 'os.kill(pid, 0)' in source:\n"
        "        error = os.environ['PROCESS_PROBE_ERROR']\n"
        "        def injected_kill(pid, signal_number):\n"
        "            number = errno.ESRCH if error == 'ESRCH' else errno.EPERM\n"
        "            raise OSError(number, error)\n"
        "        os.kill = injected_kill\n"
        "    exec(compile(source, '<stdin>', 'exec'))\n"
        "else:\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    environment["PROCESS_PROBE_ERROR"] = probe_error
    script = f"""
source {str(PROJECT_ROOT / 'start.sh')!r} help >/dev/null
set +e
stop_recorded_process {str(pid_file)!r} {str(config_path)!r}
exit $?
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == expected_status
    assert pid_file.exists() is pid_metadata_survives
    if probe_error == "EPERM":
        assert "development process identity mismatch" in result.stderr


@pytest.mark.integration
def test_pid_metadata_symlink_fails_closed_without_following_target():
    dev_dir = Path(mkdtemp(prefix="ieg-pid-symlink-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    target = dev_dir / "pid-target"
    target.write_text("99999999\n", encoding="ascii")
    pid_file = dev_dir / "agent.pid"
    pid_file.symlink_to(target)
    try:
        returncode, output = _bounded_all(environment)

        assert returncode is not None
        assert returncode != 0
        assert "development process identity mismatch" in output
        assert pid_file.is_symlink()
        assert target.read_text(encoding="ascii") == "99999999\n"
    finally:
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_pid_metadata_fifo_fails_closed_without_blocking():
    dev_dir = Path(mkdtemp(prefix="ieg-pid-fifo-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    os.mkfifo(dev_dir / "agent.pid", mode=0o600)
    try:
        returncode, output = _bounded_all(environment)

        assert returncode is not None
        assert returncode != 0
        assert "development process identity mismatch" in output
    finally:
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_pid_metadata_replaced_with_fifo_after_lstat_fails_closed_without_blocking(
    tmp_path,
):
    dev_dir = (tmp_path / "development").resolve()
    dev_dir.mkdir(mode=0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text("99999999\n", encoding="ascii")
    pid_file.chmod(0o600)
    python = dev_dir / "bin" / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        f"target = {str(pid_file)!r}\n"
        "if len(sys.argv) == 4 and sys.argv[1:3] == ['-', target]:\n"
        "    source = sys.stdin.read()\n"
        "    sys.argv = sys.argv[1:]\n"
        "    original_lstat = os.lstat\n"
        "    def replace_after_lstat(path):\n"
        "        metadata = original_lstat(path)\n"
        "        os.lstat = original_lstat\n"
        "        os.unlink(path)\n"
        "        os.mkfifo(path, 0o600)\n"
        "        return metadata\n"
        "    os.lstat = replace_after_lstat\n"
        "    exec(compile(source, '<stdin>', 'exec'))\n"
        "else:\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    started = time.monotonic()
    returncode, output = _bounded_all(environment, timeout=1)

    assert time.monotonic() - started < 3
    assert returncode is not None
    assert returncode != 0
    assert "development process identity mismatch" in output
    assert "Traceback" not in output
    assert stat.S_ISFIFO(pid_file.lstat().st_mode), output


@pytest.mark.integration
def test_pid_metadata_oversize_fails_closed_without_reading_it():
    dev_dir = Path(mkdtemp(prefix="ieg-pid-oversize-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text("9" * 5000, encoding="ascii")
    pid_file.chmod(0o600)
    try:
        returncode, output = _bounded_all(environment)

        assert returncode is not None
        assert returncode != 0
        assert "development process identity mismatch" in output
        assert pid_file.stat().st_size == 5000
    finally:
        rmtree(dev_dir, ignore_errors=True)


@pytest.mark.integration
def test_pid_metadata_unreadable_fails_closed():
    dev_dir = Path(mkdtemp(prefix="ieg-pid-unreadable-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    environment, _, _ = _launcher_environment(dev_dir)
    pid_file = dev_dir / "agent.pid"
    pid_file.write_text("99999999\n", encoding="ascii")
    pid_file.chmod(0o000)
    try:
        returncode, output = _bounded_all(environment)

        assert returncode is not None
        assert returncode != 0
        assert "development process identity mismatch" in output
    finally:
        rmtree(dev_dir, ignore_errors=True)


def test_recorded_process_inspection_requests_untruncated_ps_without_shlex():
    launcher = (PROJECT_ROOT / "start.sh").read_text(encoding="utf-8")
    inspection = launcher.split("recorded_process_matches() {", 1)[1].split(
        "\n}\n", 1
    )[0]

    assert '["ps", "-ww"' in inspection
    assert "shlex" not in inspection


def test_start_all_runs_terminal_readiness_after_bootstrap_and_before_frontend():
    launcher = (PROJECT_ROOT / "start.sh").read_text(encoding="utf-8")
    start_all = launcher.split("start_all() {", 1)[1].split('\n}\n\ncommand="', 1)[0]

    bootstrap_position = start_all.index("bootstrap_local_agent")
    readiness_position = start_all.index("run_terminal_readiness")
    frontend_position = start_all.index("start_tracked_frontend")

    assert bootstrap_position < readiness_position < frontend_position

import os
import signal
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


def _wait_for_health(url: str) -> None:
    for _ in range(80):
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"listener did not become ready: {url}")


@pytest.mark.integration
def test_manager_development_config_is_restartable_and_isolates_fleet_state(tmp_path):
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
    config = yaml.safe_load((tmp_path / "control-plane.yaml").read_text())
    control_plane = config["control_plane"]
    assert config["mode"] == "control-plane"
    assert control_plane["audit_database"] == str(tmp_path / "control-plane.db")
    assert control_plane["credential_directory"] == str(tmp_path / "manager-credentials")
    assert control_plane["allowed_agent_cidrs"] == ["127.0.0.0/8"]
    assert control_plane["transport_profiles"] == []
    assert control_plane["discovery"] == {"scopes": []}
    assert config["enrollment"]["manager_socket_path"] == str(
        tmp_path / "manager-enrollment.sock"
    )
    assert "ingest" not in config
    assert (tmp_path / "control-plane.token").stat().st_mode & 0o777 == 0o600


@pytest.mark.integration
def test_start_all_runs_isolated_agent_and_manager_lifecycle(tmp_path):
    dev_dir = Path(mkdtemp(prefix="ieg-all-", dir="/tmp"))
    dev_dir.chmod(0o700)
    executable_dir = dev_dir / "bin"
    executable_dir.mkdir()
    (executable_dir / "python").symlink_to(Path(sys.executable))
    environment = os.environ | {
        "CONDA_DEFAULT_ENV": "venv312",
        "SKIP_INSTALL": "1",
        "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
        "PATH": f"{executable_dir}:{os.environ['PATH']}",
    }
    process = subprocess.Popen(
        [str(PROJECT_ROOT / "start.sh"), "all"],
        cwd=PROJECT_ROOT,
        env=environment,
        start_new_session=True,
    )
    try:
        _wait_for_health("http://127.0.0.1:8765/healthz")
        _wait_for_health("http://127.0.0.1:8766/healthz")
        agent = yaml.safe_load((dev_dir / "agent.yaml").read_text())
        manager = yaml.safe_load((dev_dir / "control-plane.yaml").read_text())
        assert agent["server"]["port"] == 8766
        assert agent["state_database"] == str(dev_dir / "state.db")
        assert manager["server"]["port"] == 8765
        assert manager["control_plane"]["audit_database"] == str(dev_dir / "control-plane.db")
        assert manager["control_plane"]["credential_directory"] == str(
            dev_dir / "manager-credentials"
        )
        assert agent["auth"]["token_file"] != manager["auth"]["token_file"]
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        rmtree(dev_dir, ignore_errors=True)

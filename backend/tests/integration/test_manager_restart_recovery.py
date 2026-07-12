import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]


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

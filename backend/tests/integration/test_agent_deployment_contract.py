import os
import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _start_config(tmp_path: Path, mode: str, **overrides: str) -> tuple[str, dict]:
    environment = os.environ.copy()
    environment.update(
        {
            "CONDA_DEFAULT_ENV": "venv312",
            "SKIP_INSTALL": "1",
            "IC_ENV_GUARD_DEV_DIR": str(tmp_path),
            **overrides,
        }
    )
    result = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", mode],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout, yaml.safe_load((tmp_path / f"{mode}.yaml").read_text())


def test_standalone_agent_config_uses_public_8765_and_ingest_8766(tmp_path):
    output, config = _start_config(tmp_path, "agent")

    assert config["server"]["port"] == 8765
    assert config["ingest"]["port"] == 8766
    assert "Public listener: 127.0.0.1:8765" in output
    assert "Ingest listener: 127.0.0.1:8766" in output


def test_agent_config_honors_public_and_ingest_port_overrides(tmp_path):
    _output, config = _start_config(
        tmp_path,
        "agent",
        IC_ENV_GUARD_PORT="19065",
        IC_ENV_GUARD_AGENT_INGEST_PORT="19066",
    )

    assert config["server"]["port"] == 19065
    assert config["ingest"]["port"] == 19066


def test_existing_agent_dev_config_gains_explicit_ingest_listener(tmp_path):
    token = tmp_path / "agent.token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        "mode: agent\nserver:\n  bind: 127.0.0.1\n  port: 8765\n"
        f"auth:\n  token_file: {token}\nstate_database: {tmp_path / 'state.db'}\n",
        encoding="utf-8",
    )

    _output, config = _start_config(tmp_path, "agent")

    assert config["server"]["port"] == 8765
    assert config["ingest"] == {"bind": "127.0.0.1", "port": 8766}


def test_systemd_template_selects_existing_non_root_account_and_runtime_dir():
    template = (
        PROJECT_ROOT / "packaging/systemd/ic-env-guard@.service"
    ).read_text(encoding="utf-8")
    compatibility = (
        PROJECT_ROOT / "packaging/systemd/ic-env-guard.service"
    ).read_text(encoding="utf-8")

    assert "User=%i" in template
    assert "RuntimeDirectory=ic-env-guard" in template
    assert "RuntimeDirectoryMode=0700" in template
    assert "NoNewPrivileges=false" in template
    assert "User=root" not in template
    assert "DEPRECATED" in compatibility
    assert "User=ic-env-guard" in compatibility

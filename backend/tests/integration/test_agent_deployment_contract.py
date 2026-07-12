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


def test_existing_agent_config_is_rewritten_for_all_mode_ports(tmp_path):
    _start_config(tmp_path, "agent")

    output, config = _start_config(
        tmp_path,
        "agent",
        IC_ENV_GUARD_PORT="8766",
        IC_ENV_GUARD_AGENT_INGEST_PORT="8767",
    )

    assert config["server"]["port"] == 8766
    assert config["ingest"]["port"] == 8767
    assert "Public listener: 127.0.0.1:8766" in output
    assert "Ingest listener: 127.0.0.1:8767" in output


def test_agent_config_rejects_current_port_override_collision(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "CONDA_DEFAULT_ENV": "venv312",
            "SKIP_INSTALL": "1",
            "IC_ENV_GUARD_DEV_DIR": str(tmp_path),
            "IC_ENV_GUARD_PORT": "19065",
            "IC_ENV_GUARD_AGENT_INGEST_PORT": "19065",
        }
    )

    result = subprocess.run(
        [str(PROJECT_ROOT / "start.sh"), "config", "agent"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "public and ingest ports must differ" in result.stdout + result.stderr


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
    assert "Environment=IC_ENV_GUARD_CONFIG=/etc/ic-env-guard/%i.yaml" in template
    assert "User=root" not in template
    assert "DEPRECATED" in compatibility
    assert "User=ic-env-guard" in compatibility


def test_default_installer_requires_existing_user_and_never_enables_legacy_unit():
    installer = (PROJECT_ROOT / "packaging/install/install.sh").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "useradd" not in installer
    assert "id \"${account}\"" in installer
    assert "id -u \"${account}\"" in installer
    assert "ic-env-guard@${account}.service" in installer
    assert "enable ic-env-guard.service" not in installer
    assert "sudo packaging/install/install.sh edaops" in readme


def test_installer_parent_directories_are_traversable_but_user_state_is_private():
    installer = (PROJECT_ROOT / "packaging/install/install.sh").read_text(encoding="utf-8")

    assert "install -d -o root -g root -m 0755 /etc/ic-env-guard" in installer
    assert "install -d -o root -g root -m 0755 /var/lib/ic-env-guard" in installer
    assert '-o "${account}" -g "${group}" -m 0700 "${state_dir}"' in installer
    assert 'chmod 0600 "${token_file}"' in installer
    assert 'chmod 0640 "${config_file}"' in installer

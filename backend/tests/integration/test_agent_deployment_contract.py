import os
import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _upgrade_environment(
    tmp_path: Path,
    *,
    fail_new_start: bool = False,
    fail_staged_validation: bool = False,
) -> tuple[dict, Path]:
    root = tmp_path / "root"
    legacy_config = root / "etc/ic-env-guard/config.yaml"
    legacy_state = root / "var/lib/ic-env-guard"
    legacy_config.parent.mkdir(parents=True)
    legacy_state.mkdir(parents=True)
    legacy_config.write_text(
        "mode: agent\n"
        "auth:\n  token_file: /var/lib/ic-env-guard/token\n"
        "state_database: /var/lib/ic-env-guard/state.db\n",
        encoding="utf-8",
    )
    (legacy_state / "token").write_text("legacy-token\n", encoding="utf-8")
    (legacy_state / "state.db").write_bytes(b"legacy-state")
    (legacy_state / "instance-id").write_text("legacy-instance\n", encoding="utf-8")

    event_log = tmp_path / "events.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "id",
        "#!/usr/bin/env bash\n"
        "case \"${1:-}\" in\n"
        "  -u) echo 1000 ;;\n"
        "  -gn) echo eda ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    _write_executable(fake_bin / "chown", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "ic-env-guard-config",
        "#!/usr/bin/env bash\n"
        "echo \"validate $2\" >> \"$EVENT_LOG\"\n"
        "test \"$1\" = validate\n"
        "test -s \"$2\"\n"
        "if [[ \"${FAIL_STAGED_VALIDATION:-0}\" = 1 "
        "&& \"$2\" = *.upgrade.* ]]; then exit 1; fi\n",
    )
    _write_executable(
        fake_bin / "systemctl",
        "#!/usr/bin/env bash\n"
        "echo \"systemctl $*\" >> \"$EVENT_LOG\"\n"
        "if [[ \"${FAIL_NEW_START:-0}\" = 1 "
        "&& \"$*\" = \"start ic-env-guard@edaops.service\" ]]; then\n"
        "  exit 1\n"
        "fi\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "EVENT_LOG": str(event_log),
            "IC_ENV_GUARD_ROOT": str(root),
            "FAIL_NEW_START": "1" if fail_new_start else "0",
            "FAIL_STAGED_VALIDATION": "1" if fail_staged_validation else "0",
        }
    )
    return environment, root


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
    upgrader = (PROJECT_ROOT / "packaging/install/upgrade.sh").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "useradd" not in installer
    assert "useradd" not in upgrader
    assert "id \"${account}\"" in installer
    assert "id -u \"${account}\"" in installer
    assert "id \"${account}\"" in upgrader
    assert "id -u \"${account}\"" in upgrader
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


def test_upgrade_migrates_legacy_layout_before_retiring_legacy_unit(tmp_path):
    environment, root = _upgrade_environment(tmp_path)

    result = subprocess.run(
        [str(PROJECT_ROOT / "packaging/install/upgrade.sh"), "edaops"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    migrated_config = (root / "etc/ic-env-guard/edaops.yaml").read_text(encoding="utf-8")
    migrated_state = root / "var/lib/ic-env-guard/edaops"
    assert "token_file: /var/lib/ic-env-guard/edaops/token" in migrated_config
    assert "state_database: /var/lib/ic-env-guard/edaops/state.db" in migrated_config
    assert (migrated_state / "token").read_text(encoding="utf-8") == "legacy-token\n"
    assert (migrated_state / "state.db").read_bytes() == b"legacy-state"
    assert (migrated_state / "instance-id").read_text(encoding="utf-8") == (
        "legacy-instance\n"
    )
    events = (tmp_path / "events.log").read_text(encoding="utf-8").splitlines()
    first_validation = next(
        index for index, event in enumerate(events) if event.startswith("validate ")
    )
    stop_legacy = events.index("systemctl stop ic-env-guard.service")
    start_new = events.index("systemctl start ic-env-guard@edaops.service")
    disable_legacy = events.index("systemctl disable ic-env-guard.service")
    assert first_validation < stop_legacy < start_new < disable_legacy
    assert "systemctl start ic-env-guard.service" not in events


def test_upgrade_restarts_legacy_unit_when_new_instance_fails(tmp_path):
    environment, _root = _upgrade_environment(tmp_path, fail_new_start=True)

    result = subprocess.run(
        [str(PROJECT_ROOT / "packaging/install/upgrade.sh"), "edaops"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    events = (tmp_path / "events.log").read_text(encoding="utf-8").splitlines()
    assert events.index("systemctl stop ic-env-guard.service") < events.index(
        "systemctl start ic-env-guard@edaops.service"
    )
    assert "systemctl disable ic-env-guard.service" not in events
    assert events[-2:] == [
        "systemctl enable ic-env-guard.service",
        "systemctl start ic-env-guard.service",
    ]


def test_upgrade_validation_failure_leaves_legacy_running_and_cleans_stage(tmp_path):
    environment, root = _upgrade_environment(tmp_path, fail_staged_validation=True)

    result = subprocess.run(
        [str(PROJECT_ROOT / "packaging/install/upgrade.sh"), "edaops"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    events = (tmp_path / "events.log").read_text(encoding="utf-8").splitlines()
    assert not any(event.startswith("systemctl stop") for event in events)
    assert not (root / "etc/ic-env-guard/edaops.yaml").exists()
    assert not list((root / "etc/ic-env-guard").glob("edaops.yaml.upgrade.*"))
    assert not (root / "var/lib/ic-env-guard/edaops").exists()

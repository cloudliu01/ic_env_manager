from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ic_env_guard.bootstrap.composition import build_manager_container
from ic_env_guard.config.models import AppConfig
from ic_env_guard.enrollment.local_socket import LocalEnrollmentSocketClient
from ic_env_guard.systemd import cli


def test_helper_config_prefers_environment_then_current_user_then_compatibility(
    tmp_path, monkeypatch
):
    user_dir = tmp_path / "users"
    user_dir.mkdir()
    user_config = user_dir / "edaops.yaml"
    user_config.write_text("mode: agent\n", encoding="utf-8")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("mode: agent\n", encoding="utf-8")
    monkeypatch.setattr(cli, "USER_CONFIG_DIR", user_dir)
    monkeypatch.setattr(cli.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_name="edaops"))

    monkeypatch.setenv("IC_ENV_GUARD_CONFIG", str(explicit))
    assert cli.resolve_helper_config_path() == explicit
    monkeypatch.delenv("IC_ENV_GUARD_CONFIG")
    assert cli.resolve_helper_config_path() == user_config
    user_config.unlink()
    assert cli.resolve_helper_config_path() == cli.DEFAULT_CONFIG


def test_runtime_passes_source_config_path_to_coordinated_launcher(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        f"mode: agent\nauth:\n  token_file: {token}\n",
        encoding="utf-8",
    )
    launcher = AsyncMock()
    monkeypatch.setattr("ic_env_guard.main.serve_config", launcher)

    assert cli.runtime_main(["--config", str(config_path)]) == 0

    assert launcher.await_args.kwargs["config_path"] == config_path


def test_fixed_helper_uses_resolved_user_config_without_cli_arguments(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    config_path = tmp_path / "edaops.yaml"
    config_path.write_text(
        f"mode: agent\nauth:\n  token_file: {token}\n",
        encoding="utf-8",
    )
    helper = Mock(return_value=0)
    monkeypatch.setattr(cli, "resolve_helper_config_path", lambda: config_path)
    monkeypatch.setattr(cli, "run_helper", helper)

    assert cli.runtime_main(["agent", "enroll-manager"]) == 0

    helper.assert_called_once()


def test_local_bootstrap_composition_follows_development_gate(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)

    enabled = AppConfig.model_validate(
        {
            "mode": "control-plane",
            "server": {"bind": "127.0.0.1"},
            "auth": {"token_file": token},
            "development": {
                "allow_insecure_http": True,
                "local_agent_bootstrap": True,
            },
            "enrollment": {"manager_socket_path": runtime / "manager.sock"},
            "control_plane": {"audit_database": tmp_path / "enabled.db"},
        }
    )
    disabled = enabled.model_copy(
        update={
            "development": enabled.development.model_copy(
                update={"local_agent_bootstrap": False}
            ),
            "control_plane": enabled.control_plane.model_copy(
                update={"audit_database": tmp_path / "disabled.db"}
            ),
        }
    )

    enabled_container = build_manager_container(enabled)
    disabled_container = build_manager_container(disabled)
    try:
        enabled_client = enabled_container.enrollment_orchestrator._local_socket_client
        assert isinstance(enabled_client, LocalEnrollmentSocketClient)
        assert enabled_client._allowed_root == runtime.resolve()
        assert enabled_container.enrollment_orchestrator._local_bootstrap_enabled is True
        assert enabled_container.manager_enrollment_socket._local_bootstrap_enabled is True

        assert disabled_container.enrollment_orchestrator._local_socket_client is None
        assert disabled_container.enrollment_orchestrator._local_bootstrap_enabled is False
        assert disabled_container.manager_enrollment_socket._local_bootstrap_enabled is False
    finally:
        enabled_container.database_engine.dispose()
        disabled_container.database_engine.dispose()

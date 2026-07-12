from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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

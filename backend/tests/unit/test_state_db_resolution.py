from pathlib import Path

import pytest

from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import _resolve_state_db


def _config(tmp_path: Path, state_database: Path | None = None) -> AppConfig:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return AppConfig(auth=AuthConfig(token_file=token_file), state_database=state_database)


@pytest.mark.unit
def test_explicit_state_database_argument_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "env.db"))
    config = _config(tmp_path, tmp_path / "config.db")

    assert _resolve_state_db(tmp_path / "arg.db", config) == tmp_path / "arg.db"


@pytest.mark.unit
def test_configured_state_database_wins_when_argument_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "env.db"))
    config = _config(tmp_path, tmp_path / "config.db")

    assert _resolve_state_db(None, config) == tmp_path / "config.db"


@pytest.mark.unit
def test_environment_state_database_wins_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "env.db"))
    config = _config(tmp_path)

    assert _resolve_state_db(None, config) == tmp_path / "env.db"


@pytest.mark.unit
def test_default_state_database_is_used_last(tmp_path, monkeypatch):
    monkeypatch.delenv("IC_ENV_GUARD_STATE_DB", raising=False)
    config = _config(tmp_path)

    assert _resolve_state_db(None, config) == Path("/var/lib/ic-env-guard/state.db")

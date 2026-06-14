from pathlib import Path

import pytest
from pydantic import ValidationError

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app


def _token_file(tmp_path: Path) -> Path:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


@pytest.mark.contract
def test_agent_mode_is_default(tmp_path):
    config = AppConfig(auth=AuthConfig(token_file=_token_file(tmp_path)))

    assert config.mode == "agent"


@pytest.mark.contract
def test_combined_mode_is_not_accepted(tmp_path):
    with pytest.raises(ValidationError, match="combined"):
        AppConfig(auth=AuthConfig(token_file=_token_file(tmp_path)), mode="combined")


@pytest.mark.contract
def test_control_plane_mode_does_not_create_agent_state_database(tmp_path):
    state_database = tmp_path / "must-not-exist.db"
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[],
    )

    create_app(config=config, state_database=state_database)

    assert not state_database.exists()

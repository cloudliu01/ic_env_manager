import pytest


@pytest.fixture(autouse=True)
def isolated_state_database(tmp_path, monkeypatch):
    monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "state.db"))

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, LogsConfig
from ic_env_guard.logs.models import LogSourceInput
from ic_env_guard.main import create_ingest_app, create_public_app

AUTH = {"Authorization": "Bearer secret-token"}


@pytest.mark.integration
def test_log_metadata_persists_and_stale_tail_returns_gone_after_restart(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    path = root / "run.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file), logs=LogsConfig(allowed_roots=[root])
    )
    database = tmp_path / "state.db"
    first = build_agent_container(config, database)
    observed = datetime.now(UTC) - timedelta(seconds=2)
    first.log_source_service.upsert(
        "run-log",
        LogSourceInput(
            path=str(path),
            last_updated=observed,
            observed_at=observed,
            ttl_seconds=1,
        ),
        now=observed,
    )
    first.database_engine.dispose()

    second = build_agent_container(config, database)
    with TestClient(create_public_app(second)) as public:
        stale = public.get("/api/v2/logs/run-log/tail", headers=AUTH)
    with TestClient(create_ingest_app(second), client=("127.0.0.1", 50000)) as ingest:
        assert ingest.get("/api/v2/logs/run-log/tail").status_code == 404

    assert stale.status_code == 410
    assert stale.json()["error"]["code"] == "log_source_stale"
    second.database_engine.dispose()

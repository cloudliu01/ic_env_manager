from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, EnrollmentConfig, LogsConfig
from ic_env_guard.logs.models import LogSourceInput
from ic_env_guard.main import create_ingest_app, create_public_app

AUTH = {"Authorization": "Bearer secret-token"}


@pytest.fixture
def enrollment_runtime_dir():
    path = Path(mkdtemp(prefix="ieg-log-tail-", dir="/tmp"))
    path.chmod(0o700)
    yield path
    rmtree(path, ignore_errors=True)


@pytest.mark.integration
def test_log_metadata_persists_and_stale_tail_returns_gone_after_restart(
    tmp_path, enrollment_runtime_dir
):
    root = tmp_path / "logs"
    root.mkdir()
    path = root / "run.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        logs=LogsConfig(allowed_roots=[root]),
        enrollment=EnrollmentConfig(
            socket_path=enrollment_runtime_dir / "enrollment.sock", socket_mode="0600"
        ),
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
        stale_detail = public.get("/api/v2/logs/run-log", headers=AUTH)
        listing = public.get("/api/v2/logs", headers=AUTH)
    with TestClient(create_ingest_app(second), client=("127.0.0.1", 50000)) as ingest:
        assert ingest.get("/api/v2/logs/run-log/tail").status_code == 404

    assert stale.status_code == 410
    assert stale.json()["error"]["code"] == "log_source_stale"
    assert stale_detail.status_code == 410
    assert stale_detail.json()["error"]["code"] == "log_source_stale"
    assert listing.json()["items"] == []
    assert not config.enrollment.socket_path.exists()
    second.database_engine.dispose()


@pytest.mark.integration
def test_log_tail_response_metadata_and_content_use_one_repository_snapshot(
    tmp_path, monkeypatch, enrollment_runtime_dir
):
    root = tmp_path / "logs"
    root.mkdir()
    first_path = root / "first.log"
    first_path.write_text("first-snapshot\n", encoding="utf-8")
    second_path = root / "second.log"
    second_path.write_text("second-snapshot\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        logs=LogsConfig(allowed_roots=[root]),
        enrollment=EnrollmentConfig(
            socket_path=enrollment_runtime_dir / "enrollment.sock", socket_mode="0600"
        ),
    )
    container = build_agent_container(config, tmp_path / "state.db")
    now = datetime.now(UTC)
    first = container.log_source_service.upsert(
        "run-log",
        LogSourceInput(
            path=str(first_path),
            last_updated=now,
            observed_at=now,
            ttl_seconds=120,
        ),
        now=now,
    ).record
    second = replace(
        first,
        path=second_path.resolve(),
        last_updated=first.last_updated + timedelta(seconds=1),
    )
    calls = 0

    def changing_get(log_id):
        nonlocal calls
        assert log_id == "run-log"
        calls += 1
        return first if calls == 1 else second

    monkeypatch.setattr(container.log_source_repository, "get", changing_get)
    with TestClient(create_public_app(container)) as public:
        response = public.get("/api/v2/logs/run-log/tail", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["path"] == str(first_path.resolve())
    assert response.json()["last_updated"] == first.last_updated.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    assert response.json()["lines"] == ["first-snapshot"]
    assert calls == 1
    assert not config.enrollment.socket_path.exists()
    container.database_engine.dispose()

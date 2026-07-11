from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, LogsConfig
from ic_env_guard.main import create_ingest_app, create_public_app

AUTH = {"Authorization": "Bearer secret-token"}


def _apps(tmp_path, **log_settings):
    root = tmp_path / "logs"
    root.mkdir()
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    container = build_agent_container(
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            logs=LogsConfig(allowed_roots=[root], **log_settings),
        ),
        tmp_path / "state.db",
    )
    return (
        root,
        container,
        TestClient(create_ingest_app(container), client=("127.0.0.1", 50000)),
        TestClient(create_public_app(container)),
    )


def _register(ingest, path, log_id="run-log"):
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return ingest.put(
        f"/api/v2/logs/{log_id}",
        json={
            "path": str(path),
            "last_updated": now,
            "observed_at": now,
            "ttl_seconds": 120,
        },
    )


@pytest.mark.contract
@pytest.mark.security
def test_log_reads_require_auth_and_ingest_listener_exposes_no_reads(tmp_path):
    _, container, ingest, public = _apps(tmp_path)

    assert public.get("/api/v2/logs").status_code == 401
    assert public.get("/api/v2/logs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert ingest.get("/api/v2/logs").status_code == 404
    assert ingest.get("/api/v2/logs/run-log").status_code == 404
    assert ingest.get("/api/v2/logs/run-log/tail").status_code == 404
    container.database_engine.dispose()


@pytest.mark.contract
def test_log_list_and_detail_return_fresh_metadata(tmp_path):
    root, container, ingest, public = _apps(tmp_path)
    path = root / "run.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    assert _register(ingest, path).status_code == 201

    listed = public.get("/api/v2/logs", headers=AUTH)
    detail = public.get("/api/v2/logs/run-log", headers=AUTH)
    missing = public.get("/api/v2/logs/missing", headers=AUTH)

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == ["run-log"]
    assert detail.status_code == 200
    assert detail.json()["stale"] is False
    assert detail.json()["path"] == str(path.resolve())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "log_source_not_found"
    container.database_engine.dispose()


@pytest.mark.contract
def test_log_tail_uses_default_and_validates_configured_line_bounds(tmp_path):
    root, container, ingest, public = _apps(tmp_path)
    path = root / "run.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert _register(ingest, path).status_code == 201

    response = public.get("/api/v2/logs/run-log/tail", headers=AUTH)
    zero = public.get("/api/v2/logs/run-log/tail?lines=0", headers=AUTH)
    too_many = public.get("/api/v2/logs/run-log/tail?lines=1001", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["lines"] == ["one", "two", "three"]
    assert response.json()["line_count"] == 3
    assert zero.status_code == 422
    assert too_many.status_code == 422
    container.database_engine.dispose()


@pytest.mark.contract
def test_log_tail_uses_container_configured_default_and_maximum_lines(tmp_path):
    root, container, ingest, public = _apps(
        tmp_path, default_tail_lines=2, max_tail_lines=2
    )
    path = root / "run.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert _register(ingest, path).status_code == 201

    defaulted = public.get("/api/v2/logs/run-log/tail", headers=AUTH)
    over_configured_maximum = public.get(
        "/api/v2/logs/run-log/tail?lines=3", headers=AUTH
    )

    assert defaulted.json()["lines"] == ["two", "three"]
    assert over_configured_maximum.status_code == 422
    assert over_configured_maximum.json()["error"]["code"] == "validation_error"
    container.database_engine.dispose()

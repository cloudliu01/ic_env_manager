import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ic_env_guard.api.logs import get_log_tail_audit_recorder
from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, LogsConfig
from ic_env_guard.main import create_ingest_app, create_public_app

AUTH = {"Authorization": "Bearer secret-token", "X-Correlation-ID": "tail-correlation"}


def _setup(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    container = build_agent_container(
        AppConfig(auth=AuthConfig(token_file=token_file), logs=LogsConfig(allowed_roots=[root])),
        tmp_path / "state.db",
    )
    return root, container, create_ingest_app(container), create_public_app(container)


def _register(app, path, log_id="run-log", ttl=120):
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        return client.put(
            f"/api/v2/logs/{log_id}",
            json={"path": str(path), "last_updated": now, "observed_at": now, "ttl_seconds": ttl},
        )


@pytest.mark.security
def test_tail_distinguishes_unknown_stale_missing_and_moved_paths(tmp_path):
    root, container, ingest_app, public_app = _setup(tmp_path)
    path = root / "run.log"
    path.write_text("safe\n", encoding="utf-8")
    assert _register(ingest_app, path).status_code == 201
    with TestClient(public_app) as public:
        unknown = public.get("/api/v2/logs/unknown/tail", headers=AUTH)
        path.unlink()
        missing = public.get("/api/v2/logs/run-log/tail", headers=AUTH)
        path.write_text("safe\n", encoding="utf-8")
        assert _register(ingest_app, path, "moved").status_code == 201
        outside = tmp_path / "outside.log"
        outside.write_text("secret\n", encoding="utf-8")
        path.unlink()
        path.symlink_to(outside)
        moved = public.get("/api/v2/logs/moved/tail", headers=AUTH)

    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "log_source_not_found")
    assert (missing.status_code, missing.json()["error"]["code"]) == (410, "log_file_unavailable")
    assert (moved.status_code, moved.json()["error"]["code"]) == (403, "log_path_forbidden")
    with container.database_engine.connect() as connection:
        audits = connection.execute(
            text(
                "SELECT * FROM audit_events "
                "WHERE operation = 'logs.tail' ORDER BY id"
            )
        ).mappings().all()
    by_target = {row["target_id"]: row for row in audits}
    moved_audit = by_target["moved"]
    assert by_target["unknown"]["result"] == "rejected"
    assert by_target["run-log"]["result"] == "failed"
    assert moved_audit["result"] == "denied"
    assert moved_audit["failure_reason"] == "lines=100;result=log_path_forbidden"
    assert str(path) not in str(dict(moved_audit))
    assert "secret" not in str(dict(moved_audit))
    container.database_engine.dispose()


@pytest.mark.security
def test_tail_invalid_utf8_is_bounded_and_content_never_enters_sqlite_or_audit(tmp_path):
    root, container, ingest_app, public_app = _setup(tmp_path)
    marker = "UNIQUE-LOG-CONTENT-DO-NOT-PERSIST"
    path = root / "large.log"
    path.write_bytes((b'"\\' * 500_000) + b"\n" + marker.encode() + b"-\xff\n")
    assert _register(ingest_app, path).status_code == 201

    with TestClient(public_app) as public:
        response = public.get("/api/v2/logs/run-log/tail?lines=1000", headers=AUTH)

    assert response.status_code == 200
    assert "\ufffd" in response.text
    assert response.json()["truncated"] is True
    assert len(response.content) < 1024 * 1024
    with container.database_engine.connect() as connection:
        for table in ("log_sources", "audit_events"):
            rows = connection.execute(text(f"SELECT * FROM {table}")).fetchall()
            assert marker not in json.dumps([tuple(row) for row in rows], default=str)
        audit = connection.execute(
            text("SELECT * FROM audit_events WHERE operation = 'logs.tail'")
        ).mappings().one()
    assert audit["actor_id"] == "local-admin"
    assert audit["target_id"] == "run-log"
    assert audit["source_addr"] == "testclient"
    assert audit["correlation_id"] == "tail-correlation"
    assert "lines=1000" in audit["failure_reason"]
    assert str(path) not in str(dict(audit))
    container.database_engine.dispose()


@pytest.mark.security
def test_tail_audit_failure_fails_closed_without_returning_content(tmp_path):
    root, container, ingest_app, public_app = _setup(tmp_path)
    path = root / "run.log"
    path.write_text("must-not-be-returned\n", encoding="utf-8")
    assert _register(ingest_app, path).status_code == 201

    class FailingRecorder:
        def record(self, **kwargs):
            raise RuntimeError("audit offline")

    public_app.dependency_overrides[get_log_tail_audit_recorder] = lambda: FailingRecorder()
    with TestClient(public_app) as public:
        response = public.get("/api/v2/logs/run-log/tail", headers=AUTH)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audit_storage_unavailable"
    assert "must-not-be-returned" not in response.text
    container.database_engine.dispose()

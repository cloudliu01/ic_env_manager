from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, IngestConfig
from ic_env_guard.main import create_ingest_app, create_public_app


def _container(tmp_path, **ingest_settings):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file), ingest=IngestConfig(**ingest_settings)
    )
    return build_agent_container(config, tmp_path / "state.db")


def _payload(**changes):
    payload = {
        "namespace": "eda",
        "name": "check",
        "kind": "gauge",
        "value": 1,
        "status": "ok",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }
    payload.update(changes)
    return payload


@pytest.mark.security
def test_public_and_ingest_apps_expose_disjoint_route_sets(tmp_path):
    container = _container(tmp_path)
    public = TestClient(create_public_app(container))
    ingest = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))

    assert ingest.put("/api/v2/observations", json=_payload()).status_code == 201
    assert public.put("/api/v2/observations", json=_payload()).status_code == 404
    assert ingest.get("/api/v2/observations").status_code == 404
    assert ingest.post("/api/terminals", json={"title": "escape"}).status_code == 404
    container.database_engine.dispose()


@pytest.mark.security
def test_ingest_uses_actual_peer_and_ignores_forwarding_headers(tmp_path):
    container = _container(tmp_path)
    remote = TestClient(create_ingest_app(container), client=("198.51.100.10", 50000))

    response = remote.put(
        "/api/v2/observations",
        json=_payload(),
        headers={
            "Forwarded": "for=127.0.0.1",
            "X-Forwarded-For": "127.0.0.1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ingest_peer_forbidden"
    container.database_engine.dispose()


@pytest.mark.security
def test_ingest_enforces_request_body_limit_with_stable_error(tmp_path):
    container = _container(tmp_path, max_request_bytes=1024)
    client = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))

    response = client.put(
        "/api/v2/observations", json=_payload(details={"blob": "x" * 2000})
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    container.database_engine.dispose()


@pytest.mark.security
def test_ingest_enforces_concurrency_limit_with_stable_error(tmp_path, monkeypatch):
    container = _container(tmp_path, max_concurrent_requests=1)
    app = create_ingest_app(container)
    entered = Event()
    release = Event()
    original = container.observation_service.upsert

    def blocked_upsert(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)
        return original(*args, **kwargs)

    monkeypatch.setattr(container.observation_service, "upsert", blocked_upsert)

    def first_request():
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            return client.put("/api/v2/observations", json=_payload(name="first"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(first_request)
        assert entered.wait(timeout=3)
        with TestClient(app, client=("127.0.0.1", 50001)) as client:
            overflow = client.put("/api/v2/observations", json=_payload(name="second"))
        release.set()
        assert pending.result(timeout=3).status_code == 201

    assert overflow.status_code == 503
    assert overflow.json()["error"]["code"] == "ingest_capacity_exceeded"
    container.database_engine.dispose()

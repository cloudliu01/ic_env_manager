import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import create_ingest_app, create_public_app
from ic_env_guard.observations.models import ObservationInput


def _apps(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    container = build_agent_container(
        AppConfig(auth=AuthConfig(token_file=token_file)), tmp_path / "state.db"
    )
    return (
        container,
        TestClient(create_ingest_app(container), client=("127.0.0.1", 50000)),
        TestClient(create_public_app(container)),
    )


def _payload(name: str, observed_at: datetime, *, status="ok", details=None, ttl=120):
    return {
        "namespace": "eda",
        "name": name,
        "kind": "gauge",
        "value": 1,
        "status": status,
        "labels": {},
        "details": details or {},
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "ttl_seconds": ttl,
    }


AUTH = {"Authorization": "Bearer secret-token"}


@pytest.mark.contract
@pytest.mark.security
def test_observation_reads_require_valid_bearer_authentication(tmp_path):
    container, _, public = _apps(tmp_path)

    missing = public.get("/api/v2/observations")
    invalid = public.get(
        "/api/v2/observations", headers={"Authorization": "Bearer wrong"}
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"
    container.database_engine.dispose()


@pytest.mark.contract
def test_observation_list_filters_and_preserves_details(tmp_path):
    container, ingest, public = _apps(tmp_path)
    now = datetime.now(UTC)
    ingest.put(
        "/api/v2/observations",
        json=_payload("warning_check", now, status="warning", details={"pid": 1234}),
    )
    ingest.put(
        "/api/v2/observations", json=_payload("ok_check", now, status="ok")
    )

    response = public.get(
        "/api/v2/observations?namespace=eda&status=warning&limit=10", headers=AUTH
    )

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["warning_check"]
    assert response.json()["items"][0]["details"] == {"pid": 1234}
    assert response.json()["next_cursor"] is None
    container.database_engine.dispose()


@pytest.mark.contract
def test_observation_reads_hide_stale_by_default_and_allow_authorized_opt_in(tmp_path):
    container, _, public = _apps(tmp_path)
    observed_at = datetime.now(UTC) - timedelta(seconds=2)
    record = container.observation_service.upsert(
        ObservationInput.model_validate(_payload("short_lived", observed_at, ttl=1)),
        now=observed_at,
    )
    identity_key = record.record.identity_key

    default_list = public.get("/api/v2/observations", headers=AUTH)
    default_detail = public.get(f"/api/v2/observations/{identity_key}", headers=AUTH)
    stale_list = public.get(
        "/api/v2/observations?include_stale=true", headers=AUTH
    )
    stale_detail = public.get(
        f"/api/v2/observations/{identity_key}?include_stale=true", headers=AUTH
    )

    assert default_list.json()["items"] == []
    assert default_detail.status_code == 404
    assert [item["identity_key"] for item in stale_list.json()["items"]] == [identity_key]
    assert stale_list.json()["items"][0]["stale"] is True
    assert stale_detail.status_code == 200
    assert stale_detail.json()["stale"] is True
    container.database_engine.dispose()


@pytest.mark.contract
def test_observation_cursor_is_opaque_versioned_and_malformed_values_are_rejected(tmp_path):
    container, ingest, public = _apps(tmp_path)
    now = datetime.now(UTC)
    for name in ("first", "second"):
        assert ingest.put("/api/v2/observations", json=_payload(name, now)).status_code == 201

    first = public.get("/api/v2/observations?limit=1", headers=AUTH)
    cursor = first.json()["next_cursor"]
    second = public.get(
        "/api/v2/observations", params={"limit": 1, "cursor": cursor}, headers=AUTH
    )
    malformed = public.get(
        "/api/v2/observations?cursor=definitely-not-a-cursor", headers=AUTH
    )
    wrong_version = base64.urlsafe_b64encode(
        json.dumps({"v": 2, "sort": ["a" * 64]}).encode()
    ).decode().rstrip("=")
    unsupported = public.get(
        "/api/v2/observations", params={"cursor": wrong_version}, headers=AUTH
    )

    assert first.status_code == 200
    assert cursor and len(cursor) < 256
    assert first.json()["items"][0]["identity_key"] not in cursor
    assert second.status_code == 200
    assert second.json()["items"][0]["identity_key"] != first.json()["items"][0]["identity_key"]
    for response in (malformed, unsupported):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"
    container.database_engine.dispose()

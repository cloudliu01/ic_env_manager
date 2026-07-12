from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import (
    AppConfig,
    AuthConfig,
    ServerConfig,
    TrustedLanHttpServerConfig,
)
from ic_env_guard.main import create_ingest_app, create_public_app


def _container(tmp_path, *client_cidrs: str):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        server=ServerConfig(
            bind="0.0.0.0",
            remote_bind_enabled=True,
            trusted_lan_http=TrustedLanHttpServerConfig(
                enabled=True,
                client_cidrs=list(client_cidrs),
            ),
        ),
    )
    return build_agent_container(config, tmp_path / "state.db")


@pytest.mark.security
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/healthz"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/terminals"),
        ("GET", "/api/v2/logs/example/tail"),
        ("GET", "/metrics"),
    ],
)
def test_public_http_routes_reject_socket_peers_outside_configured_cidrs(tmp_path, method, path):
    container = _container(tmp_path, "10.20.30.0/24")
    client = TestClient(create_public_app(container), client=("192.168.50.10", 50000))

    response = client.request(
        method,
        path,
        headers={
            "Forwarded": "for=10.20.30.40",
            "X-Forwarded-For": "10.20.30.40",
            "X-Correlation-ID": "cidr-denied-42",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "public_client_forbidden",
            "message": "public access requires a trusted client network",
            "correlation_id": "cidr-denied-42",
        }
    }
    assert response.headers["X-Correlation-ID"] == "cidr-denied-42"
    container.database_engine.dispose()


@pytest.mark.security
@pytest.mark.parametrize(
    ("cidr", "peer"),
    [
        ("10.20.30.0/24", "10.20.30.40"),
        ("fd12:3456:789a::/48", "fd12:3456:789a::40"),
    ],
)
def test_public_http_uses_actual_ipv4_or_ipv6_socket_peer(tmp_path, cidr, peer):
    container = _container(tmp_path, cidr)
    client = TestClient(create_public_app(container), client=(peer, 50000))

    response = client.get(
        "/healthz",
        headers={
            "Forwarded": "for=192.168.50.10",
            "X-Forwarded-For": "192.168.50.10",
        },
    )

    assert response.status_code == 200
    container.database_engine.dispose()


@pytest.mark.security
@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "not-an-ip", ""])
def test_public_http_fails_closed_unless_exact_peer_is_in_a_configured_cidr(tmp_path, peer):
    container = _container(tmp_path, "10.20.30.0/24")
    response = TestClient(create_public_app(container), client=(peer, 50000)).get("/healthz")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "public_client_forbidden"
    container.database_engine.dispose()


@pytest.mark.security
@pytest.mark.asyncio
async def test_public_http_fails_closed_when_asgi_client_is_missing(tmp_path):
    container = _container(tmp_path, "10.20.30.0/24")
    app = create_public_app(container)
    sent = []
    request_received = False

    async def receive():
        nonlocal request_received
        if request_received:
            return {"type": "http.disconnect"}
        request_received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/healthz",
            "raw_path": b"/healthz",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": None,
            "server": ("0.0.0.0", 8765),
        },
        receive,
        send,
    )

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    assert response_start["status"] == 403
    container.database_engine.dispose()


@pytest.mark.security
def test_public_websocket_rejects_peer_with_stable_close_code_and_reason(tmp_path):
    container = _container(tmp_path, "10.20.30.0/24")
    client = TestClient(create_public_app(container), client=("192.168.50.10", 50000))

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            "/ws/terminals/not-created",
            headers={"X-Forwarded-For": "10.20.30.40"},
        ):
            pass

    assert rejected.value.code == 4403
    assert rejected.value.reason == "public_client_forbidden"
    container.database_engine.dispose()


@pytest.mark.security
def test_public_websocket_allows_configured_peer_to_reach_route(tmp_path):
    container = _container(tmp_path, "10.20.30.0/24")
    client = TestClient(create_public_app(container), client=("10.20.30.40", 50000))

    with pytest.raises(WebSocketDisconnect) as route_rejection:
        with client.websocket_connect("/ws/terminals/not-created"):
            pass

    assert route_rejection.value.code == 4401
    container.database_engine.dispose()


@pytest.mark.security
def test_local_ingest_does_not_apply_public_client_cidrs(tmp_path):
    container = _container(tmp_path, "10.20.30.0/24")
    ingest = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))
    payload = {
        "namespace": "eda",
        "name": "trusted_lan_isolation",
        "kind": "gauge",
        "value": 1,
        "status": "ok",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }

    assert ingest.put("/api/v2/observations", json=payload).status_code == 201
    container.database_engine.dispose()

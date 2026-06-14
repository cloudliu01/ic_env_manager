import asyncio
import ssl
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.agents.terminal_proxy import GatewayTicketStore
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agent_terminal_ws import AgentWebSocketConnector, get_agent_ws_connector
from ic_env_guard.api.agent_terminals import get_gateway_ticket_store
from ic_env_guard.api.agents import get_agent_availability
from ic_env_guard.config.models import (
    AgentConfig,
    AgentTlsConfig,
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
)
from ic_env_guard.main import create_app

CAPABILITIES = {
    "api_version": "1",
    "agent_version": "0.2.0",
    "capabilities": ["services.v1", "terminals.v1", "audit.v1", "monitoring.snapshot.v1"],
}


def _token_file(tmp_path, name="token"):
    token_file = tmp_path / name
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _config(tmp_path):
    return AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[
            AgentConfig(
                id="lab-01",
                name="Lab 01",
                base_url="https://lab-01.example",
                token_file=_token_file(tmp_path, "lab-01.token"),
            )
        ],
    )


class FakeUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.close_codes: list[int] = []
        self._input_received = asyncio.Event()
        self._output_sent = False

    async def send(self, text: str) -> None:
        self.sent.append(text)
        self._input_received.set()

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)
        self._input_received.set()
        return None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._output_sent:
            raise StopAsyncIteration
        await self._input_received.wait()
        self._output_sent = True
        return "remote-output"


class FakeConnectContext:
    def __init__(self, upstream: FakeUpstreamWebSocket) -> None:
        self.upstream = upstream

    async def __aenter__(self) -> FakeUpstreamWebSocket:
        return self.upstream

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeConnector:
    def __init__(self, upstream: FakeUpstreamWebSocket) -> None:
        self.upstream = upstream
        self.calls: list[tuple[str, str, str, int, str]] = []

    def connect(
        self,
        agent: AgentConfig,
        terminal_id: str,
        ticket: str,
        cursor: int,
        correlation_id: str,
    ):
        self.calls.append((agent.id, terminal_id, ticket, cursor, correlation_id))
        return FakeConnectContext(self.upstream)


def _ws_headers() -> dict[str, str]:
    return {"Sec-WebSocket-Protocol": "bearer.c2VjcmV0LXRva2Vu"}


def _availability(config: AppConfig, capabilities: tuple[str, ...]) -> AgentAvailabilityService:
    service = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    service.record_ready_for_test("lab-01", datetime.now(UTC), capabilities=capabilities)
    return service


@pytest.mark.integration
def test_agent_terminal_websocket_proxies_frames_and_audits_attach(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        assert request.url.path == "/api/terminals/term-1/connect-token"
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _availability(
        config, tuple(CAPABILITIES["capabilities"])
    )
    upstream = FakeUpstreamWebSocket()
    connector = FakeConnector(upstream)
    app.dependency_overrides[get_agent_ws_connector] = lambda: connector

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        ticket = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        ).json()["ticket"]
        with client.websocket_connect(
            f"/ws/agents/lab-01/terminals/term-1?ticket={ticket}&cursor=7",
            headers=_ws_headers(),
        ) as ws:
            ws.send_text("input")
            assert ws.receive_text() == "remote-output"
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        )

    assert connector.calls[0][:4] == ("lab-01", "term-1", "upstream-ticket", 7)
    assert upstream.sent == ["input"]
    event = audit.json()["events"][0]
    assert event["result"] == "success"
    assert event["dispatch_state"] == "dispatched"
    assert event["actor_id"] == "local-admin"
    assert event["correlation_id"]
    assert connector.calls[0][4] == event["correlation_id"]


@pytest.mark.integration
def test_agent_terminal_websocket_rejects_oversized_browser_frames_and_audits(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        assert request.url.path == "/api/terminals/term-1/connect-token"
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _availability(
        config, tuple(CAPABILITIES["capabilities"])
    )
    upstream = FakeUpstreamWebSocket()
    app.dependency_overrides[get_agent_ws_connector] = lambda: FakeConnector(upstream)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        ticket = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        ).json()["ticket"]
        with client.websocket_connect(
            f"/ws/agents/lab-01/terminals/term-1?ticket={ticket}&cursor=0",
            headers=_ws_headers(),
        ) as ws:
            ws.send_text("x" * (64 * 1024 + 1))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        )

    assert upstream.sent == []
    assert upstream.close_codes == [4413]
    event = audit.json()["events"][0]
    assert event["result"] == "failed"
    assert event["dispatch_state"] == "dispatched"
    assert event["failure_category"] == "frame_limit"


@pytest.mark.integration
def test_agent_terminal_websocket_rejects_proxy_capacity_with_audit(tmp_path):
    config = _config(tmp_path)
    config.control_plane.max_active_terminal_proxies = 0
    app = create_app(config=config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/agents/lab-01/terminals/term-1?ticket=unused&cursor=0",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        )

    assert exc.value.code == 4429
    event = audit.json()["events"][0]
    assert event["result"] == "failed"
    assert event["dispatch_state"] == "not_dispatched"
    assert event["failure_category"] == "gateway_capacity_exceeded"
    assert event["correlation_id"]


@pytest.mark.integration
def test_agent_terminal_websocket_audits_invalid_cursor_before_dispatch(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/agents/lab-01/terminals/term-1?ticket=unused&cursor=bad",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        ).json()["events"]

    assert exc.value.code == 4400
    assert audit[0]["failure_category"] == "invalid_cursor"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.integration
def test_agent_terminal_websocket_audits_missing_ticket_before_dispatch(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/agents/lab-01/terminals/term-1?cursor=0",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        ).json()["events"]

    assert exc.value.code == 4401
    assert audit[0]["failure_category"] == "missing_ticket"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.integration
def test_agent_terminal_websocket_audits_invalid_ticket_before_dispatch(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/agents/lab-01/terminals/term-1?ticket=invalid&cursor=0",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        ).json()["events"]

    assert exc.value.code == 4401
    assert audit[0]["failure_category"] == "invalid_ticket"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.integration
def test_agent_terminal_websocket_rejects_missing_capability_before_ticket_use(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        assert request.url.path == "/api/terminals/term-1/connect-token"
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _availability(
        config, tuple(CAPABILITIES["capabilities"])
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        ticket = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        ).json()["ticket"]
        app.dependency_overrides[get_agent_availability] = lambda: _availability(
            config, ("services.v1",)
        )
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/ws/agents/lab-01/terminals/term-1?ticket={ticket}&cursor=0",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        ).json()["events"]

    assert exc.value.code == 4409
    assert audit[0]["failure_category"] == "missing_capability"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.integration
def test_agent_terminal_websocket_rejects_missing_browser_auth(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/agents/lab-01/terminals/term-1?ticket=unused&cursor=0"
            ):
                pass

    assert exc.value.code == 4401


@pytest.mark.integration
def test_agent_terminal_websocket_rejects_mismatched_authenticated_actor(tmp_path):
    app = create_app(config=_config(tmp_path))
    tickets = GatewayTicketStore()
    reservation = tickets.reserve()
    assert reservation is not None
    gateway_ticket = tickets.commit(
        reservation,
        actor_id="other-actor",
        agent_id="lab-01",
        terminal_id="term-1",
        intended_ws_path="/ws/agents/lab-01/terminals/term-1",
        upstream_ticket="upstream-ticket",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    app.dependency_overrides[get_gateway_ticket_store] = lambda: tickets

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/ws/agents/lab-01/terminals/term-1?ticket={gateway_ticket.ticket}&cursor=0",
                headers=_ws_headers(),
            ):
                pass
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.attach"},
        ).json()["events"]

    assert exc.value.code == 4403
    assert audit[0]["failure_category"] == "actor_mismatch"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.integration
def test_agent_websocket_connector_uses_agent_tls_ca_bundle(tmp_path, monkeypatch):
    token_file = _token_file(tmp_path, "tls-agent.token")
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")
    captured = {}

    def fake_create_default_context(*, cafile=None):
        captured["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    class FakeConnection:
        pass

    def fake_connect(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr("ic_env_guard.api.agent_terminal_ws.websockets.connect", fake_connect)

    agent = AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        token_file=token_file,
        tls=AgentTlsConfig(ca_bundle=ca_bundle),
    )

    result = AgentWebSocketConnector().connect(agent, "term-1", "ticket", 5, "corr-1")

    assert result is captured["ssl"] or result is not None
    assert captured["url"] == "wss://lab-01.example/ws/terminals/term-1?ticket=ticket&cursor=5"
    assert captured["cafile"] == str(ca_bundle)
    assert isinstance(captured["ssl"], ssl.SSLContext)
    assert captured["proxy"] is None
    assert captured["additional_headers"]["X-Correlation-ID"] == "corr-1"
    assert result.process_redirect(RuntimeError("redirect")) is not None

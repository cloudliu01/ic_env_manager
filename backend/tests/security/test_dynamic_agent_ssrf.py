import ssl
from contextlib import asynccontextmanager

import httpx
import pytest

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import VerifiedTlsProfile


def _target():
    return AgentTargetPolicy(
        allowed_agent_cidrs=["10.20.30.0/24"],
        resolver=lambda _host, _port: ("10.20.30.10",),
    ).resolve("https://agent.example:8765", VerifiedTlsProfile(id="system-tls"))


@pytest.mark.security
@pytest.mark.asyncio
async def test_client_connects_to_pinned_ip_and_preserves_host_and_sni():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions["sni_hostname"]
        return httpx.Response(200, json={"ok": True})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    response = await client.request(_target(), b"manager-token", "GET", "/api/v2/summary")
    await client.aclose()

    assert response.json() == {"ok": True}
    assert seen == {
        "url": "https://10.20.30.10:8765/api/v2/summary",
        "host": "agent.example:8765",
        "sni": b"agent.example",
    }


@pytest.mark.security
@pytest.mark.asyncio
async def test_client_disables_redirects_environment_proxy_and_uses_connect_and_total_timeouts(
    monkeypatch,
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            captured["request"] = kwargs
            request = httpx.Request(args[0], args[1])
            yield httpx.Response(
                302, headers={"location": "https://evil.example"}, request=request
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("ic_env_guard.agents.client.httpx.AsyncClient", FakeClient)
    client = AgentHttpClient(connect_timeout_seconds=2, request_timeout_seconds=7)
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(_target(), b"token", "GET", "/api/v2/summary")

    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    timeout = captured["request"]["timeout"]
    assert timeout.connect == 2
    assert timeout.read == 7


@pytest.mark.security
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (httpx.ConnectError("no route"), "agent_network_error"),
        (httpx.ConnectError("TLS", request=None), "agent_tls_error"),
        (httpx.TimeoutException("slow"), "agent_network_error"),
        (httpx.RemoteProtocolError("invalid framing"), "agent_protocol_error"),
    ],
)
async def test_client_maps_transport_failures_to_stable_safe_categories(
    monkeypatch, exception, category
):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            if str(exception) == "TLS":
                exception.__cause__ = ssl.SSLError("private tls detail")
            raise exception
            yield

        async def aclose(self):
            pass

    monkeypatch.setattr("ic_env_guard.agents.client.httpx.AsyncClient", FakeClient)
    with pytest.raises(AgentClientError) as error:
        await AgentHttpClient().request(_target(), b"token", "GET", "/api/v2/summary")
    assert error.value.category == category
    assert "private" not in error.value.message


@pytest.mark.security
@pytest.mark.asyncio
async def test_normal_and_tail_responses_have_separate_hard_bounds():
    async def normal_handler(_request):
        return httpx.Response(200, json={"value": "x" * (1024 * 1024)})

    normal = AgentHttpClient(transport=httpx.MockTransport(normal_handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await normal.request(_target(), b"token", "GET", "/api/v2/summary")
    await normal.aclose()

    async def tail_handler(_request):
        return httpx.Response(200, json={"content": "x" * 1024})

    tail = AgentHttpClient(transport=httpx.MockTransport(tail_handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await tail.request_tail(
            _target(), b"token", "/api/v2/logs/log-1/tail", max_response_bytes=128
        )
    await tail.aclose()

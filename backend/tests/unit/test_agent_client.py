import httpx
import pytest

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.config.models import AgentConfig, AgentTlsConfig


def _agent(tmp_path):
    token_file = tmp_path / "agent-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        token_file=token_file,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_forwards_only_allowlisted_headers(tmp_path):
    observed_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    response = await client.request(
        _agent(tmp_path),
        "GET",
        "/api/capabilities",
        incoming_headers={
            "Authorization": "Bearer browser",
            "Cookie": "x=y",
            "Accept": "application/json",
        },
        correlation_id="corr-1",
    )
    await client.aclose()

    assert response.json() == {"ok": True}
    assert observed_headers["authorization"] == "Bearer agent-secret"
    assert observed_headers["accept"] == "application/json"
    assert observed_headers["x-correlation-id"] == "corr-1"
    assert "cookie" not in observed_headers


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_rejects_redirects_as_protocol_errors(tmp_path):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://other.example"})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(_agent(tmp_path), "GET", "/api/capabilities")
    await client.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_rejects_non_json_responses(tmp_path):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", headers={"Content-Type": "text/plain"})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(_agent(tmp_path), "GET", "/api/capabilities")
    await client.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_rejects_malformed_json_responses(tmp_path):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="not-json",
            headers={"Content-Type": "application/json"},
        )

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(_agent(tmp_path), "GET", "/api/capabilities")
    await client.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_rejects_oversized_responses(tmp_path):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"payload": "x" * (1024 * 1024)})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(_agent(tmp_path), "GET", "/api/capabilities")
    await client.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_client_applies_per_agent_ca_bundle(tmp_path, monkeypatch):
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def request(self, *_args, **_kwargs):
            return httpx.Response(200, json={"ok": True})

        async def aclose(self):
            captured["closed"] = True

    monkeypatch.setattr("ic_env_guard.agents.client.httpx.AsyncClient", FakeAsyncClient)
    agent = AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        token_file=_agent(tmp_path).token_file,
        tls=AgentTlsConfig(ca_bundle=ca_bundle),
    )

    response = await AgentHttpClient().request(agent, "GET", "/api/capabilities")

    assert response.json() == {"ok": True}
    assert captured["verify"] == str(ca_bundle)
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["closed"] is True

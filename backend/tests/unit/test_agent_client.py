import gzip
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.config.models import AgentConfig, AgentTlsConfig
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import VerifiedTlsProfile

VALID_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDCzCCAfOgAwIBAgIUaV8FbGmZ3V8q0KtmyBxGQBEJZ1cwDQYJKoZIhvcNAQEL
BQAwFTETMBEGA1UEAwwKdGFzazMtdGVzdDAeFw0yNjA3MTIwNDE4NDRaFw0zNjA3
MDkwNDE4NDRaMBUxEzARBgNVBAMMCnRhc2szLXRlc3QwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQDNWJb2+4uyhJ/TPPhezMI/1iJSJjRzKV4tKGYwlkny
GScAsnYk8RegAGzkMmGH6JU6j3pDcffzwnLvsaCNbghfD+/1LJNQSn633wXFvPDl
iuPb4wnHCnvAWQuguVpbJDjOd4cSJV/xVJOx9rjRbOwUVJmSUdr+BnK2BuvQbPzz
KDZQ3XsU8H4FciVgWWZwKc8Jny3Ry3+5h2Nl+SNMYlV6HMgXo8457Oijc6A9cp1w
3V9zqLy/wcyuOx95ynNU2FIZKFW2hPrOOxOHJ5bYCI/Y4K0nOW42XsGN4sLQ5Cfh
mo+bH6PjkcBG/iFkdMSUFQG5CBnOjjGotiE5GJyEBpPxAgMBAAGjUzBRMB0GA1Ud
DgQWBBS0ZGGTz4ImyfZ9mCr4w6rhxtPRNTAfBgNVHSMEGDAWgBS0ZGGTz4ImyfZ9
mCr4w6rhxtPRNTAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQAt
Jf82fxp1zlCBf7D7cVS9k4uVKBsL7dIjLC9ZFaG7srNTVjItuWFtjmFWL2MVLejE
hLidVJFcX3g9KBL7qdC0is5TLIj0BPRlqPe8YDP9IYMzsT7p7mGlliV5VwOx3vwj
16lRD8o0IwXr4j10XkkftEPTgmqYOrAonxWmvdZJ0Rk9sDPGKxszjmF5ACrTVb1G
MRFizTlRW6VrnAVNybpHbxyJFB5m9PZCU9r/oR0gL/bca58ctknimav08eP2vPWw
hCs9x7kqc/wLtHVevgxh8OKd5iXQUXPvnOp8FLub+rICRg7WeozXNNeW3Kt9J00t
vVl4EauxOZ8Vuh6SZ/87
-----END CERTIFICATE-----
"""


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


def _validated_target(profile=None):
    return AgentTargetPolicy(
        allowed_agent_cidrs=["10.20.30.0/24"],
        resolver=lambda _host, _port: ("10.20.30.10",),
        self_targets=[("10.20.30.1", 8765)],
    ).resolve(
        "https://agent.example:8765",
        profile or VerifiedTlsProfile(id="system-tls"),
    )


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


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
async def test_agent_client_uses_injected_legacy_credential_loader(tmp_path, monkeypatch):
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        "ic_env_guard.agents.client.load_bearer_token",
        lambda _path: (_ for _ in ()).throw(AssertionError("raw path read")),
    )
    client = AgentHttpClient(
        transport=httpx.MockTransport(handler),
        legacy_credential_loader=lambda _agent: "stored-secret",
    )
    response = await client.request(_agent(tmp_path), "GET", "/api/capabilities")
    await client.aclose()

    assert response.status_code == 200
    assert observed["authorization"] == "Bearer stored-secret"


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

        @asynccontextmanager
        async def stream(self, method, url, **_kwargs):
            yield httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request(method, url),
            )

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validated_target_uses_credential_bytes_and_never_forwards_browser_auth():
    observed_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    await client.request(
        _validated_target(),
        b"manager-token",
        "GET",
        "/api/v2/summary",
        incoming_headers={"Authorization": "Bearer browser", "Cookie": "session=secret"},
    )
    await client.aclose()

    assert observed_headers["authorization"] == "Bearer manager-token"
    assert "cookie" not in observed_headers
    with pytest.raises(TypeError, match="credential bytes"):
        await AgentHttpClient().request(
            _validated_target(), "plaintext-string", "GET", "/api/v2/summary"  # type: ignore[arg-type]
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validated_target_maps_upstream_auth_failure_to_stable_category():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "do not expose this"})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError) as error:
        await client.request(
            _validated_target(), b"manager-token", "GET", "/api/v2/summary"
        )
    await client.aclose()

    assert error.value.category == "agent_auth_error"
    assert "expose" not in error.value.message


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_client_streams_identity_encoded_responses_with_wire_size_precheck(
    tmp_path, legacy
):
    stream = TrackingStream([b'{"ok":true}'])
    observed_accept_encoding = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_accept_encoding
        observed_accept_encoding = request.headers["accept-encoding"]
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "1048577"},
            stream=stream,
        )

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        if legacy:
            await client.request(_agent(tmp_path), "GET", "/api/capabilities")
        else:
            await client.request(
                _validated_target(), b"manager-token", "GET", "/api/v2/summary"
            )
    await client.aclose()

    assert observed_accept_encoding == "identity"
    assert stream.iterated is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_client_rejects_gzip_bomb_before_decompression(tmp_path, legacy):
    compressed = gzip.compress(b"x" * (2 * 1024 * 1024))
    stream = TrackingStream([compressed])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=stream,
        )

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        if legacy:
            await client.request(_agent(tmp_path), "GET", "/api/capabilities")
        else:
            await client.request(
                _validated_target(), b"manager-token", "GET", "/api/v2/summary"
            )
    await client.aclose()

    assert len(compressed) < 4096
    assert stream.iterated is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", ["+10", "10, 10"])
async def test_client_rejects_noncanonical_content_length(content_length):
    stream = TrackingStream([b'{"ok":true}'])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": content_length,
            },
            stream=stream,
        )

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(
            _validated_target(), b"manager-token", "GET", "/api/v2/summary"
        )
    await client.aclose()

    assert stream.iterated is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/summary?x=1",
        "/api/v2/summary#x",
        "/api\\v2",
        "/api/%",
        "/api/%GG",
        "/api/../secret",
        "/api/./summary",
        "/api/%2e%2e/secret",
        "/api/%2Fsecret",
        "/api/%5csecret",
        "/api/%00secret",
    ],
)
async def test_validated_target_rejects_ambiguous_or_traversing_paths(path):
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await AgentHttpClient().request(
            _validated_target(), b"manager-token", "GET", path
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        ("application/json-not", b'{"ok":true}'),
        ("application/json", b'{"value":NaN}'),
        ("application/json", b'{"value":Infinity}'),
        ("application/json", b"[" * 2000 + b"0" + b"]" * 2000),
    ],
)
async def test_client_rejects_ambiguous_or_nonstandard_json(content_type, payload):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": content_type})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await client.request(
            _validated_target(), b"manager-token", "GET", "/api/v2/summary"
        )
    await client.aclose()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["[{" * 100, 'escaped quote: " and escaped backslash: \\'],
)
async def test_json_depth_scanner_ignores_brackets_and_escapes_inside_strings(text):
    payload = json.dumps({"text": text}).encode()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=payload, headers={"content-type": "application/json"}
        )

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    response = await client.request(
        _validated_target(), b"manager-token", "GET", "/api/v2/summary"
    )
    await client.aclose()

    assert response.json() == {"text": text}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_json_parser_recursion_error_maps_to_protocol(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"[]", headers={"content-type": "application/json"}
        )

    def recurse(*_args, **_kwargs):
        raise RecursionError("internal parser detail")

    monkeypatch.setattr("ic_env_guard.agents.client.json_module.loads", recurse)
    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentClientError) as error:
        await client.request(
            _validated_target(), b"manager-token", "GET", "/api/v2/summary"
        )
    await client.aclose()

    assert error.value.category == "agent_protocol_error"
    assert "internal" not in error.value.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tail_bound_cannot_exceed_wire_constant():
    with pytest.raises(AgentClientError, match="agent_protocol_error"):
        await AgentHttpClient().request_tail(
            _validated_target(),
            b"manager-token",
            "/api/v2/logs/log-1/tail",
            max_response_bytes=1024 * 1024 + 1,
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [None, "not a certificate"])
async def test_ca_file_disappearance_or_replacement_maps_to_tls_error(tmp_path, replacement):
    ca = tmp_path / "ca.pem"
    ca.write_text(VALID_CA_PEM, encoding="utf-8")
    ca.chmod(0o600)
    target = _validated_target(VerifiedTlsProfile(id="lab-tls", ca_bundle=ca))
    if replacement is None:
        ca.unlink()
    else:
        ca.write_text(replacement, encoding="utf-8")

    with pytest.raises(AgentClientError) as error:
        await AgentHttpClient().request(
            target, b"manager-token", "GET", "/api/v2/summary"
        )
    assert error.value.category == "agent_tls_error"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_ca_profile_does_not_inherit_system_trust_roots(tmp_path, monkeypatch):
    ca = tmp_path / "isolated-ca.pem"
    ca.write_text(VALID_CA_PEM, encoding="utf-8")
    ca.chmod(0o600)
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @asynccontextmanager
        async def stream(self, method, url, **_kwargs):
            yield httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request(method, url),
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("ic_env_guard.agents.client.httpx.AsyncClient", FakeAsyncClient)
    target = _validated_target(VerifiedTlsProfile(id="lab-tls", ca_bundle=ca))

    await AgentHttpClient().request(
        target, b"manager-token", "GET", "/api/v2/summary"
    )

    context = captured["verify"]
    assert context.cert_store_stats()["x509_ca"] == 1
    assert len(context.get_ca_certs()) == 1

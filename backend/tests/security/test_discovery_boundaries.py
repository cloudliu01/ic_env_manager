import asyncio

import pytest

from ic_env_guard.discovery.fingerprint import (
    DiscoveryProbeError,
    HttpHealthFingerprinter,
)
from ic_env_guard.discovery.models import DiscoveryTarget
from ic_env_guard.fleet.transport import TrustedLanHttpProfile


async def _serve_once(response: bytes):
    request_seen = asyncio.Future()

    async def handler(reader, writer):
        request_seen.set_result(await reader.readuntil(b"\r\n\r\n"))
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], request_seen


def _target(port):
    return DiscoveryTarget(
        ip="127.0.0.1",
        port=port,
        transport_profile_id="test-http",
        scheme="http",
    )


@pytest.mark.security
async def test_fingerprint_requires_exact_header_status_and_json_without_credentials():
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 15\r\n"
        b"X-IC-Env-Guard-Agent: 2\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}"
    )
    server, port, request_seen = await _serve_once(response)
    fingerprinter = HttpHealthFingerprinter(
        (TrustedLanHttpProfile(id="test-http", allowed_cidrs=["127.0.0.0/8"]),)
    )
    try:
        fingerprint = await fingerprinter.probe(
            _target(port), connect_timeout=0.5, fingerprint_timeout=1
        )
        request = await request_seen
    finally:
        server.close()
        await server.wait_closed()

    assert fingerprint.version == "2"
    assert request.startswith(b"GET /healthz HTTP/1.1\r\n")
    assert b"Authorization:" not in request
    assert b"Cookie:" not in request


@pytest.mark.security
async def test_fingerprint_does_not_follow_redirect_or_accept_oversized_response():
    redirect = b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/evil\r\nContent-Length: 0\r\n\r\n"
    server, port, _ = await _serve_once(redirect)
    fingerprinter = HttpHealthFingerprinter(
        (TrustedLanHttpProfile(id="test-http", allowed_cidrs=["127.0.0.0/8"]),)
    )
    try:
        assert await fingerprinter.probe(
            _target(port), connect_timeout=0.5, fingerprint_timeout=1
        ) is None
    finally:
        server.close()
        await server.wait_closed()

    oversized = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"X-IC-Env-Guard-Agent: 2\r\nContent-Length: 999\r\n\r\n"
    )
    server, port, _ = await _serve_once(oversized)
    try:
        with pytest.raises(DiscoveryProbeError, match="fingerprint_too_large"):
            await fingerprinter.probe(
                _target(port), connect_timeout=0.5, fingerprint_timeout=1
            )
    finally:
        server.close()
        await server.wait_closed()

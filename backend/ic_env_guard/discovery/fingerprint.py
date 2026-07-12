import asyncio
import json
import ssl

from ic_env_guard.discovery.models import DiscoveryFingerprint, DiscoveryTarget
from ic_env_guard.fleet.transport import (
    TransportProfile,
    TrustedLanHttpProfile,
    VerifiedTlsProfile,
    create_ca_context,
)

_MAX_HEADERS = 8_192
_MAX_BODY = 256


class DiscoveryProbeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HttpHealthFingerprinter:
    def __init__(self, transport_profiles: tuple[TransportProfile, ...]) -> None:
        self._profiles = {profile.id: profile for profile in transport_profiles}

    async def probe(
        self,
        target: DiscoveryTarget,
        *,
        connect_timeout: float,
        fingerprint_timeout: float,
    ) -> DiscoveryFingerprint | None:
        profile = self._profiles.get(target.transport_profile_id)
        if profile is None:
            raise DiscoveryProbeError("transport_profile_unknown")
        ssl_context: ssl.SSLContext | None = None
        if isinstance(profile, TrustedLanHttpProfile):
            if target.scheme != "http":
                raise DiscoveryProbeError("transport_profile_mismatch")
        elif isinstance(profile, VerifiedTlsProfile):
            if target.scheme != "https":
                raise DiscoveryProbeError("transport_profile_mismatch")
            ssl_context = (
                create_ca_context(profile.ca_bundle)
                if profile.ca_bundle is not None
                else ssl.create_default_context()
            )
        else:
            raise DiscoveryProbeError("transport_profile_unknown")

        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=target.ip,
                    port=target.port,
                    ssl=ssl_context,
                    server_hostname=target.ip if ssl_context is not None else None,
                    limit=_MAX_HEADERS,
                ),
                timeout=connect_timeout,
            )
            host = f"[{target.ip}]" if ":" in target.ip else target.ip
            writer.write(
                f"GET /healthz HTTP/1.1\r\nHost: {host}:{target.port}\r\n"
                "Accept: application/json\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            return await asyncio.wait_for(
                self._read_fingerprint(reader), timeout=fingerprint_timeout
            )
        except TimeoutError as exc:
            raise DiscoveryProbeError("timeout") from exc
        except DiscoveryProbeError:
            raise
        except (OSError, ssl.SSLError, asyncio.IncompleteReadError) as exc:
            raise DiscoveryProbeError("network_error") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def _read_fingerprint(
        self, reader: asyncio.StreamReader
    ) -> DiscoveryFingerprint | None:
        try:
            raw_headers = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError as exc:
            raise DiscoveryProbeError("fingerprint_too_large") from exc
        if len(raw_headers) > _MAX_HEADERS:
            raise DiscoveryProbeError("fingerprint_too_large")
        lines = raw_headers[:-4].split(b"\r\n")
        if len(lines) < 2 or len(lines[0]) > 128:
            return None
        status = lines[0].split(b" ", 2)
        if len(status) < 2 or status[0] not in {b"HTTP/1.0", b"HTTP/1.1"}:
            return None
        if status[1] != b"200":
            return None
        headers: dict[bytes, bytes] = {}
        for line in lines[1:]:
            if b":" not in line:
                return None
            name, value = line.split(b":", 1)
            name = name.strip().lower()
            if name in headers:
                return None
            headers[name] = value.strip()
        if headers.get(b"x-ic-env-guard-agent") != b"2":
            return None
        if headers.get(b"content-type") != b"application/json":
            return None
        if b"transfer-encoding" in headers:
            return None
        try:
            content_length = int(headers[b"content-length"])
        except (KeyError, ValueError):
            return None
        if content_length < 0 or content_length > _MAX_BODY:
            raise DiscoveryProbeError("fingerprint_too_large")
        body = await reader.readexactly(content_length)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if payload != {"status": "ok"}:
            return None
        return DiscoveryFingerprint(version="2")

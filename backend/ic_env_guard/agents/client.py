import json as json_module
import re
import ssl
from collections.abc import Callable, Mapping
from typing import overload
from urllib.parse import unquote_to_bytes

import httpx

from ic_env_guard.auth.token import load_bearer_token
from ic_env_guard.config.models import AgentConfig
from ic_env_guard.fleet.target_policy import ValidatedTarget
from ic_env_guard.fleet.transport import VerifiedTlsProfile, create_ca_context

FORWARDED_HEADERS = {"accept", "content-type"}
MAX_AGENT_RESPONSE_BYTES = 1024 * 1024
MAX_AGENT_TAIL_RESPONSE_BYTES = 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
MAX_JSON_NESTING = 64
PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


class AgentClientError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(f"{category}: {message}")
        self.category = category
        self.message = message


class AgentHttpClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        connect_timeout_seconds: float = 3,
        request_timeout_seconds: float = 10,
        legacy_credential_loader: Callable[[AgentConfig], str] | None = None,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        self._connect_timeout_seconds = connect_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._legacy_credential_loader = (
            legacy_credential_loader
            if legacy_credential_loader is not None
            else lambda agent: load_bearer_token(agent.token_file)
        )
        if transport is not None:
            self._client = httpx.AsyncClient(
                follow_redirects=False, transport=transport, trust_env=False
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    @overload
    async def request(
        self,
        target: ValidatedTarget,
        credential: bytes,
        method: str,
        path: str,
        *,
        incoming_headers: Mapping[str, str] | None = None,
        correlation_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json: object | None = None,
    ) -> httpx.Response: ...

    @overload
    async def request(
        self,
        target: AgentConfig,
        credential: str,
        method: str,
        *,
        incoming_headers: Mapping[str, str] | None = None,
        correlation_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json: object | None = None,
    ) -> httpx.Response: ...

    async def request(
        self,
        target: ValidatedTarget | AgentConfig,
        credential: bytes | str,
        method: str,
        path: str | None = None,
        *,
        incoming_headers: Mapping[str, str] | None = None,
        correlation_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        if isinstance(target, AgentConfig):
            if not isinstance(credential, str) or path is not None:
                raise TypeError("legacy Agent request requires method and path")
            return await self._legacy_request(
                target,
                credential,
                method,
                incoming_headers=incoming_headers,
                correlation_id=correlation_id,
                params=params,
                json=json,
            )
        if not isinstance(credential, bytes) or path is None:
            raise TypeError("validated Agent request requires credential bytes, method, and path")
        return await self._target_request(
            target,
            credential,
            method,
            path,
            incoming_headers=incoming_headers,
            correlation_id=correlation_id,
            params=params,
            json=json,
            max_response_bytes=MAX_AGENT_RESPONSE_BYTES,
        )

    async def request_tail(
        self,
        target: ValidatedTarget,
        credential: bytes,
        path: str,
        *,
        correlation_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        max_response_bytes: int = MAX_AGENT_TAIL_RESPONSE_BYTES,
    ) -> httpx.Response:
        if not 1 <= max_response_bytes <= MAX_AGENT_TAIL_RESPONSE_BYTES:
            raise AgentClientError(
                "agent_protocol_error", "agent tail response limit is invalid"
            )
        return await self._target_request(
            target,
            credential,
            "GET",
            path,
            incoming_headers=None,
            correlation_id=correlation_id,
            params=params,
            json=None,
            max_response_bytes=max_response_bytes,
        )

    async def _target_request(
        self,
        target: ValidatedTarget,
        credential: bytes,
        method: str,
        path: str,
        *,
        incoming_headers: Mapping[str, str] | None,
        correlation_id: str | None,
        params: Mapping[str, str | int] | None,
        json: object | None,
        max_response_bytes: int,
    ) -> httpx.Response:
        _validate_upstream_path(path)
        token = _decode_credential(credential)
        headers = self._headers(token, incoming_headers or {}, correlation_id)
        headers["Host"] = target.host_header
        extensions = (
            {"sni_hostname": target.sni_hostname.encode("ascii")}
            if target.sni_hostname is not None
            else None
        )
        client = self._client
        transient_client: httpx.AsyncClient | None = None
        timeout = httpx.Timeout(
            self._request_timeout_seconds, connect=self._connect_timeout_seconds
        )
        try:
            if client is None:
                transient_client = httpx.AsyncClient(
                    follow_redirects=False,
                    verify=_target_verify_setting(target),
                    trust_env=False,
                )
                client = transient_client
            async with client.stream(
                method,
                f"{target.pinned_url}{path}",
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
                extensions=extensions,
            ) as streamed:
                if streamed.status_code in {401, 403}:
                    raise AgentClientError(
                        "agent_auth_error", "agent authentication failed"
                    )
                response = await _read_bounded_response(streamed, max_response_bytes)
        except AgentClientError:
            raise
        except (ssl.SSLError, OSError) as exc:
            raise AgentClientError("agent_tls_error", "agent TLS validation failed") from exc
        except httpx.TimeoutException as exc:
            raise AgentClientError("agent_network_error", "agent request timed out") from exc
        except (
            httpx.InvalidURL,
            httpx.ProtocolError,
            httpx.DecodingError,
            httpx.StreamError,
        ) as exc:
            raise AgentClientError(
                "agent_protocol_error", "agent response protocol is invalid"
            ) from exc
        except httpx.TransportError as exc:
            category = "agent_tls_error" if _caused_by_tls(exc) else "agent_network_error"
            message = (
                "agent TLS validation failed"
                if category == "agent_tls_error"
                else "agent is unavailable"
            )
            raise AgentClientError(category, message) from exc
        finally:
            if transient_client is not None:
                await transient_client.aclose()
        return _validate_response(response, max_response_bytes)

    async def _legacy_request(
        self,
        agent: AgentConfig,
        method: str,
        path: str,
        *,
        incoming_headers: Mapping[str, str] | None,
        correlation_id: str | None,
        params: Mapping[str, str | int] | None,
        json: object | None,
    ) -> httpx.Response:
        try:
            token = self._legacy_credential_loader(agent)
        except Exception as exc:
            raise AgentClientError(
                "agent_auth_error", "Agent credential is unavailable"
            ) from exc
        headers = self._headers(token, incoming_headers or {}, correlation_id)
        _validate_upstream_path(path)
        client = self._client
        transient_client: httpx.AsyncClient | None = None
        try:
            if client is None:
                transient_client = httpx.AsyncClient(
                    follow_redirects=False,
                    verify=_legacy_verify_setting(agent),
                    trust_env=False,
                )
                client = transient_client
            async with client.stream(
                method,
                f"{agent.base_url}{path}",
                headers=headers,
                params=params,
                json=json,
                timeout=agent.request_timeout_seconds,
            ) as streamed:
                response = await _read_bounded_response(
                    streamed, MAX_AGENT_RESPONSE_BYTES
                )
        except AgentClientError:
            raise
        except httpx.TimeoutException as exc:
            raise AgentClientError("agent_timeout", "agent request timed out") from exc
        except (OSError, httpx.TransportError) as exc:
            raise AgentClientError("agent_unavailable", "agent is unavailable") from exc
        except (httpx.InvalidURL, httpx.DecodingError, httpx.StreamError) as exc:
            raise AgentClientError(
                "agent_protocol_error", "agent response protocol is invalid"
            ) from exc
        finally:
            if transient_client is not None:
                await transient_client.aclose()
        return _validate_response(response, MAX_AGENT_RESPONSE_BYTES)

    @staticmethod
    def _headers(
        token: str,
        incoming_headers: Mapping[str, str],
        correlation_id: str | None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "identity",
        }
        for name, value in incoming_headers.items():
            if name.lower() in FORWARDED_HEADERS:
                headers[name] = value
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return headers


async def _read_bounded_response(
    streamed: httpx.Response, max_response_bytes: int
) -> httpx.Response:
    content_encoding = streamed.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise AgentClientError(
            "agent_protocol_error", "agent response content encoding is invalid"
        )
    content_length = streamed.headers.get("content-length")
    if content_length is not None:
        if re.fullmatch(r"[0-9]+", content_length) is None:
            raise AgentClientError(
                "agent_protocol_error", "agent response content length is invalid"
            )
        declared_length = int(content_length)
        if declared_length < 0 or declared_length > max_response_bytes:
            raise AgentClientError("agent_protocol_error", "agent response is too large")
    content = bytearray()
    if streamed.is_stream_consumed:
        content.extend(streamed.content)
        if len(content) > max_response_bytes:
            raise AgentClientError("agent_protocol_error", "agent response is too large")
    else:
        chunk_size = min(STREAM_CHUNK_BYTES, max_response_bytes + 1)
        async for chunk in streamed.aiter_raw(chunk_size=chunk_size):
            content.extend(chunk)
            if len(content) > max_response_bytes:
                raise AgentClientError(
                    "agent_protocol_error", "agent response is too large"
                )
    return httpx.Response(
        streamed.status_code,
        headers=streamed.headers,
        content=bytes(content),
        request=streamed.request,
        extensions=streamed.extensions,
    )


def _validate_response(
    response: httpx.Response, max_response_bytes: int
) -> httpx.Response:
    if 300 <= response.status_code < 400:
        raise AgentClientError("agent_protocol_error", "agent redirects are not allowed")
    if len(response.content) > max_response_bytes:
        raise AgentClientError("agent_protocol_error", "agent response is too large")
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if response.status_code != 204 and content_type != "application/json":
        raise AgentClientError("agent_protocol_error", "agent response content type is invalid")
    if response.status_code != 204:
        try:
            _validate_json_nesting(response.content)
            json_module.loads(
                response.content,
                parse_constant=lambda _value: _reject_json_constant(),
            )
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise AgentClientError(
                "agent_protocol_error", "agent response JSON is invalid"
            ) from exc
    return response


def _decode_credential(credential: bytes) -> str:
    try:
        token = credential.decode("ascii")
    except UnicodeDecodeError:
        raise AgentClientError("agent_auth_error", "agent credential is invalid") from None
    if not token or any(character.isspace() or ord(character) < 0x20 for character in token):
        raise AgentClientError("agent_auth_error", "agent credential is invalid")
    return token


def _validate_upstream_path(path: str) -> None:
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "://" in path
        or "?" in path
        or "#" in path
        or "\\" in path
        or any(ord(character) < 0x20 for character in path)
    ):
        raise AgentClientError("agent_protocol_error", "agent request path is invalid")
    for index, character in enumerate(path):
        if character == "%" and (
            index + 2 >= len(path) or not PERCENT_ESCAPE.fullmatch(path[index : index + 3])
        ):
            raise AgentClientError("agent_protocol_error", "agent request path is invalid")
    try:
        decoded = unquote_to_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        raise AgentClientError(
            "agent_protocol_error", "agent request path is invalid"
        ) from None
    if (
        decoded.count("/") != path.count("/")
        or "\\" in decoded
        or "?" in decoded
        or "#" in decoded
        or any(ord(character) < 0x20 for character in decoded)
        or any(segment in {"", ".", ".."} for segment in decoded.split("/")[1:])
        or PERCENT_ESCAPE.search(decoded)
    ):
        raise AgentClientError("agent_protocol_error", "agent request path is invalid")


def _reject_json_constant() -> None:
    raise ValueError("non-standard JSON constant")


def _validate_json_nesting(payload: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for value in payload:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in {ord("["), ord("{")}:
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise ValueError("JSON nesting is too deep")
        elif value in {ord("]"), ord("}")}:
            depth -= 1


def _caused_by_tls(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _target_verify_setting(target: ValidatedTarget) -> bool | ssl.SSLContext:
    if not isinstance(target.profile, VerifiedTlsProfile):
        return True
    if target.profile.ca_bundle is None:
        return True
    return create_ca_context(target.profile.ca_bundle)


def _legacy_verify_setting(agent: AgentConfig) -> bool | str:
    if not agent.tls.verify:
        return False
    if agent.tls.ca_bundle is not None:
        return str(agent.tls.ca_bundle)
    return True

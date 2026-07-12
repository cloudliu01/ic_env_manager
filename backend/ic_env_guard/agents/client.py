import ssl
from collections.abc import Mapping
from typing import overload

import httpx

from ic_env_guard.auth.token import load_bearer_token
from ic_env_guard.config.models import AgentConfig
from ic_env_guard.fleet.target_policy import ValidatedTarget
from ic_env_guard.fleet.transport import VerifiedTlsProfile

FORWARDED_HEADERS = {"accept", "content-type"}
MAX_AGENT_RESPONSE_BYTES = 1024 * 1024
MAX_AGENT_TAIL_RESPONSE_BYTES = 1024 * 1024


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
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        self._connect_timeout_seconds = connect_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
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
        if client is None:
            transient_client = httpx.AsyncClient(
                follow_redirects=False,
                verify=_target_verify_setting(target),
                trust_env=False,
            )
            client = transient_client
        timeout = httpx.Timeout(
            self._request_timeout_seconds, connect=self._connect_timeout_seconds
        )
        try:
            async with client.stream(
                method,
                f"{target.pinned_url}{path}",
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
                extensions=extensions,
            ) as streamed:
                content = bytearray()
                async for chunk in streamed.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_response_bytes:
                        raise AgentClientError(
                            "agent_protocol_error", "agent response is too large"
                        )
                response = httpx.Response(
                    streamed.status_code,
                    headers=streamed.headers,
                    content=bytes(content),
                    request=streamed.request,
                    extensions=streamed.extensions,
                )
        except AgentClientError:
            raise
        except httpx.TimeoutException as exc:
            raise AgentClientError("agent_network_error", "agent request timed out") from exc
        except (httpx.ProtocolError, httpx.DecodingError) as exc:
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
        if response.status_code in {401, 403}:
            raise AgentClientError("agent_auth_error", "agent authentication failed")
        return _validate_response(response)

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
        headers = self._headers(
            load_bearer_token(agent.token_file), incoming_headers or {}, correlation_id
        )
        client = self._client
        transient_client: httpx.AsyncClient | None = None
        if client is None:
            transient_client = httpx.AsyncClient(
                follow_redirects=False,
                verify=_legacy_verify_setting(agent),
                trust_env=False,
            )
            client = transient_client
        try:
            response = await client.request(
                method,
                f"{agent.base_url}{path}",
                headers=headers,
                params=params,
                json=json,
                timeout=agent.request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise AgentClientError("agent_timeout", "agent request timed out") from exc
        except httpx.TransportError as exc:
            raise AgentClientError("agent_unavailable", "agent is unavailable") from exc
        finally:
            if transient_client is not None:
                await transient_client.aclose()
        return _validate_response(response)

    @staticmethod
    def _headers(
        token: str,
        incoming_headers: Mapping[str, str],
        correlation_id: str | None,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        for name, value in incoming_headers.items():
            if name.lower() in FORWARDED_HEADERS:
                headers[name] = value
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return headers


def _validate_response(response: httpx.Response) -> httpx.Response:
    if 300 <= response.status_code < 400:
        raise AgentClientError("agent_protocol_error", "agent redirects are not allowed")
    if len(response.content) > MAX_AGENT_RESPONSE_BYTES:
        raise AgentClientError("agent_protocol_error", "agent response is too large")
    content_type = response.headers.get("content-type", "")
    if response.status_code != 204 and not content_type.startswith("application/json"):
        raise AgentClientError("agent_protocol_error", "agent response content type is invalid")
    if response.status_code != 204:
        try:
            response.json()
        except ValueError as exc:
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
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise AgentClientError("agent_protocol_error", "agent request path is invalid")


def _caused_by_tls(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _target_verify_setting(target: ValidatedTarget) -> bool | str:
    if not isinstance(target.profile, VerifiedTlsProfile):
        return True
    return str(target.profile.ca_bundle) if target.profile.ca_bundle is not None else True


def _legacy_verify_setting(agent: AgentConfig) -> bool | str:
    if not agent.tls.verify:
        return False
    if agent.tls.ca_bundle is not None:
        return str(agent.tls.ca_bundle)
    return True

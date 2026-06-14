from collections.abc import Mapping

import httpx

from ic_env_guard.auth.token import load_bearer_token
from ic_env_guard.config.models import AgentConfig

FORWARDED_HEADERS = {"accept", "content-type"}
MAX_AGENT_RESPONSE_BYTES = 1024 * 1024


class AgentClientError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(f"{category}: {message}")
        self.category = category
        self.message = message


class AgentHttpClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client: httpx.AsyncClient | None = None
        if transport is not None:
            self._client = httpx.AsyncClient(
                follow_redirects=False, transport=transport, trust_env=False
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def request(
        self,
        agent: AgentConfig,
        method: str,
        path: str,
        *,
        incoming_headers: Mapping[str, str] | None = None,
        correlation_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        headers = self._headers(agent, incoming_headers or {}, correlation_id)
        client = self._client
        transient_client: httpx.AsyncClient | None = None
        if client is None:
            transient_client = httpx.AsyncClient(
                follow_redirects=False,
                verify=_verify_setting(agent),
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

    def _headers(
        self,
        agent: AgentConfig,
        incoming_headers: Mapping[str, str],
        correlation_id: str | None,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {load_bearer_token(agent.token_file)}"}
        for name, value in incoming_headers.items():
            if name.lower() in FORWARDED_HEADERS:
                headers[name] = value
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return headers


def _verify_setting(agent: AgentConfig) -> bool | str:
    if not agent.tls.verify:
        return False
    if agent.tls.ca_bundle is not None:
        return str(agent.tls.ca_bundle)
    return True

import asyncio
import base64
import ssl
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

import websockets
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from websockets.exceptions import WebSocketException

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentCredentialError
from ic_env_guard.agents.registry import AgentNotFoundError, AgentRegistry
from ic_env_guard.agents.terminal_proxy import (
    MAX_TERMINAL_FRAME_BYTES,
    GatewayTicketStore,
)
from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.api.agent_terminals import get_gateway_ticket_store
from ic_env_guard.api.agents import get_agent_availability, get_agent_registry
from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    AuditStorageUnavailable,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.auth.dependencies import AuthState, get_auth_state
from ic_env_guard.auth.token import load_bearer_token
from ic_env_guard.config.models import AgentConfig
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError, AgentRouteSnapshot

router = APIRouter(tags=["agent-terminal-websocket"])
TERMINAL_DRAIN_TIMEOUT_SECONDS = 10


class TerminalProxyClosed(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


class AgentWebSocketConnector:
    def __init__(
        self,
        legacy_credential_loader: Callable[[AgentConfig], str] | None = None,
    ) -> None:
        self._legacy_credential_loader = (
            legacy_credential_loader
            if legacy_credential_loader is not None
            else self._load_file_credential
        )

    @staticmethod
    def _load_file_credential(agent: AgentConfig) -> str:
        if agent.managed_credential:
            raise AgentCredentialError("managed Agent requires a credential loader")
        return load_bearer_token(agent.token_file)

    def connect(
        self,
        agent: AgentConfig,
        terminal_id: str,
        ticket: str,
        cursor: int,
        correlation_id: str,
    ):
        parsed = urlsplit(agent.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"/ws/terminals/{terminal_id}"
        query = urlencode({"ticket": ticket, "cursor": str(cursor)})
        url = urlunsplit((scheme, parsed.netloc, path, query, ""))
        try:
            token = self._legacy_credential_loader(agent)
        except Exception as exc:
            raise AgentCredentialError("credential unavailable") from exc
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Correlation-ID": correlation_id,
        }
        kwargs = {
            "additional_headers": headers,
            "open_timeout": agent.connect_timeout_seconds,
            "max_size": MAX_TERMINAL_FRAME_BYTES,
            "proxy": None,
        }
        if scheme == "wss":
            kwargs["ssl"] = _ssl_context(agent)
        connection = websockets.connect(url, **kwargs)
        connection.process_redirect = lambda exc: exc
        return connection

    def connect_snapshot(
        self,
        route: AgentRouteSnapshot,
        terminal_id: str,
        ticket: str,
        cursor: int,
        correlation_id: str,
    ):
        target = route.target
        parsed = urlsplit(target.normalized_endpoint)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode({"ticket": ticket, "cursor": str(cursor)})
        url = urlunsplit((scheme, parsed.netloc, f"/ws/terminals/{terminal_id}", query, ""))
        try:
            token = route.credential.decode("ascii")
        except UnicodeDecodeError as exc:
            raise AgentCredentialError("credential unavailable") from exc
        if not token or any(character.isspace() or ord(character) < 0x20 for character in token):
            raise AgentCredentialError("credential unavailable")
        kwargs = {
            "additional_headers": {
                "Authorization": f"Bearer {token}",
                "X-Correlation-ID": correlation_id,
            },
            "open_timeout": 3,
            "max_size": MAX_TERMINAL_FRAME_BYTES,
            "proxy": None,
            "host": str(target.pinned_address),
            "port": target.port,
        }
        if scheme == "wss":
            kwargs["ssl"] = _target_ssl_context(target)
            kwargs["server_hostname"] = target.sni_hostname
        connection = websockets.connect(url, **kwargs)
        connection.process_redirect = lambda exc: exc
        return connection


def get_agent_ws_connector() -> AgentWebSocketConnector:
    return AgentWebSocketConnector()


def _ssl_context(agent: AgentConfig) -> ssl.SSLContext:
    if not agent.tls.verify:
        return ssl._create_unverified_context()
    if agent.tls.ca_bundle is not None:
        return ssl.create_default_context(cafile=str(agent.tls.ca_bundle))
    return ssl.create_default_context()


def _target_ssl_context(target) -> ssl.SSLContext:
    profile = target.profile
    ca_bundle = getattr(profile, "ca_bundle", None)
    return (
        ssl.create_default_context(cafile=str(ca_bundle))
        if ca_bundle is not None
        else ssl.create_default_context()
    )


def _decode_bearer_subprotocol(value: str) -> str | None:
    if not value.startswith("bearer."):
        return None
    encoded = value.removeprefix("bearer.")
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _authenticate_websocket(
    websocket: WebSocket, auth_state: AuthState
) -> tuple[bool, str | None, str | None]:
    header = websocket.headers.get("authorization")
    token: str | None = None
    selected_subprotocol: str | None = None
    if header is not None and header.lower().startswith("bearer "):
        token = header[7:]
    else:
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(","):
            candidate = protocol.strip()
            decoded = _decode_bearer_subprotocol(candidate)
            if decoded is not None:
                token = decoded
                selected_subprotocol = candidate
                break
    if token is None:
        return False, None, None
    try:
        return True, auth_state.authenticate(token).actor_id, selected_subprotocol
    except Exception:
        return True, None, selected_subprotocol


def _record_attach_failure(
    *,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    actor_id: str | None,
    source_addr: str | None,
    agent_id: str,
    terminal_id: str,
    correlation_id: str,
    failure_category: str,
) -> None:
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation="terminals.attach",
            target=f"terminal:{terminal_id}",
            correlation_id=correlation_id,
        )
    )
    audit_repo.finalize(
        audit.id,
        result="failed",
        dispatch_state="not_dispatched",
        failure_category=failure_category,
    )
    commit_audit_outcome(audit_repo, audit_health)


async def _browser_to_upstream(websocket: WebSocket, upstream) -> None:
    while True:
        text = await websocket.receive_text()
        if len(text.encode("utf-8")) > MAX_TERMINAL_FRAME_BYTES:
            await websocket.close(code=4413)
            await upstream.close(code=4413)
            raise TerminalProxyClosed("frame_limit")
        try:
            await asyncio.wait_for(upstream.send(text), timeout=TERMINAL_DRAIN_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            await websocket.close(code=4413)
            await upstream.close(code=4413)
            raise TerminalProxyClosed("backpressure_limit") from exc


async def _upstream_to_browser(websocket: WebSocket, upstream) -> None:
    async for message in upstream:
        if not isinstance(message, str) or len(message.encode("utf-8")) > MAX_TERMINAL_FRAME_BYTES:
            await websocket.close(code=4413)
            await upstream.close(code=4413)
            raise TerminalProxyClosed("frame_limit")
        try:
            await asyncio.wait_for(
                websocket.send_text(message), timeout=TERMINAL_DRAIN_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            await websocket.close(code=4413)
            await upstream.close(code=4413)
            raise TerminalProxyClosed("backpressure_limit") from exc


async def _watch_agent_revision(
    websocket: WebSocket, upstream, registry: AgentRegistry, ticket
) -> None:
    while True:
        await asyncio.sleep(0.05)
        record = registry.record(ticket.agent_id)
        if not _ticket_matches_record(ticket, record):
            await websocket.close(code=4409)
            await upstream.close(code=4409)
            raise TerminalProxyClosed("agent_target_changed")


def _ticket_matches_record(ticket, record) -> bool:
    return ticket.revision is None or (
        record is not None
        and record.enabled
        and record.revision == ticket.revision
        and record.credential_ref == ticket.credential_ref
        and record.transport_profile_id == ticket.transport_profile_id
        and record.normalized_endpoint == ticket.normalized_endpoint
    )


async def _run_proxy_tasks(websocket: WebSocket, upstream, *, revision_watch=None) -> None:
    browser_task = asyncio.create_task(_browser_to_upstream(websocket, upstream))
    upstream_task = asyncio.create_task(_upstream_to_browser(websocket, upstream))
    tasks = {browser_task, upstream_task}
    if revision_watch is not None:
        tasks.add(asyncio.create_task(revision_watch))
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, TerminalProxyClosed):
            raise result
    for result in results:
        if isinstance(result, WebSocketDisconnect | asyncio.CancelledError):
            continue
        if isinstance(result, Exception):
            raise result


@router.websocket("/ws/agents/{agent_id}/terminals/{terminal_id}")
async def agent_terminal_websocket(
    websocket: WebSocket,
    agent_id: str,
    terminal_id: str,
    auth_state: Annotated[AuthState, Depends(get_auth_state)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    tickets: Annotated[GatewayTicketStore, Depends(get_gateway_ticket_store)],
    connector: Annotated[AgentWebSocketConnector, Depends(get_agent_ws_connector)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> None:
    correlation_id = str(uuid4())
    source_addr = websocket.client.host if websocket.client else None
    auth_present, authenticated_actor_id, selected_subprotocol = _authenticate_websocket(
        websocket, auth_state
    )
    if not auth_present:
        _record_attach_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor_id=None,
            source_addr=source_addr,
            agent_id=agent_id,
            terminal_id=terminal_id,
            correlation_id=correlation_id,
            failure_category="missing_auth",
        )
        await websocket.close(code=4401)
        return
    if auth_present and authenticated_actor_id is None:
        _record_attach_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor_id=None,
            source_addr=source_addr,
            agent_id=agent_id,
            terminal_id=terminal_id,
            correlation_id=correlation_id,
            failure_category="invalid_auth",
        )
        await websocket.close(code=4403)
        return
    try:
        cursor = int(websocket.query_params.get("cursor", "0"))
    except ValueError:
        _record_attach_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor_id=authenticated_actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            terminal_id=terminal_id,
            correlation_id=correlation_id,
            failure_category="invalid_cursor",
        )
        await websocket.close(code=4400)
        return
    ticket = websocket.query_params.get("ticket")
    if not ticket:
        _record_attach_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor_id=authenticated_actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            terminal_id=terminal_id,
            correlation_id=correlation_id,
            failure_category="missing_ticket",
        )
        await websocket.close(code=4401)
        return
    gateway_ticket = None
    try:
        try:
            agent = registry.get(agent_id)
        except AgentNotFoundError:
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=authenticated_actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="agent_not_found",
            )
            await websocket.close(code=4404)
            return
        if not agent.enabled:
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=authenticated_actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="agent_disabled",
            )
            await websocket.close(code=4409)
            return
        ticket_status, gateway_ticket = tickets.consume_for_websocket(
            ticket,
            agent_id=agent_id,
            terminal_id=terminal_id,
            intended_ws_path=f"/ws/agents/{agent_id}/terminals/{terminal_id}",
            actor_id=authenticated_actor_id,
        )
        if ticket_status == "actor_mismatch":
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=gateway_ticket.actor_id if gateway_ticket else None,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="actor_mismatch",
            )
            await websocket.close(code=4403)
            return
        if gateway_ticket is None:
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=authenticated_actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="invalid_ticket",
            )
            await websocket.close(code=4401)
            return
        captured_record = registry.record(agent_id)
        if not _ticket_matches_record(gateway_ticket, captured_record):
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=authenticated_actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="agent_target_changed",
            )
            await websocket.close(code=4409)
            return
        if not await availability.ensure_capability(agent_id, "terminals.v1"):
            _record_attach_failure(
                audit_repo=audit_repo,
                audit_health=audit_health,
                actor_id=authenticated_actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                terminal_id=terminal_id,
                correlation_id=correlation_id,
                failure_category="missing_capability",
            )
            await websocket.close(code=4409)
            return
        audit = audit_repo.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id=gateway_ticket.actor_id,
                source_addr=source_addr,
                agent_id=agent_id,
                operation="terminals.attach",
                target=f"terminal:{terminal_id}",
                correlation_id=correlation_id,
            )
        )
        try:
            commit_audit_intent(audit_repo, audit_health)
        except AuditStorageUnavailable:
            await websocket.close(code=4503)
            return

        try:
            route_snapshot = None
            if isinstance(connector, AgentWebSocketConnector):
                try:
                    route_snapshot = proxy.resolve_captured_route(
                        agent_id=agent_id,
                        revision=gateway_ticket.revision,
                        credential_ref=gateway_ticket.credential_ref,
                        transport_profile_id=gateway_ticket.transport_profile_id,
                        normalized_endpoint=gateway_ticket.normalized_endpoint,
                    )
                except (AgentProxyError, TypeError):
                    audit_repo.finalize(
                        audit.id,
                        result="failed",
                        dispatch_state="not_dispatched",
                        failure_category="agent_target_changed",
                    )
                    commit_audit_outcome(audit_repo, audit_health)
                    await websocket.close(code=4409)
                    return
            connection = (
                connector.connect_snapshot(
                    route_snapshot,
                    terminal_id,
                    gateway_ticket.upstream_ticket,
                    cursor,
                    correlation_id,
                )
                if route_snapshot is not None
                else connector.connect(
                    agent,
                    terminal_id,
                    gateway_ticket.upstream_ticket,
                    cursor,
                    correlation_id,
                )
            )
            async with connection as upstream:
                await websocket.accept(subprotocol=selected_subprotocol)
                await _run_proxy_tasks(
                    websocket,
                    upstream,
                    revision_watch=_watch_agent_revision(
                        websocket, upstream, registry, gateway_ticket
                    ),
                )
        except WebSocketDisconnect:
            pass
        except TerminalProxyClosed as exc:
            audit_repo.finalize(
                audit.id,
                result="failed",
                dispatch_state="dispatched",
                failure_category=exc.category,
            )
            commit_audit_outcome(audit_repo, audit_health)
            return
        except AgentCredentialError:
            audit_repo.finalize(
                audit.id,
                result="failed",
                dispatch_state="not_dispatched",
                failure_category="agent_auth_error",
            )
            commit_audit_outcome(audit_repo, audit_health)
            await websocket.close(code=4503)
            return
        except TimeoutError:
            audit_repo.finalize(
                audit.id,
                result="failed",
                dispatch_state="unknown",
                failure_category="agent_timeout",
            )
            commit_audit_outcome(audit_repo, audit_health)
            await websocket.close(code=4504)
            return
        except OSError:
            audit_repo.finalize(
                audit.id,
                result="failed",
                dispatch_state="unknown",
                failure_category="agent_unavailable",
            )
            commit_audit_outcome(audit_repo, audit_health)
            await websocket.close(code=4503)
            return
        except WebSocketException:
            audit_repo.finalize(
                audit.id,
                result="failed",
                dispatch_state="unknown",
                failure_category="agent_protocol_error",
            )
            commit_audit_outcome(audit_repo, audit_health)
            await websocket.close(code=4502)
            return

        audit_repo.finalize(audit.id, result="success", dispatch_state="dispatched")
        commit_audit_outcome(audit_repo, audit_health)
    finally:
        if gateway_ticket is not None:
            tickets.release_active(gateway_ticket)

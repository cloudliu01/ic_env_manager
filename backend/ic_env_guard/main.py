import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.agents.terminal_proxy import GatewayProxyLimiter, GatewayTicketStore
from ic_env_guard.api import terminal_ws
from ic_env_guard.api.agent_audit import router as agent_audit_router
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agent_monitoring import router as agent_monitoring_router
from ic_env_guard.api.agent_services import router as agent_services_router
from ic_env_guard.api.agent_terminal_ws import get_gateway_proxy_limiter
from ic_env_guard.api.agent_terminal_ws import router as agent_terminal_ws_router
from ic_env_guard.api.agent_terminals import get_gateway_ticket_store
from ic_env_guard.api.agent_terminals import router as agent_terminals_router
from ic_env_guard.api.agents import (
    control_plane_agents_router,
    get_agent_availability,
    get_agent_registry,
    local_capabilities_router,
)
from ic_env_guard.api.audit import get_audit_query_repository
from ic_env_guard.api.audit import router as audit_router
from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.api.auth import router as auth_router
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.control_plane_audit import router as control_plane_audit_router
from ic_env_guard.api.errors import register_error_handlers
from ic_env_guard.api.fleet import router as fleet_router
from ic_env_guard.api.health import router as health_router
from ic_env_guard.api.metrics import (
    MetricsAccessPolicy,
    get_metrics_access_policy,
    get_metrics_registry,
)
from ic_env_guard.api.metrics import (
    router as metrics_router,
)
from ic_env_guard.api.monitoring import get_machine_registry
from ic_env_guard.api.monitoring import router as monitoring_router
from ic_env_guard.api.risk import classify_route
from ic_env_guard.api.services import get_service_manager
from ic_env_guard.api.services import router as services_router
from ic_env_guard.api.static import mount_static_ui
from ic_env_guard.api.terminals import (
    get_terminal_manager,
    get_ticket_manager,
)
from ic_env_guard.api.terminals import (
    router as terminals_router,
)
from ic_env_guard.auth.dependencies import AuthState, get_auth_state
from ic_env_guard.bootstrap.composition import (
    AgentContainer,
    ManagerContainer,
    build_agent_container,
    build_manager_container,
)
from ic_env_guard.bootstrap.lifecycle import create_lifespan
from ic_env_guard.config.loader import load_config
from ic_env_guard.config.models import AppConfig
from ic_env_guard.db.audit import AuditRepository
from ic_env_guard.db.audit_queries import AuditQueryRepository
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.monitoring.machines import MachineRegistry
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager

DEFAULT_STATE_DB = Path("/var/lib/ic-env-guard/state.db")


def _resolve_config(config_path: Path | None, config: AppConfig | None) -> AppConfig | None:
    if config is not None:
        return config
    path = config_path or os.environ.get("IC_ENV_GUARD_CONFIG")
    if path is None:
        return None
    return load_config(Path(path))


def _resolve_state_db(state_database: Path | None, config: AppConfig | None) -> Path:
    if state_database is not None:
        return state_database
    if config is not None and config.state_database is not None:
        return config.state_database
    env_state_database = os.environ.get("IC_ENV_GUARD_STATE_DB")
    if env_state_database:
        return Path(env_state_database)
    return DEFAULT_STATE_DB


def create_app(
    token_file: Path | None = None,
    token: str | None = None,
    config_path: Path | None = None,
    config: AppConfig | None = None,
    state_database: Path | None = None,
) -> FastAPI:
    app_config = _resolve_config(config_path, config)
    mode = app_config.mode if app_config else "agent"
    auth_token_file = token_file or (app_config.auth.token_file if app_config else None)

    auth_state = AuthState(token_file=auth_token_file, token=token)
    if mode == "agent":
        db_path = _resolve_state_db(state_database, app_config)
        container: AgentContainer | ManagerContainer = build_agent_container(
            app_config, db_path
        )
        audit_session_factory = container.session_factory
        control_plane_audit_session_factory = None
        agent_client = None
        agent_availability = None
        gateway_ticket_store = None
        gateway_proxy_limiter = None
    else:
        if app_config is None:
            raise RuntimeError("manager mode requires configuration")
        container = build_manager_container(app_config)
        audit_session_factory = None
        control_plane_audit_session_factory = container.control_plane_session_factory
        agent_client = container.agent_client
        agent_availability = container.agent_availability
        gateway_ticket_store = container.gateway_ticket_store
        gateway_proxy_limiter = container.gateway_proxy_limiter

    terminal_manager = container.terminal_manager
    service_manager = container.service_manager
    ticket_manager = container.ticket_manager
    machine_registry = container.machine_registry
    audit_storage_health = container.audit_storage_health
    agent_registry = container.agent_registry
    metrics_registry = container.metrics_registry
    metrics_config = container.metrics_config

    app = FastAPI(
        title="IC Design Environment Guard",
        version="0.1.0",
        lifespan=create_lifespan(container),
    )
    app.state.config = app_config
    app.state.container = container
    register_error_handlers(app)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers.setdefault("X-Correlation-ID", correlation_id)
        collector = metrics_registry._names_to_collectors["ic_env_guard_api_requests"]
        collector.labels(
            method=request.method,
            route_group=classify_route(request.url.path, request.method).value,
            status_class=f"{response.status_code // 100}xx",
        ).inc()
        return response

    def configured_auth_state() -> AuthState:
        return auth_state

    def configured_terminal_manager() -> TerminalManager:
        return terminal_manager

    def configured_ticket_manager() -> TerminalTicketManager:
        return ticket_manager

    def configured_service_manager() -> ServiceManager:
        return service_manager

    def configured_metrics_registry():
        return metrics_registry

    def configured_metrics_access_policy() -> MetricsAccessPolicy:
        return MetricsAccessPolicy(metrics_config.remote_network_allowlist)

    def configured_audit_storage_health() -> AuditStorageHealth:
        return audit_storage_health

    def configured_machine_registry() -> MachineRegistry:
        return machine_registry

    def configured_agent_registry() -> AgentRegistry:
        return agent_registry

    def configured_agent_availability() -> AgentAvailabilityService:
        if agent_availability is None:
            raise RuntimeError("agent availability is not configured in agent mode")
        return agent_availability

    def configured_agent_http_client() -> AgentHttpClient:
        if agent_client is None:
            raise RuntimeError("agent client is not configured in agent mode")
        return agent_client

    def configured_gateway_ticket_store() -> GatewayTicketStore:
        if gateway_ticket_store is None:
            raise RuntimeError("gateway ticket store is not configured in agent mode")
        return gateway_ticket_store

    def configured_gateway_proxy_limiter() -> GatewayProxyLimiter:
        if gateway_proxy_limiter is None:
            raise RuntimeError("gateway proxy limiter is not configured in agent mode")
        return gateway_proxy_limiter

    def configured_audit_query_repository() -> Iterator[AuditQueryRepository]:
        if audit_session_factory is None:
            raise RuntimeError("agent audit repository is not configured in control-plane mode")
        audit_session = audit_session_factory()
        try:
            yield AuditQueryRepository(AuditRepository(audit_session))
        finally:
            audit_session.close()

    def configured_control_plane_audit_repository() -> Iterator[ControlPlaneAuditRepository]:
        if control_plane_audit_session_factory is None:
            raise RuntimeError("control-plane audit repository is not configured in agent mode")
        session = control_plane_audit_session_factory()
        try:
            yield ControlPlaneAuditRepository(session)
        finally:
            session.close()

    app.dependency_overrides[get_auth_state] = configured_auth_state
    app.dependency_overrides[get_terminal_manager] = configured_terminal_manager
    app.dependency_overrides[get_ticket_manager] = configured_ticket_manager
    app.dependency_overrides[get_service_manager] = configured_service_manager
    app.dependency_overrides[get_metrics_registry] = configured_metrics_registry
    app.dependency_overrides[get_metrics_access_policy] = configured_metrics_access_policy
    app.dependency_overrides[get_audit_storage_health] = configured_audit_storage_health
    app.dependency_overrides[get_machine_registry] = configured_machine_registry
    app.dependency_overrides[get_agent_registry] = configured_agent_registry
    app.dependency_overrides[get_agent_availability] = configured_agent_availability
    app.dependency_overrides[get_agent_http_client] = configured_agent_http_client
    app.dependency_overrides[get_gateway_ticket_store] = configured_gateway_ticket_store
    app.dependency_overrides[get_gateway_proxy_limiter] = configured_gateway_proxy_limiter
    app.dependency_overrides[get_audit_query_repository] = configured_audit_query_repository
    app.dependency_overrides[get_control_plane_audit_repository] = (
        configured_control_plane_audit_repository
    )
    terminal_ws.get_terminal_ws_dependencies = lambda: (terminal_manager, ticket_manager)

    app.include_router(health_router)
    app.include_router(auth_router)
    if mode == "agent":
        app.include_router(local_capabilities_router)
        app.include_router(terminals_router)
        app.include_router(services_router)
        app.include_router(metrics_router)
        app.include_router(monitoring_router)
        app.include_router(audit_router)
        app.include_router(terminal_ws.router)
    else:
        app.include_router(fleet_router)
        app.include_router(control_plane_agents_router)
        app.include_router(agent_audit_router)
        app.include_router(agent_services_router)
        app.include_router(agent_monitoring_router)
        app.include_router(agent_terminals_router)
        app.include_router(agent_terminal_ws_router)
        app.include_router(control_plane_audit_router)

    mount_static_ui(app)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ic_env_guard.main:create_app", factory=True, host="127.0.0.1", port=8765)

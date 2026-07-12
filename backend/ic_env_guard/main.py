import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.models import V2_LOCAL_CAPABILITIES
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.agents.terminal_proxy import GatewayProxyLimiter, GatewayTicketStore
from ic_env_guard.api import terminal_ws
from ic_env_guard.api.agent_audit import router as agent_audit_router
from ic_env_guard.api.agent_enrollments import get_enrollment_orchestrator
from ic_env_guard.api.agent_enrollments import router as agent_enrollments_router
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agent_monitoring import router as agent_monitoring_router
from ic_env_guard.api.agent_registry import (
    get_enrollment_orchestrator as get_registry_enrollment_orchestrator,
)
from ic_env_guard.api.agent_registry import (
    get_fleet_probe_service,
    get_fleet_status_service,
)
from ic_env_guard.api.agent_registry import router as agent_registry_v2_router
from ic_env_guard.api.agent_services import router as agent_services_router
from ic_env_guard.api.agent_terminal_ws import (
    AgentWebSocketConnector,
    get_agent_ws_connector,
    get_gateway_proxy_limiter,
)
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
from ic_env_guard.api.auth import (
    AgentLoginAuditRecorder,
    ManagerLoginAuditRecorder,
    get_login_audit_recorder,
    get_login_rate_limiter,
)
from ic_env_guard.api.auth import router as auth_router
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.control_plane_audit import router as control_plane_audit_router
from ic_env_guard.api.errors import register_error_handlers
from ic_env_guard.api.fleet import router as fleet_router
from ic_env_guard.api.fleet_v2 import router as fleet_v2_router
from ic_env_guard.api.health import router as health_router
from ic_env_guard.api.ingest_guard import IngestCapacityMiddleware
from ic_env_guard.api.ingest_logs import router as ingest_logs_router
from ic_env_guard.api.ingest_observations import router as ingest_observations_router
from ic_env_guard.api.logs import (
    AgentLogTailAuditRecorder,
    get_log_source_service,
    get_log_tail_audit_recorder,
    get_logs_config,
)
from ic_env_guard.api.logs import router as logs_router
from ic_env_guard.api.manager_credentials import get_enrollment_service
from ic_env_guard.api.manager_credentials import router as manager_credentials_router
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
from ic_env_guard.api.observations import get_observation_service
from ic_env_guard.api.observations import router as observations_router
from ic_env_guard.api.public_client_cidr import PublicClientCidrMiddleware
from ic_env_guard.api.risk import classify_route
from ic_env_guard.api.runtime import RuntimeMetadata, get_runtime_metadata
from ic_env_guard.api.runtime import router as runtime_router
from ic_env_guard.api.services import get_service_manager
from ic_env_guard.api.services import router as services_router
from ic_env_guard.api.static import mount_static_ui
from ic_env_guard.api.summary import get_summary_service
from ic_env_guard.api.summary import router as summary_router
from ic_env_guard.api.terminals import (
    get_terminal_manager,
    get_ticket_manager,
)
from ic_env_guard.api.terminals import (
    router as terminals_router,
)
from ic_env_guard.api.v2_errors import (
    V2ApiError,
    is_v2_path,
    register_v2_error_handlers,
    resolve_v2_correlation_id,
    unexpected_v2_error_response,
    v2_error_response,
)
from ic_env_guard.auth.dependencies import AuthState, get_auth_state
from ic_env_guard.auth.rate_limit import LoginRateLimiter
from ic_env_guard.bootstrap.composition import (
    AgentContainer,
    ManagerContainer,
    build_agent_container,
    build_manager_container,
)
from ic_env_guard.bootstrap.lifecycle import close_container, create_lifespan
from ic_env_guard.config.loader import load_config
from ic_env_guard.config.models import AppConfig
from ic_env_guard.db.audit import AuditRepository
from ic_env_guard.db.audit_queries import AuditQueryRepository
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.fleet.probes import FleetProbeService
from ic_env_guard.fleet.status import FleetStatusService
from ic_env_guard.fleet.transport import TrustedLanHttpProfile
from ic_env_guard.monitoring.machines import MachineRegistry
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager

DEFAULT_STATE_DB = Path("/var/lib/ic-env-guard/state.db")
DEFAULT_INSTANCE_ID = Path("/var/lib/ic-env-guard/instance-id")


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


def _resolve_instance_id_path(
    instance_id_path: Path | None,
    state_database: Path | None,
    token_file: Path | None,
    config_path: Path | None,
    config: AppConfig | None,
) -> Path:
    if instance_id_path is not None:
        return instance_id_path
    environment_path = os.environ.get("IC_ENV_GUARD_INSTANCE_ID")
    if environment_path:
        return Path(environment_path)
    if state_database is not None:
        return state_database.with_name("instance-id")
    if config is not None and config.state_database is not None:
        return config.state_database.with_name("instance-id")
    if config_path is not None:
        return config_path.with_name("instance-id")
    if token_file is not None and config is None:
        return token_file.with_name("instance-id")
    return DEFAULT_INSTANCE_ID


def create_app(
    token_file: Path | None = None,
    token: str | None = None,
    config_path: Path | None = None,
    config: AppConfig | None = None,
    state_database: Path | None = None,
    instance_id_path: Path | None = None,
    login_limiter: LoginRateLimiter | None = None,
    _container: AgentContainer | ManagerContainer | None = None,
) -> FastAPI:
    app_config = (
        _container.config
        if _container is not None
        else _resolve_config(config_path, config)
    )
    mode = app_config.mode if app_config else "agent"
    auth_token_file = token_file or (app_config.auth.token_file if app_config else None)

    if mode == "agent":
        db_path = _resolve_state_db(state_database, app_config)
        container: AgentContainer | ManagerContainer
        if _container is not None:
            if not isinstance(_container, AgentContainer):
                raise TypeError("agent mode requires an AgentContainer")
            container = _container
        else:
            container = build_agent_container(
                app_config,
                db_path,
                _resolve_instance_id_path(
                    instance_id_path, state_database, token_file, config_path, app_config
                ),
            )
        audit_session_factory = container.session_factory
        control_plane_audit_session_factory = None
        agent_client = None
        agent_availability = None
        gateway_ticket_store = None
        gateway_proxy_limiter = None
        agent_ws_connector = None
        login_audit_recorder = AgentLoginAuditRecorder(container.session_factory)
        auth_state = AuthState(
            token_file=auth_token_file,
            token=token,
            manager_verifier=container.enrollment_service,
        )
    else:
        if app_config is None:
            raise RuntimeError("manager mode requires configuration")
        if _container is not None:
            if not isinstance(_container, ManagerContainer):
                raise TypeError("manager mode requires a ManagerContainer")
            container = _container
        else:
            container = build_manager_container(app_config)
        audit_session_factory = None
        control_plane_audit_session_factory = container.control_plane_session_factory
        agent_client = container.agent_client
        agent_availability = container.agent_availability
        gateway_ticket_store = container.gateway_ticket_store
        gateway_proxy_limiter = container.gateway_proxy_limiter
        agent_ws_connector = container.agent_ws_connector
        login_audit_recorder = ManagerLoginAuditRecorder(
            container.control_plane_session_factory
        )
        auth_state = AuthState(token_file=auth_token_file, token=token)

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
    register_v2_error_handlers(app)

    if mode == "agent":
        runtime_metadata = RuntimeMetadata(
            mode="agent",
            capabilities=("runtime.v2", *container.capabilities),
            instance_id=container.instance_id,
            name=container.instance_name,
            agent_capabilities=(
                *V2_LOCAL_CAPABILITIES,
                "runtime.v2",
                *container.capabilities,
            ),
        )
    else:
        runtime_metadata = RuntimeMetadata(
            mode="manager",
            capabilities=(
                "fleet.v2",
                "agent-registry.v2",
                *(
                    ("trusted-lan-http.v1",)
                    if container.fleet_probe_service is not None
                    and any(
                        isinstance(profile, TrustedLanHttpProfile)
                        for profile in container.config.control_plane.transport_profiles
                    )
                    else ()
                ),
            ),
        )
    configured_login_limiter = login_limiter or LoginRateLimiter()

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        if is_v2_path(request.url.path):
            correlation_id = resolve_v2_correlation_id(
                request.headers.get("X-Correlation-ID")
            )
        else:
            correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        request.state.correlation_id = correlation_id
        try:
            if (
                mode == "agent"
                and request.method == "PUT"
                and (
                    request.url.path == "/api/v2/observations"
                    or request.url.path.startswith("/api/v2/logs/")
                )
            ):
                response = v2_error_response(
                    404, "not_found", "resource not found", correlation_id
                )
            else:
                response = await call_next(request)
        except Exception as exc:
            if not is_v2_path(request.url.path):
                raise
            response = unexpected_v2_error_response(request, exc)
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

    def configured_runtime_metadata() -> RuntimeMetadata:
        if isinstance(container, ManagerContainer):
            adapter = container.ssh_enrollment_adapter
            service_key_adapter = container.service_key_enrollment_adapter
            return RuntimeMetadata(
                mode=runtime_metadata.mode,
                capabilities=(
                    *runtime_metadata.capabilities,
                    *(
                        ("ssh-enrollment.auto.v1",)
                        if adapter is not None and adapter.healthy
                        else ()
                    ),
                    *(
                        ("ssh-enrollment.cli.v1",)
                        if container.manager_enrollment_socket is not None
                        and container.manager_enrollment_socket.healthy
                        else ()
                    ),
                    *(
                        ("ssh-enrollment.service-key.v1",)
                        if service_key_adapter is not None
                        and service_key_adapter.healthy
                        else ()
                    ),
                ),
            )
        enrollment_socket = container.enrollment_socket_server
        if enrollment_socket is None or not enrollment_socket.healthy:
            return runtime_metadata
        return RuntimeMetadata(
            mode=runtime_metadata.mode,
            capabilities=(*runtime_metadata.capabilities, "manager-enrollment.v1"),
            instance_id=runtime_metadata.instance_id,
            name=runtime_metadata.name,
            agent_capabilities=(
                *runtime_metadata.agent_capabilities,
                "manager-enrollment.v1",
            ),
        )

    def configured_login_rate_limiter() -> LoginRateLimiter:
        return configured_login_limiter

    def configured_login_audit_recorder():
        return login_audit_recorder

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

    def configured_agent_ws_connector() -> AgentWebSocketConnector:
        if agent_ws_connector is None:
            raise RuntimeError("Agent WebSocket connector is not configured in agent mode")
        return agent_ws_connector

    def configured_fleet_status_service() -> FleetStatusService:
        if not isinstance(container, ManagerContainer):
            raise RuntimeError("Fleet status is not configured in Agent mode")
        return container.fleet_status_service

    def configured_fleet_probe_service() -> FleetProbeService:
        if (
            not isinstance(container, ManagerContainer)
            or container.fleet_probe_service is None
        ):
            raise V2ApiError(503, "probe_unavailable", "fleet probing is unavailable")
        return container.fleet_probe_service

    def configured_enrollment_orchestrator():
        if not isinstance(container, ManagerContainer):
            raise RuntimeError("Enrollment orchestrator is not configured in Agent mode")
        return container.enrollment_orchestrator

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
    app.dependency_overrides[get_runtime_metadata] = configured_runtime_metadata
    app.dependency_overrides[get_login_rate_limiter] = configured_login_rate_limiter
    app.dependency_overrides[get_login_audit_recorder] = configured_login_audit_recorder
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
    app.dependency_overrides[get_agent_ws_connector] = configured_agent_ws_connector
    app.dependency_overrides[get_fleet_status_service] = configured_fleet_status_service
    app.dependency_overrides[get_fleet_probe_service] = configured_fleet_probe_service
    app.dependency_overrides[get_enrollment_orchestrator] = (
        configured_enrollment_orchestrator
    )
    app.dependency_overrides[get_registry_enrollment_orchestrator] = (
        configured_enrollment_orchestrator
    )
    app.dependency_overrides[get_audit_query_repository] = configured_audit_query_repository
    app.dependency_overrides[get_control_plane_audit_repository] = (
        configured_control_plane_audit_repository
    )
    terminal_ws.get_terminal_ws_dependencies = lambda: (terminal_manager, ticket_manager)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(runtime_router)
    if mode == "agent":
        app.dependency_overrides[get_observation_service] = (
            lambda: container.observation_service
        )
        app.dependency_overrides[get_log_source_service] = (
            lambda: container.log_source_service
        )
        app.dependency_overrides[get_logs_config] = lambda: container.logs_config
        app.dependency_overrides[get_summary_service] = lambda: container.summary_service
        app.dependency_overrides[get_enrollment_service] = (
            lambda: container.enrollment_service
        )
        app.dependency_overrides[get_log_tail_audit_recorder] = lambda: (
            AgentLogTailAuditRecorder(
                container.session_factory, container.audit_storage_health
            )
        )
        app.include_router(local_capabilities_router)
        app.include_router(observations_router)
        app.include_router(logs_router)
        app.include_router(terminals_router)
        app.include_router(services_router)
        app.include_router(metrics_router)
        app.include_router(summary_router)
        app.include_router(manager_credentials_router)
        app.include_router(monitoring_router)
        app.include_router(audit_router)
        app.include_router(terminal_ws.router)
    else:
        app.include_router(agent_enrollments_router)
        app.include_router(agent_registry_v2_router)
        app.include_router(fleet_v2_router)
        app.include_router(fleet_router)
        app.include_router(control_plane_agents_router)
        app.include_router(agent_audit_router)
        app.include_router(agent_services_router)
        app.include_router(agent_monitoring_router)
        app.include_router(agent_terminals_router)
        app.include_router(agent_terminal_ws_router)
        app.include_router(control_plane_audit_router)

    mount_static_ui(app)

    if app_config is not None and app_config.server.trusted_lan_http.enabled:
        app.add_middleware(
            PublicClientCidrMiddleware,
            networks=tuple(app_config.server.trusted_lan_http.client_cidrs),
        )

    return app


def create_public_app(container: AgentContainer | ManagerContainer) -> FastAPI:
    if container.config is None:
        raise ValueError("a configured container is required for the public app")
    return create_app(config=container.config, _container=container)


def create_ingest_app(container: AgentContainer) -> FastAPI:
    if container.config is None:
        raise ValueError("a configured AgentContainer is required for the ingest app")
    app = FastAPI(
        title="IC Env Guard Local Ingest",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = container.config
    app.state.container = container
    register_error_handlers(app)
    register_v2_error_handlers(app)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = resolve_v2_correlation_id(
            request.headers.get("X-Correlation-ID")
        )
        request.state.correlation_id = correlation_id
        try:
            if request.method != "PUT" and (
                request.url.path == "/api/v2/observations"
                or request.url.path == "/api/v2/logs"
                or request.url.path.startswith("/api/v2/logs/")
            ):
                response = v2_error_response(
                    404, "not_found", "resource not found", correlation_id
                )
            else:
                response = await call_next(request)
        except Exception as exc:
            response = unexpected_v2_error_response(request, exc)
        response.headers.setdefault("X-Correlation-ID", correlation_id)
        return response

    app.dependency_overrides[get_observation_service] = (
        lambda: container.observation_service
    )
    app.dependency_overrides[get_log_source_service] = lambda: container.log_source_service
    app.include_router(ingest_observations_router)
    app.include_router(ingest_logs_router)
    app.add_middleware(
        IngestCapacityMiddleware,
        maximum=container.config.ingest.max_concurrent_requests,
        max_request_bytes=container.config.ingest.max_request_bytes,
    )
    return app


async def serve_config(
    config: AppConfig,
    *,
    config_path: Path | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    import uvicorn

    if config.mode == "agent":
        container: AgentContainer | ManagerContainer = build_agent_container(
            config,
            _resolve_state_db(None, config),
            _resolve_instance_id_path(None, None, None, config_path, config),
        )
    else:
        container = build_manager_container(config)

    public_app: FastAPI | None = None
    server_specs: list[tuple[uvicorn.Server, str, int]] = []
    sockets = []
    tasks: list[asyncio.Task[None]] = []
    shutdown_task: asyncio.Task[bool] | None = None
    completion_error: BaseException | None = None
    try:
        public_app = create_public_app(container)
        server_specs.append(
            (
                uvicorn.Server(
                    uvicorn.Config(
                        public_app,
                        host=config.server.bind,
                        port=config.server.port,
                        proxy_headers=False,
                        lifespan="on",
                    )
                ),
                config.server.bind,
                config.server.port,
            )
        )
        if isinstance(container, AgentContainer):
            server_specs.append(
                (
                    uvicorn.Server(
                        uvicorn.Config(
                            create_ingest_app(container),
                            host=config.ingest.bind,
                            port=config.ingest.port,
                            proxy_headers=False,
                            lifespan="off",
                        )
                    ),
                    config.ingest.bind,
                    config.ingest.port,
                )
            )
        for server, _host, _port in server_specs:
            sockets.append(server.config.bind_socket())
        tasks = [
            asyncio.create_task(server.serve(sockets=[listener]))
            for (server, _host, _port), listener in zip(server_specs, sockets, strict=True)
        ]
        waiters: list[asyncio.Task[object]] = list(tasks)
        if shutdown_event is not None:
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            waiters.append(shutdown_task)
        done, _pending = await asyncio.wait(
            waiters, return_when=asyncio.FIRST_COMPLETED
        )
        explicit_shutdown = shutdown_task is not None and shutdown_task in done
        if not explicit_shutdown:
            for task, (server, _host, port) in zip(tasks, server_specs, strict=True):
                if task not in done:
                    continue
                if task.cancelled():
                    completion_error = RuntimeError(
                        f"listener task on port {port} was cancelled"
                    )
                else:
                    exception = task.exception()
                    if exception is not None:
                        completion_error = exception
                    elif not server.started:
                        completion_error = RuntimeError(
                            f"listener on port {port} failed to start"
                        )
                    elif not server.should_exit:
                        completion_error = RuntimeError(
                            f"listener task on port {port} returned unexpectedly"
                        )
                if completion_error is not None:
                    break
    finally:
        for server, _host, _port in server_specs:
            server.should_exit = True
        if shutdown_task is not None:
            shutdown_task.cancel()
            await asyncio.gather(shutdown_task, return_exceptions=True)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for listener in sockets:
            listener.close()
        if public_app is None or not getattr(
            public_app.state, "lifecycle_cleanup_complete", False
        ):
            await close_container(container)

    if completion_error is not None:
        raise completion_error
    for result in results:
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise RuntimeError("listener task was cancelled") from result
            raise result


def main() -> None:
    configured_path = os.environ.get("IC_ENV_GUARD_CONFIG")
    if configured_path is None:
        raise RuntimeError("IC_ENV_GUARD_CONFIG is required")
    config_path = Path(configured_path)
    config = _resolve_config(config_path, None)
    assert config is not None
    asyncio.run(serve_config(config, config_path=config_path))

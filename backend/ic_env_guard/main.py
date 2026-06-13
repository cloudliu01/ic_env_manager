import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ic_env_guard.api import terminal_ws
from ic_env_guard.api.audit import get_audit_query_repository
from ic_env_guard.api.audit import router as audit_router
from ic_env_guard.api.auth import router as auth_router
from ic_env_guard.api.errors import register_error_handlers
from ic_env_guard.api.health import router as health_router
from ic_env_guard.api.metrics import (
    MetricsAccessPolicy,
    get_metrics_access_policy,
    get_metrics_registry,
)
from ic_env_guard.api.metrics import router as metrics_router
from ic_env_guard.api.monitoring import get_machine_registry
from ic_env_guard.api.monitoring import router as monitoring_router
from ic_env_guard.api.risk import classify_route
from ic_env_guard.api.services import get_service_manager
from ic_env_guard.api.services import router as services_router
from ic_env_guard.api.terminals import (
    get_terminal_manager,
    get_ticket_manager,
)
from ic_env_guard.api.terminals import (
    router as terminals_router,
)
from ic_env_guard.auth.dependencies import AuthState, get_auth_state
from ic_env_guard.config.loader import load_config
from ic_env_guard.config.models import AppConfig, MetricsConfig, ServiceConfig
from ic_env_guard.db.audit import AuditRepository
from ic_env_guard.db.audit_queries import AuditQueryRepository
from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.db.session import Base
from ic_env_guard.metrics.collector import MetricsCollector
from ic_env_guard.metrics.registry import create_registry
from ic_env_guard.monitoring.machines import MachineRegistry
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager


def _resolve_config(config_path: Path | None, config: AppConfig | None) -> AppConfig | None:
    if config is not None:
        return config
    path = config_path or os.environ.get("IC_ENV_GUARD_CONFIG")
    if path is None:
        return None
    return load_config(Path(path))


def _service_runtime(service: ServiceConfig) -> ServiceRuntime:
    return ServiceRuntime(
        id=service.id,
        name=service.name,
        command=service.command,
        systemd_unit=service.systemd_unit,
        allowed_operations=list(service.allowed_operations),
        description=service.description,
        cwd=service.cwd,
        env=dict(service.env),
        autostart=service.autostart,
        restart_policy=service.restart,
        start_timeout_seconds=service.start_timeout_seconds,
        stop_timeout_seconds=service.stop_timeout_seconds,
    )


async def _metrics_refresh_loop(collector: MetricsCollector, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        collector.refresh()


def create_app(
    token_file: Path | None = None,
    token: str | None = None,
    config_path: Path | None = None,
    config: AppConfig | None = None,
) -> FastAPI:
    app_config = _resolve_config(config_path, config)
    metrics_config = app_config.metrics if app_config else MetricsConfig()
    auth_token_file = token_file or (app_config.auth.token_file if app_config else None)

    auth_state = AuthState(token_file=auth_token_file, token=token)
    terminal_manager = TerminalManager()
    service_manager = ServiceManager(
        [_service_runtime(service) for service in app_config.services] if app_config else []
    )
    machine_registry = MachineRegistry()
    ticket_manager = TerminalTicketManager()
    metrics_registry = create_registry()
    metrics_collector = MetricsCollector(metrics_registry, terminal_manager, service_manager)
    metrics_collector.refresh()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        refresh_task: asyncio.Task[None] | None = None
        if metrics_config.enabled:
            refresh_task = asyncio.create_task(
                _metrics_refresh_loop(metrics_collector, metrics_config.collect_interval_seconds)
            )
            app.state.metrics_refresh_task = refresh_task
        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh_task

    app = FastAPI(title="IC Design Environment Guard", version="0.1.0", lifespan=lifespan)
    app.state.config = app_config
    register_error_handlers(app)

    audit_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(audit_engine)
    audit_session_factory = sessionmaker(bind=audit_engine, future=True)
    audit_session = audit_session_factory()
    audit_query_repository = AuditQueryRepository(AuditRepository(audit_session))

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
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

    def configured_machine_registry() -> MachineRegistry:
        return machine_registry

    def configured_audit_query_repository() -> AuditQueryRepository:
        return audit_query_repository

    app.dependency_overrides[get_auth_state] = configured_auth_state
    app.dependency_overrides[get_terminal_manager] = configured_terminal_manager
    app.dependency_overrides[get_ticket_manager] = configured_ticket_manager
    app.dependency_overrides[get_service_manager] = configured_service_manager
    app.dependency_overrides[get_metrics_registry] = configured_metrics_registry
    app.dependency_overrides[get_metrics_access_policy] = configured_metrics_access_policy
    app.dependency_overrides[get_machine_registry] = configured_machine_registry
    app.dependency_overrides[get_audit_query_repository] = configured_audit_query_repository
    terminal_ws.get_terminal_ws_dependencies = lambda: (terminal_manager, ticket_manager)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(terminals_router)
    app.include_router(services_router)
    app.include_router(metrics_router)
    app.include_router(monitoring_router)
    app.include_router(audit_router)
    app.include_router(terminal_ws.router)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ic_env_guard.main:create_app", factory=True, host="127.0.0.1", port=8765)

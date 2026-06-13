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
from ic_env_guard.api.metrics import get_metrics_registry
from ic_env_guard.api.metrics import router as metrics_router
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
from ic_env_guard.db.audit import AuditRepository
from ic_env_guard.db.audit_queries import AuditQueryRepository
from ic_env_guard.db.session import Base
from ic_env_guard.metrics.collector import MetricsCollector
from ic_env_guard.metrics.registry import create_registry
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager


def create_app(token_file: Path | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="IC Design Environment Guard", version="0.1.0")
    register_error_handlers(app)

    auth_state = AuthState(token_file=token_file, token=token)
    terminal_manager = TerminalManager()
    service_manager = ServiceManager()
    ticket_manager = TerminalTicketManager()
    metrics_registry = create_registry()
    MetricsCollector(metrics_registry, terminal_manager, service_manager).refresh()

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

    def configured_audit_query_repository() -> AuditQueryRepository:
        return audit_query_repository

    app.dependency_overrides[get_auth_state] = configured_auth_state
    app.dependency_overrides[get_terminal_manager] = configured_terminal_manager
    app.dependency_overrides[get_ticket_manager] = configured_ticket_manager
    app.dependency_overrides[get_service_manager] = configured_service_manager
    app.dependency_overrides[get_metrics_registry] = configured_metrics_registry
    app.dependency_overrides[get_audit_query_repository] = configured_audit_query_repository
    terminal_ws.get_terminal_ws_dependencies = lambda: (terminal_manager, ticket_manager)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(terminals_router)
    app.include_router(services_router)
    app.include_router(metrics_router)
    app.include_router(audit_router)
    app.include_router(terminal_ws.router)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ic_env_guard.main:create_app", factory=True, host="127.0.0.1", port=8765)

import socket
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from prometheus_client import CollectorRegistry
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.agents.terminal_proxy import GatewayProxyLimiter, GatewayTicketStore
from ic_env_guard.api.audit_health import AuditStorageHealth
from ic_env_guard.bootstrap.identity import (
    initialize_instance_id,
)
from ic_env_guard.config.models import (
    AppConfig,
    EnrollmentConfig,
    LogsConfig,
    MetricsConfig,
    ServiceConfig,
)
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.migrations import run_migrations
from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.db.session import create_session_factory, create_sqlite_engine
from ic_env_guard.enrollment.audit import AgentEnrollmentAudit
from ic_env_guard.enrollment.credential_store import CredentialStore
from ic_env_guard.enrollment.service import EnrollmentService
from ic_env_guard.enrollment.socket_server import EnrollmentSocketServer
from ic_env_guard.logs.policy import LogPathPolicy, LogTailReader
from ic_env_guard.logs.service import LogSourceService
from ic_env_guard.metrics.collector import MetricsCollector
from ic_env_guard.metrics.observability import ObservabilityCollector
from ic_env_guard.metrics.registry import create_registry
from ic_env_guard.monitoring.machines import MachineRegistry
from ic_env_guard.observations.service import ObservationService
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.log_sources import SQLiteLogSourceRepository
from ic_env_guard.storage.manager_credentials import SQLiteManagerCredentialRepository
from ic_env_guard.storage.manager_registry import (
    AgentStatusRepository,
)
from ic_env_guard.storage.manager_registry import (
    ManagerRegistryRepository as SQLiteManagerRegistryRepository,
)
from ic_env_guard.storage.observations import SQLiteObservationRepository
from ic_env_guard.summary.service import SummaryService
from ic_env_guard.terminal.manager import TerminalManager
from ic_env_guard.terminal.tickets import TerminalTicketManager


@dataclass
class AgentContainer:
    config: AppConfig | None
    instance_id: UUID
    instance_name: str
    capabilities: tuple[str, ...]
    terminal_manager: TerminalManager
    service_manager: ServiceManager
    session_factory: sessionmaker
    metrics_registry: CollectorRegistry
    database_engine: Engine
    metrics_collector: MetricsCollector
    metrics_config: MetricsConfig
    ticket_manager: TerminalTicketManager
    machine_registry: MachineRegistry
    audit_storage_health: AuditStorageHealth
    agent_registry: AgentRegistry
    observation_service: ObservationService
    summary_service: SummaryService
    logs_config: LogsConfig
    log_path_policy: LogPathPolicy
    log_source_repository: SQLiteLogSourceRepository
    log_tail_reader: LogTailReader
    log_source_service: LogSourceService
    manager_credential_repository: SQLiteManagerCredentialRepository
    enrollment_service: EnrollmentService
    enrollment_socket_server: EnrollmentSocketServer | None


@dataclass
class ManagerContainer:
    config: AppConfig
    agent_registry: AgentRegistry
    agent_client: AgentHttpClient
    agent_availability: AgentAvailabilityService
    gateway_ticket_store: GatewayTicketStore
    gateway_proxy_limiter: GatewayProxyLimiter
    control_plane_session_factory: sessionmaker
    metrics_registry: CollectorRegistry
    database_engine: Engine
    metrics_collector: MetricsCollector
    metrics_config: MetricsConfig
    terminal_manager: TerminalManager
    service_manager: ServiceManager
    ticket_manager: TerminalTicketManager
    machine_registry: MachineRegistry
    audit_storage_health: AuditStorageHealth
    manager_id: UUID
    registry_repository: SQLiteManagerRegistryRepository
    status_repository: AgentStatusRepository
    enrollment_journal_repository: EnrollmentJournalRepository
    credential_store: CredentialStore


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


def configured_agent_capabilities(config: AppConfig) -> tuple[str, ...]:
    if config.server.trusted_lan_http.enabled:
        return ("trusted-lan-http.v1",)
    return ()


def build_agent_container(
    config: AppConfig | None,
    state_database: Path,
    instance_id_path: Path | None = None,
) -> AgentContainer:
    instance_id = initialize_instance_id(
        instance_id_path or state_database.with_name("instance-id"),
        state_database,
        run_migrations,
    )
    database_engine = create_sqlite_engine(state_database)
    session_factory = create_session_factory(database_engine)
    terminal_manager = TerminalManager()
    service_manager = ServiceManager(
        [_service_runtime(service) for service in config.services] if config else []
    )
    metrics_registry = create_registry()
    metrics_collector = MetricsCollector(metrics_registry, terminal_manager, service_manager)
    metrics_collector.refresh()
    metrics_config = config.metrics if config else MetricsConfig()
    observation_repository = SQLiteObservationRepository(
        database_engine, max_series=metrics_config.max_observation_series
    )
    observation_service = ObservationService(observation_repository)
    logs_config = config.logs if config else LogsConfig()
    log_path_policy = LogPathPolicy(logs_config.allowed_roots)
    log_source_repository = SQLiteLogSourceRepository(database_engine)
    log_tail_reader = LogTailReader(log_path_policy, max_bytes=logs_config.max_tail_bytes)
    log_source_service = LogSourceService(
        log_source_repository, log_path_policy, log_tail_reader
    )
    metrics_registry.register(
        ObservabilityCollector(observation_repository, log_source_repository)
    )
    summary_service = SummaryService(
        observation_repository,
        log_source_repository,
        service_manager,
        terminal_manager,
    )
    manager_credential_repository = SQLiteManagerCredentialRepository(database_engine)
    enrollment_config = config.enrollment if config else EnrollmentConfig()
    enrollment_service = EnrollmentService(
        manager_credential_repository,
        AgentEnrollmentAudit(session_factory),
        pending_ttl_seconds=enrollment_config.pending_ttl_seconds,
        max_pending=enrollment_config.max_pending,
    )
    enrollment_socket_server = (
        EnrollmentSocketServer(
            enrollment_config.socket_path,
            int(enrollment_config.socket_mode, 8),
            instance_id,
            enrollment_service,
        )
        if config is not None
        else None
    )
    return AgentContainer(
        config=config,
        instance_id=instance_id,
        instance_name=socket.gethostname(),
        capabilities=configured_agent_capabilities(config) if config else (),
        terminal_manager=terminal_manager,
        service_manager=service_manager,
        session_factory=session_factory,
        metrics_registry=metrics_registry,
        database_engine=database_engine,
        metrics_collector=metrics_collector,
        metrics_config=metrics_config,
        ticket_manager=TerminalTicketManager(),
        machine_registry=MachineRegistry(),
        audit_storage_health=AuditStorageHealth(),
        agent_registry=AgentRegistry(config.agents if config else []),
        observation_service=observation_service,
        summary_service=summary_service,
        logs_config=logs_config,
        log_path_policy=log_path_policy,
        log_source_repository=log_source_repository,
        log_tail_reader=log_tail_reader,
        log_source_service=log_source_service,
        manager_credential_repository=manager_credential_repository,
        enrollment_service=enrollment_service,
        enrollment_socket_server=enrollment_socket_server,
    )


def build_manager_container(config: AppConfig) -> ManagerContainer:
    run_control_plane_migrations(config.control_plane.audit_database)
    database_engine = create_sqlite_engine(config.control_plane.audit_database)
    registry_repository = SQLiteManagerRegistryRepository(database_engine)
    status_repository = AgentStatusRepository(database_engine)
    enrollment_journal_repository = EnrollmentJournalRepository(database_engine)
    credential_store = CredentialStore(config.control_plane.effective_credential_directory)
    agent_registry = AgentRegistry(config.agents)
    agent_client = AgentHttpClient()
    metrics_registry = create_registry()
    terminal_manager = TerminalManager()
    service_manager = ServiceManager([])
    metrics_collector = MetricsCollector(metrics_registry, terminal_manager, service_manager)
    metrics_collector.refresh()
    return ManagerContainer(
        config=config,
        agent_registry=agent_registry,
        agent_client=agent_client,
        agent_availability=AgentAvailabilityService(
            agent_registry,
            agent_client,
            stale_after_seconds=config.control_plane.status_stale_after_seconds,
            max_parallel_probes=config.control_plane.max_parallel_probes,
        ),
        gateway_ticket_store=GatewayTicketStore(
            config.control_plane.max_outstanding_tickets
        ),
        gateway_proxy_limiter=GatewayProxyLimiter(
            config.control_plane.max_active_terminal_proxies
        ),
        control_plane_session_factory=create_session_factory(database_engine),
        metrics_registry=metrics_registry,
        database_engine=database_engine,
        metrics_collector=metrics_collector,
        metrics_config=config.metrics,
        terminal_manager=terminal_manager,
        service_manager=service_manager,
        ticket_manager=TerminalTicketManager(),
        machine_registry=MachineRegistry(),
        audit_storage_health=AuditStorageHealth(),
        manager_id=registry_repository.get_or_create_manager_id(),
        registry_repository=registry_repository,
        status_repository=status_repository,
        enrollment_journal_repository=enrollment_journal_repository,
        credential_store=credential_store,
    )

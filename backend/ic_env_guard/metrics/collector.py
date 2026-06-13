from prometheus_client import CollectorRegistry

from ic_env_guard.metrics.agent import update_agent_metrics
from ic_env_guard.metrics.host import update_host_metrics
from ic_env_guard.metrics.services import update_service_metrics
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager


class MetricsCollector:
    def __init__(
        self,
        registry: CollectorRegistry,
        terminal_manager: TerminalManager | None = None,
        service_manager: ServiceManager | None = None,
    ) -> None:
        self.registry = registry
        self.terminal_manager = terminal_manager
        self.service_manager = service_manager

    def refresh(self) -> None:
        update_host_metrics(self.registry)
        update_agent_metrics(self.registry, self.terminal_manager)
        update_service_metrics(self.registry, self.service_manager)

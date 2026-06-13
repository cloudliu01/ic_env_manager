from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager


class StateReconciler:
    def __init__(self, terminal_manager: TerminalManager, service_manager: ServiceManager) -> None:
        self.terminal_manager = terminal_manager
        self.service_manager = service_manager

    def reconcile(self) -> dict[str, int]:
        terminal_count = len(self.terminal_manager.list())
        service_count = len(self.service_manager.list_services())
        return {"terminals": terminal_count, "services": service_count}

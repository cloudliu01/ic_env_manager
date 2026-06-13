import pytest

from ic_env_guard.db.reconciliation import StateReconciler
from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager


@pytest.mark.integration
def test_state_reconciliation_reports_current_terminal_and_service_state():
    terminal_manager = TerminalManager()
    service_manager = ServiceManager([ServiceRuntime(id="demo", name="Demo", command="sleep 5")])

    terminal = terminal_manager.create_terminal(title="Shell")
    try:
        summary = StateReconciler(terminal_manager, service_manager).reconcile()

        assert summary == {"terminals": 1, "services": 1}
        assert terminal_manager.get(terminal.id).status == "running"
    finally:
        terminal_manager.close(terminal.id)

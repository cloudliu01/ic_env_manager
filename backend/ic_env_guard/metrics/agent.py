from prometheus_client import CollectorRegistry

from ic_env_guard.terminal.manager import TerminalManager


def update_agent_metrics(
    registry: CollectorRegistry, terminal_manager: TerminalManager | None = None
) -> None:
    names = registry._names_to_collectors
    if terminal_manager is None:
        return
    counts: dict[str, int] = {}
    for session in terminal_manager.list():
        counts[session.status] = counts.get(session.status, 0) + 1
    for status, count in counts.items():
        names["ic_env_guard_terminal_sessions"].labels(status=status).set(count)

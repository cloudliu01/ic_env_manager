from datetime import UTC, datetime, timedelta

from ic_env_guard.fleet.models import AgentStatus
from ic_env_guard.fleet.status import derive_connection_status, derive_workload_status

NOW = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)


def _probe(**changes):
    values = {
        "agent_id": "lab-01",
        "target_revision": 1,
        "connection_status": "ready",
        "workload_status": "healthy",
        "observed_at": NOW - timedelta(seconds=5),
        "stale_after": NOW + timedelta(seconds=5),
        "api_version": "2",
        "agent_version": "0.2.0",
        "capabilities": ("summary.v2",),
        "summary": {},
        "last_error_code": None,
        "updated_at": NOW,
    }
    values.update(changes)
    return AgentStatus(**values)


def test_connection_status_is_separate_from_workload_and_expires():
    assert derive_connection_status(agent_enabled=False, probe=None, now=NOW) == "disabled"
    assert derive_connection_status(agent_enabled=True, probe=None, now=NOW) == "unknown"
    assert (
        derive_connection_status(
            agent_enabled=True,
            probe=_probe(stale_after=NOW),
            now=NOW,
        )
        == "unknown"
    )
    assert derive_connection_status(agent_enabled=True, probe=_probe(), now=NOW) == "ready"


def test_workload_status_uses_summary_priority_and_staleness():
    assert derive_workload_status(None, now=NOW, stale_after=None) == "unknown"
    assert derive_workload_status({}, now=NOW, stale_after=NOW) == "stale"
    assert (
        derive_workload_status(
            {"observations": {"critical": 1}, "services": {"unhealthy": 0}},
            now=NOW,
            stale_after=NOW + timedelta(seconds=1),
        )
        == "critical"
    )
    assert (
        derive_workload_status(
            {
                "observations": {"critical": 0, "warning": 1, "stale": 0},
                "services": {"unhealthy": 0},
                "logs": {"stale": 0},
            },
            now=NOW,
            stale_after=NOW + timedelta(seconds=1),
        )
        == "warning"
    )
    assert (
        derive_workload_status(
            {
                "observations": {"critical": 0, "warning": 0, "stale": 0},
                "services": {"unhealthy": 0},
                "logs": {"stale": 0},
            },
            now=NOW,
            stale_after=NOW + timedelta(seconds=1),
        )
        == "healthy"
    )

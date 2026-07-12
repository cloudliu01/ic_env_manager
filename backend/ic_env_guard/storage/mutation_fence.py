import sqlite3

from ic_env_guard.fleet.models import RegistryConflict

_TERMINAL_STATES = ("consumed", "cancelled", "failed", "expired")


def assert_agent_mutation_allowed(
    connection: sqlite3.Connection,
    agent_id: str,
    *,
    owner_enrollment_id: str | None = None,
    owner_removal_id: str | None = None,
) -> None:
    removal_sql = (
        "SELECT removal_id FROM agent_removal_jobs WHERE agent_id=? "
        "AND phase!='completed'"
    )
    removal_parameters: list[object] = [agent_id]
    if owner_removal_id is not None:
        removal_sql += " AND removal_id<>?"
        removal_parameters.append(owner_removal_id)
    removal = connection.execute(
        removal_sql + " LIMIT 1", tuple(removal_parameters)
    ).fetchone()
    if removal is not None:
        raise RegistryConflict("agent_mutation_in_progress")
    placeholders = ",".join("?" for _ in _TERMINAL_STATES)
    rotation_sql = (
        "SELECT enrollment_id FROM agent_enrollment_jobs WHERE replace_agent_id=? "
        "AND save_requested=1 "
        f"AND state NOT IN ({placeholders})"
    )
    rotation_parameters: list[object] = [agent_id, *_TERMINAL_STATES]
    if owner_enrollment_id is not None:
        rotation_sql += " AND enrollment_id<>?"
        rotation_parameters.append(owner_enrollment_id)
    rotation = connection.execute(
        rotation_sql + " LIMIT 1", tuple(rotation_parameters)
    ).fetchone()
    if rotation is not None:
        raise RegistryConflict("agent_mutation_in_progress")

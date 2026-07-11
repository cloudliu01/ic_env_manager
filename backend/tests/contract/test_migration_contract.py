import sqlite3

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.migrations import MigrationError, run_migrations


@pytest.mark.contract
def test_migration_runner_records_forward_only_metadata(tmp_path):
    db_path = tmp_path / "state.db"

    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT version, direction, result FROM schema_versions ORDER BY version"
        ).fetchall()
    finally:
        connection.close()

    assert ("0001_initial", "upgrade", "success") in rows
    assert ("0002_state_audit_indexes", "upgrade", "success") in rows
    assert rows.count(("0003_observability", "upgrade", "success")) == 1


@pytest.mark.contract
def test_migration_runner_fails_on_failed_migration_state(tmp_path):
    db_path = tmp_path / "state.db"
    run_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE schema_versions SET result = 'failed', failure_reason = 'boom' "
            "WHERE version = '0002_state_audit_indexes'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(MigrationError, match="failed migration"):
        run_migrations(db_path)


@pytest.mark.contract
def test_control_plane_migration_runner_records_forward_only_metadata(tmp_path):
    db_path = tmp_path / "control-plane.db"

    run_control_plane_migrations(db_path)
    run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT version, direction, result FROM schema_versions ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert rows.count(("0001_control_plane_audit", "upgrade", "success")) == 1
    assert "control_plane_audit_events" in tables


@pytest.mark.contract
def test_agent_and_control_plane_migrations_keep_databases_isolated(tmp_path):
    agent_db = tmp_path / "agent.db"
    control_plane_db = tmp_path / "control-plane.db"

    run_migrations(agent_db)
    run_control_plane_migrations(control_plane_db)

    agent_connection = sqlite3.connect(agent_db)
    control_plane_connection = sqlite3.connect(control_plane_db)
    try:
        agent_tables = {
            row[0]
            for row in agent_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        control_plane_tables = {
            row[0]
            for row in control_plane_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        agent_connection.close()
        control_plane_connection.close()

    assert "audit_events" in agent_tables
    assert "control_plane_audit_events" not in agent_tables
    assert "control_plane_audit_events" in control_plane_tables
    assert "audit_events" not in control_plane_tables

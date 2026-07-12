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
    assert rows.count(("0004_manager_credentials", "upgrade", "success")) == 1


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
    assert rows.count(("0002_fleet_registry", "upgrade", "success")) == 1
    assert "control_plane_audit_events" in tables


@pytest.mark.contract
def test_fleet_registry_migration_has_exact_control_plane_tables(tmp_path):
    db_path = tmp_path / "control-plane.db"
    run_control_plane_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        expected_columns = {
            "manager_metadata": ("key", "value"),
            "agents": (
                "agent_id", "instance_id", "display_name", "normalized_endpoint",
                "credential_ref", "remote_credential_id", "transport_profile_id",
                "enrollment_method", "enabled", "source", "revision", "created_at",
                "updated_at",
            ),
            "agent_status": (
                "agent_id", "target_revision", "connection_status", "workload_status",
                "observed_at", "stale_after", "api_version", "agent_version",
                "capabilities_json", "summary_json", "last_error_code", "updated_at",
            ),
            "agent_enrollment_jobs": (
                "enrollment_id", "manager_id", "state", "normalized_endpoint",
                "transport_profile_id", "discovery_result_id", "replace_agent_id",
                "requested_display_name", "ssh_user", "ssh_host", "ssh_port",
                "enrollment_method", "remote_instance_id", "remote_credential_id",
                "credential_temp_ref", "old_credential_ref", "old_remote_credential_id",
                "save_requested", "expires_at", "last_error_code", "created_at", "updated_at",
            ),
        }
        for table, columns in expected_columns.items():
            actual = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            )
            assert actual == columns

        agent_indexes = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info({row[1]})").fetchall()
            )
            for row in connection.execute("PRAGMA index_list(agents)").fetchall()
            if row[2]
        }
        assert agent_indexes == {
            ("agent_id",),
            ("instance_id",),
            ("normalized_endpoint",),
        }
        status_fks = connection.execute("PRAGMA foreign_key_list(agent_status)").fetchall()
        journal_fks = connection.execute(
            "PRAGMA foreign_key_list(agent_enrollment_jobs)"
        ).fetchall()
    finally:
        connection.close()

    assert any(
        row[2] == "agents" and row[3] == "agent_id" and row[6] == "CASCADE"
        for row in status_fks
    )
    assert any(row[2] == "agents" and row[3] == "replace_agent_id" for row in journal_fks)


@pytest.mark.contract
def test_manager_database_never_stores_plaintext_credentials(tmp_path):
    db_path = tmp_path / "control-plane.db"
    run_control_plane_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for table in ("agents", "agent_enrollment_jobs")
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
    finally:
        connection.close()

    assert "token" not in columns
    assert "token_hash" not in columns
    assert "private_key" not in columns


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

import importlib.util
import sqlite3

import pytest

from ic_env_guard.db.migrations import MIGRATIONS_DIR

_MIGRATION_PATH = MIGRATIONS_DIR / "0001_initial.py"
_SPEC = importlib.util.spec_from_file_location("migration_0001_initial", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
initial_migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(initial_migration)


@pytest.mark.integration
def test_initial_migration_creates_required_tables(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)

    initial_migration.upgrade(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "schema_versions",
        "local_administrators",
        "agent_lifecycle_events",
        "configuration_load_events",
        "audit_events",
        "terminal_sessions",
        "managed_services",
        "service_state",
        "service_runs",
        "service_operations",
        "service_events",
        "healthcheck_results",
        "metrics_exposure",
    }.issubset(tables)


@pytest.mark.integration
def test_initial_migration_is_idempotent_for_current_schema(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")

    initial_migration.upgrade(connection)
    initial_migration.upgrade(connection)

    versions = connection.execute(
        "SELECT version FROM schema_versions WHERE version = ?", (initial_migration.VERSION,)
    ).fetchall()
    assert len(versions) == 1


@pytest.mark.integration
def test_initial_migration_records_failed_schema_state(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")
    connection.execute("CREATE TABLE schema_versions (version text primary key)")
    connection.execute("INSERT INTO schema_versions(version) VALUES (?)", ("failed-version",))

    initial_migration.upgrade(connection)

    versions = {row[0] for row in connection.execute("SELECT version FROM schema_versions")}
    assert "failed-version" in versions
    assert initial_migration.VERSION in versions

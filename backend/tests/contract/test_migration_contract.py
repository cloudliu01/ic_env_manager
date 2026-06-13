import sqlite3

import pytest

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

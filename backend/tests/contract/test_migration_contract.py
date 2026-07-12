import sqlite3
from pathlib import Path

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.migrations import MigrationError, _load_migration, run_migrations

CONTROL_PLANE_MIGRATIONS = (
    Path(__file__).parents[2] / "ic_env_guard" / "control_plane_migrations"
)


def _run_control_plane_through(connection, version):
    for path in sorted(CONTROL_PLANE_MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        if path.name > version:
            break
        _load_migration(path).upgrade(connection)


def _create_applied_parent_fleet_schema(connection):
    connection.executescript(
        """
        CREATE TABLE schema_versions (
            version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL,
            direction TEXT NOT NULL, result TEXT NOT NULL, failure_reason TEXT
        );
        INSERT INTO schema_versions VALUES
            ('0001_control_plane_audit', datetime('now'), 'audit', 'upgrade', 'success', NULL),
            ('0002_fleet_registry', datetime('now'), 'fleet', 'upgrade', 'success', NULL);
        CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY, instance_id TEXT NULL UNIQUE, display_name TEXT NOT NULL,
            normalized_endpoint TEXT NOT NULL UNIQUE, credential_ref TEXT NOT NULL,
            remote_credential_id TEXT NULL, transport_profile_id TEXT NOT NULL,
            enrollment_method TEXT NOT NULL, enabled INTEGER NOT NULL, source TEXT NOT NULL,
            revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE agent_status (
            agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
            target_revision INTEGER NOT NULL, connection_status TEXT NOT NULL,
            workload_status TEXT NOT NULL, observed_at TEXT NULL, stale_after TEXT NULL,
            api_version TEXT NULL, agent_version TEXT NULL, capabilities_json TEXT NOT NULL,
            summary_json TEXT NOT NULL, last_error_code TEXT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE agent_enrollment_jobs (
            enrollment_id TEXT PRIMARY KEY, manager_id TEXT NOT NULL, state TEXT NOT NULL,
            normalized_endpoint TEXT NOT NULL, transport_profile_id TEXT NOT NULL,
            discovery_result_id TEXT NULL,
            replace_agent_id TEXT NULL REFERENCES agents(agent_id),
            requested_display_name TEXT NULL, ssh_user TEXT NULL, ssh_host TEXT NULL,
            ssh_port INTEGER NULL, enrollment_method TEXT NOT NULL,
            remote_instance_id TEXT NULL, remote_credential_id TEXT NULL,
            credential_temp_ref TEXT NULL, old_credential_ref TEXT NULL,
            old_remote_credential_id TEXT NULL, save_requested INTEGER NOT NULL,
            expires_at TEXT NOT NULL, last_error_code TEXT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_agent_status_updated ON agent_status(updated_at);
        CREATE INDEX idx_enrollment_state_expiry ON agent_enrollment_jobs(state, expires_at);
        INSERT INTO agents VALUES (
            'lab-01', '11111111-1111-1111-1111-111111111111', 'Lab 01',
            'https://lab-01.example:8765', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'remote-1', 'system-tls', 'ssh_auto', 1, 'manual', 1,
            '2026-07-12T10:00:00.000000Z', '2026-07-12T10:00:00.000000Z'
        );
        INSERT INTO agent_status VALUES (
            'lab-01', 1, 'ready', 'healthy', NULL, NULL, 'v2', '0.1.0', '[]', '{}',
            NULL, '2026-07-12T10:00:00.000000Z'
        );
        INSERT INTO agent_enrollment_jobs VALUES (
            'enroll-01', '22222222-2222-2222-2222-222222222222', 'completed',
            'https://lab-01.example:8765', 'system-tls', NULL, 'lab-01', 'Lab 01',
            'agent', 'lab-01.example', 22, 'ssh_auto',
            '11111111-1111-1111-1111-111111111111', 'remote-1',
            NULL, NULL, NULL, 1,
            '2026-07-12T10:10:00.000000Z', NULL,
            '2026-07-12T10:00:00.000000Z', '2026-07-12T10:00:00.000000Z'
        );
        """
    )
    connection.commit()


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
                    "recovery_owner", "recovery_lease_until", "recovery_revision",
                    "validated_http_address",
            ),
        }
        for table, columns in expected_columns.items():
            actual = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            )
            assert actual == columns

        enrollment_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_enrollment_jobs'"
        ).fetchone()[0]
        assert "'consumed'" in enrollment_sql
        assert "'completed'" not in enrollment_sql

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
def test_fleet_hardening_forward_migration_preserves_parent_schema_data(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _create_applied_parent_fleet_schema(connection)
    finally:
        connection.close()

    run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert connection.execute(
            "SELECT connection_status, workload_status FROM agent_status"
        ).fetchone() == ("ready", "healthy")
        assert connection.execute(
            "SELECT state, credential_temp_ref FROM agent_enrollment_jobs"
        ).fetchone() == ("consumed", None)
        versions = connection.execute(
            "SELECT version FROM schema_versions ORDER BY version"
        ).fetchall()
        status_fks = connection.execute("PRAGMA foreign_key_list(agent_status)").fetchall()
        journal_fks = connection.execute(
            "PRAGMA foreign_key_list(agent_enrollment_jobs)"
        ).fetchall()
        indexes = {
            row[1]
            for table in ("agent_status", "agent_enrollment_jobs")
            for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE agent_status SET connection_status='online' WHERE agent_id='lab-01'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE agent_enrollment_jobs SET state='completed' "
                "WHERE enrollment_id='enroll-01'"
            )
    finally:
        connection.close()

    assert ("0003_fleet_registry_hardening",) in versions
    assert any(row[2] == "agents" and row[6] == "CASCADE" for row in status_fks)
    assert any(row[2] == "agents" and row[3] == "replace_agent_id" for row in journal_fks)
    assert {"idx_agent_status_updated", "idx_enrollment_state_expiry"} <= indexes


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


@pytest.mark.contract
def test_legacy_manual_migration_preserves_rows_indexes_fks_and_checks(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0003_fleet_registry_hardening.py")
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-01", None, "Legacy", "https://legacy.example:8765", "c" * 48,
                None, "system-tls", "legacy_admin_token", 1, "config_import", 7,
                "2026-07-12T10:00:00.000000Z", "2026-07-12T11:00:00.000000Z",
            ),
        )
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ssh-01", "instance-01", "SSH", "https://ssh.example:8765", "d" * 48,
                "remote-01", "system-tls", "ssh_auto", 0, "manual", 3,
                "2026-07-12T09:00:00.000000Z", "2026-07-12T09:30:00.000000Z",
            ),
        )
        connection.execute(
            "INSERT INTO agent_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ssh-01", 3, "ready", "healthy", None, None, "2", "0.2.0", "[]",
                "{}", None, "2026-07-12T09:30:00.000000Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        rows = connection.execute(
            "SELECT agent_id, instance_id, display_name, normalized_endpoint, credential_ref, "
            "remote_credential_id, transport_profile_id, enrollment_method, enabled, source, "
            "revision, created_at, updated_at FROM agents ORDER BY agent_id"
        ).fetchall()
        assert rows[0] == (
            "legacy-01", None, "Legacy", "https://legacy.example:8765", "c" * 48,
            None, "system-tls", "legacy_admin_token", 1, "config_import", 7,
            "2026-07-12T10:00:00.000000Z", "2026-07-12T11:00:00.000000Z",
        )
        assert rows[1][0:11] == (
            "ssh-01", "instance-01", "SSH", "https://ssh.example:8765", "d" * 48,
            "remote-01", "system-tls", "ssh_auto", 0, "manual", 3,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "idx_agent_status_updated" in {
            item[1] for item in connection.execute("PRAGMA index_list(agent_status)")
        }
        assert "idx_enrollment_state_expiry" in {
            item[1]
            for item in connection.execute("PRAGMA index_list(agent_enrollment_jobs)")
        }
        assert any(
            item[2] == "agents" and item[6] == "CASCADE"
            for item in connection.execute("PRAGMA foreign_key_list(agent_status)")
        )
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        status_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(agent_status)")
        }
        journal_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(agent_enrollment_jobs)")
        }
        assert "idx_agent_status_updated" in status_indexes
        assert "idx_enrollment_state_expiry" in journal_indexes
        assert any(
            row[2] == "agents" and row[6] == "CASCADE"
            for row in connection.execute("PRAGMA foreign_key_list(agent_status)")
        )
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "manual-legacy", None, "Manual", "https://manual.example:8765", "e" * 48,
                None, "system-tls", "legacy_admin_token", 1, "manual", 1,
                "2026-07-12T12:00:00.000000Z", "2026-07-12T12:00:00.000000Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "bad-ssh", None, "Bad", "https://bad.example:8765", "f" * 48,
                    "remote-bad", "system-tls", "ssh_auto", 1, "manual", 1,
                    "2026-07-12T12:00:00.000000Z", "2026-07-12T12:00:00.000000Z",
                ),
            )
    finally:
        connection.close()


@pytest.mark.contract
def test_legacy_manual_migration_rolls_back_ddl_failure(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0003_fleet_registry_hardening.py")
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agents'"
        ).fetchone()
        migration = _load_migration(
            CONTROL_PLANE_MIGRATIONS / "0004_legacy_manual_enrollment.py"
        )
        connection.set_authorizer(
            lambda action, *_args: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_ALTER_TABLE
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            migration.upgrade(connection)
        connection.set_authorizer(None)
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agents'"
        ).fetchone() == before
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='agents_next'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM schema_versions WHERE version='0004_legacy_manual_enrollment'"
        ).fetchone() is None
    finally:
        connection.close()


@pytest.mark.contract
def test_control_plane_future_user_version_fails_closed_without_writes(tmp_path):
    db_path = tmp_path / "control-plane.db"
    run_control_plane_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA user_version=999")
        connection.commit()
        before = connection.execute(
            "SELECT version, result FROM schema_versions ORDER BY version"
        ).fetchall()
    finally:
        connection.close()

    with pytest.raises(MigrationError, match="newer control-plane schema"):
        run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (999,)
        assert connection.execute(
            "SELECT version, result FROM schema_versions ORDER BY version"
        ).fetchall() == before
    finally:
        connection.close()


@pytest.mark.contract
def test_legacy_manual_downgrade_rebuilds_v3_schema_and_preserves_compatible_rows(
    tmp_path,
):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0004_legacy_manual_enrollment.py")
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-01", None, "Legacy", "https://legacy.example:8765", "c" * 48,
                None, "system-tls", "legacy_admin_token", 1, "config_import", 7,
                "2026-07-12T10:00:00.000000Z", "2026-07-12T11:00:00.000000Z",
            ),
        )
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ssh-01", "instance-01", "SSH", "https://ssh.example:8765", "d" * 48,
                "remote-01", "system-tls", "ssh_auto", 0, "manual", 3,
                "2026-07-12T09:00:00.000000Z", "2026-07-12T09:30:00.000000Z",
            ),
        )
        connection.commit()
        migration = _load_migration(
            CONTROL_PLANE_MIGRATIONS / "0004_legacy_manual_enrollment.py"
        )

        migration.downgrade(connection)

        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agents'"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT agent_id, instance_id, display_name, normalized_endpoint, credential_ref, "
            "remote_credential_id, transport_profile_id, enrollment_method, enabled, source, "
            "revision, created_at, updated_at FROM agents ORDER BY agent_id"
        ).fetchall()
        assert "source = 'config_import'" in schema
        assert [row[0] for row in rows] == ["legacy-01", "ssh-01"]
        assert rows[0][1] is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "idx_agent_status_updated" in {
            item[1] for item in connection.execute("PRAGMA index_list(agent_status)")
        }
        assert "idx_enrollment_state_expiry" in {
            item[1]
            for item in connection.execute("PRAGMA index_list(agent_enrollment_jobs)")
        }
        assert any(
            item[2] == "agents" and item[6] == "CASCADE"
            for item in connection.execute("PRAGMA foreign_key_list(agent_status)")
        )
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert connection.execute(
            "SELECT 1 FROM schema_versions WHERE version='0004_legacy_manual_enrollment'"
        ).fetchone() is None
    finally:
        connection.close()


@pytest.mark.contract
def test_legacy_manual_downgrade_rejects_incompatible_rows_without_changes(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0004_legacy_manual_enrollment.py")
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "manual-legacy", None, "Manual", "https://manual.example:8765", "e" * 48,
                None, "system-tls", "legacy_admin_token", 1, "manual", 1,
                "2026-07-12T12:00:00.000000Z", "2026-07-12T12:00:00.000000Z",
            ),
        )
        connection.commit()
        before_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agents'"
        ).fetchone()[0]
        before_versions = connection.execute(
            "SELECT version FROM schema_versions ORDER BY version"
        ).fetchall()
        migration = _load_migration(
            CONTROL_PLANE_MIGRATIONS / "0004_legacy_manual_enrollment.py"
        )

        with pytest.raises(sqlite3.IntegrityError, match="manual legacy"):
            migration.downgrade(connection)

        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agents'"
        ).fetchone()[0] == before_schema
        assert connection.execute(
            "SELECT version FROM schema_versions ORDER BY version"
        ).fetchall() == before_versions
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
    finally:
        connection.close()


def _insert_legacy_enrollment_row(connection, *, reference):
    connection.execute(
        "INSERT INTO agent_enrollment_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "enroll-legacy", "11111111-1111-4111-8111-111111111111", "verifying",
            "https://legacy.example:8765", "system-tls", None, None, None, None, None,
            None, "legacy_admin_token", None, None, reference, None, None, 0,
            "2026-07-12T12:10:00.000000Z", None,
            "2026-07-12T12:00:00.000000Z", "2026-07-12T12:00:00.000000Z",
        ),
    )


@pytest.mark.contract
def test_enrollment_recovery_migration_preserves_legal_rows_and_adds_constraints(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0004_legacy_manual_enrollment.py")
        _insert_legacy_enrollment_row(connection, reference="a" * 48)
        connection.commit()
    finally:
        connection.close()

    run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT state, credential_temp_ref, recovery_owner, recovery_lease_until, "
            "recovery_revision "
            "FROM agent_enrollment_jobs WHERE enrollment_id='enroll-legacy'"
        ).fetchone()
        assert row == ("verifying", "a" * 48, None, None, 0)
        indexes = {
            item[1]
            for item in connection.execute("PRAGMA index_list(agent_enrollment_jobs)")
        }
        assert {"idx_enrollment_state_expiry", "idx_enrollment_recovery_lease"} <= indexes
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE agent_enrollment_jobs SET credential_temp_ref=NULL "
                "WHERE enrollment_id='enroll-legacy'"
            )
    finally:
        connection.close()


@pytest.mark.contract
def test_recovery_fencing_downgrade_is_forward_only_without_mutation(tmp_path):
    db_path = tmp_path / "control-plane.db"
    run_control_plane_migrations(db_path)
    migration = _load_migration(
        CONTROL_PLANE_MIGRATIONS / "0006_enrollment_recovery_fencing.py"
    )
    connection = sqlite3.connect(db_path)
    try:
        before_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_enrollment_jobs'"
        ).fetchone()[0]
        before_versions = connection.execute(
            "SELECT * FROM schema_versions ORDER BY version"
        ).fetchall()
        before_user_version = connection.execute("PRAGMA user_version").fetchone()

        with pytest.raises(sqlite3.NotSupportedError, match="forward-only"):
            migration.downgrade(connection)

        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_enrollment_jobs'"
        ).fetchone()[0] == before_schema
        assert connection.execute(
            "SELECT * FROM schema_versions ORDER BY version"
        ).fetchall() == before_versions
        assert connection.execute("PRAGMA user_version").fetchone() == before_user_version
    finally:
        connection.close()


@pytest.mark.contract
def test_enrollment_recovery_migration_rejects_invalid_old_phase_without_changes(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0004_legacy_manual_enrollment.py")
        _insert_legacy_enrollment_row(connection, reference=None)
        connection.commit()
        before_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_enrollment_jobs'"
        ).fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="phase invariant.*enroll-legacy"):
        run_control_plane_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_enrollment_jobs'"
        ).fetchone()[0] == before_schema
        assert connection.execute(
            "SELECT 1 FROM schema_versions "
            "WHERE version='0005_enrollment_recovery_hardening'"
        ).fetchone() is None
        assert len(connection.execute("PRAGMA table_info(agent_enrollment_jobs)").fetchall()) == 22
    finally:
        connection.close()


@pytest.mark.contract
def test_validated_address_migration_preserves_legal_pending_ssh_row(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0006_enrollment_recovery_fencing.py")
        connection.execute(
            """INSERT INTO agent_enrollment_jobs (
                enrollment_id, manager_id, state, normalized_endpoint,
                transport_profile_id, ssh_user, ssh_host, ssh_port, enrollment_method,
                save_requested, expires_at, created_at, updated_at, recovery_revision
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "pending-pin", "22222222-2222-4222-8222-222222222222", "pending",
                "https://agent.example:8765", "system-tls", "edaops",
                "agent.example", 22, "ssh_auto", 0,
                "2026-07-12T12:10:00.000000Z", "2026-07-12T12:00:00.000000Z",
                "2026-07-12T12:00:00.000000Z", 0,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    run_control_plane_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute(
            "SELECT validated_http_address FROM agent_enrollment_jobs"
        ).fetchone() == (None,)
    finally:
        connection.close()


@pytest.mark.contract
def test_validated_address_migration_rejects_unbound_ssh_credential_atomically(tmp_path):
    db_path = tmp_path / "control-plane.db"
    connection = sqlite3.connect(db_path)
    try:
        _run_control_plane_through(connection, "0006_enrollment_recovery_fencing.py")
        connection.execute(
            """INSERT INTO agent_enrollment_jobs (
                enrollment_id, manager_id, state, normalized_endpoint,
                transport_profile_id, ssh_user, ssh_host, ssh_port, enrollment_method,
                remote_instance_id, credential_temp_ref, save_requested,
                expires_at, created_at, updated_at, recovery_revision
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "issued-no-pin", "22222222-2222-4222-8222-222222222222",
                "credential_issued", "https://agent.example:8765", "system-tls",
                "edaops", "agent.example", 22, "ssh_auto", "instance-1", "a" * 48,
                0, "2026-07-12T12:10:00.000000Z", "2026-07-12T12:00:00.000000Z",
                "2026-07-12T12:00:00.000000Z", 0,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="validated address"):
        run_control_plane_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert "validated_http_address" not in {
            row[1] for row in connection.execute("PRAGMA table_info(agent_enrollment_jobs)")
        }
    finally:
        connection.close()


@pytest.mark.contract
def test_validated_address_downgrade_is_forward_only_without_mutation(tmp_path):
    db_path = tmp_path / "control-plane.db"
    run_control_plane_migrations(db_path)
    migration = _load_migration(
        CONTROL_PLANE_MIGRATIONS / "0007_enrollment_validated_http_address.py"
    )
    connection = sqlite3.connect(db_path)
    before = connection.execute("SELECT * FROM schema_versions ORDER BY version").fetchall()
    try:
        with pytest.raises(sqlite3.NotSupportedError):
            migration.downgrade(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute(
            "SELECT * FROM schema_versions ORDER BY version"
        ).fetchall() == before
    finally:
        connection.close()

import sqlite3

VERSION = "0003_fleet_registry_hardening"

_STATUS_COLUMNS = (
    "agent_id, target_revision, connection_status, workload_status, observed_at, stale_after, "
    "api_version, agent_version, capabilities_json, summary_json, last_error_code, updated_at"
)
_JOURNAL_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at"
)


def _create_status(connection: sqlite3.Connection, table: str, *, hardened: bool) -> None:
    connection_check = (
        "CHECK (connection_status IN "
        "('disabled', 'unknown', 'ready', 'degraded', 'unavailable'))"
        if hardened
        else ""
    )
    workload_check = (
        "CHECK (workload_status IN ('unknown', 'healthy', 'warning', 'critical', 'stale'))"
        if hardened
        else ""
    )
    connection.execute(
        f"""
        CREATE TABLE {table} (
            agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
            target_revision INTEGER NOT NULL CHECK (target_revision >= 1),
            connection_status TEXT NOT NULL {connection_check},
            workload_status TEXT NOT NULL {workload_check},
            observed_at TEXT NULL,
            stale_after TEXT NULL,
            api_version TEXT NULL,
            agent_version TEXT NULL,
            capabilities_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            last_error_code TEXT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _create_journal(connection: sqlite3.Connection, table: str, *, hardened: bool) -> None:
    terminal = "consumed" if hardened else "completed"
    connection.execute(
        f"""
        CREATE TABLE {table} (
            enrollment_id TEXT PRIMARY KEY,
            manager_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'pending', 'running', 'awaiting_cli', 'credential_issued', 'verifying',
                'verified', 'activation_requested', 'activated', '{terminal}', 'cancelled',
                'failed', 'expired'
            )),
            normalized_endpoint TEXT NOT NULL,
            transport_profile_id TEXT NOT NULL,
            discovery_result_id TEXT NULL,
            replace_agent_id TEXT NULL REFERENCES agents(agent_id),
            requested_display_name TEXT NULL,
            ssh_user TEXT NULL,
            ssh_host TEXT NULL,
            ssh_port INTEGER NULL CHECK (ssh_port IS NULL OR ssh_port BETWEEN 1 AND 65535),
            enrollment_method TEXT NOT NULL CHECK (
                enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                      'legacy_admin_token')
            ),
            remote_instance_id TEXT NULL,
            remote_credential_id TEXT NULL,
            credential_temp_ref TEXT NULL,
            old_credential_ref TEXT NULL,
            old_remote_credential_id TEXT NULL,
            save_requested INTEGER NOT NULL CHECK (save_requested IN (0, 1)),
            expires_at TEXT NOT NULL,
            last_error_code TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (discovery_result_id IS NULL OR replace_agent_id IS NULL),
            CHECK (save_requested = 0 OR requested_display_name IS NOT NULL),
            CHECK (
                (enrollment_method = 'legacy_admin_token' AND ssh_user IS NULL
                    AND ssh_host IS NULL AND ssh_port IS NULL)
                OR
                (enrollment_method != 'legacy_admin_token' AND ssh_user IS NOT NULL
                    AND ssh_host IS NOT NULL AND ssh_port IS NOT NULL)
            )
        )
        """
    )


def _rebuild(connection: sqlite3.Connection, *, hardened: bool) -> None:
    _create_status(connection, "agent_status_next", hardened=hardened)
    _create_journal(connection, "agent_enrollment_jobs_next", hardened=hardened)
    connection.execute(
        f"INSERT INTO agent_status_next ({_STATUS_COLUMNS}) "
        f"SELECT {_STATUS_COLUMNS} FROM agent_status"
    )
    state = (
        "CASE state WHEN 'completed' THEN 'consumed' ELSE state END"
        if hardened
        else "CASE state WHEN 'consumed' THEN 'completed' ELSE state END"
    )
    journal_select = _JOURNAL_COLUMNS.replace("state", f"{state} AS state", 1)
    connection.execute(
        f"INSERT INTO agent_enrollment_jobs_next ({_JOURNAL_COLUMNS}) "
        f"SELECT {journal_select} FROM agent_enrollment_jobs"
    )
    connection.execute("DROP TABLE agent_status")
    connection.execute("DROP TABLE agent_enrollment_jobs")
    connection.execute("ALTER TABLE agent_status_next RENAME TO agent_status")
    connection.execute(
        "ALTER TABLE agent_enrollment_jobs_next RENAME TO agent_enrollment_jobs"
    )
    connection.execute("CREATE INDEX idx_agent_status_updated ON agent_status(updated_at)")
    connection.execute(
        "CREATE INDEX idx_enrollment_state_expiry "
        "ON agent_enrollment_jobs(state, expires_at)"
    )


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _rebuild(connection, hardened=True)
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'harden manager fleet constraints', 'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


def downgrade(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _rebuild(connection, hardened=False)
        connection.execute("DELETE FROM schema_versions WHERE version=?", (VERSION,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")

import sqlite3

VERSION = "0005_enrollment_recovery_hardening"

_OLD_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at"
)


def _create_journal(connection: sqlite3.Connection, table: str) -> None:
    connection.execute(
        f"""
        CREATE TABLE {table} (
            enrollment_id TEXT PRIMARY KEY,
            manager_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'pending', 'running', 'awaiting_cli', 'credential_issued', 'verifying',
                'verified', 'activation_requested', 'activated', 'consumed', 'cancelled',
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
            recovery_owner TEXT NULL,
            recovery_lease_until TEXT NULL,
            CHECK (discovery_result_id IS NULL OR replace_agent_id IS NULL),
            CHECK (save_requested = 0 OR requested_display_name IS NOT NULL),
            CHECK (
                (enrollment_method = 'legacy_admin_token' AND ssh_user IS NULL
                    AND ssh_host IS NULL AND ssh_port IS NULL)
                OR
                (enrollment_method != 'legacy_admin_token' AND ssh_user IS NOT NULL
                    AND ssh_host IS NOT NULL AND ssh_port IS NOT NULL)
            ),
            CHECK (state NOT IN ('credential_issued', 'verifying', 'verified',
                                'activation_requested', 'activated')
                   OR credential_temp_ref IS NOT NULL),
            CHECK (state NOT IN ('activation_requested', 'activated') OR
                   (save_requested = 1 AND requested_display_name IS NOT NULL)),
            CHECK (state NOT IN ('activation_requested', 'activated') OR
                   enrollment_method = 'legacy_admin_token' OR
                   (remote_instance_id IS NOT NULL AND remote_credential_id IS NOT NULL)),
            CHECK ((recovery_owner IS NULL) = (recovery_lease_until IS NULL))
        )
        """
    )


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    invalid = connection.execute(
        """
        SELECT enrollment_id, state FROM agent_enrollment_jobs
        WHERE (state IN ('credential_issued', 'verifying', 'verified',
                         'activation_requested', 'activated')
               AND credential_temp_ref IS NULL)
           OR (state IN ('activation_requested', 'activated')
               AND (save_requested != 1 OR requested_display_name IS NULL))
           OR (state IN ('activation_requested', 'activated')
               AND enrollment_method != 'legacy_admin_token'
               AND (remote_instance_id IS NULL OR remote_credential_id IS NULL))
        ORDER BY enrollment_id LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise sqlite3.IntegrityError(
            f"enrollment phase invariant violation: {invalid[0]} ({invalid[1]})"
        )
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _create_journal(connection, "agent_enrollment_jobs_next")
        connection.execute(
            f"INSERT INTO agent_enrollment_jobs_next ({_OLD_COLUMNS}) "
            f"SELECT {_OLD_COLUMNS} FROM agent_enrollment_jobs"
        )
        connection.execute("DROP TABLE agent_enrollment_jobs")
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs_next RENAME TO agent_enrollment_jobs"
        )
        connection.execute(
            "CREATE INDEX idx_enrollment_state_expiry "
            "ON agent_enrollment_jobs(state, expires_at)"
        )
        connection.execute(
            "CREATE INDEX idx_enrollment_recovery_lease "
            "ON agent_enrollment_jobs(recovery_lease_until, state)"
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'harden enrollment phases and recovery claims',
                    'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=5")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={foreign_keys}")


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("enrollment recovery hardening is forward-only")

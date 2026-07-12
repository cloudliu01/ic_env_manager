import sqlite3

VERSION = "0002_fleet_registry"


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS manager_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            instance_id TEXT NULL UNIQUE,
            display_name TEXT NOT NULL,
            normalized_endpoint TEXT NOT NULL UNIQUE,
            credential_ref TEXT NOT NULL,
            remote_credential_id TEXT NULL,
            transport_profile_id TEXT NOT NULL,
            enrollment_method TEXT NOT NULL CHECK (
                enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                      'legacy_admin_token')
            ),
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            source TEXT NOT NULL CHECK (source IN ('config_import', 'manual', 'discovery')),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (instance_id IS NOT NULL OR (
                source = 'config_import' AND enrollment_method = 'legacy_admin_token'
            )),
            CHECK (remote_credential_id IS NOT NULL OR enrollment_method = 'legacy_admin_token')
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_status (
            agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
            target_revision INTEGER NOT NULL CHECK (target_revision >= 1),
            connection_status TEXT NOT NULL,
            workload_status TEXT NOT NULL,
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_enrollment_jobs (
            enrollment_id TEXT PRIMARY KEY,
            manager_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'pending', 'running', 'awaiting_cli', 'credential_issued', 'verifying',
                'verified', 'activation_requested', 'activated', 'completed', 'cancelled',
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
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_status_updated ON agent_status(updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrollment_state_expiry "
        "ON agent_enrollment_jobs(state, expires_at)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), 'manager fleet registry schema', 'upgrade', 'success')
        """,
        (VERSION,),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX IF EXISTS idx_enrollment_state_expiry")
    connection.execute("DROP INDEX IF EXISTS idx_agent_status_updated")
    connection.execute("DROP TABLE IF EXISTS agent_enrollment_jobs")
    connection.execute("DROP TABLE IF EXISTS agent_status")
    connection.execute("DROP TABLE IF EXISTS agents")
    connection.execute("DROP TABLE IF EXISTS manager_metadata")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

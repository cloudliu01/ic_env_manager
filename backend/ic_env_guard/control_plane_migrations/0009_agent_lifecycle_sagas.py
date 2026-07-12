import sqlite3

VERSION = "0009_agent_lifecycle_sagas"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        for definition in (
            "old_normalized_endpoint TEXT NULL",
            "old_transport_profile_id TEXT NULL",
            "old_instance_id TEXT NULL",
            "old_registry_revision INTEGER NULL CHECK (old_registry_revision >= 1)",
            "old_enrollment_method TEXT NULL",
            "old_source TEXT NULL",
            "old_enabled INTEGER NULL CHECK (old_enabled IN (0, 1))",
            "old_display_name TEXT NULL",
        ):
            connection.execute(
                f"ALTER TABLE agent_enrollment_jobs ADD COLUMN {definition}"
            )
        connection.execute(
            """
            CREATE TABLE agent_removal_jobs (
                removal_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                captured_revision INTEGER NOT NULL CHECK (captured_revision >= 1),
                credential_ref TEXT NOT NULL,
                remote_credential_id TEXT NULL,
                normalized_endpoint TEXT NOT NULL,
                transport_profile_id TEXT NOT NULL,
                enrollment_method TEXT NOT NULL CHECK (
                    enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                          'legacy_admin_token')
                ),
                phase TEXT NOT NULL CHECK (phase IN (
                    'pending', 'revoking', 'revoked', 'registry_deleted',
                    'credential_deleted', 'completed', 'residual'
                )),
                local_only INTEGER NOT NULL CHECK (local_only IN (0, 1)),
                audit_event_id INTEGER NOT NULL,
                last_error_code TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_agent_removal_active ON agent_removal_jobs(agent_id) "
            "WHERE phase != 'completed'"
        )
        connection.execute(
            "CREATE INDEX idx_agent_removal_phase ON agent_removal_jobs(phase, updated_at)"
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'durable Agent lifecycle sagas', 'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=9")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("Agent lifecycle sagas are forward-only")

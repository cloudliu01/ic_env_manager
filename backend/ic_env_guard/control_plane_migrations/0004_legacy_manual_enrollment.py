import sqlite3

VERSION = "0004_legacy_manual_enrollment"

_COLUMNS = (
    "agent_id, instance_id, display_name, normalized_endpoint, credential_ref, "
    "remote_credential_id, transport_profile_id, enrollment_method, enabled, source, "
    "revision, created_at, updated_at"
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
        connection.execute(
            """
            CREATE TABLE agents_next (
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
                CHECK (instance_id IS NOT NULL OR enrollment_method = 'legacy_admin_token'),
                CHECK (remote_credential_id IS NOT NULL OR enrollment_method = 'legacy_admin_token')
            )
            """
        )
        connection.execute(
            f"INSERT INTO agents_next ({_COLUMNS}) SELECT {_COLUMNS} FROM agents"
        )
        connection.execute("DROP TABLE agents")
        connection.execute("ALTER TABLE agents_next RENAME TO agents")
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'allow manual legacy enrollment identity degradation',
                    'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=4")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


def downgrade(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT 1 FROM agents WHERE instance_id IS NULL AND source != 'config_import' LIMIT 1"
    ).fetchone()
    if rows is not None:
        raise sqlite3.IntegrityError("manual legacy agents prevent downgrade")
    connection.execute("DELETE FROM schema_versions WHERE version=?", (VERSION,))
    connection.execute("PRAGMA user_version=3")
    connection.commit()

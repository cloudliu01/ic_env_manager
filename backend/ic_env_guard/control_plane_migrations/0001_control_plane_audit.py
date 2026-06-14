import sqlite3

VERSION = "0001_control_plane_audit"


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL,
            direction TEXT NOT NULL,
            result TEXT NOT NULL,
            failure_reason TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS control_plane_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            actor_id TEXT,
            source_addr TEXT,
            agent_id TEXT,
            operation TEXT NOT NULL,
            target TEXT NOT NULL,
            result TEXT NOT NULL,
            dispatch_state TEXT NOT NULL,
            upstream_status INTEGER,
            correlation_id TEXT,
            failure_category TEXT,
            failure_reason TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_control_plane_audit_agent "
        "ON control_plane_audit_events(agent_id, timestamp)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_control_plane_audit_correlation "
        "ON control_plane_audit_events(correlation_id)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), 'control-plane gateway audit schema', 'upgrade', 'success')
        """,
        (VERSION,),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX IF EXISTS idx_control_plane_audit_correlation")
    connection.execute("DROP INDEX IF EXISTS idx_control_plane_audit_agent")
    connection.execute("DROP TABLE IF EXISTS control_plane_audit_events")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

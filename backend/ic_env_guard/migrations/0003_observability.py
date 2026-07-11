import sqlite3

VERSION = "0003_observability"


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            identity_key TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            numeric_value REAL,
            unit TEXT,
            status TEXT NOT NULL,
            message TEXT,
            labels_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_namespace_name "
        "ON observations(namespace, name)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_status_expiry "
        "ON observations(status, expires_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_expires_at ON observations(expires_at)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS log_sources (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_log_sources_expires_at ON log_sources(expires_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_log_sources_last_updated ON log_sources(last_updated)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), 'observability latest-value storage', 'upgrade', 'success')
        """,
        (VERSION,),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS log_sources")
    connection.execute("DROP TABLE IF EXISTS observations")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

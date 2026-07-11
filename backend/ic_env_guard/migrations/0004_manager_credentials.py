import sqlite3

VERSION = "0004_manager_credentials"


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS manager_credentials (
            credential_id TEXT PRIMARY KEY,
            manager_id TEXT NOT NULL,
            enrollment_id TEXT NOT NULL UNIQUE,
            token_hash TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            pending_expires_at TEXT,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            last_used_at TEXT,
            revoked_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), 'manager-specific Agent credentials', 'upgrade', 'success')
        """,
        (VERSION,),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS manager_credentials")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

import sqlite3

VERSION = "0006_enrollment_recovery_fencing"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN recovery_revision "
            "INTEGER NOT NULL DEFAULT 0 CHECK (recovery_revision >= 0)"
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'add fenced enrollment recovery revisions',
                    'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=6")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("enrollment recovery fencing is forward-only")

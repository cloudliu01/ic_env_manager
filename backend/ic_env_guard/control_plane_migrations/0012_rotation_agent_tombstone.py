import sqlite3

VERSION = "0012_rotation_agent_tombstone"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs "
            "ADD COLUMN replace_agent_tombstone TEXT NULL"
        )
        connection.execute(
            "INSERT INTO schema_versions(version,applied_at,description,direction,result) "
            "VALUES (?,datetime('now'),'preserve removed rotation Agent identity',"
            "'upgrade','success')",
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=12")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("rotation Agent tombstones are forward-only")

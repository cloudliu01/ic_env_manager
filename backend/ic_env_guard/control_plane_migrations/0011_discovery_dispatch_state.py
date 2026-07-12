import sqlite3

VERSION = "0011_discovery_dispatch_state"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE discovery_jobs ADD COLUMN aggregate_dispatch_state TEXT "
            "NOT NULL DEFAULT 'not_dispatched' CHECK (aggregate_dispatch_state IN "
            "('not_dispatched','unknown','dispatched'))"
        )
        connection.execute(
            "UPDATE discovery_jobs SET aggregate_dispatch_state='unknown' "
            "WHERE checked_targets>0"
        )
        connection.execute(
            "INSERT INTO schema_versions(version,applied_at,description,direction,result) "
            "VALUES (?,datetime('now'),'durable discovery dispatch state','upgrade','success')",
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=11")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("Discovery dispatch schema is forward-only")

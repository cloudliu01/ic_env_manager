import sqlite3

VERSION = "0002_state_audit_indexes"


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events(timestamp)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_target ON audit_events(target_type, target_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_events_service_created "
        "ON service_events(service_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_healthcheck_service_created "
        "ON healthcheck_results(service_id, created_at)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), 'state and audit retention indexes', 'upgrade', 'success')
        """,
        (VERSION,),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    for name in [
        "idx_healthcheck_service_created",
        "idx_service_events_service_created",
        "idx_audit_events_target",
        "idx_audit_events_timestamp",
    ]:
        connection.execute(f"DROP INDEX IF EXISTS {name}")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

import sqlite3

VERSION = "0001_initial"

TABLE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_versions (
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL,
        direction TEXT NOT NULL,
        result TEXT NOT NULL,
        failure_reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_administrators (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        credential_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_authenticated_at TEXT,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_lifecycle_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS configuration_load_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        config_path TEXT NOT NULL,
        config_hash TEXT,
        result TEXT NOT NULL,
        failure_reason TEXT,
        loaded_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        actor_id TEXT,
        source_addr TEXT,
        operation TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT,
        result TEXT NOT NULL,
        failure_reason TEXT,
        correlation_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS terminal_sessions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        command TEXT NOT NULL,
        cwd TEXT,
        pid INTEGER,
        rows INTEGER,
        cols INTEGER,
        status TEXT NOT NULL,
        output_cursor INTEGER NOT NULL,
        replay_buffer_start_cursor INTEGER NOT NULL,
        idle_timeout_minutes INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        last_active_at TEXT NOT NULL,
        last_connected_at TEXT,
        exited_at TEXT,
        closed_at TEXT,
        close_reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS managed_services (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        command TEXT,
        systemd_unit TEXT,
        cwd TEXT,
        env_keys TEXT NOT NULL,
        allowed_operations TEXT NOT NULL,
        autostart INTEGER NOT NULL,
        restart_policy TEXT NOT NULL,
        start_timeout_seconds INTEGER NOT NULL,
        stop_timeout_seconds INTEGER NOT NULL,
        healthcheck_json TEXT,
        log_rules_json TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS service_state (
        service_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        pid INTEGER,
        started_at TEXT,
        stopped_at TEXT,
        exit_code INTEGER,
        restart_count INTEGER NOT NULL,
        health_status TEXT NOT NULL,
        health_latency_ms INTEGER,
        last_error TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(service_id) REFERENCES managed_services(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS service_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id TEXT NOT NULL,
        pid INTEGER,
        started_at TEXT NOT NULL,
        stopped_at TEXT,
        exit_code INTEGER,
        stop_reason TEXT,
        FOREIGN KEY(service_id) REFERENCES managed_services(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS service_operations (
        id TEXT PRIMARY KEY,
        service_id TEXT NOT NULL,
        actor_id TEXT,
        operation TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        result TEXT NOT NULL,
        failure_reason TEXT,
        source_addr TEXT,
        FOREIGN KEY(service_id) REFERENCES managed_services(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS service_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        message TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(service_id) REFERENCES managed_services(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS healthcheck_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id TEXT NOT NULL,
        success INTEGER NOT NULL,
        latency_ms INTEGER,
        status_code INTEGER,
        error TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(service_id) REFERENCES managed_services(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metrics_exposure (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        enabled INTEGER NOT NULL,
        local_only INTEGER NOT NULL,
        network_allowlist_json TEXT NOT NULL,
        collect_interval_seconds INTEGER NOT NULL,
        last_collection_at TEXT,
        last_collection_status TEXT
    )
    """,
]


def _ensure_schema_versions_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schema_versions)").fetchall()
    }
    additions = {
        "applied_at": "TEXT",
        "description": "TEXT",
        "direction": "TEXT",
        "result": "TEXT",
        "failure_reason": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE schema_versions ADD COLUMN {column} {column_type}")


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in TABLE_STATEMENTS:
        connection.execute(statement)
    _ensure_schema_versions_columns(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_versions(version, applied_at, description, direction, result)
        VALUES (?, datetime('now'), ?, 'upgrade', 'success')
        """,
        (VERSION, "initial local state, audit, terminal, service, metrics schema"),
    )
    connection.commit()


def downgrade(connection: sqlite3.Connection) -> None:
    for table in reversed(
        [
            "metrics_exposure",
            "healthcheck_results",
            "service_events",
            "service_operations",
            "service_runs",
            "service_state",
            "managed_services",
            "terminal_sessions",
            "audit_events",
            "configuration_load_events",
            "agent_lifecycle_events",
            "local_administrators",
        ]
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_versions WHERE version = ?", (VERSION,))
    connection.commit()

import sqlite3

VERSION = "0010_discovery"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE discovery_jobs (
                job_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('queued', 'running', 'completed', 'cancelled', 'failed')
                ),
                total_targets INTEGER NOT NULL CHECK (total_targets BETWEEN 0 AND 2048),
                checked_targets INTEGER NOT NULL CHECK (
                    checked_targets BETWEEN 0 AND total_targets
                ),
                found_targets INTEGER NOT NULL CHECK (
                    found_targets BETWEEN 0 AND checked_targets
                ),
                cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
                safe_error_code TEXT NULL,
                start_audit_event_id INTEGER NOT NULL UNIQUE
                    REFERENCES control_plane_audit_events(id),
                deadline_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_discovery_job_state "
            "ON discovery_jobs(state, created_at)"
        )
        connection.execute(
            """
            CREATE TABLE discovery_results (
                result_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES discovery_jobs(job_id) ON DELETE CASCADE,
                canonical_url TEXT NOT NULL,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
                transport_profile_id TEXT NOT NULL,
                fingerprint_version TEXT NULL,
                found INTEGER NOT NULL CHECK (found IN (0, 1)),
                safe_error_code TEXT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                linked_enrollment_id TEXT NULL UNIQUE
                    REFERENCES agent_enrollment_jobs(enrollment_id),
                UNIQUE(job_id, ip, port, transport_profile_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_discovery_result_target "
            "ON discovery_results(ip, port, transport_profile_id)"
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'bounded Agent discovery jobs', 'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=10")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("Discovery schema is forward-only")

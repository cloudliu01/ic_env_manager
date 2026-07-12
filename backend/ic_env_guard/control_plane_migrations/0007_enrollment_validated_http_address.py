import sqlite3

VERSION = "0007_enrollment_validated_http_address"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    invalid = connection.execute(
        """
        SELECT enrollment_id FROM agent_enrollment_jobs
        WHERE enrollment_method != 'legacy_admin_token'
          AND (state IN ('credential_issued', 'verifying', 'verified',
                         'activation_requested', 'activated')
               OR (state IN ('consumed', 'cancelled', 'failed', 'expired')
                   AND credential_temp_ref IS NOT NULL))
        ORDER BY enrollment_id LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise sqlite3.IntegrityError(
            f"enrollment validated address is unavailable: {invalid[0]}"
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            ALTER TABLE agent_enrollment_jobs ADD COLUMN validated_http_address TEXT NULL
            CHECK (
                (validated_http_address IS NULL)
                OR (length(validated_http_address) BETWEEN 2 AND 45
                    AND validated_http_address = lower(validated_http_address)
                    AND validated_http_address NOT GLOB '*[^0-9a-f:.]*'
                    AND instr(validated_http_address, '%') = 0)
            )
            CHECK (
                (enrollment_method = 'legacy_admin_token'
                    AND validated_http_address IS NULL)
                OR
                (enrollment_method != 'legacy_admin_token' AND (
                    (state IN ('pending', 'running', 'awaiting_cli')
                        AND validated_http_address IS NULL)
                    OR
                    (state IN ('credential_issued', 'verifying', 'verified',
                               'activation_requested', 'activated')
                        AND validated_http_address IS NOT NULL)
                    OR
                    (state IN ('consumed', 'cancelled', 'failed', 'expired') AND (
                        (credential_temp_ref IS NULL AND validated_http_address IS NULL)
                        OR
                        (credential_temp_ref IS NOT NULL
                            AND validated_http_address IS NOT NULL)
                    ))
                ))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at, description, direction, result)
            VALUES (?, datetime('now'), 'persist validated enrollment HTTP address',
                    'upgrade', 'success')
            """,
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=7")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("validated enrollment addresses are forward-only")

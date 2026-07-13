import sqlite3

VERSION = "0013_local_socket_bootstrap"

_AGENT_COLUMNS = (
    "agent_id, instance_id, display_name, normalized_endpoint, credential_ref, "
    "remote_credential_id, transport_profile_id, enrollment_method, enabled, source, "
    "revision, created_at, updated_at"
)
_ENROLLMENT_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at, recovery_owner, "
    "recovery_lease_until, recovery_revision, validated_http_address, cli_resume_nonce, "
    "cli_peer_uid, cli_input_fingerprint, cli_pinned_address, cli_accept_receipt, "
    "old_normalized_endpoint, old_transport_profile_id, old_instance_id, "
    "old_registry_revision, old_enrollment_method, old_source, old_enabled, "
    "old_display_name, replace_agent_tombstone"
)
_REMOVAL_COLUMNS = (
    "removal_id, agent_id, captured_revision, credential_ref, remote_credential_id, "
    "normalized_endpoint, transport_profile_id, enrollment_method, phase, local_only, "
    "audit_event_id, last_error_code, created_at, updated_at"
)


def _create_agents(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE agents_next (
            agent_id TEXT PRIMARY KEY,
            instance_id TEXT NULL UNIQUE,
            display_name TEXT NOT NULL,
            normalized_endpoint TEXT NOT NULL UNIQUE,
            credential_ref TEXT NOT NULL,
            remote_credential_id TEXT NULL,
            transport_profile_id TEXT NOT NULL,
            enrollment_method TEXT NOT NULL CHECK (
                enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                      'local_socket', 'legacy_admin_token')
            ),
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            source TEXT NOT NULL CHECK (
                source IN ('config_import', 'manual', 'discovery',
                           'local_dev_bootstrap')
            ),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (instance_id IS NOT NULL OR enrollment_method = 'legacy_admin_token'),
            CHECK (
                remote_credential_id IS NOT NULL
                OR enrollment_method = 'legacy_admin_token'
            )
        )
        """
    )


def _create_enrollment_jobs(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE agent_enrollment_jobs_next (
            enrollment_id TEXT PRIMARY KEY,
            manager_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'pending', 'running', 'awaiting_cli', 'credential_issued', 'verifying',
                'verified', 'activation_requested', 'activated', 'consumed', 'cancelled',
                'failed', 'expired'
            )),
            normalized_endpoint TEXT NOT NULL,
            transport_profile_id TEXT NOT NULL,
            discovery_result_id TEXT NULL,
            replace_agent_id TEXT NULL REFERENCES agents(agent_id),
            requested_display_name TEXT NULL,
            ssh_user TEXT NULL,
            ssh_host TEXT NULL,
            ssh_port INTEGER NULL CHECK (ssh_port IS NULL OR ssh_port BETWEEN 1 AND 65535),
            enrollment_method TEXT NOT NULL CHECK (
                enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                      'local_socket', 'legacy_admin_token')
            ),
            remote_instance_id TEXT NULL,
            remote_credential_id TEXT NULL,
            credential_temp_ref TEXT NULL,
            old_credential_ref TEXT NULL,
            old_remote_credential_id TEXT NULL,
            save_requested INTEGER NOT NULL CHECK (save_requested IN (0, 1)),
            expires_at TEXT NOT NULL,
            last_error_code TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            recovery_owner TEXT NULL,
            recovery_lease_until TEXT NULL,
            recovery_revision INTEGER NOT NULL DEFAULT 0 CHECK (recovery_revision >= 0),
            validated_http_address TEXT NULL CHECK (
                validated_http_address IS NULL
                OR (
                    length(validated_http_address) BETWEEN 2 AND 45
                    AND validated_http_address = lower(validated_http_address)
                    AND validated_http_address NOT GLOB '*[^0-9a-f:.]*'
                    AND instr(validated_http_address, '%') = 0
                )
            ) CHECK (
                (
                    enrollment_method = 'legacy_admin_token'
                    AND validated_http_address IS NULL
                )
                OR (
                    enrollment_method != 'legacy_admin_token'
                    AND (
                        (
                            state IN ('pending', 'running', 'awaiting_cli')
                            AND validated_http_address IS NULL
                        )
                        OR (
                            state IN (
                                'credential_issued', 'verifying', 'verified',
                                'activation_requested', 'activated'
                            )
                            AND validated_http_address IS NOT NULL
                        )
                        OR (
                            state IN ('consumed', 'cancelled', 'failed', 'expired')
                            AND (
                                (
                                    credential_temp_ref IS NULL
                                    AND validated_http_address IS NULL
                                )
                                OR (
                                    credential_temp_ref IS NOT NULL
                                    AND validated_http_address IS NOT NULL
                                )
                            )
                        )
                    )
                )
            ),
            cli_resume_nonce TEXT NULL CHECK (
                cli_resume_nonce IS NULL
                OR (
                    length(cli_resume_nonce) = 36
                    AND cli_resume_nonce = lower(cli_resume_nonce)
                    AND cli_resume_nonce NOT GLOB '*[^0-9a-f-]*'
                )
            ),
            cli_peer_uid INTEGER NULL CHECK (cli_peer_uid IS NULL OR cli_peer_uid >= 0),
            cli_input_fingerprint TEXT NULL CHECK (
                cli_input_fingerprint IS NULL
                OR (
                    length(cli_input_fingerprint) = 64
                    AND cli_input_fingerprint = lower(cli_input_fingerprint)
                    AND cli_input_fingerprint NOT GLOB '*[^0-9a-f]*'
                )
            ),
            cli_pinned_address TEXT NULL CHECK (
                cli_pinned_address IS NULL
                OR (
                    length(cli_pinned_address) BETWEEN 2 AND 45
                    AND cli_pinned_address = lower(cli_pinned_address)
                    AND cli_pinned_address NOT GLOB '*[^0-9a-f:.]*'
                    AND instr(cli_pinned_address, '%') = 0
                )
            ) CHECK (
                (
                    cli_resume_nonce IS NULL
                    AND cli_peer_uid IS NULL
                    AND cli_input_fingerprint IS NULL
                    AND cli_pinned_address IS NULL
                )
                OR (
                    cli_resume_nonce IS NOT NULL
                    AND cli_peer_uid IS NOT NULL
                    AND cli_input_fingerprint IS NOT NULL
                    AND cli_pinned_address IS NOT NULL
                    AND enrollment_method = 'ssh_cli'
                    AND state IN (
                        'running', 'credential_issued', 'verifying', 'verified',
                        'activation_requested', 'activated'
                    )
                )
            ),
            cli_accept_receipt TEXT NULL CHECK (
                cli_accept_receipt IS NULL
                OR (
                    length(cli_accept_receipt) = 64
                    AND cli_accept_receipt = lower(cli_accept_receipt)
                    AND cli_accept_receipt NOT GLOB '*[^0-9a-f]*'
                )
            ) CHECK (
                cli_accept_receipt IS NULL
                OR (state = 'consumed' AND enrollment_method = 'ssh_cli')
            ) CHECK (
                cli_accept_receipt IS NULL OR cli_resume_nonce IS NULL
            ) CHECK (
                NOT (
                    state = 'running'
                    AND enrollment_method = 'ssh_cli'
                    AND credential_temp_ref IS NULL
                )
                OR cli_resume_nonce IS NOT NULL
            ),
            old_normalized_endpoint TEXT NULL,
            old_transport_profile_id TEXT NULL,
            old_instance_id TEXT NULL,
            old_registry_revision INTEGER NULL CHECK (old_registry_revision >= 1),
            old_enrollment_method TEXT NULL,
            old_source TEXT NULL,
            old_enabled INTEGER NULL CHECK (old_enabled IN (0, 1)),
            old_display_name TEXT NULL,
            replace_agent_tombstone TEXT NULL,
            CHECK (discovery_result_id IS NULL OR replace_agent_id IS NULL),
            CHECK (save_requested = 0 OR requested_display_name IS NOT NULL),
            CHECK (
                (
                    enrollment_method IN ('legacy_admin_token', 'local_socket')
                    AND ssh_user IS NULL
                    AND ssh_host IS NULL
                    AND ssh_port IS NULL
                )
                OR (
                    enrollment_method NOT IN ('legacy_admin_token', 'local_socket')
                    AND ssh_user IS NOT NULL
                    AND ssh_host IS NOT NULL
                    AND ssh_port IS NOT NULL
                )
            ),
            CHECK (
                state NOT IN (
                    'credential_issued', 'verifying', 'verified',
                    'activation_requested', 'activated'
                )
                OR credential_temp_ref IS NOT NULL
            ),
            CHECK (
                state NOT IN ('activation_requested', 'activated')
                OR (save_requested = 1 AND requested_display_name IS NOT NULL)
            ),
            CHECK (
                state NOT IN ('activation_requested', 'activated')
                OR enrollment_method = 'legacy_admin_token'
                OR (
                    remote_instance_id IS NOT NULL
                    AND remote_credential_id IS NOT NULL
                )
            ),
            CHECK ((recovery_owner IS NULL) = (recovery_lease_until IS NULL))
        )
        """
    )


def _create_removal_jobs(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE agent_removal_jobs_next (
            removal_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            captured_revision INTEGER NOT NULL CHECK (captured_revision >= 1),
            credential_ref TEXT NOT NULL,
            remote_credential_id TEXT NULL,
            normalized_endpoint TEXT NOT NULL,
            transport_profile_id TEXT NOT NULL,
            enrollment_method TEXT NOT NULL CHECK (
                enrollment_method IN ('ssh_auto', 'ssh_cli', 'ssh_service_key',
                                      'local_socket', 'legacy_admin_token')
            ),
            phase TEXT NOT NULL CHECK (phase IN (
                'pending', 'revoking', 'revoked', 'registry_deleted',
                'credential_deleted', 'completed', 'residual'
            )),
            local_only INTEGER NOT NULL CHECK (local_only IN (0, 1)),
            audit_event_id INTEGER NOT NULL,
            last_error_code TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _create_agents(connection)
        _create_enrollment_jobs(connection)
        _create_removal_jobs(connection)
        connection.execute(
            f"INSERT INTO agents_next ({_AGENT_COLUMNS}) "
            f"SELECT {_AGENT_COLUMNS} FROM agents"
        )
        connection.execute(
            f"INSERT INTO agent_enrollment_jobs_next ({_ENROLLMENT_COLUMNS}) "
            f"SELECT {_ENROLLMENT_COLUMNS} FROM agent_enrollment_jobs"
        )
        connection.execute(
            f"INSERT INTO agent_removal_jobs_next ({_REMOVAL_COLUMNS}) "
            f"SELECT {_REMOVAL_COLUMNS} FROM agent_removal_jobs"
        )
        connection.execute("DROP TABLE agent_enrollment_jobs")
        connection.execute("DROP TABLE agent_removal_jobs")
        connection.execute("DROP TABLE agents")
        connection.execute("ALTER TABLE agents_next RENAME TO agents")
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs_next RENAME TO agent_enrollment_jobs"
        )
        connection.execute(
            "ALTER TABLE agent_removal_jobs_next RENAME TO agent_removal_jobs"
        )
        connection.execute(
            "CREATE INDEX idx_enrollment_state_expiry "
            "ON agent_enrollment_jobs(state, expires_at)"
        )
        connection.execute(
            "CREATE INDEX idx_enrollment_recovery_lease "
            "ON agent_enrollment_jobs(recovery_lease_until, state)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_agent_removal_active "
            "ON agent_removal_jobs(agent_id) WHERE phase != 'completed'"
        )
        connection.execute(
            "CREATE INDEX idx_agent_removal_phase "
            "ON agent_removal_jobs(phase, updated_at)"
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchone()
        if violations is not None:
            raise sqlite3.IntegrityError("local socket migration violated foreign keys")
        connection.execute(
            "INSERT INTO schema_versions(version,applied_at,description,direction,result) "
            "VALUES (?,datetime('now'),'allow guarded local socket Agent identity',"
            "'upgrade','success')",
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=13")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={foreign_keys}")


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("local socket bootstrap schema is forward-only")

import sqlite3

VERSION = "0008_cli_resume_claim"


def upgrade(connection: sqlite3.Connection) -> None:
    applied = connection.execute(
        "SELECT 1 FROM schema_versions WHERE version=? AND result='success'", (VERSION,)
    ).fetchone()
    if applied is not None:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN cli_resume_nonce TEXT NULL "
            "CHECK (cli_resume_nonce IS NULL OR (length(cli_resume_nonce)=36 "
            "AND cli_resume_nonce=lower(cli_resume_nonce) "
            "AND cli_resume_nonce NOT GLOB '*[^0-9a-f-]*'))"
        )
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN cli_peer_uid INTEGER NULL "
            "CHECK (cli_peer_uid IS NULL OR cli_peer_uid>=0)"
        )
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN cli_input_fingerprint TEXT NULL "
            "CHECK (cli_input_fingerprint IS NULL OR "
            "(length(cli_input_fingerprint)=64 "
            "AND cli_input_fingerprint=lower(cli_input_fingerprint) "
            "AND cli_input_fingerprint NOT GLOB '*[^0-9a-f]*'))"
        )
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN cli_pinned_address TEXT NULL "
            "CHECK (cli_pinned_address IS NULL OR "
            "(length(cli_pinned_address) BETWEEN 2 AND 45 "
            "AND cli_pinned_address=lower(cli_pinned_address) "
            "AND cli_pinned_address NOT GLOB '*[^0-9a-f:.]*' "
            "AND instr(cli_pinned_address, '%')=0)) "
            "CHECK ((cli_resume_nonce IS NULL AND cli_peer_uid IS NULL "
            "AND cli_input_fingerprint IS NULL AND cli_pinned_address IS NULL) OR "
            "(cli_resume_nonce IS NOT NULL AND cli_peer_uid IS NOT NULL "
            "AND cli_input_fingerprint IS NOT NULL AND cli_pinned_address IS NOT NULL "
            "AND enrollment_method='ssh_cli' AND state IN "
            "('running','credential_issued','verifying','verified',"
            "'activation_requested','activated')))"
        )
        connection.execute(
            "UPDATE agent_enrollment_jobs SET state='expired', recovery_owner=NULL, "
            "recovery_lease_until=NULL, recovery_revision=recovery_revision+1, "
            "last_error_code='cli_resume_unavailable', updated_at=datetime('now') "
            "WHERE state='running' AND enrollment_method='ssh_cli' "
            "AND credential_temp_ref IS NULL"
        )
        connection.execute(
            "ALTER TABLE agent_enrollment_jobs ADD COLUMN cli_accept_receipt TEXT NULL "
            "CHECK (cli_accept_receipt IS NULL OR "
            "(length(cli_accept_receipt)=64 "
            "AND cli_accept_receipt=lower(cli_accept_receipt) "
            "AND cli_accept_receipt NOT GLOB '*[^0-9a-f]*')) "
            "CHECK (cli_accept_receipt IS NULL OR "
            "(state='consumed' AND enrollment_method='ssh_cli')) "
            "CHECK (cli_accept_receipt IS NULL OR cli_resume_nonce IS NULL) "
            "CHECK (NOT (state='running' AND enrollment_method='ssh_cli' "
            "AND credential_temp_ref IS NULL) OR cli_resume_nonce IS NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_versions(version, applied_at, description, direction, result) "
            "VALUES (?, datetime('now'), 'persist peer-bound CLI resume claims', "
            "'upgrade', 'success')",
            (VERSION,),
        )
        connection.execute("PRAGMA user_version=8")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def downgrade(connection: sqlite3.Connection) -> None:
    raise sqlite3.NotSupportedError("CLI resume claims are forward-only")

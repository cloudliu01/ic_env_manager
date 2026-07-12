import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import Any
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.fleet.models import (
    EnrollmentJob,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RegistryError,
    RegistryInvariantError,
    RevisionConflict,
)
from ic_env_guard.storage.manager_registry import (
    _AGENT_COLUMNS,
    _agent,
    _format_time,
    _parse_time,
    _SQLiteRepository,
)
from ic_env_guard.storage.mutation_fence import assert_agent_mutation_allowed

_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at, recovery_owner, "
    "recovery_lease_until, recovery_revision, validated_http_address, cli_resume_nonce, "
    "cli_peer_uid, cli_input_fingerprint, cli_pinned_address, cli_accept_receipt, "
    "old_normalized_endpoint, old_transport_profile_id, old_instance_id, "
    "old_registry_revision, old_enrollment_method, old_source, old_enabled, old_display_name"
)
_ENROLLMENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CREDENTIAL_REF = re.compile(r"^[0-9a-f]{48}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_TRANSITIONS = {
    EnrollmentState.PENDING: {
        EnrollmentState.RUNNING,
        EnrollmentState.AWAITING_CLI,
        EnrollmentState.CANCELLED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.RUNNING: {
        EnrollmentState.AWAITING_CLI,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.CANCELLED,
        EnrollmentState.FAILED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.AWAITING_CLI: {
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.CANCELLED,
        EnrollmentState.FAILED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.CREDENTIAL_ISSUED: {
        EnrollmentState.VERIFYING,
        EnrollmentState.CANCELLED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.VERIFYING: {
        EnrollmentState.VERIFIED,
        EnrollmentState.CANCELLED,
        EnrollmentState.FAILED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.VERIFIED: {
        EnrollmentState.ACTIVATION_REQUESTED,
        EnrollmentState.CANCELLED,
        EnrollmentState.EXPIRED,
    },
    EnrollmentState.ACTIVATION_REQUESTED: {
        EnrollmentState.ACTIVATED,
        EnrollmentState.FAILED,
    },
    EnrollmentState.ACTIVATED: {
        EnrollmentState.CONSUMED,
        EnrollmentState.FAILED,
    },
}
_EXPIRABLE_STATES = (
    EnrollmentState.PENDING,
    EnrollmentState.RUNNING,
    EnrollmentState.AWAITING_CLI,
    EnrollmentState.CREDENTIAL_ISSUED,
    EnrollmentState.VERIFYING,
    EnrollmentState.VERIFIED,
)


def _validate_job(job: EnrollmentJob) -> None:
    if not _ENROLLMENT_ID.fullmatch(job.enrollment_id):
        raise RegistryInvariantError("invalid enrollment ID")
    try:
        manager_id = UUID(job.manager_id)
    except ValueError as exc:
        raise RegistryInvariantError("manager ID must be a UUID") from exc
    if str(manager_id) != job.manager_id:
        raise RegistryInvariantError("manager ID must be canonical")
    if not job.normalized_endpoint or not job.transport_profile_id:
        raise RegistryInvariantError("enrollment target fields must not be empty")
    ssh_fields = (job.ssh_user, job.ssh_host, job.ssh_port)
    if job.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN:
        if any(value is not None for value in ssh_fields):
            raise RegistryInvariantError("legacy enrollment must not contain SSH fields")
    elif any(value is None for value in ssh_fields):
        raise RegistryInvariantError("SSH enrollment requires all SSH fields")
    if job.ssh_port is not None and not 1 <= job.ssh_port <= 65535:
        raise RegistryInvariantError("SSH port is invalid")
    if job.save_requested and not job.requested_display_name:
        raise RegistryInvariantError("saved enrollment requires a display name")
    if job.discovery_result_id is not None and job.replace_agent_id is not None:
        raise RegistryInvariantError("discovery enrollment cannot replace an agent")
    rotation_metadata = (
        job.old_normalized_endpoint,
        job.old_transport_profile_id,
        job.old_registry_revision,
        job.old_enrollment_method,
        job.old_source,
        job.old_enabled,
        job.old_display_name,
    )
    if job.replace_agent_id is None:
        if any(value is not None for value in rotation_metadata) or any(
            value is not None
            for value in (
                job.old_credential_ref,
                job.old_remote_credential_id,
                job.old_instance_id,
            )
        ):
            raise RegistryInvariantError("non-rotation enrollment has an old Agent snapshot")
    else:
        if any(value is None for value in rotation_metadata):
            raise RegistryInvariantError("rotation enrollment requires an old Agent snapshot")
        if job.state not in {EnrollmentState.CONSUMED, EnrollmentState.FAILED}:
            if job.old_credential_ref is None:
                raise RegistryInvariantError("rotation requires the old credential reference")
            if job.old_enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN and (
                job.old_remote_credential_id is None or job.old_instance_id is None
            ):
                raise RegistryInvariantError("managed rotation requires old remote identity")
        if (
            job.remote_instance_id is not None
            and job.old_instance_id is not None
            and job.remote_instance_id != job.old_instance_id
        ):
            raise RegistryInvariantError("rotation changed Agent identity")
    credential_states = {
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
        EnrollmentState.ACTIVATION_REQUESTED,
        EnrollmentState.ACTIVATED,
    }
    if job.state in credential_states and job.credential_temp_ref is None:
        raise RegistryInvariantError("enrollment phase requires a credential reference")
    if job.state in {
        EnrollmentState.ACTIVATION_REQUESTED,
        EnrollmentState.ACTIVATED,
    }:
        if not job.save_requested or not job.requested_display_name:
            raise RegistryInvariantError("activation phase requires a saved display name")
        if job.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN and (
            job.remote_instance_id is None or job.remote_credential_id is None
        ):
            raise RegistryInvariantError("SSH activation requires remote identity and credential")
    if (job.recovery_owner is None) != (job.recovery_lease_until is None):
        raise RegistryInvariantError("recovery claim fields must be set together")
    if job.recovery_owner is not None:
        try:
            owner = UUID(job.recovery_owner)
        except ValueError as exc:
            raise RegistryInvariantError("recovery owner must be a UUID") from exc
        if str(owner) != job.recovery_owner:
            raise RegistryInvariantError("recovery owner must be canonical")
    if (
        not isinstance(job.recovery_revision, int)
        or isinstance(job.recovery_revision, bool)
        or job.recovery_revision < 0
    ):
        raise RegistryInvariantError("recovery revision must not be negative")
    if job.last_error_code is not None and not _ERROR_CODE.fullmatch(job.last_error_code):
        raise RegistryInvariantError("enrollment error code is invalid")
    pinned = job.validated_http_address
    if pinned is not None:
        try:
            if str(ip_address(pinned)) != pinned:
                raise ValueError
        except ValueError as exc:
            raise RegistryInvariantError("validated HTTP address is invalid") from exc
    cli_claim = (
        job.cli_resume_nonce,
        job.cli_peer_uid,
        job.cli_input_fingerprint,
        job.cli_pinned_address,
    )
    if any(value is not None for value in cli_claim):
        if any(value is None for value in cli_claim):
            raise RegistryInvariantError("CLI resume claim fields must be set together")
        if job.enrollment_method is not EnrollmentMethod.SSH_CLI:
            raise RegistryInvariantError("CLI resume claim requires CLI enrollment")
        if job.state not in {
            EnrollmentState.RUNNING,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
            EnrollmentState.VERIFIED,
            EnrollmentState.ACTIVATION_REQUESTED,
            EnrollmentState.ACTIVATED,
        }:
            raise RegistryInvariantError("CLI resume claim is invalid for enrollment phase")
        try:
            nonce = UUID(job.cli_resume_nonce or "")
        except ValueError as exc:
            raise RegistryInvariantError("CLI resume nonce must be a UUID") from exc
        if str(nonce) != job.cli_resume_nonce:
            raise RegistryInvariantError("CLI resume nonce must be canonical")
        if not isinstance(job.cli_peer_uid, int) or isinstance(job.cli_peer_uid, bool):
            raise RegistryInvariantError("CLI peer UID is invalid")
        if job.cli_peer_uid < 0:
            raise RegistryInvariantError("CLI peer UID is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", job.cli_input_fingerprint or ""):
            raise RegistryInvariantError("CLI input fingerprint is invalid")
        try:
            if str(ip_address(job.cli_pinned_address or "")) != job.cli_pinned_address:
                raise ValueError
        except ValueError as exc:
            raise RegistryInvariantError("CLI pinned address is invalid") from exc
    elif (
        job.enrollment_method is EnrollmentMethod.SSH_CLI
        and job.state is EnrollmentState.RUNNING
        and job.credential_temp_ref is None
    ):
        raise RegistryInvariantError("running CLI enrollment requires resume identity")
    receipt = job.cli_accept_receipt
    if receipt is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", receipt):
            raise RegistryInvariantError("CLI accept receipt is invalid")
        if (
            job.state is not EnrollmentState.CONSUMED
            or job.enrollment_method is not EnrollmentMethod.SSH_CLI
            or any(value is not None for value in cli_claim)
        ):
            raise RegistryInvariantError("CLI accept receipt is invalid for phase")
    credential_pin_required = job.state in credential_states or (
        job.state.terminal and job.credential_temp_ref is not None
    )
    if job.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN:
        if pinned is not None:
            raise RegistryInvariantError("legacy enrollment cannot store a pinned address")
    elif credential_pin_required != (pinned is not None):
        raise RegistryInvariantError("SSH credential phase requires a pinned address")
    for reference in (
        job.credential_temp_ref,
        job.old_credential_ref,
    ):
        if reference is not None and not _CREDENTIAL_REF.fullmatch(reference):
            raise RegistryInvariantError("invalid credential reference")
    _format_time(job.expires_at)
    _format_time(job.created_at)
    _format_time(job.updated_at)


def _job(row: Any) -> EnrollmentJob:
    return EnrollmentJob(
        enrollment_id=row[0],
        manager_id=row[1],
        state=EnrollmentState(row[2]),
        normalized_endpoint=row[3],
        transport_profile_id=row[4],
        discovery_result_id=row[5],
        replace_agent_id=row[6],
        requested_display_name=row[7],
        ssh_user=row[8],
        ssh_host=row[9],
        ssh_port=row[10],
        enrollment_method=EnrollmentMethod(row[11]),
        remote_instance_id=row[12],
        remote_credential_id=row[13],
        credential_temp_ref=row[14],
        old_credential_ref=row[15],
        old_remote_credential_id=row[16],
        save_requested=bool(row[17]),
        expires_at=_parse_time(row[18]),
        last_error_code=row[19],
        created_at=_parse_time(row[20]),
        updated_at=_parse_time(row[21]),
        recovery_owner=row[22],
        recovery_lease_until=_parse_time(row[23]),
        recovery_revision=row[24],
        validated_http_address=row[25],
        cli_resume_nonce=row[26],
        cli_peer_uid=row[27],
        cli_input_fingerprint=row[28],
        cli_pinned_address=row[29],
        cli_accept_receipt=row[30],
        old_normalized_endpoint=row[31],
        old_transport_profile_id=row[32],
        old_instance_id=row[33],
        old_registry_revision=row[34],
        old_enrollment_method=(
            EnrollmentMethod(row[35]) if row[35] is not None else None
        ),
        old_source=row[36],
        old_enabled=bool(row[37]) if row[37] is not None else None,
        old_display_name=row[38],
    )


class EnrollmentJournalRepository(_SQLiteRepository):
    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)

    def create(self, job: EnrollmentJob) -> EnrollmentJob:
        _validate_job(job)
        try:
            with self._write() as connection:
                connection.execute(
                    f"INSERT INTO agent_enrollment_jobs ({_COLUMNS}) "
                    f"VALUES ({','.join('?' * 39)})",
                    self._values(job),
                )
            return job
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("enrollment job conflicts with existing state") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def create_with_capacity(
        self,
        job: EnrollmentJob,
        *,
        now: datetime,
        max_active: int,
    ) -> EnrollmentJob:
        _validate_job(job)
        if max_active < 1:
            raise RegistryInvariantError("enrollment capacity is invalid")
        terminal = tuple(state.value for state in EnrollmentState if state.terminal)
        terminal_placeholders = ",".join("?" for _ in terminal)
        expirable = tuple(state.value for state in _EXPIRABLE_STATES)
        expirable_placeholders = ",".join("?" for _ in expirable)
        try:
            with self._write() as connection:
                connection.execute(
                    f"UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    f"WHERE state IN ({expirable_placeholders}) AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        *expirable,
                        _format_time(now),
                    ),
                )
                count = connection.execute(
                    f"SELECT COUNT(*) FROM agent_enrollment_jobs "
                    f"WHERE state NOT IN ({terminal_placeholders})",
                    terminal,
                ).fetchone()[0]
                if count >= max_active:
                    raise RegistryConflict("agent_enrollment_capacity")
                connection.execute(
                    f"INSERT INTO agent_enrollment_jobs ({_COLUMNS}) "
                    f"VALUES ({','.join('?' * 39)})",
                    self._values(job),
                )
            return job
        except RegistryConflict:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("enrollment job conflicts with existing state") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def create_rotation_with_capacity(
        self,
        job: EnrollmentJob,
        *,
        now: datetime,
        max_active: int,
    ) -> EnrollmentJob:
        if job.replace_agent_id is None:
            raise RegistryInvariantError("rotation requires a replacement Agent")
        terminal = tuple(state.value for state in EnrollmentState if state.terminal)
        terminal_placeholders = ",".join("?" for _ in terminal)
        expirable = tuple(state.value for state in _EXPIRABLE_STATES)
        expirable_placeholders = ",".join("?" for _ in expirable)
        try:
            with self._write() as connection:
                connection.execute(
                    f"UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    f"WHERE state IN ({expirable_placeholders}) AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        *expirable,
                        _format_time(now),
                    ),
                )
                row = connection.execute(
                    f"SELECT {_AGENT_COLUMNS} FROM agents WHERE agent_id=?",
                    (job.replace_agent_id,),
                ).fetchone()
                if row is None:
                    raise RegistryConflict("agent_not_found")
                old = _agent(row)
                assert_agent_mutation_allowed(connection, old.agent_id)
                blocker = connection.execute(
                    f"SELECT enrollment_id FROM agent_enrollment_jobs "
                    f"WHERE replace_agent_id=? AND state NOT IN "
                    f"({terminal_placeholders}) LIMIT 1",
                    (old.agent_id, *terminal),
                ).fetchone()
                if blocker is not None:
                    raise RegistryConflict("agent_mutation_in_progress")
                captured = replace(
                    job,
                    normalized_endpoint=old.normalized_endpoint,
                    transport_profile_id=old.transport_profile_id,
                    requested_display_name=old.display_name,
                    old_credential_ref=old.credential_ref,
                    old_remote_credential_id=old.remote_credential_id,
                    old_normalized_endpoint=old.normalized_endpoint,
                    old_transport_profile_id=old.transport_profile_id,
                    old_instance_id=old.instance_id,
                    old_registry_revision=old.revision,
                    old_enrollment_method=old.enrollment_method,
                    old_source=old.source,
                    old_enabled=old.enabled,
                    old_display_name=old.display_name,
                )
                _validate_job(captured)
                count = connection.execute(
                    f"SELECT COUNT(*) FROM agent_enrollment_jobs "
                    f"WHERE state NOT IN ({terminal_placeholders})",
                    terminal,
                ).fetchone()[0]
                if count >= max_active:
                    raise RegistryConflict("agent_enrollment_capacity")
                connection.execute(
                    f"INSERT INTO agent_enrollment_jobs ({_COLUMNS}) "
                    f"VALUES ({','.join('?' * 39)})",
                    self._values(captured),
                )
            return captured
        except RegistryConflict:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("agent_enrollment_conflict") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def consume_rotation(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        display_name: str,
        now: datetime,
    ) -> EnrollmentJob:
        try:
            with self._write() as connection:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
                if row is None:
                    raise RegistryConflict("agent_enrollment_not_found")
                job = _job(row)
                if job.state is EnrollmentState.CONSUMED:
                    raise RegistryConflict("agent_enrollment_consumed")
                if now >= job.expires_at:
                    raise RegistryConflict("agent_enrollment_expired")
                if job.state is not EnrollmentState.VERIFIED:
                    raise RegistryConflict("agent_enrollment_not_verified")
                if job.replace_agent_id != agent_id:
                    raise RegistryConflict("agent_enrollment_conflict")
                registered_row = connection.execute(
                    f"SELECT {_AGENT_COLUMNS} FROM agents WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
                if registered_row is None:
                    raise RegistryConflict("agent_not_found")
                registered = _agent(registered_row)
                if (
                    registered.revision != job.old_registry_revision
                    or registered.credential_ref != job.old_credential_ref
                    or registered.instance_id != job.old_instance_id
                ):
                    raise RegistryConflict("agent_changed")
                assert_agent_mutation_allowed(
                    connection,
                    agent_id,
                    owner_enrollment_id=enrollment_id,
                )
                updated = replace(
                    job,
                    state=EnrollmentState.ACTIVATION_REQUESTED,
                    requested_display_name=display_name,
                    save_requested=True,
                    updated_at=now,
                )
                _validate_job(updated)
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET state='activation_requested', "
                    "requested_display_name=?, save_requested=1, updated_at=? "
                    "WHERE enrollment_id=? AND state='verified' AND save_requested=0",
                    (display_name, _format_time(now), enrollment_id),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("rotation enrollment state changed")
            return updated
        except (RegistryConflict, RevisionConflict):
            raise
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def get(self, enrollment_id: str) -> EnrollmentJob | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id = ?",
                    (enrollment_id,),
                ).first()
            return _job(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def set_state(
        self,
        enrollment_id: str,
        state: EnrollmentState,
        updated_at: datetime,
        *,
        expected_state: EnrollmentState,
    ) -> None:
        if state not in _ALLOWED_TRANSITIONS.get(expected_state, set()):
            raise RegistryInvariantError("invalid enrollment state transition")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    "WHERE enrollment_id=? AND state=?",
                    (
                        state.value,
                        _format_time(updated_at),
                        enrollment_id,
                        expected_state.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("enrollment state changed")
        except RevisionConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def replace_if_state(
        self,
        job: EnrollmentJob,
        *,
        expected_state: EnrollmentState,
        expected_recovery_owner: str | None = None,
        expected_recovery_revision: int | None = None,
        recovery_now: datetime | None = None,
    ) -> EnrollmentJob:
        _validate_job(job)
        if job.state not in _ALLOWED_TRANSITIONS.get(expected_state, set()):
            raise RegistryInvariantError("invalid enrollment state transition")
        if expected_recovery_owner is not None and (
            expected_recovery_revision is None
            or recovery_now is None
            or job.recovery_revision != expected_recovery_revision + 1
        ):
            raise RegistryInvariantError("recovery transition fence is invalid")
        assignments = ", ".join(
            f"{column.strip()}=?"
            for column in _COLUMNS.split(",")
            if column.strip() != "enrollment_id"
        )
        values = self._values(job)
        try:
            with self._write() as connection:
                owner_clause = ""
                parameters = [*values[1:], job.enrollment_id, expected_state.value]
                if expected_recovery_owner is not None:
                    owner_clause = (
                        " AND recovery_owner=? AND recovery_revision=? "
                        "AND recovery_lease_until>?"
                    )
                    parameters.extend(
                        (
                            expected_recovery_owner,
                            expected_recovery_revision,
                            _format_time(recovery_now),
                        )
                    )
                cursor = connection.execute(
                    f"UPDATE agent_enrollment_jobs SET {assignments} "
                    f"WHERE enrollment_id=? AND state=?{owner_clause}",
                    tuple(parameters),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("enrollment state changed")
            return job
        except RevisionConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def list_non_terminal(self) -> tuple[EnrollmentJob, ...]:
        terminal = tuple(state.value for state in EnrollmentState if state.terminal)
        placeholders = ",".join("?" for _ in terminal)
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs "
                    f"WHERE state NOT IN ({placeholders}) ORDER BY created_at, enrollment_id",
                    terminal,
                ).fetchall()
            return tuple(_job(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def claim_pending_auto(self, enrollment_id: str, *, now: datetime) -> EnrollmentJob | None:
        """Atomically expire or claim one pending auto-enrollment for dispatch."""
        try:
            with self._write() as connection:
                connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    "WHERE enrollment_id=? AND state=? AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        enrollment_id,
                        EnrollmentState.PENDING.value,
                        _format_time(now),
                    ),
                )
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    "WHERE enrollment_id=? AND state=? AND expires_at>?",
                    (
                        EnrollmentState.RUNNING.value,
                        _format_time(now),
                        enrollment_id,
                        EnrollmentState.PENDING.value,
                        _format_time(now),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
            return _job(row)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def recheck_auto_dispatch(
        self, enrollment_id: str, *, now: datetime
    ) -> EnrollmentJob | None:
        """Fence dispatch against cancellation and TTL expiry in one transaction."""
        try:
            with self._write() as connection:
                connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    "WHERE enrollment_id=? AND state=? AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        enrollment_id,
                        EnrollmentState.RUNNING.value,
                        _format_time(now),
                    ),
                )
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs "
                    "WHERE enrollment_id=? AND state=? AND credential_temp_ref IS NULL "
                    "AND expires_at>?",
                    (
                        enrollment_id,
                        EnrollmentState.RUNNING.value,
                        _format_time(now),
                    ),
                ).fetchone()
            return _job(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def recheck_cli_submission(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        now: datetime,
    ) -> EnrollmentJob | None:
        """Atomically expire or fence a CLI result before its helper is parsed."""
        try:
            with self._write() as connection:
                connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=?, "
                    "recovery_owner=NULL, recovery_lease_until=NULL, "
                    "recovery_revision=recovery_revision+1, cli_resume_nonce=NULL, "
                    "cli_peer_uid=NULL, cli_input_fingerprint=NULL, "
                    "cli_pinned_address=NULL, cli_accept_receipt=NULL "
                    "WHERE enrollment_id=? AND state=? AND recovery_owner=? "
                    "AND recovery_revision=? AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        enrollment_id,
                        EnrollmentState.RUNNING.value,
                        owner,
                        expected_revision,
                        _format_time(now),
                    ),
                )
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs "
                    "WHERE enrollment_id=? AND state=? AND enrollment_method=? "
                    "AND recovery_owner=? AND recovery_revision=? "
                    "AND recovery_lease_until>? AND expires_at>?",
                    (
                        enrollment_id,
                        EnrollmentState.RUNNING.value,
                        EnrollmentMethod.SSH_CLI.value,
                        owner,
                        expected_revision,
                        _format_time(now),
                        _format_time(now),
                    ),
                ).fetchone()
            return _job(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def prepare_recovery(self, *, now: datetime) -> tuple[str, ...]:
        """Expire TTL phases and list currently claimable jobs without claiming them."""
        expirable = tuple(state.value for state in _EXPIRABLE_STATES)
        recoverable = (
            EnrollmentState.CREDENTIAL_ISSUED.value,
            EnrollmentState.VERIFYING.value,
            EnrollmentState.VERIFIED.value,
            EnrollmentState.ACTIVATION_REQUESTED.value,
            EnrollmentState.ACTIVATED.value,
        )
        try:
            with self._write() as connection:
                connection.execute(
                    f"UPDATE agent_enrollment_jobs SET state=?, updated_at=?, "
                    "recovery_owner=NULL, recovery_lease_until=NULL, "
                    "recovery_revision=recovery_revision+1, cli_resume_nonce=NULL, "
                    "cli_peer_uid=NULL, cli_input_fingerprint=NULL, "
                    "cli_pinned_address=NULL, cli_accept_receipt=NULL "
                    f"WHERE state IN ({','.join('?' for _ in expirable)}) "
                    "AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        *expirable,
                        _format_time(now),
                    ),
                )
                rows = connection.execute(
                    "SELECT enrollment_id FROM agent_enrollment_jobs "
                    f"WHERE state IN ({','.join('?' for _ in recoverable)}) "
                    "AND (recovery_owner IS NULL OR recovery_lease_until<=?) "
                    "ORDER BY created_at, enrollment_id",
                    (*recoverable, _format_time(now)),
                ).fetchall()
            return tuple(row[0] for row in rows)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def claim_recovery(
        self,
        enrollment_id: str,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> EnrollmentJob | None:
        try:
            parsed_owner = UUID(owner)
        except ValueError as exc:
            raise RegistryInvariantError("recovery owner must be a UUID") from exc
        if str(parsed_owner) != owner or lease_seconds < 1:
            raise RegistryInvariantError("recovery claim is invalid")
        recoverable = (
            EnrollmentState.CREDENTIAL_ISSUED.value,
            EnrollmentState.VERIFYING.value,
            EnrollmentState.VERIFIED.value,
            EnrollmentState.ACTIVATION_REQUESTED.value,
            EnrollmentState.ACTIVATED.value,
        )
        expirable = tuple(state.value for state in _EXPIRABLE_STATES)
        try:
            with self._write() as connection:
                connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=?, "
                    "recovery_owner=NULL, recovery_lease_until=NULL, "
                    "recovery_revision=recovery_revision+1, cli_resume_nonce=NULL, "
                    "cli_peer_uid=NULL, cli_input_fingerprint=NULL, "
                    "cli_pinned_address=NULL, cli_accept_receipt=NULL "
                    "WHERE enrollment_id=? "
                    f"AND state IN ({','.join('?' for _ in expirable)}) "
                    "AND expires_at<=?",
                    (
                        EnrollmentState.EXPIRED.value,
                        _format_time(now),
                        enrollment_id,
                        *expirable,
                        _format_time(now),
                    ),
                )
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET recovery_owner=?, "
                    "recovery_lease_until=?, recovery_revision=recovery_revision+1, "
                    "updated_at=? WHERE enrollment_id=? "
                    f"AND state IN ({','.join('?' for _ in recoverable)}) "
                    "AND (recovery_owner IS NULL OR recovery_lease_until<=?)",
                    (
                        owner,
                        _format_time(now + timedelta(seconds=lease_seconds)),
                        _format_time(now),
                        enrollment_id,
                        *recoverable,
                        _format_time(now),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
            return _job(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def renew_recovery_claim(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        now: datetime,
        lease_seconds: int,
    ) -> EnrollmentJob | None:
        if lease_seconds < 1 or expected_revision < 1:
            raise RegistryInvariantError("recovery renewal is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET recovery_lease_until=?, "
                    "recovery_revision=recovery_revision+1, updated_at=? "
                    "WHERE enrollment_id=? AND recovery_owner=? AND recovery_revision=? "
                    "AND recovery_lease_until>?",
                    (
                        _format_time(now + timedelta(seconds=lease_seconds)),
                        _format_time(now),
                        enrollment_id,
                        owner,
                        expected_revision,
                        _format_time(now),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
            return _job(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def release_recovery_claim(
        self, enrollment_id: str, *, owner: str, expected_revision: int, now: datetime
    ) -> bool:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET recovery_owner=NULL, "
                    "recovery_lease_until=NULL, recovery_revision=recovery_revision+1 "
                    "WHERE enrollment_id=? AND recovery_owner=? AND recovery_revision=? "
                    "AND recovery_lease_until>?",
                    (enrollment_id, owner, expected_revision, _format_time(now)),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def fail_recovery_claim(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        error_code: str,
        now: datetime,
    ) -> bool:
        if not _ERROR_CODE.fullmatch(error_code):
            raise RegistryInvariantError("enrollment error code is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, recovery_owner=NULL, "
                    "recovery_lease_until=NULL, recovery_revision=recovery_revision+1, "
                    "cli_resume_nonce=NULL, cli_peer_uid=NULL, "
                    "cli_input_fingerprint=NULL, cli_pinned_address=NULL, "
                    "cli_accept_receipt=NULL, "
                    "last_error_code=?, updated_at=? WHERE enrollment_id=? "
                    "AND recovery_owner=? AND recovery_revision=? "
                    "AND recovery_lease_until>?",
                    (
                        EnrollmentState.FAILED.value,
                        error_code,
                        _format_time(now),
                        enrollment_id,
                        owner,
                        expected_revision,
                        _format_time(now),
                    ),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def release_recovery_residual(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        error_code: str,
        now: datetime,
    ) -> bool:
        if not _ERROR_CODE.fullmatch(error_code):
            raise RegistryInvariantError("enrollment error code is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET last_error_code=?, "
                    "recovery_owner=NULL, recovery_lease_until=NULL, "
                    "recovery_revision=recovery_revision+1, updated_at=? "
                    "WHERE enrollment_id=? AND recovery_owner=? AND recovery_revision=? "
                    "AND state IN ('activation_requested','activated')",
                    (
                        error_code,
                        _format_time(now),
                        enrollment_id,
                        owner,
                        expected_revision,
                    ),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def mark_recovery_claim_error(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        error_code: str,
        now: datetime,
    ) -> bool:
        """Persist a compensation marker without releasing the active lease."""
        if not _ERROR_CODE.fullmatch(error_code):
            raise RegistryInvariantError("enrollment error code is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET last_error_code=?, "
                    "recovery_revision=recovery_revision+1, updated_at=? "
                    "WHERE enrollment_id=? AND recovery_owner=? AND recovery_revision=? "
                    "AND recovery_lease_until>? AND state='activated'",
                    (
                        error_code,
                        _format_time(now),
                        enrollment_id,
                        owner,
                        expected_revision,
                        _format_time(now),
                    ),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def list_terminal_cleanup(self) -> tuple[EnrollmentJob, ...]:
        states = (
            EnrollmentState.CANCELLED.value,
            EnrollmentState.FAILED.value,
            EnrollmentState.EXPIRED.value,
        )
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs "
                    "WHERE state IN (?,?,?) AND credential_temp_ref IS NOT NULL "
                    "ORDER BY updated_at, enrollment_id",
                    states,
                ).fetchall()
            return tuple(_job(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def finish_terminal_cleanup(
        self,
        enrollment_id: str,
        *,
        state: EnrollmentState,
        expected_reference: str,
        now: datetime,
    ) -> EnrollmentJob:
        if state not in {
            EnrollmentState.CANCELLED,
            EnrollmentState.FAILED,
            EnrollmentState.EXPIRED,
        }:
            raise RegistryInvariantError("terminal cleanup state is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET credential_temp_ref=NULL, "
                    "validated_http_address=NULL, "
                    "last_error_code=CASE WHEN last_error_code='credential_cleanup_failed' "
                    "THEN NULL ELSE last_error_code END, updated_at=? "
                    "WHERE enrollment_id=? AND state=? "
                    "AND credential_temp_ref=?",
                    (
                        _format_time(now),
                        enrollment_id,
                        state.value,
                        expected_reference,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("terminal cleanup state changed")
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
            assert row is not None
            return _job(row)
        except RevisionConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def mark_terminal_cleanup_failed(
        self,
        enrollment_id: str,
        *,
        state: EnrollmentState,
        expected_reference: str,
        now: datetime,
    ) -> EnrollmentJob:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET last_error_code=CASE "
                    "WHEN last_error_code IS NULL OR "
                    "last_error_code='credential_cleanup_failed' THEN ? "
                    "ELSE last_error_code END, updated_at=? "
                    "WHERE enrollment_id=? AND state=? AND credential_temp_ref=?",
                    (
                        "credential_cleanup_failed",
                        _format_time(now),
                        enrollment_id,
                        state.value,
                        expected_reference,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("terminal cleanup state changed")
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
            assert row is not None
            return _job(row)
        except RevisionConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def dump_serialized_rows(self) -> str:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_enrollment_jobs ORDER BY enrollment_id"
                ).mappings().all()
            projected = []
            private = {
                "recovery_owner",
                "recovery_lease_until",
                "recovery_revision",
                "cli_resume_nonce",
                "cli_peer_uid",
                "cli_input_fingerprint",
                "cli_pinned_address",
                "cli_accept_receipt",
            }
            for row in rows:
                projected.append(
                    {key: value for key, value in row.items() if key not in private}
                )
            return json.dumps(projected, sort_keys=True, default=str)
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def recovery_credential_references(self) -> set[str]:
        terminal = tuple(state.value for state in EnrollmentState if state.terminal)
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT credential_temp_ref, old_credential_ref "
                    "FROM agent_enrollment_jobs WHERE state NOT IN (?,?,?,?) OR "
                    "credential_temp_ref IS NOT NULL OR old_credential_ref IS NOT NULL",
                    terminal,
                ).fetchall()
            return {value for row in rows for value in row if value is not None}
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def non_terminal_credential_references(self) -> set[str]:
        return self.recovery_credential_references()

    @staticmethod
    def _values(job: EnrollmentJob) -> tuple[Any, ...]:
        return (
            job.enrollment_id,
            job.manager_id,
            job.state.value,
            job.normalized_endpoint,
            job.transport_profile_id,
            job.discovery_result_id,
            job.replace_agent_id,
            job.requested_display_name,
            job.ssh_user,
            job.ssh_host,
            job.ssh_port,
            job.enrollment_method.value,
            job.remote_instance_id,
            job.remote_credential_id,
            job.credential_temp_ref,
            job.old_credential_ref,
            job.old_remote_credential_id,
            int(job.save_requested),
            _format_time(job.expires_at),
            job.last_error_code,
            _format_time(job.created_at),
            _format_time(job.updated_at),
            job.recovery_owner,
            _format_time(job.recovery_lease_until),
            job.recovery_revision,
            job.validated_http_address,
            job.cli_resume_nonce,
            job.cli_peer_uid,
            job.cli_input_fingerprint,
            job.cli_pinned_address,
            job.cli_accept_receipt,
            job.old_normalized_endpoint,
            job.old_transport_profile_id,
            job.old_instance_id,
            job.old_registry_revision,
            (
                job.old_enrollment_method.value
                if job.old_enrollment_method is not None
                else None
            ),
            job.old_source,
            int(job.old_enabled) if job.old_enabled is not None else None,
            job.old_display_name,
        )

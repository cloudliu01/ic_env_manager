import json
import re
import sqlite3
from datetime import datetime, timedelta
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
from ic_env_guard.storage.manager_registry import _format_time, _parse_time, _SQLiteRepository

_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at, recovery_owner, "
    "recovery_lease_until"
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
    if job.last_error_code is not None and not _ERROR_CODE.fullmatch(job.last_error_code):
        raise RegistryInvariantError("enrollment error code is invalid")
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
                    f"VALUES ({','.join('?' * 24)})",
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
                    f"VALUES ({','.join('?' * 24)})",
                    self._values(job),
                )
            return job
        except RegistryConflict:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("enrollment job conflicts with existing state") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
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
    ) -> EnrollmentJob:
        _validate_job(job)
        if job.state not in _ALLOWED_TRANSITIONS.get(expected_state, set()):
            raise RegistryInvariantError("invalid enrollment state transition")
        assignments = ", ".join(
            f"{column.strip()}=?"
            for column in _COLUMNS.split(",")
            if column.strip() != "enrollment_id"
        )
        values = self._values(job)
        try:
            with self._write() as connection:
                owner_clause = (
                    " AND recovery_owner=?" if expected_recovery_owner is not None else ""
                )
                parameters = [*values[1:], job.enrollment_id, expected_state.value]
                if expected_recovery_owner is not None:
                    parameters.append(expected_recovery_owner)
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

    def expire_and_claim_recovery(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> tuple[EnrollmentJob, ...]:
        try:
            parsed_owner = UUID(owner)
        except ValueError as exc:
            raise RegistryInvariantError("recovery owner must be a UUID") from exc
        if str(parsed_owner) != owner or lease_seconds < 1:
            raise RegistryInvariantError("recovery claim is invalid")
        expirable = tuple(state.value for state in _EXPIRABLE_STATES)
        recoverable = (
            EnrollmentState.CREDENTIAL_ISSUED.value,
            EnrollmentState.VERIFYING.value,
            EnrollmentState.VERIFIED.value,
            EnrollmentState.ACTIVATION_REQUESTED.value,
            EnrollmentState.ACTIVATED.value,
        )
        try:
            claimed_ids: list[str] = []
            with self._write() as connection:
                connection.execute(
                    f"UPDATE agent_enrollment_jobs SET state=?, updated_at=?, "
                    "recovery_owner=NULL, recovery_lease_until=NULL "
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
                    f"SELECT enrollment_id FROM agent_enrollment_jobs "
                    f"WHERE state IN ({','.join('?' for _ in recoverable)}) "
                    "AND (recovery_owner IS NULL OR recovery_lease_until<=?) "
                    "ORDER BY created_at, enrollment_id",
                    (*recoverable, _format_time(now)),
                ).fetchall()
                for position, row in enumerate(rows, start=1):
                    lease_until = now + timedelta(seconds=lease_seconds * position)
                    cursor = connection.execute(
                        "UPDATE agent_enrollment_jobs SET recovery_owner=?, "
                        "recovery_lease_until=?, updated_at=? WHERE enrollment_id=? "
                        "AND (recovery_owner IS NULL OR recovery_lease_until<=?)",
                        (
                            owner,
                            _format_time(lease_until),
                            _format_time(now),
                            row[0],
                            _format_time(now),
                        ),
                    )
                    if cursor.rowcount == 1:
                        claimed_ids.append(row[0])
                claimed = [
                    connection.execute(
                        f"SELECT {_COLUMNS} FROM agent_enrollment_jobs "
                        "WHERE enrollment_id=?",
                        (enrollment_id,),
                    ).fetchone()
                    for enrollment_id in claimed_ids
                ]
            return tuple(_job(row) for row in claimed if row is not None)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def release_recovery_claim(self, enrollment_id: str, *, owner: str) -> bool:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET recovery_owner=NULL, "
                    "recovery_lease_until=NULL WHERE enrollment_id=? AND recovery_owner=?",
                    (enrollment_id, owner),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def fail_recovery_claim(
        self,
        enrollment_id: str,
        *,
        owner: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        if not _ERROR_CODE.fullmatch(error_code):
            raise RegistryInvariantError("enrollment error code is invalid")
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET recovery_owner=NULL, "
                    "recovery_lease_until=NULL, last_error_code=?, updated_at=? "
                    "WHERE enrollment_id=? AND recovery_owner=?",
                    (error_code, _format_time(now), enrollment_id, owner),
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
                    "last_error_code=NULL, updated_at=? WHERE enrollment_id=? AND state=? "
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
                    "UPDATE agent_enrollment_jobs SET last_error_code=?, updated_at=? "
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
            return json.dumps([dict(row) for row in rows], sort_keys=True, default=str)
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
        )

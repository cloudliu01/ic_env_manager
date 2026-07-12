import re
import sqlite3
from datetime import datetime
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
)
from ic_env_guard.storage.manager_registry import _format_time, _parse_time, _SQLiteRepository

_COLUMNS = (
    "enrollment_id, manager_id, state, normalized_endpoint, transport_profile_id, "
    "discovery_result_id, replace_agent_id, requested_display_name, ssh_user, ssh_host, "
    "ssh_port, enrollment_method, remote_instance_id, remote_credential_id, "
    "credential_temp_ref, old_credential_ref, old_remote_credential_id, save_requested, "
    "expires_at, last_error_code, created_at, updated_at"
)
_ENROLLMENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CREDENTIAL_REF = re.compile(r"^[0-9a-f]{48}$")


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
                    f"VALUES ({','.join('?' * 22)})",
                    self._values(job),
                )
            return job
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
        self, enrollment_id: str, state: EnrollmentState, updated_at: datetime
    ) -> None:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_enrollment_jobs SET state=?, updated_at=? "
                    "WHERE enrollment_id=?",
                    (state.value, _format_time(updated_at), enrollment_id),
                )
                if cursor.rowcount != 1:
                    raise RegistryConflict("enrollment job does not exist")
        except RegistryConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

    def non_terminal_credential_references(self) -> set[str]:
        terminal = tuple(state.value for state in EnrollmentState if state.terminal)
        placeholders = ",".join("?" * len(terminal))
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT credential_temp_ref, old_credential_ref "
                    f"FROM agent_enrollment_jobs WHERE state NOT IN ({placeholders})",
                    terminal,
                ).fetchall()
            return {value for row in rows for value in row if value is not None}
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("enrollment journal storage is unavailable") from exc

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
        )

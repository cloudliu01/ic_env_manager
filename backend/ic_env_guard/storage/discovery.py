import sqlite3
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.discovery.models import (
    DiscoveryFingerprint,
    DiscoveryJob,
    DiscoveryResult,
    DiscoveryState,
    DiscoveryTarget,
)
from ic_env_guard.fleet.models import RegistryConflict, RegistryError
from ic_env_guard.storage.manager_registry import _format_time, _parse_time, _SQLiteRepository

_JOB_COLUMNS = (
    "job_id,scope_id,state,total_targets,checked_targets,found_targets,"
    "cancel_requested,safe_error_code,start_audit_event_id,deadline_at,"
    "created_at,updated_at,completed_at"
)


class DiscoveryRepository(_SQLiteRepository):
    def expire_deadlines(self, *, now: datetime) -> tuple[DiscoveryJob, ...]:
        try:
            with self._write() as connection:
                rows = connection.execute(
                    f"SELECT {_JOB_COLUMNS} FROM discovery_jobs "
                    "WHERE state IN ('queued','running') AND deadline_at<=?",
                    (_format_time(now),),
                ).fetchall()
                connection.execute(
                    "UPDATE discovery_jobs SET state='failed',safe_error_code='job_timeout',"
                    "updated_at=?,completed_at=? WHERE state IN ('queued','running') "
                    "AND deadline_at<=?",
                    (_format_time(now), _format_time(now), _format_time(now)),
                )
            return tuple(
                replace(
                    _job(row),
                    state=DiscoveryState.FAILED,
                    safe_error_code="job_timeout",
                    updated_at=now,
                    completed_at=now,
                )
                for row in rows
            )
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def create_job(self, job: DiscoveryJob, *, max_active_jobs: int = 32) -> DiscoveryJob:
        try:
            with self._write() as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM discovery_jobs WHERE state IN ('queued','running')"
                ).fetchone()[0]
                if active >= max_active_jobs:
                    raise RegistryConflict("discovery_capacity")
                connection.execute(
                    "INSERT INTO discovery_jobs(job_id,scope_id,state,total_targets,"
                    "checked_targets,found_targets,cancel_requested,safe_error_code,"
                    "start_audit_event_id,deadline_at,created_at,updated_at,completed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job.job_id,
                        job.scope_id,
                        job.state.value,
                        job.total_targets,
                        job.checked_targets,
                        job.found_targets,
                        int(job.cancel_requested),
                        job.safe_error_code,
                        job.start_audit_event_id,
                        _format_time(job.deadline_at),
                        _format_time(job.created_at),
                        _format_time(job.updated_at),
                        None,
                    ),
                )
            return job
        except RegistryConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def get_job(self, job_id: str) -> DiscoveryJob | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_JOB_COLUMNS} FROM discovery_jobs WHERE job_id=?",
                    (job_id,),
                ).first()
            return _job(row) if row else None
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def recover(
        self, *, now: datetime
    ) -> tuple[tuple[DiscoveryJob, ...], tuple[DiscoveryJob, ...]]:
        try:
            with self._write() as connection:
                cancelled_rows = connection.execute(
                    f"SELECT {_JOB_COLUMNS} FROM discovery_jobs "
                    "WHERE state IN ('queued','running') AND cancel_requested=1"
                ).fetchall()
                connection.execute(
                    "UPDATE discovery_jobs SET state='cancelled',updated_at=?,completed_at=? "
                    "WHERE state IN ('queued','running') AND cancel_requested=1",
                    (_format_time(now), _format_time(now)),
                )
                expired_rows = connection.execute(
                    f"SELECT {_JOB_COLUMNS} FROM discovery_jobs "
                    "WHERE state IN ('queued','running') AND cancel_requested=0 "
                    "AND deadline_at<=?",
                    (_format_time(now),),
                ).fetchall()
                connection.execute(
                    "UPDATE discovery_jobs SET state='queued',updated_at=? "
                    "WHERE state='running' AND cancel_requested=0 AND deadline_at>?",
                    (_format_time(now), _format_time(now)),
                )
                connection.execute(
                    "UPDATE discovery_jobs SET state='failed',safe_error_code='job_timeout',"
                    "updated_at=?,completed_at=? WHERE state IN ('queued','running') "
                    "AND cancel_requested=0 AND deadline_at<=?",
                    (_format_time(now), _format_time(now), _format_time(now)),
                )
                resumable_rows = connection.execute(
                    f"SELECT {_JOB_COLUMNS} FROM discovery_jobs WHERE state='queued' "
                    "AND cancel_requested=0 ORDER BY created_at"
                ).fetchall()
            return (
                tuple(_job(row) for row in resumable_rows),
                tuple(
                    replace(
                        _job(row),
                        state=DiscoveryState.CANCELLED,
                        updated_at=now,
                        completed_at=now,
                    )
                    for row in cancelled_rows
                )
                + tuple(
                    replace(
                        _job(row),
                        state=DiscoveryState.FAILED,
                        safe_error_code="job_timeout",
                        updated_at=now,
                        completed_at=now,
                    )
                    for row in expired_rows
                ),
            )
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def cleanup(self, *, retained_after: datetime) -> int:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "DELETE FROM discovery_jobs WHERE state IN "
                    "('completed','cancelled','failed') AND completed_at<? "
                    "AND EXISTS (SELECT 1 FROM control_plane_audit_events a "
                    "WHERE a.id=discovery_jobs.start_audit_event_id "
                    "AND a.operation='discovery.start' "
                    "AND a.target='discovery:' || discovery_jobs.scope_id "
                    "AND a.result IN ('success','failed')) "
                    "AND NOT EXISTS (SELECT 1 FROM discovery_results r "
                    "JOIN agent_enrollment_jobs e ON e.enrollment_id=r.linked_enrollment_id "
                    "WHERE r.job_id=discovery_jobs.job_id AND e.state NOT IN "
                    "('consumed','cancelled','failed','expired'))",
                    (_format_time(retained_after),),
                )
            return cursor.rowcount
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def terminal_jobs_with_pending_audit(self) -> tuple[DiscoveryJob, ...]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {','.join(f'j.{name}' for name in _JOB_COLUMNS.split(','))} "
                    "FROM discovery_jobs j JOIN control_plane_audit_events a "
                    "ON a.id=j.start_audit_event_id "
                    "WHERE j.state IN ('completed','cancelled','failed') "
                    "AND a.result='pending' AND a.operation='discovery.start' "
                    "AND a.target='discovery:' || j.scope_id ORDER BY j.created_at"
                ).fetchall()
            return tuple(_job(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def claim(self, job_id: str, *, now: datetime) -> DiscoveryJob | None:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE discovery_jobs SET state='running',updated_at=? "
                    "WHERE job_id=? AND state='queued' AND cancel_requested=0",
                    (_format_time(now), job_id),
                )
            return self.get_job(job_id) if cursor.rowcount == 1 else None
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def request_cancel(self, job_id: str, *, now: datetime) -> DiscoveryJob:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE discovery_jobs SET cancel_requested=1,updated_at=? "
                    "WHERE job_id=? AND state IN ('queued','running')",
                    (_format_time(now), job_id),
                )
                if cursor.rowcount != 1:
                    raise RegistryConflict("discovery_not_cancellable")
            job = self.get_job(job_id)
            assert job is not None
            return job
        except RegistryConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def record_result(
        self,
        job_id: str,
        target: DiscoveryTarget,
        fingerprint: DiscoveryFingerprint | None,
        safe_error_code: str | None,
        *,
        now: datetime,
    ) -> None:
        try:
            with self._write() as connection:
                connection.execute(
                    "INSERT INTO discovery_results(result_id,job_id,canonical_url,ip,port,"
                    "transport_profile_id,fingerprint_version,found,safe_error_code,"
                    "first_seen_at,last_seen_at,linked_enrollment_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(job_id,ip,port,transport_profile_id) DO UPDATE SET "
                    "fingerprint_version=excluded.fingerprint_version,found=excluded.found,"
                    "safe_error_code=excluded.safe_error_code,last_seen_at=excluded.last_seen_at",
                    (
                        str(uuid4()),
                        job_id,
                        target.canonical_url,
                        target.ip,
                        target.port,
                        target.transport_profile_id,
                        fingerprint.version if fingerprint else None,
                        int(fingerprint is not None),
                        safe_error_code,
                        _format_time(now),
                        _format_time(now),
                    ),
                )
                counts = connection.execute(
                    "SELECT COUNT(*),COALESCE(SUM(found),0) FROM discovery_results WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                connection.execute(
                    "UPDATE discovery_jobs SET checked_targets=?,found_targets=?,updated_at=? "
                    "WHERE job_id=? AND state='running'",
                    (counts[0], counts[1], _format_time(now), job_id),
                )
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def finish(
        self,
        job_id: str,
        state: DiscoveryState,
        *,
        now: datetime,
        safe_error_code: str | None = None,
    ) -> DiscoveryJob:
        try:
            with self._write() as connection:
                connection.execute(
                    "UPDATE discovery_jobs SET state=?,safe_error_code=?,updated_at=?,"
                    "completed_at=? WHERE job_id=? AND state IN ('queued','running')",
                    (
                        state.value,
                        safe_error_code,
                        _format_time(now),
                        _format_time(now),
                        job_id,
                    ),
                )
            job = self.get_job(job_id)
            assert job is not None
            return job
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def list_results(self, job_id: str) -> tuple[DiscoveryResult, ...]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT result_id,job_id,canonical_url,ip,port,transport_profile_id,"
                    "fingerprint_version,found,safe_error_code,first_seen_at,last_seen_at,"
                    "linked_enrollment_id FROM discovery_results WHERE job_id=? ORDER BY ip,port",
                    (job_id,),
                ).fetchall()
            return tuple(_result(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def get_result(self, result_id: str) -> DiscoveryResult | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT result_id,job_id,canonical_url,ip,port,transport_profile_id,"
                    "fingerprint_version,found,safe_error_code,first_seen_at,last_seen_at,"
                    "linked_enrollment_id FROM discovery_results WHERE result_id=?",
                    (result_id,),
                ).first()
            return _result(row) if row else None
        except (SQLAlchemyError, sqlite3.Error, ValueError) as exc:
            raise RegistryError("discovery storage unavailable") from exc

    def result_keys(self, job_id: str) -> set[tuple[str, int, str]]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT ip,port,transport_profile_id FROM discovery_results "
                    "WHERE job_id=?",
                    (job_id,),
                ).fetchall()
            return {(row[0], row[1], row[2]) for row in rows}
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("discovery storage unavailable") from exc


def _job(row) -> DiscoveryJob:
    return DiscoveryJob(
        job_id=row[0], scope_id=row[1], state=DiscoveryState(row[2]),
        total_targets=row[3], checked_targets=row[4], found_targets=row[5],
        cancel_requested=bool(row[6]), safe_error_code=row[7],
        start_audit_event_id=row[8], deadline_at=_parse_time(row[9]),
        created_at=_parse_time(row[10]), updated_at=_parse_time(row[11]),
        completed_at=_parse_time(row[12]) if row[12] else None,
    )


def _result(row) -> DiscoveryResult:
    return DiscoveryResult(
        result_id=row[0], job_id=row[1], canonical_url=row[2], ip=row[3], port=row[4],
        transport_profile_id=row[5], fingerprint_version=row[6], found=bool(row[7]),
        safe_error_code=row[8], first_seen_at=_parse_time(row[9]),
        last_seen_at=_parse_time(row[10]), linked_enrollment_id=row[11],
    )

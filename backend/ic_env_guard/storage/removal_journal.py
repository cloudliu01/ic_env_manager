import re
import sqlite3
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.fleet.models import (
    AgentRemovalJob,
    EnrollmentMethod,
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

_COLUMNS = (
    "removal_id, agent_id, captured_revision, credential_ref, remote_credential_id, "
    "normalized_endpoint, transport_profile_id, enrollment_method, phase, local_only, "
    "audit_event_id, last_error_code, created_at, updated_at"
)
_PHASES = {
    "pending",
    "revoking",
    "revoked",
    "registry_deleted",
    "credential_deleted",
    "completed",
    "residual",
}
_REF = re.compile(r"^[0-9a-f]{48}$")


def _removal(row) -> AgentRemovalJob:
    return AgentRemovalJob(
        removal_id=row[0],
        agent_id=row[1],
        captured_revision=row[2],
        credential_ref=row[3],
        remote_credential_id=row[4],
        normalized_endpoint=row[5],
        transport_profile_id=row[6],
        enrollment_method=EnrollmentMethod(row[7]),
        phase=row[8],
        local_only=bool(row[9]),
        audit_event_id=row[10],
        last_error_code=row[11],
        created_at=_parse_time(row[12]),
        updated_at=_parse_time(row[13]),
    )


class AgentRemovalRepository(_SQLiteRepository):
    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)

    def create_for_agent(
        self,
        agent_id: str,
        *,
        audit_event_id: int,
        local_only: bool,
        now: datetime,
    ) -> AgentRemovalJob:
        if audit_event_id < 1:
            raise RegistryInvariantError("removal audit event is invalid")
        try:
            with self._write() as connection:
                audit = connection.execute(
                    "SELECT 1 FROM control_plane_audit_events WHERE id=? AND result='pending' "
                    "AND agent_id=? AND operation='agents.v2.remove'",
                    (audit_event_id, agent_id),
                ).fetchone()
                if audit is None:
                    raise RegistryConflict("agent_removal_audit_invalid")
                row = connection.execute(
                    f"SELECT {_AGENT_COLUMNS} FROM agents WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
                if row is None:
                    raise RegistryConflict("agent_not_found")
                agent = _agent(row)
                job = AgentRemovalJob(
                    removal_id=str(uuid4()),
                    agent_id=agent.agent_id,
                    captured_revision=agent.revision,
                    credential_ref=agent.credential_ref,
                    remote_credential_id=agent.remote_credential_id,
                    normalized_endpoint=agent.normalized_endpoint,
                    transport_profile_id=agent.transport_profile_id,
                    enrollment_method=agent.enrollment_method,
                    phase="pending",
                    local_only=local_only,
                    audit_event_id=audit_event_id,
                    last_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
                self._validate(job)
                connection.execute(
                    f"INSERT INTO agent_removal_jobs ({_COLUMNS}) "
                    f"VALUES ({','.join('?' * 14)})",
                    self._values(job),
                )
            return job
        except RegistryConflict:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("agent_removal_in_progress") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def get(self, removal_id: str) -> AgentRemovalJob | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_removal_jobs WHERE removal_id=?",
                    (removal_id,),
                ).first()
            return _removal(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def list_recoverable(self) -> tuple[AgentRemovalJob, ...]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM agent_removal_jobs AS removal "
                    "WHERE phase!='completed' OR EXISTS ("
                    "SELECT 1 FROM control_plane_audit_events AS audit "
                    "WHERE audit.id=removal.audit_event_id AND audit.result='pending') "
                    "ORDER BY created_at, removal_id"
                ).fetchall()
            return tuple(_removal(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def blocks_usage(self, agent_id: str) -> bool:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT 1 FROM agent_removal_jobs WHERE agent_id=? "
                    "AND phase!='completed' LIMIT 1",
                    (agent_id,),
                ).first()
            return row is not None
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def audit_is_recoverable(self, audit_event_id: int) -> bool:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT phase, last_error_code FROM agent_removal_jobs "
                    "WHERE audit_event_id=? ORDER BY created_at DESC LIMIT 1",
                    (audit_event_id,),
                ).first()
            return row is not None and row[0] != "completed" and row[1] not in {
                "agent_changed",
                "agent_credential_unavailable",
                "agent_not_found",
            }
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def transition(
        self,
        job: AgentRemovalJob,
        phase: str,
        *,
        now: datetime,
        last_error_code: str | None = None,
    ) -> AgentRemovalJob:
        updated = replace(
            job,
            phase=phase,
            last_error_code=last_error_code,
            updated_at=now,
        )
        self._validate(updated)
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agent_removal_jobs SET phase=?, last_error_code=?, updated_at=? "
                    "WHERE removal_id=? AND phase=?",
                    (
                        phase,
                        last_error_code,
                        _format_time(now),
                        job.removal_id,
                        job.phase,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("agent removal phase changed")
            return updated
        except RevisionConflict:
            raise
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal journal is unavailable") from exc

    def finalize_audit_if_pending(
        self, job: AgentRemovalJob, *, success: bool
    ) -> bool:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE control_plane_audit_events SET result=?, dispatch_state=?, "
                    "failure_category=? WHERE id=? AND result='pending'",
                    (
                        "success" if success else "failed",
                        (
                            "not_dispatched"
                            if job.local_only
                            or job.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN
                            else "dispatched"
                        ),
                        "remote_credential_residual" if job.local_only else None,
                        job.audit_event_id,
                    ),
                )
            return cursor.rowcount == 1
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal audit is unavailable") from exc

    def finalize_orphaned_pending_audits(self) -> int:
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE control_plane_audit_events SET result='failed', "
                    "dispatch_state='not_dispatched', "
                    "failure_category='agent_removal_interrupted' "
                    "WHERE operation='agents.v2.remove' AND result='pending' "
                    "AND NOT EXISTS (SELECT 1 FROM agent_removal_jobs AS removal "
                    "WHERE removal.audit_event_id=control_plane_audit_events.id)"
                )
            return cursor.rowcount
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent removal audit is unavailable") from exc

    @staticmethod
    def _validate(job: AgentRemovalJob) -> None:
        if job.phase not in _PHASES or not _REF.fullmatch(job.credential_ref):
            raise RegistryInvariantError("agent removal record is invalid")
        if job.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN and (
            job.remote_credential_id is None
        ):
            raise RegistryInvariantError("managed Agent removal requires remote credential")

    @staticmethod
    def _values(job: AgentRemovalJob) -> tuple[object, ...]:
        return (
            job.removal_id,
            job.agent_id,
            job.captured_revision,
            job.credential_ref,
            job.remote_credential_id,
            job.normalized_endpoint,
            job.transport_profile_id,
            job.enrollment_method.value,
            job.phase,
            int(job.local_only),
            job.audit_event_id,
            job.last_error_code,
            _format_time(job.created_at),
            _format_time(job.updated_at),
        )

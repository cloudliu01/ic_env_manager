from sqlalchemy.orm import sessionmaker

from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)


class AgentEnrollmentAudit:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def record_intent(
        self, *, operation: str, actor_id: str, credential_id: str | None
    ) -> None:
        with self._session_factory() as session:
            AuditRepository(session).add(
                AuditEventCreate(
                    actor_id=actor_id,
                    operation=f"{operation}.intent",
                    target_type="manager_credential",
                    target_id=credential_id,
                    result="success",
                )
            )
            session.commit()

    def record_outcome(
        self, *, operation: str, actor_id: str, credential_id: str | None, result: str
    ) -> None:
        with self._session_factory() as session:
            AuditRepository(session).add(
                AuditEventCreate(
                    actor_id=actor_id,
                    operation=f"{operation}.outcome",
                    target_type="manager_credential",
                    target_id=credential_id,
                    result=result,  # type: ignore[arg-type]
                )
            )
            session.commit()


class ManagerAutoEnrollmentAudit:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def record_intent(self, enrollment_id: str, context) -> int:
        return self._record_intent(enrollment_id, context, "agent-enrollment.ssh-auto")

    def record_cli_intent(self, enrollment_id: str, context) -> int:
        return self._record_intent(enrollment_id, context, "agent-enrollment.ssh-cli")

    def _record_intent(self, enrollment_id: str, context, operation: str) -> int:
        with self._session_factory() as session:
            repository = ControlPlaneAuditRepository(session)
            row = repository.record_intent(
                ControlPlaneAuditEventCreate(
                    actor_id=context.actor_id,
                    source_addr=context.source_addr,
                    agent_id=None,
                    operation=operation,
                    target=f"enrollment:{enrollment_id}",
                    correlation_id=context.correlation_id,
                )
            )
            session.commit()
            return row.id

    def record_outcome(
        self,
        event_id: int,
        *,
        result: str,
        dispatch_state: str,
        failure_category: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            ControlPlaneAuditRepository(session).finalize(
                event_id,
                result=result,
                dispatch_state=dispatch_state,
                failure_category=failure_category,
            )
            session.commit()

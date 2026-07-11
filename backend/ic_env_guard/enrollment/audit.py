from sqlalchemy.orm import sessionmaker

from ic_env_guard.db.audit import AuditEventCreate, AuditRepository


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
        try:
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
        except Exception:
            # The credential mutation already happened. Do not invite a dangerous retry.
            return

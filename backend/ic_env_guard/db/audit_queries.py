from datetime import datetime

from ic_env_guard.db.audit import AuditRepository


class AuditQueryRepository:
    def __init__(self, audit_repository: AuditRepository) -> None:
        self.audit_repository = audit_repository

    def list_events(
        self,
        *,
        limit: int = 100,
        target_type: str | None = None,
        result: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, object]]:
        rows = self.audit_repository.session.execute(
            self.audit_repository.query(
                limit=limit,
                target_type=target_type,
                result=result,
                since=since,
            )
        ).scalars()
        return [row.to_safe_dict() for row in rows]

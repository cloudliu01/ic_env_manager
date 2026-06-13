from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.audit_queries import AuditQueryRepository

router = APIRouter(prefix="/api/audit", tags=["audit"])


def get_audit_query_repository() -> AuditQueryRepository:
    raise RuntimeError("AuditQueryRepository dependency was not configured")


def audit_authorization_failure(
    repo: AuditRepository,
    operation: str,
    target_type: str,
    target_id: str | None = None,
    source_addr: str | None = None,
) -> None:
    repo.add(
        AuditEventCreate(
            source_addr=source_addr,
            operation=operation,
            target_type=target_type,
            target_id=target_id,
            result="denied",
            failure_reason="authorization failed",
        )
    )


@router.get("")
def list_audit_events(
    _: Annotated[AuthContext, Depends(require_auth)],
    repo: Annotated[AuditQueryRepository, Depends(get_audit_query_repository)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    target_type: str | None = None,
    result: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    return {"events": repo.list_events(limit=limit, target_type=target_type, result=result)}

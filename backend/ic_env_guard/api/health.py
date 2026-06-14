from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> JSONResponse:
    ready = audit_health.healthy
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "degraded",
            "config_loaded": True,
            "security_valid": True,
            "audit_storage": "ok" if ready else "unavailable",
        },
    )

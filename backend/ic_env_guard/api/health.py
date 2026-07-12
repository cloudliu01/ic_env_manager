from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.api.runtime import RuntimeMetadata, get_runtime_metadata

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(
    metadata: Annotated[RuntimeMetadata, Depends(get_runtime_metadata)],
) -> JSONResponse:
    headers = {"X-IC-Env-Guard-Agent": "2"} if metadata.mode == "agent" else {}
    return JSONResponse(content={"status": "ok"}, headers=headers)


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

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ic_env_guard.agents.models import V2CapabilityResponse
from ic_env_guard.api.errors import ApiError
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, AuthState, get_auth_state

router = APIRouter(prefix="/api/v2", tags=["runtime"])
_AUTH_SCHEME = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class RuntimeMetadata:
    mode: Literal["agent", "manager"]
    capabilities: tuple[str, ...]
    instance_id: UUID | None = None
    name: str | None = None
    agent_capabilities: tuple[str, ...] = ()


def get_runtime_metadata() -> RuntimeMetadata:
    raise RuntimeError("RuntimeMetadata dependency was not configured")


def require_v2_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_AUTH_SCHEME),
    auth_state: AuthState = Depends(get_auth_state),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise V2ApiError(401, "unauthorized", "missing bearer token")
    try:
        return auth_state.authenticate(credentials.credentials, allow_pending=True)
    except ApiError as exc:
        raise V2ApiError(401, "unauthorized", "invalid bearer token") from exc


@router.get("/runtime")
def runtime(metadata: Annotated[RuntimeMetadata, Depends(get_runtime_metadata)]) -> JSONResponse:
    return JSONResponse(
        {"mode": metadata.mode, "capabilities": list(metadata.capabilities)},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/capabilities")
def capabilities(
    metadata: Annotated[RuntimeMetadata, Depends(get_runtime_metadata)],
    _: Annotated[AuthContext, Depends(require_v2_auth)],
) -> JSONResponse:
    if metadata.mode != "agent" or metadata.instance_id is None or metadata.name is None:
        raise V2ApiError(404, "not_found", "capabilities are not available")
    content = V2CapabilityResponse(
        instance_id=metadata.instance_id,
        name=metadata.name,
        capabilities=metadata.agent_capabilities,
    ).to_dict()
    return JSONResponse(content, headers={"Cache-Control": "no-store"})

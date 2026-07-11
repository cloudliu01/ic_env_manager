from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from ic_env_guard.api.errors import ApiError
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, AuthState, get_auth_state
from ic_env_guard.enrollment.models import (
    CredentialNotFound,
    CredentialStorageError,
    EnrollmentForbidden,
)
from ic_env_guard.enrollment.service import EnrollmentService

router = APIRouter(prefix="/api/v2/manager-credentials", tags=["manager-credentials"])
_AUTH_SCHEME = HTTPBearer(auto_error=False)


class ActivateCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enrollment_id: str = Field(min_length=1, max_length=128)


def get_enrollment_service() -> EnrollmentService:
    raise RuntimeError("EnrollmentService dependency was not configured")


def _map_error(exc: Exception) -> V2ApiError:
    if isinstance(exc, EnrollmentForbidden):
        return V2ApiError(403, "forbidden", "credential operation is forbidden")
    if isinstance(exc, CredentialNotFound):
        return V2ApiError(404, "credential_not_found", "credential not found")
    if isinstance(exc, CredentialStorageError):
        return V2ApiError(503, "storage_unavailable", "credential storage is unavailable")
    return V2ApiError(422, "validation_error", "request validation failed")


@router.get("")
def list_credentials(
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[EnrollmentService, Depends(get_enrollment_service)],
) -> dict[str, list[dict[str, str | None]]]:
    try:
        records = service.list(actor_id=actor.actor_id)
    except (EnrollmentForbidden, CredentialStorageError) as exc:
        raise _map_error(exc) from exc
    return {"credentials": [record.safe_dict() for record in records]}


@router.post("/{credential_id}/activate")
def activate_credential(
    credential_id: str,
    body: ActivateCredentialRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_AUTH_SCHEME)],
    service: Annotated[EnrollmentService, Depends(get_enrollment_service)],
) -> dict[str, str | None]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise V2ApiError(401, "unauthorized", "missing bearer token")
    try:
        return service.activate(
            credential_id, body.enrollment_id, credentials.credentials
        ).safe_dict()
    except (EnrollmentForbidden, CredentialNotFound, CredentialStorageError, ValueError) as exc:
        raise _map_error(exc) from exc


@router.delete("/{credential_id}")
def revoke_credential(
    credential_id: str,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_AUTH_SCHEME)],
    auth_state: Annotated[AuthState, Depends(get_auth_state)],
    service: Annotated[EnrollmentService, Depends(get_enrollment_service)],
) -> dict[str, str]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise V2ApiError(401, "unauthorized", "missing bearer token")
    try:
        actor = auth_state.authenticate(credentials.credentials)
    except ApiError:
        revoked_context = service.authenticate_revoked_for_revoke(
            credentials.credentials, credential_id
        )
        if revoked_context is None:
            raise V2ApiError(401, "unauthorized", "invalid bearer token") from None
        actor = AuthContext(
            revoked_context.actor_id,
            manager_id=revoked_context.manager_id,
            credential_id=revoked_context.credential_id,
        )
    try:
        record = service.revoke(
            credential_id,
            actor_id=actor.actor_id,
            manager_id=actor.manager_id,
        )
    except (EnrollmentForbidden, CredentialNotFound, CredentialStorageError, ValueError) as exc:
        raise _map_error(exc) from exc
    return {"credential_id": record.credential_id, "state": record.state.value}

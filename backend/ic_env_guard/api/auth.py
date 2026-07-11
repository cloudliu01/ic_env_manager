from json import JSONDecodeError
from typing import Protocol

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import sessionmaker

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, AuthState, get_auth_state, require_auth
from ic_env_guard.auth.rate_limit import LoginRateLimiter
from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


class LoginResponse(BaseModel):
    actor: str
    token_type: str = "bearer"


class LoginAuditRecorder(Protocol):
    def record_success(
        self, actor_id: str, source_addr: str | None, correlation_id: str | None
    ) -> None: ...

    def record_failure(
        self, source_addr: str | None, correlation_id: str | None, reason: str
    ) -> None: ...


class AgentLoginAuditRecorder:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def record_success(
        self, actor_id: str, source_addr: str | None, correlation_id: str | None
    ) -> None:
        with self._session_factory() as session:
            AuditRepository(session).add(
                AuditEventCreate(
                    actor_id=actor_id,
                    source_addr=source_addr,
                    operation="auth.login",
                    target_type="auth",
                    result="success",
                    correlation_id=correlation_id,
                )
            )
            session.commit()

    def record_failure(
        self, source_addr: str | None, correlation_id: str | None, reason: str
    ) -> None:
        with self._session_factory() as session:
            AuditRepository(session).add(
                AuditEventCreate(
                    source_addr=source_addr,
                    operation="auth.login",
                    target_type="auth",
                    result="denied",
                    failure_reason=reason,
                    correlation_id=correlation_id,
                )
            )
            session.commit()


class ManagerLoginAuditRecorder:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def _record(
        self,
        *,
        actor_id: str | None,
        source_addr: str | None,
        correlation_id: str | None,
        result: str,
        failure_category: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            repository = ControlPlaneAuditRepository(session)
            event = repository.record_intent(
                ControlPlaneAuditEventCreate(
                    actor_id=actor_id,
                    source_addr=source_addr,
                    agent_id=None,
                    operation="auth.login",
                    target="manager",
                    correlation_id=correlation_id,
                )
            )
            repository.finalize(
                event.id,
                result=result,
                dispatch_state="not_dispatched",
                failure_category=failure_category,
            )
            session.commit()

    def record_success(
        self, actor_id: str, source_addr: str | None, correlation_id: str | None
    ) -> None:
        self._record(
            actor_id=actor_id,
            source_addr=source_addr,
            correlation_id=correlation_id,
            result="success",
        )

    def record_failure(
        self, source_addr: str | None, correlation_id: str | None, reason: str
    ) -> None:
        self._record(
            actor_id=None,
            source_addr=source_addr,
            correlation_id=correlation_id,
            result="denied",
            failure_category=reason,
        )


def get_login_rate_limiter() -> LoginRateLimiter:
    raise RuntimeError("LoginRateLimiter dependency was not configured")


def get_login_audit_recorder() -> LoginAuditRecorder:
    raise RuntimeError("LoginAuditRecorder dependency was not configured")


def _has_json_content_type(request: Request) -> bool:
    content_type = request.headers.get("content-type")
    if content_type is None:
        return True
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    auth_state: AuthState = Depends(get_auth_state),
    limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
    audit: LoginAuditRecorder = Depends(get_login_audit_recorder),
) -> LoginResponse:
    source_addr = request.client.host if request.client else None
    correlation_id = getattr(request.state, "correlation_id", None)
    if not limiter.allow(source_addr or "<unknown>"):
        audit.record_failure(source_addr, correlation_id, "rate_limited")
        raise ApiError(429, "too_many_login_attempts", "too many login attempts")
    if not _has_json_content_type(request):
        audit.record_failure(source_addr, correlation_id, "invalid_request")
        raise RequestValidationError(
            [
                {
                    "type": "model_attributes_type",
                    "loc": ("body",),
                    "msg": "Input should be a valid dictionary or object",
                }
            ]
        )
    try:
        submitted = await request.json()
        payload = LoginRequest.model_validate(submitted)
    except (JSONDecodeError, UnicodeDecodeError):
        audit.record_failure(source_addr, correlation_id, "invalid_request")
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "JSON decode error"}]
        ) from None
    except PydanticValidationError as exc:
        audit.record_failure(source_addr, correlation_id, "invalid_request")
        safe_errors = [
            {
                "type": error["type"],
                "loc": ("body", *error["loc"]),
                "msg": error["msg"],
            }
            for error in exc.errors()
        ]
        raise RequestValidationError(safe_errors) from None
    try:
        context = auth_state.authenticate(payload.token)
    except ApiError:
        audit.record_failure(source_addr, correlation_id, "invalid_credentials")
        raise ApiError(401, "unauthorized", "invalid bearer token") from None
    audit.record_success(context.actor_id, source_addr, correlation_id)
    return LoginResponse(actor=context.actor_id)


@router.post("/logout", status_code=204)
def logout(_: AuthContext = Depends(require_auth)) -> Response:
    return Response(status_code=204)

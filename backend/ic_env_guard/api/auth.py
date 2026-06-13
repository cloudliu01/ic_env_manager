from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, AuthState, get_auth_state, require_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


class LoginResponse(BaseModel):
    actor: str
    token_type: str = "bearer"


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, auth_state: AuthState = Depends(get_auth_state)) -> LoginResponse:
    try:
        context = auth_state.authenticate(payload.token)
    except ApiError:
        raise ApiError(401, "unauthorized", "invalid bearer token") from None
    return LoginResponse(actor=context.actor_id)


@router.post("/logout", status_code=204)
def logout(_: AuthContext = Depends(require_auth)) -> Response:
    return Response(status_code=204)

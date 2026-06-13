from pathlib import Path

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.token import (
    BearerTokenValidator,
    load_bearer_token,
    validate_token_file_permissions,
)

_AUTH_SCHEME = HTTPBearer(auto_error=False)


class AuthContext:
    def __init__(self, actor_id: str = "local-admin") -> None:
        self.actor_id = actor_id


class AuthState:
    def __init__(self, token_file: Path | None = None, token: str | None = None) -> None:
        if token is None and token_file is not None:
            validate_token_file_permissions(token_file)
            token = load_bearer_token(token_file)
        if not token:
            raise ValueError("valid bearer token configuration is required")
        self.validator = BearerTokenValidator(token)

    def authenticate(self, token: str) -> AuthContext:
        if not self.validator.validate(token):
            raise ApiError(401, "unauthorized", "invalid bearer token")
        return AuthContext()


def get_auth_state() -> AuthState:
    raise RuntimeError("AuthState dependency was not configured")


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_AUTH_SCHEME),
    auth_state: AuthState = Depends(get_auth_state),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "unauthorized", "missing bearer token")
    return auth_state.authenticate(credentials.credentials)

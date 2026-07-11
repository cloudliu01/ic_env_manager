from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.token import (
    BearerTokenValidator,
    load_bearer_token,
    validate_token_file_permissions,
)

_AUTH_SCHEME = HTTPBearer(auto_error=False)


class CredentialAuthContext(Protocol):
    actor_id: str
    manager_id: str
    credential_id: str
    state: object


class ManagerCredentialVerifier(Protocol):
    def authenticate(
        self, token: str, now: datetime | None = None
    ) -> CredentialAuthContext | None: ...


class AuthContext:
    def __init__(
        self,
        actor_id: str = "local-admin",
        *,
        manager_id: str | None = None,
        credential_id: str | None = None,
        pending: bool = False,
    ) -> None:
        self.actor_id = actor_id
        self.manager_id = manager_id
        self.credential_id = credential_id
        self.pending = pending


class AuthState:
    def __init__(
        self,
        token_file: Path | None = None,
        token: str | None = None,
        manager_verifier: ManagerCredentialVerifier | None = None,
    ) -> None:
        if token is None and token_file is not None:
            validate_token_file_permissions(token_file)
            token = load_bearer_token(token_file)
        if not token:
            raise ValueError("valid bearer token configuration is required")
        self.validator = BearerTokenValidator(token)
        self.manager_verifier = manager_verifier

    def authenticate(self, token: str, *, allow_pending: bool = False) -> AuthContext:
        if self.validator.validate(token):
            return AuthContext()
        if self.manager_verifier is not None:
            credential = self.manager_verifier.authenticate(token, datetime.now(UTC))
            if credential is not None:
                pending = getattr(credential.state, "value", credential.state) == "pending"
                if not pending or allow_pending:
                    return AuthContext(
                        credential.actor_id,
                        manager_id=credential.manager_id,
                        credential_id=credential.credential_id,
                        pending=pending,
                    )
        raise ApiError(401, "unauthorized", "invalid bearer token")


def get_auth_state() -> AuthState:
    raise RuntimeError("AuthState dependency was not configured")


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_AUTH_SCHEME),
    auth_state: AuthState = Depends(get_auth_state),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "unauthorized", "missing bearer token")
    return auth_state.authenticate(credentials.credentials)

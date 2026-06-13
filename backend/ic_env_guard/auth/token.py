import secrets
import stat
from pathlib import Path

from pydantic import BaseModel, Field


class TokenConfig(BaseModel):
    token_file: Path = Field(..., description="Path to generated local bearer token file")


def redact_token(value: str | None) -> str | None:
    if value is None:
        return None
    return "<redacted>"


def load_bearer_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("bearer token file is empty")
    return token


def validate_token_file_permissions(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("bearer token file must not be readable, writable, or executable by group/other")


def generate_bearer_token() -> str:
    return secrets.token_urlsafe(32)


class BearerTokenValidator:
    def __init__(self, token: str) -> None:
        self._token = token

    def validate(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate, self._token)

from collections.abc import Mapping
from typing import Any

SECRET_KEYS = {
    "token",
    "password",
    "passwd",
    "secret",
    "private_key",
    "bearer",
    "authorization",
    "credential",
}


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(secret in lowered for secret in SECRET_KEYS)


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "<redacted>" if is_secret_key(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def bounded_text(value: str | None, max_length: int = 2048) -> str | None:
    if value is None:
        return None
    if len(value) <= max_length:
        return value
    return value[: max_length - 15] + "<truncated>"

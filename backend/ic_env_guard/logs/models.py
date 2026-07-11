import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

_LOG_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class LogError(Exception):
    pass


class LogPathForbidden(LogError):
    pass


class LogFileUnavailable(LogError):
    pass


class LogSourceConflict(LogError):
    pass


class LogSourceExpired(LogError):
    pass


class LogStorageError(LogError):
    pass


def aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def validate_log_id(value: str) -> str:
    if not _LOG_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid log id")
    return value


def _timestamp(value: datetime | str, field: str) -> datetime:
    if isinstance(value, str):
        if not _RFC3339_PATTERN.fullmatch(value):
            raise ValueError(f"{field} must be RFC3339")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be RFC3339") from exc
    return aware_utc(value, field)


class LogSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    last_updated: datetime
    observed_at: datetime
    ttl_seconds: int = Field(ge=1, le=604800)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("path must not contain NUL")
        if not Path(value).is_absolute():
            raise ValueError("path must be absolute")
        return value

    @field_validator("last_updated", "observed_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value: datetime | str, info) -> datetime:
        return _timestamp(value, info.field_name)


@dataclass(frozen=True)
class LogSource:
    id: str
    path: Path
    last_updated: datetime
    observed_at: datetime
    ttl_seconds: int
    received_at: datetime
    expires_at: datetime
    producer_id: str
    updated_at: datetime

    @classmethod
    def reconstitute(
        cls,
        *,
        id: str,
        path: str,
        last_updated: datetime,
        observed_at: datetime,
        received_at: datetime,
        expires_at: datetime,
        producer_id: str,
        updated_at: datetime,
    ) -> "LogSource":
        validate_log_id(id)
        if os.path.normpath(path) != path:
            raise ValueError("stored log source path must be normalized")
        normalized_observed = aware_utc(observed_at, "observed_at")
        normalized_expires = aware_utc(expires_at, "expires_at")
        ttl = (normalized_expires - normalized_observed).total_seconds()
        if not ttl.is_integer():
            raise ValueError("stored log source TTL must be whole seconds")
        payload = LogSourceInput.model_validate(
            {
                "path": path,
                "last_updated": aware_utc(last_updated, "last_updated"),
                "observed_at": normalized_observed,
                "ttl_seconds": int(ttl),
            }
        )
        if producer_id != "local":
            raise ValueError("stored log source producer must be local")
        return cls(
            id=id,
            path=Path(payload.path),
            last_updated=payload.last_updated,
            observed_at=payload.observed_at,
            ttl_seconds=payload.ttl_seconds,
            received_at=aware_utc(received_at, "received_at"),
            expires_at=normalized_expires,
            producer_id=producer_id,
            updated_at=aware_utc(updated_at, "updated_at"),
        )

    def is_stale(self, now: datetime) -> bool:
        return aware_utc(now, "now") >= self.expires_at

    def normalized_payload(self) -> tuple[object, ...]:
        return (
            str(self.path),
            self.last_updated,
            self.observed_at,
            self.ttl_seconds,
        )


@dataclass(frozen=True)
class LogUpsertResult:
    record: LogSource
    created: bool


@dataclass(frozen=True)
class TailResult:
    lines: tuple[str, ...]
    truncated: bool

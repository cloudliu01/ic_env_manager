import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ObservationKind = Literal["gauge", "counter", "status"]
ObservationStatus = Literal["ok", "warning", "critical", "unknown"]

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,126}$")
_PROMETHEUS_RESERVED_LABELS = frozenset({"name", "namespace", "status"})
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_MAX_DETAILS_BYTES = 16 * 1024
_MAX_DETAILS_DEPTH = 4


class ObservationError(Exception):
    pass


class ObservationConflict(ObservationError):
    pass


class ObservationExpired(ObservationError):
    pass


class ObservationStorageError(ObservationError):
    pass


class ObservationSeriesLimit(ObservationError):
    pass


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_json(value: Any, depth: int) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if _utf8_size(value) > 4096:
            raise ValueError("details string exceeds 4096 UTF-8 bytes")
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("details numbers must be finite")
        return
    if isinstance(value, list):
        if depth > _MAX_DETAILS_DEPTH:
            raise ValueError("details nesting exceeds 4")
        for item in value:
            _validate_json(item, depth + 1)
        return
    if isinstance(value, dict):
        if depth > _MAX_DETAILS_DEPTH:
            raise ValueError("details nesting exceeds 4")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("details keys must be strings")
            if _utf8_size(key) > 64:
                raise ValueError("details key exceeds 64 UTF-8 bytes")
            _validate_json(item, depth + 1)
        return
    raise ValueError("details must contain only JSON values")


def compact_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    )


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    namespace: str
    name: str
    kind: ObservationKind
    value: int | float | None = None
    unit: str | None = Field(default=None, max_length=32)
    status: ObservationStatus
    message: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime
    ttl_seconds: int = Field(ge=1, le=604800)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if not _NAMESPACE_PATTERN.fullmatch(value):
            raise ValueError("invalid namespace")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid name")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str | None) -> str | None:
        if value is not None and _utf8_size(value) > 2048:
            raise ValueError("message exceeds 2048 UTF-8 bytes")
        return value

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("labels exceed 16 entries")
        for key, item in value.items():
            if key in _PROMETHEUS_RESERVED_LABELS:
                raise ValueError(f"label key '{key}' is reserved")
            if not _NAMESPACE_PATTERN.fullmatch(key):
                raise ValueError("invalid label key")
            if _utf8_size(item) > 128:
                raise ValueError("label value exceeds 128 UTF-8 bytes")
        return dict(sorted(value.items()))

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json(value, 1)
        if _utf8_size(compact_json(value)) > _MAX_DETAILS_BYTES:
            raise ValueError("details exceed 16 KiB")
        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def validate_observed_at(cls, value: datetime | str) -> datetime:
        if isinstance(value, str):
            if not _RFC3339_PATTERN.fullmatch(value):
                raise ValueError("observed_at must be RFC3339")
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("observed_at must be RFC3339") from exc
        return _aware_utc(value, "observed_at")

    @model_validator(mode="after")
    def validate_value(self) -> "ObservationInput":
        if self.kind in ("gauge", "counter") and self.value is None:
            raise ValueError(f"{self.kind} requires a numeric value")
        if self.value is not None:
            if isinstance(self.value, bool) or not math.isfinite(self.value):
                raise ValueError("value must be a finite number")
        return self

    def identity_key(self) -> str:
        canonical = compact_json(
            [self.namespace, self.name, self.labels], sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class Observation:
    identity_key: str
    namespace: str
    name: str
    kind: ObservationKind
    value: int | float | None
    unit: str | None
    status: ObservationStatus
    message: str | None
    labels: dict[str, str]
    details: dict[str, Any]
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
        identity_key: str,
        namespace: str,
        name: str,
        kind: str,
        value: int | float | None,
        unit: str | None,
        status: str,
        message: str | None,
        labels: Any,
        details: Any,
        observed_at: datetime,
        received_at: datetime,
        expires_at: datetime,
        producer_id: str,
        updated_at: datetime,
    ) -> "Observation":
        normalized_observed_at = _aware_utc(observed_at, "observed_at")
        normalized_received_at = _aware_utc(received_at, "received_at")
        normalized_expires_at = _aware_utc(expires_at, "expires_at")
        normalized_updated_at = _aware_utc(updated_at, "updated_at")
        ttl_value = (normalized_expires_at - normalized_observed_at).total_seconds()
        if not ttl_value.is_integer():
            raise ValueError("stored observation TTL must be whole seconds")
        payload = ObservationInput.model_validate(
            {
                "namespace": namespace,
                "name": name,
                "kind": kind,
                "value": value,
                "unit": unit,
                "status": status,
                "message": message,
                "labels": labels,
                "details": details,
                "observed_at": normalized_observed_at,
                "ttl_seconds": int(ttl_value),
            }
        )
        if producer_id != "local":
            raise ValueError("stored observation producer must be local")
        if identity_key != payload.identity_key():
            raise ValueError("stored observation identity mismatch")
        return cls(
            identity_key=identity_key,
            namespace=payload.namespace,
            name=payload.name,
            kind=payload.kind,
            value=payload.value,
            unit=payload.unit,
            status=payload.status,
            message=payload.message,
            labels=payload.labels,
            details=payload.details,
            observed_at=payload.observed_at,
            ttl_seconds=payload.ttl_seconds,
            received_at=normalized_received_at,
            expires_at=normalized_expires_at,
            producer_id=producer_id,
            updated_at=normalized_updated_at,
        )

    def is_stale(self, now: datetime) -> bool:
        return now.astimezone(UTC) >= self.expires_at

    def normalized_payload(self) -> tuple[Any, ...]:
        return (
            self.namespace,
            self.name,
            self.kind,
            self.value,
            self.unit,
            self.status,
            self.message,
            compact_json(self.labels, sort_keys=True),
            compact_json(self.details, sort_keys=True),
            self.observed_at,
            self.ttl_seconds,
        )


@dataclass(frozen=True)
class ObservationQuery:
    namespace: str | None = None
    name: str | None = None
    status: ObservationStatus | None = None
    include_stale: bool = False
    limit: int = 100
    cursor: str | None = None
    now: datetime | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")


@dataclass(frozen=True)
class ObservationPage:
    items: tuple[Observation, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class UpsertResult:
    record: Observation
    created: bool

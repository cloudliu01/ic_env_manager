import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)

from ic_env_guard.enrollment.models import valid_enrollment_id

PROTOCOL = "manager-enrollment.v1"
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 8192
MIN_PENDING_CREDENTIAL_TTL_SECONDS = 60
DEFAULT_PENDING_CREDENTIAL_TTL_SECONDS = 600
MAX_PENDING_CREDENTIAL_TTL_SECONDS = 900


class EnrollmentProtocolError(ValueError):
    pass


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["manager-enrollment.v1"]
    manager_id: UUID
    enrollment_id: str = Field(min_length=1, max_length=128)

    @field_validator("manager_id", mode="before")
    @classmethod
    def canonical_manager_id(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("manager_id must be a canonical lowercase UUID")
        try:
            canonical = str(UUID(value))
        except ValueError as exc:
            raise ValueError("manager_id must be a canonical lowercase UUID") from exc
        if value != canonical:
            raise ValueError("manager_id must be a canonical lowercase UUID")
        return value

    @field_validator("enrollment_id")
    @classmethod
    def safe_enrollment_id(cls, value: str) -> str:
        return valid_enrollment_id(value)

class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["manager-enrollment.v1"]
    instance_id: UUID
    credential_id: UUID
    token: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def timezone_aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_serializer("expires_at")
    def serialize_expiry(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_request(payload: bytes) -> EnrollmentRequest:
    if len(payload) > MAX_REQUEST_BYTES:
        raise EnrollmentProtocolError("stdin exceeds 4096 bytes")
    try:
        return EnrollmentRequest.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise EnrollmentProtocolError("invalid enrollment request") from exc


def encode_response(response: EnrollmentResponse) -> bytes:
    encoded = (
        json.dumps(
            response.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise EnrollmentProtocolError("enrollment response exceeds 8192 bytes")
    return encoded


def parse_response(payload: bytes) -> EnrollmentResponse:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise EnrollmentProtocolError("enrollment response exceeds 8192 bytes")
    if not payload.endswith(b"\n") or payload.count(b"\n") != 1:
        raise EnrollmentProtocolError("invalid enrollment response")
    try:
        return EnrollmentResponse.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise EnrollmentProtocolError("invalid enrollment response") from exc

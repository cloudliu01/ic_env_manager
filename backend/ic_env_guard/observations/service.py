from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ic_env_guard.observations.models import (
    Observation,
    ObservationConflict,
    ObservationExpired,
    ObservationInput,
    ObservationPage,
    ObservationQuery,
    ObservationStorageError,
    UpsertResult,
)
from ic_env_guard.observations.ports import ObservationRepository

_MAX_CAS_ATTEMPTS = 8


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(UTC)


class ObservationService:
    def __init__(self, repository: ObservationRepository) -> None:
        self.repository = repository

    def _new_record(self, payload: ObservationInput, now: datetime) -> Observation:
        expires_at = payload.observed_at + timedelta(seconds=payload.ttl_seconds)
        return Observation(
            identity_key=payload.identity_key(),
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
            received_at=now,
            expires_at=expires_at,
            producer_id="local",
            updated_at=now,
        )

    def upsert(self, payload: ObservationInput, *, now: datetime) -> UpsertResult:
        received_at = _utc(now)
        if payload.observed_at > received_at + timedelta(seconds=60):
            raise ValueError("observation_in_future")
        candidate = self._new_record(payload, received_at)
        if candidate.expires_at <= received_at:
            raise ObservationExpired("observation_expired")

        for _ in range(_MAX_CAS_ATTEMPTS):
            current = self.repository.get(candidate.identity_key)
            if current is not None:
                if candidate.observed_at < current.observed_at:
                    raise ObservationConflict("stale_observation")
                if candidate.observed_at == current.observed_at:
                    if candidate.normalized_payload() == current.normalized_payload():
                        return UpsertResult(record=current, created=False)
                    raise ObservationConflict("observation_timestamp_conflict")
            expected = current.observed_at if current is not None else None
            if self.repository.compare_and_swap(candidate, expected):
                return UpsertResult(record=candidate, created=current is None)
        raise ObservationStorageError("observation_storage_contention")

    def get(
        self,
        identity_key: str,
        *,
        now: datetime,
        include_stale: bool = False,
    ) -> Observation | None:
        record = self.repository.get(identity_key)
        if record is None:
            return None
        if not include_stale and record.is_stale(_utc(now)):
            return None
        return record

    def list(self, query: ObservationQuery, *, now: datetime) -> ObservationPage:
        return self.repository.list(replace(query, now=_utc(now)))

    def delete_expired(self, cutoff: datetime, *, limit: int) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        return self.repository.delete_expired(_utc(cutoff), limit)

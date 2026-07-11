from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ic_env_guard.observations.models import (
    Observation,
    ObservationConflict,
    ObservationExpired,
    ObservationInput,
    ObservationPage,
    ObservationQuery,
    ObservationStorageError,
)
from ic_env_guard.observations.service import ObservationService

NOW = datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC)


def input_at(observed_at: str, **overrides) -> ObservationInput:
    return ObservationInput.model_validate(
        {
            "namespace": "eda",
            "name": "license_server_alive",
            "kind": "gauge",
            "value": 1,
            "status": "ok",
            "observed_at": observed_at,
            "ttl_seconds": 120,
            **overrides,
        }
    )


class FakeRepository:
    def __init__(self) -> None:
        self.records: dict[str, Observation] = {}
        self.force_miss_with: Observation | None = None
        self.always_miss = False
        self.last_query: ObservationQuery | None = None

    def get(self, identity_key: str) -> Observation | None:
        return self.records.get(identity_key)

    def compare_and_swap(
        self, record: Observation, expected_observed_at: datetime | None
    ) -> bool:
        if self.always_miss:
            return False
        if self.force_miss_with is not None:
            self.records[record.identity_key] = self.force_miss_with
            self.force_miss_with = None
            return False
        current = self.records.get(record.identity_key)
        if expected_observed_at is None:
            if current is not None:
                return False
        elif current is None or current.observed_at != expected_observed_at:
            return False
        self.records[record.identity_key] = record
        return True

    def list(self, query: ObservationQuery) -> ObservationPage:
        self.last_query = query
        return ObservationPage(items=tuple(self.records.values()), next_cursor=None)

    def delete_expired(self, cutoff: datetime, limit: int) -> int:
        keys = [
            key
            for key, record in self.records.items()
            if record.expires_at <= cutoff
        ][:limit]
        for key in keys:
            del self.records[key]
        return len(keys)


@pytest.mark.unit
def test_upsert_is_idempotent_and_producer_is_local():
    service = ObservationService(FakeRepository())

    created = service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)
    same = service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)

    assert created.record == same.record
    assert created.created is True
    assert same.created is False
    assert same.record.producer_id == "local"
    assert same.record.received_at == NOW


@pytest.mark.unit
def test_upsert_rejects_stale_and_same_timestamp_different_payload():
    service = ObservationService(FakeRepository())
    service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)

    with pytest.raises(ObservationConflict, match="stale_observation"):
        service.upsert(input_at("2026-07-11T09:59:59Z"), now=NOW)
    with pytest.raises(ObservationConflict, match="observation_timestamp_conflict"):
        service.upsert(
            input_at("2026-07-11T10:00:00Z", value=2), now=NOW
        )


@pytest.mark.unit
def test_upsert_rejects_future_and_already_expired_input():
    service = ObservationService(FakeRepository())

    with pytest.raises(ValueError, match="observation_in_future"):
        service.upsert(input_at("2026-07-11T10:01:31Z"), now=NOW)
    with pytest.raises(ObservationExpired, match="observation_expired"):
        service.upsert(
            input_at("2026-07-11T09:58:30Z", ttl_seconds=120), now=NOW
        )


@pytest.mark.unit
def test_cas_miss_reloads_and_reapplies_stale_rule():
    repository = FakeRepository()
    service = ObservationService(repository)
    candidate = input_at("2026-07-11T10:00:00Z")
    newer = service._new_record(input_at("2026-07-11T10:00:10Z"), NOW)  # noqa: SLF001
    repository.force_miss_with = newer

    with pytest.raises(ObservationConflict, match="stale_observation"):
        service.upsert(candidate, now=NOW)

    assert repository.get(candidate.identity_key()) == newer


@pytest.mark.unit
def test_cas_contention_is_bounded():
    repository = FakeRepository()
    repository.always_miss = True

    with pytest.raises(ObservationStorageError, match="observation_storage_contention"):
        ObservationService(repository).upsert(
            input_at("2026-07-11T10:00:00Z"), now=NOW
        )


@pytest.mark.unit
def test_get_and_list_apply_freshness_semantics():
    repository = FakeRepository()
    service = ObservationService(repository)
    record = service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW).record

    assert service.get(record.identity_key, now=record.expires_at) is None
    assert service.get(record.identity_key, now=record.expires_at, include_stale=True) == record
    page = service.list(ObservationQuery(), now=NOW)

    assert page.items == (record,)
    assert repository.last_query == replace(ObservationQuery(), now=NOW)


@pytest.mark.unit
def test_delete_expired_delegates_bounded_cleanup():
    repository = FakeRepository()
    service = ObservationService(repository)
    service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)

    assert service.delete_expired(NOW.replace(minute=3), limit=1) == 1

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.logs.models import (
    LogSourceConflict,
    LogSourceExpired,
    LogSourceInput,
    LogStorageError,
)
from ic_env_guard.logs.policy import LogPathPolicy, LogTailReader
from ic_env_guard.logs.service import LogSourceService


class MemoryLogSourceRepository:
    def __init__(self):
        self.records = {}
        self.cas_calls = []

    def get(self, log_id):
        return self.records.get(log_id)

    def compare_and_swap(self, record, expected_observed_at):
        self.cas_calls.append((record, expected_observed_at))
        current = self.records.get(record.id)
        if current is None:
            if expected_observed_at is not None:
                return False
        elif current.observed_at != expected_observed_at:
            return False
        self.records[record.id] = replace(record, producer_id="local")
        return True

    def list(self):
        return tuple(self.records[key] for key in sorted(self.records))

    def delete_expired(self, cutoff, limit):
        ids = [key for key, value in self.records.items() if value.expires_at <= cutoff]
        for key in sorted(ids)[:limit]:
            del self.records[key]
        return min(len(ids), limit)


def _payload(path, observed_at="2026-07-11T10:00:00Z", **overrides):
    data = {
        "path": str(path),
        "last_updated": "2026-07-11T09:59:58Z",
        "observed_at": observed_at,
        "ttl_seconds": 120,
    }
    data.update(overrides)
    return LogSourceInput.model_validate(data)


def _service(tmp_path, repository=None):
    repository = repository or MemoryLogSourceRepository()
    policy = LogPathPolicy([tmp_path])
    return LogSourceService(repository, policy, LogTailReader(policy)), repository


def test_upsert_normalizes_path_and_forces_local_producer(tmp_path):
    path = tmp_path / "nested" / "run.log"
    path.parent.mkdir()
    path.write_text("ok", encoding="utf-8")
    service, repository = _service(tmp_path)
    now = datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC)

    result = service.upsert(
        "innovus-run",
        _payload(path.parent / ".." / "nested" / "run.log"),
        now=now,
    )

    assert result.created is True
    assert result.record.path == path.resolve()
    assert result.record.producer_id == "local"
    assert repository.cas_calls[0][0].producer_id == "local"


@pytest.mark.parametrize("log_id", ["A", "1log", "log/id", "a" * 128])
def test_upsert_rejects_invalid_log_id(tmp_path, log_id):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    service, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="invalid log id"):
        service.upsert(log_id, _payload(path), now=datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC))


def test_upsert_is_idempotent_at_same_timestamp(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    service, repository = _service(tmp_path)
    now = datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC)
    first = service.upsert("run", _payload(path), now=now)

    retry = service.upsert("run", _payload(path), now=now + timedelta(seconds=1))

    assert retry.created is False
    assert retry.record == first.record
    assert len(repository.cas_calls) == 1


def test_upsert_rejects_stale_and_conflicting_same_timestamp(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    service, _ = _service(tmp_path)
    now = datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC)
    service.upsert("run", _payload(path), now=now)

    with pytest.raises(LogSourceConflict, match="stale_log_source"):
        service.upsert("run", _payload(path, observed_at="2026-07-11T09:59:59Z"), now=now)
    with pytest.raises(LogSourceConflict, match="log_source_timestamp_conflict"):
        service.upsert("run", _payload(path, ttl_seconds=121), now=now)


def test_upsert_rejects_already_expired_source(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    service, _ = _service(tmp_path)

    with pytest.raises(LogSourceExpired, match="log_source_expired"):
        service.upsert(
            "run",
            _payload(path, ttl_seconds=1),
            now=datetime(2026, 7, 11, 10, 0, 2, tzinfo=UTC),
        )


def test_upsert_reports_storage_contention_after_bounded_retries(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    repository = MemoryLogSourceRepository()
    repository.compare_and_swap = lambda record, expected: False
    service, _ = _service(tmp_path, repository)

    with pytest.raises(LogStorageError, match="log_storage_contention"):
        service.upsert("run", _payload(path), now=datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC))


def test_list_and_get_hide_stale_sources_by_default(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("ok", encoding="utf-8")
    service, _ = _service(tmp_path)
    created = service.upsert(
        "run",
        _payload(path),
        now=datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC),
    ).record

    assert service.get("run", now=created.expires_at) is None
    assert service.get("run", now=created.expires_at, include_stale=True) == created
    assert service.list(now=created.expires_at) == ()
    assert service.list(now=created.expires_at, include_stale=True) == (created,)


def test_tail_fetches_metadata_before_opening_file(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    service, repository = _service(tmp_path)
    now = datetime(2026, 7, 11, 10, 0, 30, tzinfo=UTC)
    service.upsert("run", _payload(path), now=now)
    events = []
    original_get = repository.get
    original_read = service.tail_reader.read

    def get(log_id):
        events.append("repository-closed")
        return original_get(log_id)

    def read(path, *, lines, max_bytes=None):
        events.append("file-opened")
        return original_read(path, lines=lines, max_bytes=max_bytes)

    repository.get = get
    service.tail_reader.read = read

    result = service.tail("run", lines=1, now=now)

    assert result.lines == ("two",)
    assert events == ["repository-closed", "file-opened"]

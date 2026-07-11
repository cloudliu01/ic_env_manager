from datetime import datetime, timedelta

from ic_env_guard.logs.models import (
    LogFileUnavailable,
    LogSource,
    LogSourceConflict,
    LogSourceExpired,
    LogSourceInput,
    LogStorageError,
    LogTailResult,
    LogUpsertResult,
    aware_utc,
    validate_log_id,
)
from ic_env_guard.logs.policy import LogPathPolicy, LogTailReader
from ic_env_guard.logs.ports import LogSourceRepository

_MAX_CAS_ATTEMPTS = 8


class LogSourceService:
    def __init__(
        self,
        repository: LogSourceRepository,
        path_policy: LogPathPolicy,
        tail_reader: LogTailReader,
    ) -> None:
        self.repository = repository
        self.path_policy = path_policy
        self.tail_reader = tail_reader

    def upsert(
        self,
        log_id: str,
        payload: LogSourceInput,
        *,
        now: datetime,
    ) -> LogUpsertResult:
        validate_log_id(log_id)
        received_at = aware_utc(now, "now")
        if payload.observed_at > received_at + timedelta(seconds=60):
            raise ValueError("log_source_in_future")
        normalized_path = self.path_policy.resolve_regular_file(payload.path)
        candidate = LogSource(
            id=log_id,
            path=normalized_path,
            last_updated=payload.last_updated,
            observed_at=payload.observed_at,
            ttl_seconds=payload.ttl_seconds,
            received_at=received_at,
            expires_at=payload.observed_at + timedelta(seconds=payload.ttl_seconds),
            producer_id="local",
            updated_at=received_at,
        )
        if candidate.expires_at <= received_at:
            raise LogSourceExpired("log_source_expired")

        for _ in range(_MAX_CAS_ATTEMPTS):
            current = self.repository.get(log_id)
            if current is not None:
                if candidate.observed_at < current.observed_at:
                    raise LogSourceConflict("stale_log_source")
                if candidate.observed_at == current.observed_at:
                    if candidate.normalized_payload() == current.normalized_payload():
                        return LogUpsertResult(record=current, created=False)
                    raise LogSourceConflict("log_source_timestamp_conflict")
            expected = current.observed_at if current is not None else None
            if self.repository.compare_and_swap(candidate, expected):
                return LogUpsertResult(record=candidate, created=current is None)
        raise LogStorageError("log_storage_contention")

    def get(
        self,
        log_id: str,
        *,
        now: datetime,
        include_stale: bool = False,
    ) -> LogSource | None:
        validate_log_id(log_id)
        record = self.repository.get(log_id)
        if record is None:
            return None
        if not include_stale and record.is_stale(now):
            return None
        return record

    def list(
        self,
        *,
        now: datetime,
        include_stale: bool = False,
    ) -> tuple[LogSource, ...]:
        current_time = aware_utc(now, "now")
        records = self.repository.list()
        if include_stale:
            return records
        return tuple(record for record in records if not record.is_stale(current_time))

    def tail(self, log_id: str, *, lines: int, now: datetime) -> LogTailResult:
        record = self.get(log_id, now=now, include_stale=True)
        if record is None:
            raise LogFileUnavailable("log_source_not_found")
        if record.is_stale(now):
            raise LogSourceExpired("log_source_stale")
        result = self.tail_reader.read(record.path, lines=lines)
        return LogTailResult(
            source=record,
            lines=result.lines,
            truncated=result.truncated,
        )

    def delete_expired(self, cutoff: datetime, *, limit: int) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        return self.repository.delete_expired(aware_utc(cutoff, "cutoff"), limit)

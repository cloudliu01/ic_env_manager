from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.logs.models import LogSource, LogStorageError

_COLUMNS = "id, path, last_updated, observed_at, received_at, expires_at, producer_id, updated_at"


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored log source time must be timezone-aware")
    normalized = parsed.astimezone(UTC)
    if value != _format_time(normalized):
        raise ValueError("stored log source time is not canonical")
    return normalized


def _values(record: LogSource) -> dict[str, Any]:
    return {
        "id": record.id,
        "path": str(record.path),
        "last_updated": _format_time(record.last_updated),
        "observed_at": _format_time(record.observed_at),
        "received_at": _format_time(record.received_at),
        "expires_at": _format_time(record.expires_at),
        "producer_id": "local",
        "updated_at": _format_time(record.updated_at),
    }


def _record(row: Any) -> LogSource:
    return LogSource.reconstitute(
        id=row.id,
        path=row.path,
        last_updated=_parse_time(row.last_updated),
        observed_at=_parse_time(row.observed_at),
        received_at=_parse_time(row.received_at),
        expires_at=_parse_time(row.expires_at),
        producer_id=row.producer_id,
        updated_at=_parse_time(row.updated_at),
    )


class SQLiteLogSourceRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, log_id: str) -> LogSource | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(
                    text(f"SELECT {_COLUMNS} FROM log_sources WHERE id = :id"),
                    {"id": log_id},
                ).first()
            return _record(row) if row is not None else None
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise LogStorageError("log_storage_unavailable") from exc

    def compare_and_swap(
        self,
        record: LogSource,
        expected_observed_at: datetime | None,
    ) -> bool:
        values = _values(replace(record, producer_id="local"))
        try:
            with self.engine.begin() as connection:
                if expected_observed_at is None:
                    result = connection.execute(
                        text(
                            "INSERT INTO log_sources (" + _COLUMNS + ") VALUES ("
                            ":id, :path, :last_updated, :observed_at, :received_at, "
                            ":expires_at, :producer_id, :updated_at"
                            ") ON CONFLICT(id) DO NOTHING"
                        ),
                        values,
                    )
                else:
                    values["expected_observed_at"] = _format_time(expected_observed_at)
                    assignments = ", ".join(
                        f"{column} = :{column}"
                        for column in (
                            "path",
                            "last_updated",
                            "observed_at",
                            "received_at",
                            "expires_at",
                            "producer_id",
                            "updated_at",
                        )
                    )
                    result = connection.execute(
                        text(
                            f"UPDATE log_sources SET {assignments} "
                            "WHERE id = :id AND observed_at = :expected_observed_at"
                        ),
                        values,
                    )
        except SQLAlchemyError as exc:
            raise LogStorageError("log_storage_unavailable") from exc
        return result.rowcount == 1

    def list(self) -> tuple[LogSource, ...]:
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(
                    text(f"SELECT {_COLUMNS} FROM log_sources ORDER BY id")
                ).fetchall()
            return tuple(_record(row) for row in rows)
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise LogStorageError("log_storage_unavailable") from exc

    def counts(self, now: datetime) -> tuple[int, int]:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT COUNT(*) AS total, "
                        "SUM(CASE WHEN expires_at <= :now THEN 1 ELSE 0 END) AS stale "
                        "FROM log_sources"
                    ),
                    {"now": _format_time(now)},
                ).one()
            return int(row.total), int(row.stale or 0)
        except SQLAlchemyError as exc:
            raise LogStorageError("log_storage_unavailable") from exc

    def delete_expired(self, cutoff: datetime, limit: int) -> int:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    text(
                        "DELETE FROM log_sources WHERE id IN ("
                        "SELECT id FROM log_sources WHERE expires_at <= :cutoff "
                        "ORDER BY expires_at, id LIMIT :limit)"
                    ),
                    {"cutoff": _format_time(cutoff), "limit": limit},
                )
        except SQLAlchemyError as exc:
            raise LogStorageError("log_storage_unavailable") from exc
        return result.rowcount

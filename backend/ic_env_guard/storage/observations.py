import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.observations.models import (
    Observation,
    ObservationPage,
    ObservationQuery,
    ObservationStorageError,
    compact_json,
)

_COLUMNS = (
    "identity_key, namespace, name, kind, numeric_value, unit, status, message, "
    "labels_json, details_json, observed_at, received_at, expires_at, producer_id, updated_at"
)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored observation time must be timezone-aware")
    normalized = parsed.astimezone(UTC)
    if value != _format_time(normalized):
        raise ValueError("stored observation time is not canonical")
    return normalized


def _values(record: Observation) -> dict[str, Any]:
    return {
        "identity_key": record.identity_key,
        "namespace": record.namespace,
        "name": record.name,
        "kind": record.kind,
        "numeric_value": record.value,
        "unit": record.unit,
        "status": record.status,
        "message": record.message,
        "labels_json": compact_json(record.labels, sort_keys=True),
        "details_json": compact_json(record.details),
        "observed_at": _format_time(record.observed_at),
        "received_at": _format_time(record.received_at),
        "expires_at": _format_time(record.expires_at),
        "producer_id": "local",
        "updated_at": _format_time(record.updated_at),
    }


def _record(row: Any) -> Observation:
    return Observation.reconstitute(
        identity_key=row.identity_key,
        namespace=row.namespace,
        name=row.name,
        kind=row.kind,
        value=row.numeric_value,
        unit=row.unit,
        status=row.status,
        message=row.message,
        labels=json.loads(row.labels_json),
        details=json.loads(row.details_json),
        observed_at=_parse_time(row.observed_at),
        received_at=_parse_time(row.received_at),
        expires_at=_parse_time(row.expires_at),
        producer_id=row.producer_id,
        updated_at=_parse_time(row.updated_at),
    )


class SQLiteObservationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, identity_key: str) -> Observation | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(
                    text(f"SELECT {_COLUMNS} FROM observations WHERE identity_key = :key"),
                    {"key": identity_key},
                ).first()
            return _record(row) if row is not None else None
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise ObservationStorageError("observation_storage_unavailable") from exc

    def compare_and_swap(
        self, record: Observation, expected_observed_at: datetime | None
    ) -> bool:
        values = _values(replace(record, producer_id="local"))
        try:
            with self.engine.begin() as connection:
                if expected_observed_at is None:
                    result = connection.execute(
                        text(
                            "INSERT INTO observations ("
                            + _COLUMNS
                            + ") VALUES ("
                            ":identity_key, :namespace, :name, :kind, :numeric_value, :unit, "
                            ":status, :message, :labels_json, :details_json, :observed_at, "
                            ":received_at, :expires_at, :producer_id, :updated_at"
                            ") ON CONFLICT(identity_key) DO NOTHING"
                        ),
                        values,
                    )
                else:
                    values["expected_observed_at"] = _format_time(expected_observed_at)
                    assignments = ", ".join(
                        f"{column} = :{column}"
                        for column in (
                            "namespace",
                            "name",
                            "kind",
                            "numeric_value",
                            "unit",
                            "status",
                            "message",
                            "labels_json",
                            "details_json",
                            "observed_at",
                            "received_at",
                            "expires_at",
                            "producer_id",
                            "updated_at",
                        )
                    )
                    result = connection.execute(
                        text(
                            f"UPDATE observations SET {assignments} "
                            "WHERE identity_key = :identity_key "
                            "AND observed_at = :expected_observed_at"
                        ),
                        values,
                    )
        except SQLAlchemyError as exc:
            raise ObservationStorageError("observation_storage_unavailable") from exc
        return result.rowcount == 1

    def list(self, query: ObservationQuery) -> ObservationPage:
        clauses: list[str] = []
        parameters: dict[str, Any] = {"limit": query.limit + 1}
        if query.namespace is not None:
            clauses.append("namespace = :namespace")
            parameters["namespace"] = query.namespace
        if query.name is not None:
            clauses.append("name = :name")
            parameters["name"] = query.name
        if query.status is not None:
            clauses.append("status = :status")
            parameters["status"] = query.status
        if not query.include_stale:
            now = query.now or datetime.now(UTC)
            clauses.append("expires_at > :now")
            parameters["now"] = _format_time(now)
        if query.cursor is not None:
            clauses.append("identity_key > :cursor")
            parameters["cursor"] = query.cursor
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM observations{where} "
                        "ORDER BY identity_key LIMIT :limit"
                    ),
                    parameters,
                ).fetchall()
            records = tuple(_record(row) for row in rows[: query.limit])
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise ObservationStorageError("observation_storage_unavailable") from exc
        next_cursor = records[-1].identity_key if len(rows) > query.limit else None
        return ObservationPage(items=records, next_cursor=next_cursor)

    def delete_expired(self, cutoff: datetime, limit: int) -> int:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    text(
                        "DELETE FROM observations WHERE identity_key IN ("
                        "SELECT identity_key FROM observations WHERE expires_at <= :cutoff "
                        "ORDER BY expires_at, identity_key LIMIT :limit)"
                    ),
                    {"cutoff": _format_time(cutoff), "limit": limit},
                )
        except SQLAlchemyError as exc:
            raise ObservationStorageError("observation_storage_unavailable") from exc
        return result.rowcount

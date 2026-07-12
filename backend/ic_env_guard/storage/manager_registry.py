import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.fleet.models import (
    AgentPage,
    AgentQuery,
    AgentRecord,
    AgentStatus,
    EnrollmentMethod,
    RegistryConflict,
    RegistryError,
    RegistryInvariantError,
    RevisionConflict,
)

_AGENT_COLUMNS = (
    "agent_id, instance_id, display_name, normalized_endpoint, credential_ref, "
    "remote_credential_id, transport_profile_id, enrollment_method, enabled, source, "
    "revision, created_at, updated_at"
)
_STATUS_COLUMNS = (
    "agent_id, target_revision, connection_status, workload_status, observed_at, stale_after, "
    "api_version, agent_version, capabilities_json, summary_json, last_error_code, updated_at"
)


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise RegistryInvariantError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value is not None else None


def _validate_record(record: AgentRecord) -> None:
    required = (
        record.agent_id,
        record.display_name,
        record.normalized_endpoint,
        record.credential_ref,
        record.transport_profile_id,
    )
    if any(not value or not value.strip() for value in required):
        raise RegistryInvariantError("agent fields must not be empty")
    if record.revision < 1:
        raise RegistryInvariantError("agent revision must be positive")
    if record.source not in {"config_import", "manual", "discovery"}:
        raise RegistryInvariantError("invalid agent source")
    legacy_import = (
        record.source == "config_import"
        and record.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN
    )
    if record.instance_id is None and not legacy_import:
        raise RegistryInvariantError("registered agents require an instance_id")
    if record.remote_credential_id is None and (
        record.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN
    ):
        raise RegistryInvariantError("SSH enrollment requires a remote credential ID")
    _format_time(record.created_at)
    _format_time(record.updated_at)


def _agent(row: Any) -> AgentRecord:
    return AgentRecord(
        agent_id=row[0],
        instance_id=row[1],
        display_name=row[2],
        normalized_endpoint=row[3],
        credential_ref=row[4],
        remote_credential_id=row[5],
        transport_profile_id=row[6],
        enrollment_method=EnrollmentMethod(row[7]),
        enabled=bool(row[8]),
        source=row[9],
        revision=row[10],
        created_at=_parse_time(row[11]),
        updated_at=_parse_time(row[12]),
    )


class _SQLiteRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        raw = self.engine.raw_connection()
        connection = raw.driver_connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            raw.close()


class ManagerRegistryRepository(_SQLiteRepository):
    def get_or_create_manager_id(self) -> UUID:
        try:
            with self._write() as connection:
                row = connection.execute(
                    "SELECT value FROM manager_metadata WHERE key='manager_id'"
                ).fetchone()
                if row is None:
                    value = str(uuid4())
                    connection.execute(
                        "INSERT INTO manager_metadata(key, value) VALUES ('manager_id', ?)",
                        (value,),
                    )
                else:
                    value = row[0]
            parsed = UUID(value)
            if str(parsed) != value:
                raise ValueError("manager ID is not canonical")
            return parsed
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("manager identity storage is unavailable") from exc

    def create(self, record: AgentRecord) -> AgentRecord:
        _validate_record(record)
        try:
            with self._write() as connection:
                connection.execute(
                    f"INSERT INTO agents ({_AGENT_COLUMNS}) VALUES ({','.join('?' * 13)})",
                    self._values(record),
                )
            return record
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("agent ID, identity, or endpoint already exists") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    def get(self, agent_id: str) -> AgentRecord | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_AGENT_COLUMNS} FROM agents WHERE agent_id = ?", (agent_id,)
                ).first()
            return _agent(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    def list(self, query: AgentQuery) -> AgentPage:
        if not 1 <= query.limit <= 1000:
            raise RegistryInvariantError("query limit must be between 1 and 1000")
        parameters: list[Any] = []
        where = ""
        if query.cursor is not None:
            where = " WHERE agent_id > ?"
            parameters.append(query.cursor)
        parameters.append(query.limit + 1)
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_AGENT_COLUMNS} FROM agents{where} ORDER BY agent_id LIMIT ?",
                    tuple(parameters),
                ).fetchall()
            items = tuple(_agent(row) for row in rows[: query.limit])
            next_cursor = items[-1].agent_id if len(rows) > query.limit else None
            return AgentPage(items=items, next_cursor=next_cursor)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    def update_if_revision(
        self, record: AgentRecord, expected_revision: int
    ) -> AgentRecord:
        if expected_revision < 1:
            raise RegistryInvariantError("expected revision must be positive")
        updated = replace(record, revision=expected_revision + 1)
        _validate_record(updated)
        values = self._values(updated)
        try:
            with self._write() as connection:
                cursor = connection.execute(
                    "UPDATE agents SET instance_id=?, display_name=?, normalized_endpoint=?, "
                    "credential_ref=?, remote_credential_id=?, transport_profile_id=?, "
                    "enrollment_method=?, enabled=?, source=?, revision=?, created_at=?, "
                    "updated_at=? WHERE agent_id=? AND revision=?",
                    (*values[1:], record.agent_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("agent registry revision changed")
            return updated
        except RevisionConflict:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryConflict("agent identity or endpoint already exists") from exc
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    def delete(self, agent_id: str) -> None:
        try:
            with self._write() as connection:
                connection.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    def credential_references(self) -> set[str]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT credential_ref FROM agents"
                ).fetchall()
            return {row[0] for row in rows}
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise RegistryError("agent registry storage is unavailable") from exc

    @staticmethod
    def _values(record: AgentRecord) -> tuple[Any, ...]:
        return (
            record.agent_id,
            record.instance_id,
            record.display_name,
            record.normalized_endpoint,
            record.credential_ref,
            record.remote_credential_id,
            record.transport_profile_id,
            record.enrollment_method.value,
            int(record.enabled),
            record.source,
            record.revision,
            _format_time(record.created_at),
            _format_time(record.updated_at),
        )


class AgentStatusRepository(_SQLiteRepository):
    def get(self, agent_id: str) -> AgentStatus | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_STATUS_COLUMNS} FROM agent_status WHERE agent_id = ?",
                    (agent_id,),
                ).first()
            return self._record(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryError("agent status storage is unavailable") from exc

    def update_if_target_revision(
        self, observation: AgentStatus, expected_revision: int
    ) -> bool:
        if observation.target_revision != expected_revision or expected_revision < 1:
            raise RegistryInvariantError("status target revision is inconsistent")
        capabilities = json.dumps(
            list(observation.capabilities), sort_keys=True, separators=(",", ":")
        )
        summary = json.dumps(observation.summary, sort_keys=True, separators=(",", ":"))
        try:
            with self._write() as connection:
                current = connection.execute(
                    "SELECT revision FROM agents WHERE agent_id = ?", (observation.agent_id,)
                ).fetchone()
                if current is None or current[0] != expected_revision:
                    return False
                connection.execute(
                    f"INSERT INTO agent_status ({_STATUS_COLUMNS}) "
                    f"VALUES ({','.join('?' * 12)}) "
                    "ON CONFLICT(agent_id) DO UPDATE SET "
                    "target_revision=excluded.target_revision, "
                    "connection_status=excluded.connection_status, "
                    "workload_status=excluded.workload_status, observed_at=excluded.observed_at, "
                    "stale_after=excluded.stale_after, api_version=excluded.api_version, "
                    "agent_version=excluded.agent_version, "
                    "capabilities_json=excluded.capabilities_json, "
                    "summary_json=excluded.summary_json, "
                    "last_error_code=excluded.last_error_code, updated_at=excluded.updated_at",
                    (
                        observation.agent_id,
                        observation.target_revision,
                        observation.connection_status,
                        observation.workload_status,
                        _format_time(observation.observed_at),
                        _format_time(observation.stale_after),
                        observation.api_version,
                        observation.agent_version,
                        capabilities,
                        summary,
                        observation.last_error_code,
                        _format_time(observation.updated_at),
                    ),
                )
            return True
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RegistryError("agent status storage is unavailable") from exc

    @staticmethod
    def _record(row: Any) -> AgentStatus:
        capabilities = json.loads(row[8])
        summary = json.loads(row[9])
        if not isinstance(capabilities, list) or not all(
            isinstance(value, str) for value in capabilities
        ):
            raise ValueError("stored capabilities are invalid")
        if not isinstance(summary, dict):
            raise ValueError("stored summary is invalid")
        return AgentStatus(
            agent_id=row[0],
            target_revision=row[1],
            connection_status=row[2],
            workload_status=row[3],
            observed_at=_parse_time(row[4]),
            stale_after=_parse_time(row[5]),
            api_version=row[6],
            agent_version=row[7],
            capabilities=tuple(capabilities),
            summary=summary,
            last_error_code=row[10],
            updated_at=_parse_time(row[11]),
        )

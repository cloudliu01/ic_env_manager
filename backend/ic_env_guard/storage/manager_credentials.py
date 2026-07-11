import hmac
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ic_env_guard.enrollment.models import (
    CredentialState,
    CredentialStorageError,
    DuplicateEnrollment,
    EnrollmentCapacityExceeded,
    ManagerCredential,
    aware_utc,
    canonical_uuid,
    valid_enrollment_id,
)

_COLUMNS = (
    "credential_id, manager_id, enrollment_id, token_hash, state, pending_expires_at, "
    "created_at, activated_at, last_used_at, revoked_at"
)


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return aware_utc(value, "time").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    normalized = aware_utc(parsed, field)
    if value != _format_time(normalized):
        raise ValueError(f"stored {field} is not canonical")
    return normalized


def _record(row: Any) -> ManagerCredential:
    state = CredentialState(row[4])
    credential_id = canonical_uuid(row[0], "credential_id")
    manager_id = canonical_uuid(row[1], "manager_id")
    enrollment_id = valid_enrollment_id(row[2])
    token_hash = row[3]
    if not isinstance(token_hash, str) or len(token_hash) != 64:
        raise ValueError("stored token hash is invalid")
    int(token_hash, 16)
    pending_expires_at = _parse_time(row[5], "pending_expires_at")
    created_at = _parse_time(row[6], "created_at")
    activated_at = _parse_time(row[7], "activated_at")
    last_used_at = _parse_time(row[8], "last_used_at")
    revoked_at = _parse_time(row[9], "revoked_at")
    if created_at is None:
        raise ValueError("stored created_at is required")
    if state is CredentialState.PENDING and pending_expires_at is None:
        raise ValueError("pending credential expiry is required")
    if state is CredentialState.ACTIVE and activated_at is None:
        raise ValueError("active credential activation time is required")
    if state is CredentialState.REVOKED and revoked_at is None:
        raise ValueError("revoked credential revocation time is required")
    return ManagerCredential(
        credential_id=credential_id,
        manager_id=manager_id,
        enrollment_id=enrollment_id,
        token_hash=token_hash,
        state=state,
        pending_expires_at=pending_expires_at,
        created_at=created_at,
        activated_at=activated_at,
        last_used_at=last_used_at,
        revoked_at=revoked_at,
    )


class SQLiteManagerCredentialRepository:
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

    def issue(self, record: ManagerCredential, *, now: datetime, max_pending: int) -> None:
        try:
            with self._write() as connection:
                duplicate = connection.execute(
                    "SELECT 1 FROM manager_credentials WHERE enrollment_id = ?",
                    (record.enrollment_id,),
                ).fetchone()
                if duplicate is not None:
                    raise DuplicateEnrollment("enrollment_id was already consumed")
                pending = connection.execute(
                    "SELECT COUNT(*) FROM manager_credentials "
                    "WHERE state = 'pending' AND pending_expires_at > ?",
                    (_format_time(now),),
                ).fetchone()[0]
                if pending >= max_pending:
                    raise EnrollmentCapacityExceeded("pending credential capacity exceeded")
                connection.execute(
                    "INSERT INTO manager_credentials (" + _COLUMNS + ") "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.credential_id,
                        record.manager_id,
                        record.enrollment_id,
                        record.token_hash,
                        record.state.value,
                        _format_time(record.pending_expires_at),
                        _format_time(record.created_at),
                        _format_time(record.activated_at),
                        _format_time(record.last_used_at),
                        _format_time(record.revoked_at),
                    ),
                )
        except (DuplicateEnrollment, EnrollmentCapacityExceeded):
            raise
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

    def get(self, credential_id: str) -> ManagerCredential | None:
        try:
            with self.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM manager_credentials WHERE credential_id = ?",
                    (credential_id,),
                ).first()
            return _record(row) if row is not None else None
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

    def list_all(self) -> tuple[ManagerCredential, ...]:
        return self._list("")

    def authenticatable(self) -> tuple[ManagerCredential, ...]:
        return self._list(" WHERE state IN ('pending', 'active')")

    def _list(self, where: str) -> tuple[ManagerCredential, ...]:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM manager_credentials{where} ORDER BY credential_id"
                ).fetchall()
            return tuple(_record(row) for row in rows)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

    def activate(
        self, credential_id: str, enrollment_id: str, token_hash: str, now: datetime
    ) -> ManagerCredential | None:
        try:
            with self._write() as connection:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM manager_credentials WHERE credential_id = ?",
                    (credential_id,),
                ).fetchone()
                if row is None:
                    return None
                record = _record(row)
                if record.enrollment_id != enrollment_id or not hmac.compare_digest(
                    record.token_hash, token_hash
                ):
                    return None
                if record.state is CredentialState.ACTIVE:
                    return record
                if (
                    record.state is not CredentialState.PENDING
                    or record.pending_expires_at is None
                    or now >= record.pending_expires_at
                ):
                    return None
                connection.execute(
                    "UPDATE manager_credentials SET state='active', pending_expires_at=NULL, "
                    "activated_at=? WHERE credential_id=? AND state='pending'",
                    (_format_time(now), credential_id),
                )
                updated = connection.execute(
                    f"SELECT {_COLUMNS} FROM manager_credentials WHERE credential_id = ?",
                    (credential_id,),
                ).fetchone()
                return _record(updated)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

    def revoke(self, credential_id: str, now: datetime) -> ManagerCredential | None:
        try:
            with self._write() as connection:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM manager_credentials WHERE credential_id = ?",
                    (credential_id,),
                ).fetchone()
                if row is None:
                    return None
                record = _record(row)
                if record.state is not CredentialState.REVOKED:
                    connection.execute(
                        "UPDATE manager_credentials SET state='revoked', pending_expires_at=NULL, "
                        "revoked_at=? WHERE credential_id=? AND state!='revoked'",
                        (_format_time(now), credential_id),
                    )
                    row = connection.execute(
                        f"SELECT {_COLUMNS} FROM manager_credentials WHERE credential_id = ?",
                        (credential_id,),
                    ).fetchone()
                return _record(row)
        except (SQLAlchemyError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

    def dump_serialized_rows(self) -> str:
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"SELECT {_COLUMNS} FROM manager_credentials ORDER BY credential_id"
                ).fetchall()
            return json.dumps([list(row) for row in rows], separators=(",", ":"))
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise CredentialStorageError("credential storage is unavailable") from exc

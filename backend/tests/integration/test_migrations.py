import importlib.util
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from ic_env_guard.db.migrations import MIGRATIONS_DIR
from ic_env_guard.logs.models import LogSourceInput, LogStorageError
from ic_env_guard.logs.policy import LogPathPolicy, LogTailReader
from ic_env_guard.logs.service import LogSourceService
from ic_env_guard.observations.models import (
    ObservationInput,
    ObservationQuery,
    ObservationStorageError,
)
from ic_env_guard.observations.service import ObservationService
from ic_env_guard.storage.log_sources import SQLiteLogSourceRepository
from ic_env_guard.storage.observations import SQLiteObservationRepository

_MIGRATION_PATH = MIGRATIONS_DIR / "0001_initial.py"
_SPEC = importlib.util.spec_from_file_location("migration_0001_initial", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
initial_migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(initial_migration)

_OBSERVABILITY_PATH = MIGRATIONS_DIR / "0003_observability.py"
_OBSERVABILITY_SPEC = importlib.util.spec_from_file_location(
    "migration_0003_observability", _OBSERVABILITY_PATH
)
assert _OBSERVABILITY_SPEC is not None and _OBSERVABILITY_SPEC.loader is not None
observability_migration = importlib.util.module_from_spec(_OBSERVABILITY_SPEC)
_OBSERVABILITY_SPEC.loader.exec_module(observability_migration)

_CREDENTIAL_PATH = MIGRATIONS_DIR / "0004_manager_credentials.py"
_CREDENTIAL_SPEC = importlib.util.spec_from_file_location(
    "migration_0004_manager_credentials", _CREDENTIAL_PATH
)
assert _CREDENTIAL_SPEC is not None and _CREDENTIAL_SPEC.loader is not None
credential_migration = importlib.util.module_from_spec(_CREDENTIAL_SPEC)
_CREDENTIAL_SPEC.loader.exec_module(credential_migration)


@pytest.mark.integration
def test_initial_migration_creates_required_tables(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)

    initial_migration.upgrade(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "schema_versions",
        "local_administrators",
        "agent_lifecycle_events",
        "configuration_load_events",
        "audit_events",
        "terminal_sessions",
        "managed_services",
        "service_state",
        "service_runs",
        "service_operations",
        "service_events",
        "healthcheck_results",
        "metrics_exposure",
    }.issubset(tables)


@pytest.mark.integration
def test_initial_migration_is_idempotent_for_current_schema(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")

    initial_migration.upgrade(connection)
    initial_migration.upgrade(connection)

    versions = connection.execute(
        "SELECT version FROM schema_versions WHERE version = ?", (initial_migration.VERSION,)
    ).fetchall()
    assert len(versions) == 1


@pytest.mark.integration
def test_initial_migration_records_failed_schema_state(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")
    connection.execute("CREATE TABLE schema_versions (version text primary key)")
    connection.execute("INSERT INTO schema_versions(version) VALUES (?)", ("failed-version",))

    initial_migration.upgrade(connection)

    versions = {row[0] for row in connection.execute("SELECT version FROM schema_versions")}
    assert "failed-version" in versions
    assert initial_migration.VERSION in versions


@pytest.mark.integration
def test_observability_migration_is_additive_exact_and_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")
    initial_migration.upgrade(connection)
    connection.execute("CREATE TABLE existing_user_table (id TEXT PRIMARY KEY)")

    observability_migration.upgrade(connection)
    observability_migration.upgrade(connection)

    columns = [
        (row[1], row[2], row[3], row[5])
        for row in connection.execute("PRAGMA table_info(observations)")
    ]
    indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(observations)")
    }
    log_columns = [
        (row[1], row[2], row[3], row[5])
        for row in connection.execute("PRAGMA table_info(log_sources)")
    ]
    log_indexes = {row[1] for row in connection.execute("PRAGMA index_list(log_sources)")}
    versions = connection.execute(
        "SELECT version FROM schema_versions WHERE version = ?",
        (observability_migration.VERSION,),
    ).fetchall()
    assert columns == [
        ("identity_key", "TEXT", 0, 1),
        ("namespace", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0),
        ("kind", "TEXT", 1, 0),
        ("numeric_value", "REAL", 0, 0),
        ("unit", "TEXT", 0, 0),
        ("status", "TEXT", 1, 0),
        ("message", "TEXT", 0, 0),
        ("labels_json", "TEXT", 1, 0),
        ("details_json", "TEXT", 1, 0),
        ("observed_at", "TEXT", 1, 0),
        ("received_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 1, 0),
        ("producer_id", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert {
        "idx_observations_namespace_name",
        "idx_observations_status_expiry",
        "idx_observations_expires_at",
    }.issubset(indexes)
    assert log_columns == [
        ("id", "TEXT", 0, 1),
        ("path", "TEXT", 1, 0),
        ("last_updated", "TEXT", 1, 0),
        ("observed_at", "TEXT", 1, 0),
        ("received_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 1, 0),
        ("producer_id", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert {
        "idx_log_sources_expires_at",
        "idx_log_sources_last_updated",
    }.issubset(log_indexes)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='existing_user_table'"
    ).fetchone()
    assert len(versions) == 1


@pytest.mark.integration
def test_manager_credential_migration_is_additive_exact_and_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")
    initial_migration.upgrade(connection)
    connection.execute("CREATE TABLE existing_user_table (id TEXT PRIMARY KEY)")

    credential_migration.upgrade(connection)
    credential_migration.upgrade(connection)

    columns = [
        (row[1], row[2], row[3], row[5])
        for row in connection.execute("PRAGMA table_info(manager_credentials)")
    ]
    indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(manager_credentials)")
    }
    assert columns == [
        ("credential_id", "TEXT", 0, 1),
        ("manager_id", "TEXT", 1, 0),
        ("enrollment_id", "TEXT", 1, 0),
        ("token_hash", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("pending_expires_at", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
        ("activated_at", "TEXT", 0, 0),
        ("last_used_at", "TEXT", 0, 0),
        ("revoked_at", "TEXT", 0, 0),
    ]
    assert {
        "sqlite_autoindex_manager_credentials_1",
        "sqlite_autoindex_manager_credentials_2",
        "sqlite_autoindex_manager_credentials_3",
    } <= indexes
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='existing_user_table'"
    ).fetchone()
    versions = connection.execute(
        "SELECT version FROM schema_versions WHERE version = ?",
        (credential_migration.VERSION,),
    ).fetchall()
    assert len(versions) == 1


@pytest.mark.integration
def test_sqlite_repository_round_trip_query_cleanup_and_cas(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")
    repository = SQLiteObservationRepository(engine)
    service = ObservationService(repository)
    now = datetime.fromisoformat("2026-07-11T10:00:30+00:00")
    payload = ObservationInput.model_validate(
        {
            "namespace": "eda",
            "name": "license_server_alive",
            "kind": "gauge",
            "value": 1.5,
            "unit": "boolean",
            "status": "warning",
            "message": "é",
            "labels": {"vendor": "synopsys", "server": "a"},
            "details": {"nested": {"pid": 1234}},
            "observed_at": "2026-07-11T10:00:00Z",
            "ttl_seconds": 120,
        }
    )

    created = service.upsert(payload, now=now).record
    loaded = repository.get(created.identity_key)

    assert loaded == created
    assert loaded is not None and loaded.producer_id == "local"
    assert repository.list(
        ObservationQuery(namespace="eda", status="warning", now=now)
    ).items == (created,)
    assert repository.list(
        ObservationQuery(now=created.expires_at)
    ).items == ()
    assert repository.list(
        ObservationQuery(include_stale=True, now=created.expires_at)
    ).items == (created,)
    assert repository.delete_expired(created.expires_at, limit=1) == 1
    assert repository.get(created.identity_key) is None
    engine.dispose()


@pytest.mark.integration
def test_log_source_repository_round_trip_cas_and_metadata_only_storage(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.close()
    log_path = tmp_path / "run.log"
    log_path.write_text("secret log content", encoding="utf-8")
    engine = create_engine(f"sqlite:///{db_path}")
    repository = SQLiteLogSourceRepository(engine)
    policy = LogPathPolicy([tmp_path])
    service = LogSourceService(repository, policy, LogTailReader(policy))
    now = datetime.fromisoformat("2026-07-11T10:00:30+00:00")

    created = service.upsert(
        "innovus-run",
        LogSourceInput.model_validate(
            {
                "path": str(log_path),
                "last_updated": "2026-07-11T09:59:58Z",
                "observed_at": "2026-07-11T10:00:00Z",
                "ttl_seconds": 120,
            }
        ),
        now=now,
    ).record
    updated = service.upsert(
        "innovus-run",
        LogSourceInput.model_validate(
            {
                "path": str(log_path),
                "last_updated": "2026-07-11T10:00:08Z",
                "observed_at": "2026-07-11T10:00:10Z",
                "ttl_seconds": 120,
            }
        ),
        now=now,
    ).record

    assert updated.observed_at > created.observed_at
    assert repository.get("innovus-run") == updated
    assert repository.list() == (updated,)
    assert repository.delete_expired(updated.expires_at, 1) == 1
    with sqlite3.connect(db_path) as raw:
        schema = raw.execute("SELECT sql FROM sqlite_master WHERE name='log_sources'").fetchone()[0]
        values = raw.execute("SELECT * FROM log_sources").fetchall()
    assert "content" not in schema.lower()
    assert all("secret log content" not in str(value) for value in values)
    engine.dispose()


@pytest.mark.integration
def test_log_source_repository_rejects_non_normalized_stored_path(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.execute(
        """
        INSERT INTO log_sources (
            id, path, last_updated, observed_at, received_at, expires_at,
            producer_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run",
            "/var/log/../secret",
            "2026-07-11T09:59:58.000000Z",
            "2026-07-11T10:00:00.000000Z",
            "2026-07-11T10:00:30.000000Z",
            "2026-07-11T10:02:00.000000Z",
            "local",
            "2026-07-11T10:00:30.000000Z",
        ),
    )
    connection.commit()
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")

    with pytest.raises(LogStorageError, match="log_storage_unavailable"):
        SQLiteLogSourceRepository(engine).get("run")
    engine.dispose()


@pytest.mark.integration
def test_log_source_repository_maps_stored_nul_path_to_storage_error(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.execute(
        """
        INSERT INTO log_sources (
            id, path, last_updated, observed_at, received_at, expires_at,
            producer_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run",
            "/var/log/run\x00.log",
            "2026-07-11T09:59:58.000000Z",
            "2026-07-11T10:00:00.000000Z",
            "2026-07-11T10:00:30.000000Z",
            "2026-07-11T10:02:00.000000Z",
            "local",
            "2026-07-11T10:00:30.000000Z",
        ),
    )
    connection.commit()
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")

    with pytest.raises(LogStorageError, match="log_storage_unavailable"):
        SQLiteLogSourceRepository(engine).get("run")
    engine.dispose()


@pytest.mark.integration
def test_sqlite_cas_prevents_stale_concurrent_candidate_overwrite(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")
    repository = SQLiteObservationRepository(engine)
    service = ObservationService(repository)
    now = datetime.fromisoformat("2026-07-11T10:00:30+00:00")
    original = service.upsert(
        ObservationInput.model_validate(
            {
                "namespace": "eda",
                "name": "alive",
                "kind": "gauge",
                "value": 1,
                "status": "ok",
                "observed_at": "2026-07-11T10:00:00Z",
                "ttl_seconds": 120,
            }
        ),
        now=now,
    ).record
    stale_candidate = replace(
        original,
        value=2,
        observed_at=datetime.fromisoformat("2026-07-11T10:00:05+00:00"),
        expires_at=datetime.fromisoformat("2026-07-11T10:00:05+00:00")
        + timedelta(seconds=120),
    )
    newer = replace(
        original,
        value=3,
        observed_at=datetime.fromisoformat("2026-07-11T10:00:10+00:00"),
        expires_at=datetime.fromisoformat("2026-07-11T10:00:10+00:00")
        + timedelta(seconds=120),
    )

    assert repository.compare_and_swap(newer, original.observed_at) is True
    assert repository.compare_and_swap(stale_candidate, original.observed_at) is False
    assert repository.get(original.identity_key) == newer
    engine.dispose()


@pytest.mark.integration
def test_sqlite_repository_translates_corrupt_storage_rows(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.execute(
        """
        INSERT INTO observations (
            identity_key, namespace, name, kind, status, labels_json, details_json,
            observed_at, received_at, expires_at, producer_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "broken",
            "eda",
            "alive",
            "status",
            "unknown",
            "{}",
            "not-json",
            "2026-07-11T10:00:00.000000Z",
            "2026-07-11T10:00:00.000000Z",
            "2026-07-11T10:02:00.000000Z",
            "local",
            "2026-07-11T10:00:00.000000Z",
        ),
    )
    connection.commit()
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")

    with pytest.raises(ObservationStorageError, match="observation_storage_unavailable"):
        SQLiteObservationRepository(engine).get("broken")
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("labels_json", "[]"),
        ("labels_json", '{"server":1}'),
        ("details_json", "[]"),
        ("details_json", json.dumps({"blob": "x" * 16385})),
        ("kind", "histogram"),
        ("status", "healthy"),
        ("numeric_value", float("inf")),
        ("numeric_value", None),
        ("producer_id", "remote"),
        ("identity_key", "0" * 64),
        ("expires_at", "2026-07-11T10:00:00.000000Z"),
        ("observed_at", "2026-07-11T10:00:00+00:00"),
        ("received_at", "2026-07-11T10:00:30+00:00"),
        ("updated_at", 123),
    ],
)
def test_sqlite_repository_rejects_domain_invalid_storage_rows(
    tmp_path, column, corrupt_value
):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    initial_migration.upgrade(connection)
    observability_migration.upgrade(connection)
    connection.close()
    engine = create_engine(f"sqlite:///{db_path}")
    repository = SQLiteObservationRepository(engine)
    service = ObservationService(repository)
    record = service.upsert(
        ObservationInput.model_validate(
            {
                "namespace": "eda",
                "name": "alive",
                "kind": "gauge",
                "value": 1,
                "status": "ok",
                "observed_at": "2026-07-11T10:00:00Z",
                "ttl_seconds": 120,
            }
        ),
        now=datetime.fromisoformat("2026-07-11T10:00:30+00:00"),
    ).record
    lookup_key = corrupt_value if column == "identity_key" else record.identity_key
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE observations SET {column} = ? WHERE identity_key = ?",
            (corrupt_value, record.identity_key),
        )

    with pytest.raises(ObservationStorageError, match="observation_storage_unavailable"):
        repository.get(lookup_key)
    engine.dispose()

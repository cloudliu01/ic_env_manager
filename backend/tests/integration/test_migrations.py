import importlib.util
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from ic_env_guard.db.migrations import MIGRATIONS_DIR
from ic_env_guard.observations.models import (
    ObservationInput,
    ObservationQuery,
    ObservationStorageError,
)
from ic_env_guard.observations.service import ObservationService
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
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='existing_user_table'"
    ).fetchone()
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

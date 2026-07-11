from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from ic_env_guard.db.migrations import run_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.main import create_app
from ic_env_guard.observations.models import ObservationInput, ObservationSeriesLimit
from ic_env_guard.observations.service import ObservationService
from ic_env_guard.storage.observations import SQLiteObservationRepository

FORBIDDEN_LABELS = {"terminal_id", "command", "request_id", "source_ip", "token"}


@pytest.mark.integration
@pytest.mark.security
def test_metrics_do_not_use_forbidden_high_cardinality_labels(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    client = TestClient(create_app(token_file=token_file))

    response = client.get("/metrics")
    assert response.status_code == 200

    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            assert FORBIDDEN_LABELS.isdisjoint(sample.labels.keys())


@pytest.mark.integration
def test_series_cap_allows_update_but_atomically_rejects_new_identity(tmp_path):
    database = tmp_path / "state.db"
    run_migrations(database)
    repository = SQLiteObservationRepository(create_sqlite_engine(database), max_series=1)
    service = ObservationService(repository)
    now = datetime.now(UTC)

    def payload(name, observed_at):
        return ObservationInput.model_validate(
            {
                "namespace": "eda",
                "name": name,
                "kind": "gauge",
                "value": 1,
                "status": "ok",
                "observed_at": observed_at,
                "ttl_seconds": 120,
            }
        )

    service.upsert(payload("one", now), now=now)
    updated = service.upsert(payload("one", now + timedelta(seconds=1)), now=now)
    assert updated.created is False

    with pytest.raises(ObservationSeriesLimit, match="observation_series_limit"):
        service.upsert(payload("two", now), now=now)


@pytest.mark.integration
def test_concurrent_new_series_cannot_exceed_capacity(tmp_path):
    database = tmp_path / "state.db"
    run_migrations(database)
    engine = create_sqlite_engine(database)
    now = datetime.now(UTC)

    def insert(index):
        service = ObservationService(SQLiteObservationRepository(engine, max_series=1))
        payload = ObservationInput.model_validate(
            {
                "namespace": "eda",
                "name": f"series_{index}",
                "kind": "gauge",
                "value": index,
                "status": "ok",
                "observed_at": now,
                "ttl_seconds": 120,
            }
        )
        try:
            service.upsert(payload, now=now)
            return "created"
        except ObservationSeriesLimit:
            return "limited"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(insert, range(2)))

    assert sorted(outcomes) == ["created", "limited"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM observations").scalar_one() == 1

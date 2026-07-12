import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.discovery.models import (
    DiscoveryFingerprint,
    DiscoveryJob,
    DiscoveryState,
    DiscoveryTarget,
)
from ic_env_guard.main import create_app

AUTH = {"Authorization": "Bearer manager-secret"}


@pytest.mark.contract
def test_discovery_migration_extends_complete_0009_chain(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (10,)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"discovery_jobs", "discovery_results"} <= tables
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_discovery_result_target" in indexes
        assert "idx_discovery_job_state" in indexes


def _client(tmp_path, *, enabled):
    tmp_path.mkdir(parents=True, exist_ok=True)
    token = tmp_path / "manager.token"
    token.write_text("manager-secret\n", encoding="utf-8")
    token.chmod(0o600)
    discovery = {}
    profiles = []
    allowed = []
    if enabled:
        allowed = ["10.20.30.0/24"]
        profiles = [
            {
                "id": "eda-http",
                "type": "trusted_lan_http",
                "allowed_cidrs": allowed,
            }
        ]
        discovery = {
            "scopes": [
                {
                    "id": "lab",
                    "name": "Lab",
                    "cidr": "10.20.30.0/30",
                    "endpoints": [
                        {"port": 8765, "transport_profile_id": "eda-http"}
                    ],
                }
            ]
        }
    return TestClient(
        create_app(
            config=AppConfig(
                mode="control-plane",
                auth=AuthConfig(token_file=token),
                control_plane=ControlPlaneConfig(
                    audit_database=tmp_path / "manager.db",
                    allowed_agent_cidrs=allowed,
                    transport_profiles=profiles,
                    discovery=discovery,
                ),
            )
        )
    )


@pytest.mark.contract
def test_discovery_disabled_and_start_body_accepts_only_scope_id(tmp_path):
    disabled = _client(tmp_path / "disabled", enabled=False)
    scopes = disabled.get("/api/v2/discovery/scopes", headers=AUTH)
    assert scopes.status_code == 200
    assert scopes.json() == {"enabled": False, "scopes": []}
    assert "discovery.v2" not in disabled.get("/api/v2/runtime").json()["capabilities"]
    response = disabled.post(
        "/api/v2/discovery/jobs", headers=AUTH, json={"scope_id": "lab"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "discovery_disabled"

    enabled = _client(tmp_path / "enabled", enabled=True)
    assert enabled.get("/api/v2/runtime").json() == {
        "mode": "manager",
        "capabilities": [
            "fleet.v2",
            "agent-registry.v2",
            "discovery.v2",
            "trusted-lan-http.v1",
        ],
    }
    arbitrary = enabled.post(
        "/api/v2/discovery/jobs",
        headers=AUTH,
        json={"scope_id": "lab", "cidr": "10.0.0.0/8", "port": 22},
    )
    assert arbitrary.status_code == 422


@pytest.mark.contract
def test_discovery_start_get_cancel_and_results_contract(tmp_path):
    client = _client(tmp_path, enabled=True)
    started = client.post(
        "/api/v2/discovery/jobs", headers=AUTH, json={"scope_id": "lab"}
    )
    assert started.status_code == 201
    job_id = started.json()["job"]["job_id"]
    assert started.json()["job"]["scope_id"] == "lab"

    fetched = client.get(f"/api/v2/discovery/jobs/{job_id}", headers=AUTH)
    results = client.get(
        f"/api/v2/discovery/jobs/{job_id}/results", headers=AUTH
    )
    cancelled = client.post(
        f"/api/v2/discovery/jobs/{job_id}/cancel", headers=AUTH
    )
    assert fetched.status_code == results.status_code == 200
    assert cancelled.status_code in {200, 409}
    assert "results" in results.json()

    events = client.get("/api/control-plane/audit?limit=20", headers=AUTH).json()[
        "events"
    ]
    start = next(event for event in events if event["operation"] == "discovery.start")
    assert start["result"] in {"pending", "success", "failed"}


@pytest.mark.contract
def test_discovery_result_handoff_is_one_time_and_exact(tmp_path):
    client = _client(tmp_path, enabled=True)
    container = client.app.state.container
    now = datetime.now(UTC)
    with sqlite3.connect(tmp_path / "manager.db") as connection:
        audit = connection.execute(
            "INSERT INTO control_plane_audit_events(timestamp,operation,target,result,"
            "dispatch_state) VALUES (?,?,?,?,?)",
            (now.isoformat(), "discovery.start", "scope:lab", "pending", "not_dispatched"),
        ).lastrowid
        connection.commit()
    job = DiscoveryJob(
        "handoff-job", "lab", DiscoveryState.QUEUED, 1, 0, 0, False, None,
        audit, now + timedelta(seconds=120), now, now,
    )
    container.discovery_repository.create_job(job)
    container.discovery_repository.claim(job.job_id, now=now)
    container.discovery_repository.record_result(
        job.job_id,
        DiscoveryTarget("10.20.30.1", 8765, "eda-http", "http"),
        DiscoveryFingerprint("2"),
        None,
        now=now,
    )
    container.discovery_repository.finish(job.job_id, DiscoveryState.COMPLETED, now=now)
    result = container.discovery_repository.list_results(job.job_id)[0]
    body = {
        "base_url": result.canonical_url,
        "transport_profile_id": result.transport_profile_id,
        "discovery_result_id": result.result_id,
        "ssh": {"user": "edaops", "host": result.ip, "port": 22},
    }

    created = client.post("/api/v2/agent-enrollments", headers=AUTH, json=body)
    repeated = client.post("/api/v2/agent-enrollments", headers=AUTH, json=body)
    assert created.status_code == 201
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "agent_validation_changed"
    assert container.discovery_repository.get_result(
        result.result_id
    ).linked_enrollment_id == created.json()["enrollment_id"]


@pytest.mark.contract
def test_discovery_finish_finalizes_start_audit_with_real_counts(tmp_path):
    client = _client(tmp_path, enabled=True)

    class Fingerprinter:
        async def probe(self, target, **_kwargs):
            return DiscoveryFingerprint("2") if target.ip.endswith("1") else None

    client.app.state.container.discovery_service.fingerprinter = Fingerprinter()
    with client:
        started = client.post(
            "/api/v2/discovery/jobs", headers=AUTH, json={"scope_id": "lab"}
        )
        job_id = started.json()["job"]["job_id"]
        for _ in range(50):
            job = client.get(
                f"/api/v2/discovery/jobs/{job_id}", headers=AUTH
            ).json()["job"]
            if job["state"] == "completed":
                break
            time.sleep(0.01)
        assert job["checked_targets"] == job["total_targets"] == 2
        assert job["found_targets"] == 1

        events = client.get(
            "/api/control-plane/audit?limit=20", headers=AUTH
        ).json()["events"]
        start = next(
            event for event in events if event["operation"] == "discovery.start"
        )
        assert start["result"] == "success"
        assert start["dispatch_state"] == "dispatched"

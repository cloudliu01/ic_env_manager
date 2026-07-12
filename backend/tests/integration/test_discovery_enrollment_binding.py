import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.discovery.models import DiscoveryJob, DiscoveryState, DiscoveryTarget
from ic_env_guard.enrollment.jobs import (
    EnrollmentConflict,
    EnrollmentJobRequest,
    EnrollmentJobs,
)
from ic_env_guard.fleet.models import EnrollmentMethod
from ic_env_guard.storage.discovery import DiscoveryRepository
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _audit_id(database):
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO control_plane_audit_events(timestamp,operation,target,result,"
            "dispatch_state) VALUES (?,?,?,?,?)",
            (
                NOW.isoformat(),
                "discovery.start",
                "discovery:lab",
                "pending",
                "not_dispatched",
            ),
        )
        connection.commit()
        return cursor.lastrowid


def _candidate(database, repository):
    job = DiscoveryJob(
        job_id="discovery-1",
        scope_id="lab",
        state=DiscoveryState.QUEUED,
        total_targets=1,
        checked_targets=0,
        found_targets=0,
        cancel_requested=False,
        safe_error_code=None,
        start_audit_event_id=_audit_id(database),
        deadline_at=NOW + timedelta(seconds=120),
        created_at=NOW,
        updated_at=NOW,
    )
    repository.create_job(job)
    repository.claim(job.job_id, now=NOW)
    from ic_env_guard.discovery.models import DiscoveryFingerprint

    repository.record_result(
        job.job_id,
        DiscoveryTarget("10.20.30.1", 8765, "eda-http", "http"),
        DiscoveryFingerprint("2"),
        None,
        now=NOW,
    )
    repository.finish(job.job_id, DiscoveryState.COMPLETED, now=NOW)
    return repository.list_results(job.job_id)[0]


@pytest.mark.integration
def test_candidate_is_claimed_in_same_transaction_as_enrollment_create(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    discovery = DiscoveryRepository(engine)
    result = _candidate(database, discovery)
    jobs = EnrollmentJobs(
        EnrollmentJournalRepository(engine),
        manager_id="11111111-1111-4111-8111-111111111111",
        pending_ttl_seconds=600,
        max_active=16,
    )
    request = EnrollmentJobRequest(
        normalized_endpoint=result.canonical_url,
        transport_profile_id=result.transport_profile_id,
        discovery_result_id=result.result_id,
        ssh_user="edaops",
        ssh_host=result.ip,
        ssh_port=22,
        enrollment_method=EnrollmentMethod.SSH_AUTO,
    )
    try:
        enrollment = jobs.create(request, now=NOW)
        linked = discovery.get_result(result.result_id)
        assert linked.linked_enrollment_id == enrollment.enrollment_id
        assert enrollment.discovery_result_id == result.result_id

        with pytest.raises(EnrollmentConflict, match="agent_validation_changed"):
            jobs.create(request, now=NOW)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"normalized_endpoint": "http://10.20.30.2:8765"}, "agent_validation_changed"),
        ({"ssh_host": "10.20.30.2"}, "agent_validation_changed"),
        ({"transport_profile_id": "system-tls"}, "transport_profile_mismatch"),
    ],
)
def test_candidate_mismatch_rolls_back_without_claim(tmp_path, change, code):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    discovery = DiscoveryRepository(engine)
    result = _candidate(database, discovery)
    jobs = EnrollmentJobs(
        EnrollmentJournalRepository(engine),
        manager_id="11111111-1111-4111-8111-111111111111",
        pending_ttl_seconds=600,
        max_active=16,
    )
    fields = {
        "normalized_endpoint": result.canonical_url,
        "transport_profile_id": result.transport_profile_id,
        "discovery_result_id": result.result_id,
        "ssh_user": "edaops",
        "ssh_host": result.ip,
        "ssh_port": 22,
        "enrollment_method": EnrollmentMethod.SSH_AUTO,
        **change,
    }
    try:
        with pytest.raises(EnrollmentConflict, match=code):
            jobs.create(EnrollmentJobRequest(**fields), now=NOW)
        assert discovery.get_result(result.result_id).linked_enrollment_id is None
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("completed_at", "accepted"),
    [(NOW, True), (NOW - timedelta(days=1, seconds=1), False)],
)
def test_candidate_retention_is_measured_from_job_completion(
    tmp_path, completed_at, accepted
):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    discovery = DiscoveryRepository(engine)
    result = _candidate(database, discovery)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE discovery_jobs SET created_at=?,completed_at=? WHERE job_id=?",
                (
                    (NOW - timedelta(days=2)).isoformat(),
                    completed_at.isoformat(),
                result.job_id,
            ),
        )
    jobs = EnrollmentJobs(
        EnrollmentJournalRepository(engine),
        manager_id="11111111-1111-4111-8111-111111111111",
        pending_ttl_seconds=600,
        max_active=16,
        discovery_retention_seconds=86_400,
    )
    request = EnrollmentJobRequest(
        normalized_endpoint=result.canonical_url,
        transport_profile_id=result.transport_profile_id,
        discovery_result_id=result.result_id,
        ssh_user="edaops",
        ssh_host=result.ip,
        ssh_port=22,
        enrollment_method=EnrollmentMethod.SSH_AUTO,
    )
    try:
        if accepted:
            enrollment = jobs.create(request, now=NOW)
            assert enrollment.discovery_result_id == result.result_id
        else:
            with pytest.raises(EnrollmentConflict, match="agent_validation_changed"):
                jobs.create(request, now=NOW)
    finally:
        engine.dispose()

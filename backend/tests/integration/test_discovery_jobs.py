import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.config.models import ControlPlaneConfig
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.discovery.models import (
    DiscoveryFingerprint,
    DiscoveryState,
    DiscoveryTarget,
)
from ic_env_guard.discovery.service import DiscoveryService
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest, EnrollmentJobs
from ic_env_guard.fleet.models import EnrollmentMethod, RegistryConflict
from ic_env_guard.storage.discovery import DiscoveryRepository
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository


def _config():
    return ControlPlaneConfig(
        allowed_agent_cidrs=["10.20.30.0/24"],
        transport_profiles=[
            {
                "id": "eda-http",
                "type": "trusted_lan_http",
                "allowed_cidrs": ["10.20.30.0/24"],
            }
        ],
        discovery={
            "max_concurrency": 2,
            "scopes": [
                {
                    "id": "lab",
                    "name": "Lab",
                    "cidr": "10.20.30.0/30",
                    "endpoints": [
                        {"port": 8765, "transport_profile_id": "eda-http"}
                    ],
                }
            ],
        },
    )


def _audit_id(database):
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO control_plane_audit_events(timestamp,operation,target,result,"
            "dispatch_state) VALUES (?,?,?,?,?)",
            (
                datetime.now(UTC).isoformat(),
                "discovery.start",
                "scope:lab",
                "pending",
                "not_dispatched",
            ),
        )
        connection.commit()
        return cursor.lastrowid


@pytest.mark.integration
async def test_discovery_job_persists_bounded_progress_and_dedupes_results(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = DiscoveryRepository(engine)

    class Fingerprinter:
        active = 0
        peak = 0

        async def probe(self, target, *, connect_timeout, fingerprint_timeout):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            if target.ip.endswith("1"):
                return DiscoveryFingerprint(version="2")
            return None

    fingerprinter = Fingerprinter()
    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=repository,
        fingerprinter=fingerprinter,
        clock=lambda: datetime.now(UTC),
    )
    try:
        job = service.start("lab", start_audit_event_id=_audit_id(database))
        await service.wait(job.job_id)
        completed = repository.get_job(job.job_id)
        results = repository.list_results(job.job_id)

        assert completed.state is DiscoveryState.COMPLETED
        assert completed.checked_targets == completed.total_targets == 2
        assert completed.found_targets == 1
        assert len(results) == 2
        assert sum(result.found for result in results) == 1
        assert fingerprinter.peak <= 2
        assert len({(r.ip, r.port, r.transport_profile_id) for r in results}) == 2
    finally:
        await service.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_discovery_cancel_is_durable_and_stops_workers(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = DiscoveryRepository(engine)
    entered = asyncio.Event()
    release = asyncio.Event()

    class Fingerprinter:
        async def probe(self, target, *, connect_timeout, fingerprint_timeout):
            entered.set()
            await release.wait()
            return None

    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=repository,
        fingerprinter=Fingerprinter(),
        clock=lambda: datetime.now(UTC),
    )
    try:
        job = service.start("lab", start_audit_event_id=_audit_id(database))
        await entered.wait()
        service.cancel(job.job_id)
        release.set()
        await service.wait(job.job_id)
        assert repository.get_job(job.job_id).state is DiscoveryState.CANCELLED
    finally:
        await service.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_discovery_never_targets_manager_self_address(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    seen = []

    class Fingerprinter:
        async def probe(self, target, **_kwargs):
            seen.append(target.ip)
            return None

    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=DiscoveryRepository(engine),
        fingerprinter=Fingerprinter(),
        self_targets={"10.20.30.1"},
    )
    try:
        job = service.start("lab", start_audit_event_id=_audit_id(database))
        await service.wait(job.job_id)
        assert seen == ["10.20.30.2"]
        completed = service.get(job.job_id)
        assert completed.total_targets == completed.checked_targets == 2
        blocked = service.repository.list_results(job.job_id)[0]
        assert blocked.ip == "10.20.30.1"
        assert blocked.safe_error_code == "self_target_forbidden"
    finally:
        await service.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_concurrency_semaphore_is_shared_across_jobs(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    active = 0
    peak = 0
    release = asyncio.Event()
    saturated = asyncio.Event()

    class Fingerprinter:
        async def probe(self, target, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                saturated.set()
            await release.wait()
            active -= 1
            return None

    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=DiscoveryRepository(engine),
        fingerprinter=Fingerprinter(),
    )
    try:
        first = service.start("lab", start_audit_event_id=_audit_id(database))
        second = service.start("lab", start_audit_event_id=_audit_id(database))
        await asyncio.wait_for(saturated.wait(), timeout=1)
        await asyncio.sleep(0)
        assert peak == 2
        release.set()
        await service.wait(first.job_id)
        await service.wait(second.job_id)
    finally:
        release.set()
        await service.shutdown()
        engine.dispose()


@pytest.mark.integration
def test_discovery_active_job_capacity_is_globally_bounded(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = DiscoveryRepository(engine)
    now = datetime.now(UTC)
    from ic_env_guard.discovery.models import DiscoveryJob

    try:
        for index in range(32):
            repository.create_job(
                DiscoveryJob(
                    f"job-{index}", "lab", DiscoveryState.QUEUED, 0, 0, 0,
                    False, None, _audit_id(database), now + timedelta(seconds=120),
                    now, now,
                )
            )
        with pytest.raises(RegistryConflict, match="discovery_capacity"):
            repository.create_job(
                DiscoveryJob(
                    "overflow", "lab", DiscoveryState.QUEUED, 0, 0, 0,
                    False, None, _audit_id(database), now + timedelta(seconds=120),
                    now, now,
                )
            )
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_expired_jobs_do_not_permanently_consume_start_capacity(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = DiscoveryRepository(engine)
    now = datetime.now(UTC)
    from ic_env_guard.discovery.models import DiscoveryJob

    for index in range(32):
        repository.create_job(
            DiscoveryJob(
                f"expired-{index}", "lab", DiscoveryState.QUEUED, 0, 0, 0,
                False, None, _audit_id(database), now - timedelta(seconds=1),
                now - timedelta(seconds=2), now - timedelta(seconds=2),
            )
        )

    class Fingerprinter:
        async def probe(self, target, **_kwargs):
            return None

    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=repository,
        fingerprinter=Fingerprinter(),
        clock=lambda: now,
    )
    try:
        started = service.start("lab", start_audit_event_id=_audit_id(database))
        await service.wait(started.job_id)
        assert repository.get_job(started.job_id).state is DiscoveryState.COMPLETED
    finally:
        await service.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_discovery_restart_resumes_future_jobs_and_fails_expired_deadlines(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = DiscoveryRepository(engine)
    now = datetime.now(UTC)

    from ic_env_guard.discovery.models import DiscoveryJob

    future = DiscoveryJob(
        "future", "lab", DiscoveryState.QUEUED, 2, 0, 0, False, None,
        _audit_id(database), now + timedelta(seconds=120), now, now,
    )
    expired = DiscoveryJob(
        "expired", "lab", DiscoveryState.QUEUED, 2, 0, 0, False, None,
        _audit_id(database), now - timedelta(seconds=1), now, now,
    )
    repository.create_job(future)
    repository.claim(future.job_id, now=now)
    repository.record_result(
        future.job_id,
        DiscoveryTarget("10.20.30.1", 8765, "eda-http", "http"),
        None,
        "network_error",
        now=now,
    )
    repository.create_job(expired)
    cancel_crash = DiscoveryJob(
        "cancel-crash", "lab", DiscoveryState.QUEUED, 2, 0, 0, False, None,
        _audit_id(database), now + timedelta(seconds=120), now, now,
    )
    repository.create_job(cancel_crash)
    repository.request_cancel(cancel_crash.job_id, now=now)
    old = now - timedelta(days=2)
    stale = DiscoveryJob(
        "stale", "lab", DiscoveryState.QUEUED, 1, 0, 0, False, None,
        _audit_id(database), old + timedelta(seconds=120), old, old,
    )
    repository.create_job(stale)
    repository.claim(stale.job_id, now=old)
    repository.record_result(
        stale.job_id,
        DiscoveryTarget("10.20.30.1", 8765, "eda-http", "http"),
        DiscoveryFingerprint("2"),
        None,
        now=old,
    )
    repository.finish(stale.job_id, DiscoveryState.COMPLETED, now=old)
    enrollment_jobs = EnrollmentJobs(
        EnrollmentJournalRepository(engine),
        manager_id="11111111-1111-4111-8111-111111111111",
        pending_ttl_seconds=600,
        max_active=16,
    )
    linked = enrollment_jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint="http://10.20.30.1:8765",
            transport_profile_id="eda-http",
            ssh_user="edaops",
            ssh_host="10.20.30.1",
            ssh_port=22,
            enrollment_method=EnrollmentMethod.SSH_AUTO,
        ),
        now=now,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE discovery_results SET linked_enrollment_id=? WHERE job_id='stale'",
            (linked.enrollment_id,),
        )

    class Fingerprinter:
        seen = []

        async def probe(self, target, *, connect_timeout, fingerprint_timeout):
            self.seen.append(target.ip)
            return None

    service = DiscoveryService(
        config=_config().discovery,
        transport_profiles=_config().transport_profiles,
        repository=repository,
        fingerprinter=Fingerprinter(),
        clock=lambda: datetime.now(UTC),
    )
    try:
        await service.recover_and_cleanup()
        await service.wait("future")
        assert repository.get_job("future").state is DiscoveryState.COMPLETED
        assert service.fingerprinter.seen == ["10.20.30.2"]
        failed = repository.get_job("expired")
        assert failed.state is DiscoveryState.FAILED
        assert failed.safe_error_code == "job_timeout"
        assert repository.get_job("cancel-crash").state is DiscoveryState.CANCELLED
        assert repository.get_job("stale") is not None
        enrollment_jobs.cancel(linked.enrollment_id, now=now)
        repository.cleanup(retained_after=now - timedelta(days=1))
        assert repository.get_job("stale") is None
    finally:
        await service.shutdown()
        engine.dispose()

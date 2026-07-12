from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.jobs import (
    EnrollmentConflict,
    EnrollmentJobRequest,
    EnrollmentJobs,
    enrollment_input_fingerprint,
)
from ic_env_guard.fleet.models import EnrollmentMethod, EnrollmentState
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    repository = EnrollmentJournalRepository(engine)
    jobs = EnrollmentJobs(
        repository,
        manager_id=str(uuid4()),
        pending_ttl_seconds=600,
        max_active=1,
    )
    try:
        yield jobs, repository, engine
    finally:
        engine.dispose()


def ssh_request(**changes):
    values = {
        "normalized_endpoint": "https://10.20.30.40:8765",
        "transport_profile_id": "system-tls",
        "display_name": "Lab 01",
        "ssh_user": "edaops",
        "ssh_host": "10.20.30.40",
        "ssh_port": 22,
        "enrollment_method": EnrollmentMethod.SSH_CLI,
    }
    values.update(changes)
    return EnrollmentJobRequest(**values)


def advance(repository, job, *states):
    current = job
    for state in states:
        updated = replace(current, state=state, updated_at=NOW)
        repository.replace_if_state(updated, expected_state=current.state)
        current = updated
    return current


def test_create_capacity_ttl_and_cancel_are_durable_cas(setup):
    jobs, repository, _engine = setup
    first = jobs.create(ssh_request(), now=NOW)

    assert first.state is EnrollmentState.PENDING
    assert first.expires_at == NOW + timedelta(minutes=10)
    with pytest.raises(EnrollmentConflict, match="agent_enrollment_capacity"):
        jobs.create(ssh_request(display_name="Second"), now=NOW)

    cancelled = jobs.cancel(first.enrollment_id, now=NOW + timedelta(seconds=1))
    assert cancelled.state is EnrollmentState.CANCELLED
    assert repository.get(first.enrollment_id).state is EnrollmentState.CANCELLED


def test_expired_job_releases_capacity_and_cannot_be_cancelled(setup):
    jobs, repository, _engine = setup
    first = jobs.create(ssh_request(), now=NOW)

    second = jobs.create(
        ssh_request(display_name="Second"), now=first.expires_at
    )

    assert repository.get(first.enrollment_id).state is EnrollmentState.EXPIRED
    assert second.state is EnrollmentState.PENDING
    with pytest.raises(EnrollmentConflict, match="agent_enrollment_expired"):
        jobs.cancel(first.enrollment_id, now=first.expires_at)


def test_verifying_can_cancel_but_activation_cannot(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    advance(
        repository,
        pending,
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
    )

    assert jobs.cancel(pending.enrollment_id, now=NOW).state is EnrollmentState.CANCELLED

    activated = jobs.create(ssh_request(display_name="Other"), now=NOW)
    activated = advance(
        repository,
        activated,
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    )
    repository.replace_if_state(
        replace(
            activated,
            state=EnrollmentState.ACTIVATION_REQUESTED,
            save_requested=True,
            requested_display_name="Other",
        ),
        expected_state=EnrollmentState.VERIFIED,
    )
    with pytest.raises(EnrollmentConflict, match="agent_enrollment_not_cancellable"):
        jobs.cancel(activated.enrollment_id, now=NOW)


def test_input_fingerprint_and_single_consume_binding(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    advance(
        repository,
        pending,
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    )
    fingerprint = enrollment_input_fingerprint(ssh_request())

    with pytest.raises(EnrollmentConflict, match="agent_enrollment_input_changed"):
        jobs.consume(
            pending.enrollment_id,
            display_name="Lab 01",
            input_fingerprint="0" * 64,
            now=NOW,
        )

    requested = jobs.consume(
        pending.enrollment_id,
        display_name="Lab 01",
        input_fingerprint=fingerprint,
        now=NOW,
    )
    assert requested.state is EnrollmentState.ACTIVATION_REQUESTED
    assert requested.save_requested is True

    repository.replace_if_state(
        replace(requested, state=EnrollmentState.ACTIVATED),
        expected_state=EnrollmentState.ACTIVATION_REQUESTED,
    )
    repository.replace_if_state(
        replace(requested, state=EnrollmentState.CONSUMED),
        expected_state=EnrollmentState.ACTIVATED,
    )
    with pytest.raises(EnrollmentConflict, match="agent_enrollment_consumed"):
        jobs.consume(
            pending.enrollment_id,
            display_name="Lab 01",
            input_fingerprint=fingerprint,
            now=NOW,
        )


def test_activation_residual_does_not_expire_or_release_capacity(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    verified = advance(
        repository,
        pending,
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    )
    requested = repository.replace_if_state(
        replace(
            verified,
            state=EnrollmentState.ACTIVATION_REQUESTED,
            save_requested=True,
            requested_display_name="Lab 01",
        ),
        expected_state=EnrollmentState.VERIFIED,
    )

    assert jobs.get(requested.enrollment_id, now=requested.expires_at).state is (
        EnrollmentState.ACTIVATION_REQUESTED
    )
    with pytest.raises(EnrollmentConflict, match="agent_enrollment_capacity"):
        jobs.create(ssh_request(display_name="Second"), now=requested.expires_at)


def test_journal_serialization_contains_no_secret_shaped_fields(setup):
    jobs, repository, _engine = setup
    job = jobs.create(ssh_request(), now=NOW)
    serialized = repository.dump_serialized_rows()

    assert job.enrollment_id in serialized
    for forbidden in (
        "token",
        "Authorization",
        "SSH_AUTH_SOCK",
        "passphrase",
        "private_key",
        "ssh_output",
    ):
        assert forbidden not in serialized

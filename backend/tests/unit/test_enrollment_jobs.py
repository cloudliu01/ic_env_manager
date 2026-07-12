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
from ic_env_guard.enrollment.orchestrator import EnrollmentPublicResult
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
        updated = replace(
            current,
            state=state,
            updated_at=NOW,
            credential_temp_ref=(
                "a" * 48
                if state
                in {
                    EnrollmentState.CREDENTIAL_ISSUED,
                    EnrollmentState.VERIFYING,
                    EnrollmentState.VERIFIED,
                }
                else current.credential_temp_ref
            ),
            remote_instance_id=(
                "33333333-3333-4333-8333-333333333333"
                if state is EnrollmentState.VERIFIED
                else current.remote_instance_id
            ),
            remote_credential_id=(
                "remote-credential"
                if state is EnrollmentState.VERIFIED
                else current.remote_credential_id
            ),
        )
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


def test_recovery_claim_expires_ttl_phases_before_dispatch(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)

    claimed = repository.expire_and_claim_recovery(
        owner="11111111-1111-4111-8111-111111111111",
        now=pending.expires_at,
        lease_seconds=30,
    )

    assert claimed == ()
    assert repository.get(pending.enrollment_id).state is EnrollmentState.EXPIRED


def test_recovery_claim_is_exclusive_and_expired_lease_can_be_taken_over(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    running = repository.replace_if_state(
        replace(pending, state=EnrollmentState.RUNNING),
        expected_state=EnrollmentState.PENDING,
    )
    issued = repository.replace_if_state(
        replace(
            running,
            state=EnrollmentState.CREDENTIAL_ISSUED,
            credential_temp_ref="a" * 48,
        ),
        expected_state=EnrollmentState.RUNNING,
    )
    repository.replace_if_state(
        replace(issued, state=EnrollmentState.VERIFYING),
        expected_state=EnrollmentState.CREDENTIAL_ISSUED,
    )
    first_owner = "11111111-1111-4111-8111-111111111111"
    second_owner = "22222222-2222-4222-8222-222222222222"

    first = repository.expire_and_claim_recovery(
        owner=first_owner, now=NOW, lease_seconds=30
    )
    loser = repository.expire_and_claim_recovery(
        owner=second_owner, now=NOW, lease_seconds=30
    )
    takeover = repository.expire_and_claim_recovery(
        owner=second_owner, now=NOW + timedelta(seconds=31), lease_seconds=30
    )

    assert len(first) == 1
    assert first[0].recovery_owner == first_owner
    assert loser == ()
    assert len(takeover) == 1
    assert takeover[0].recovery_owner == second_owner


def test_recovery_claim_leases_cover_sequential_dispatch_order(setup):
    jobs, repository, _engine = setup
    jobs = EnrollmentJobs(
        repository,
        manager_id=jobs.manager_id,
        pending_ttl_seconds=600,
        max_active=2,
    )
    first = jobs.create(ssh_request(), now=NOW)
    second = jobs.create(
        ssh_request(
            normalized_endpoint="https://10.20.30.41:8765",
            ssh_host="10.20.30.41",
        ),
        now=NOW,
    )
    for job in (first, second):
        advance(
            repository,
            job,
            EnrollmentState.RUNNING,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
        )

    claimed = repository.expire_and_claim_recovery(
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
        lease_seconds=30,
    )

    lease_deadlines = sorted(job.recovery_lease_until for job in claimed)
    assert lease_deadlines == [
        NOW + timedelta(seconds=30),
        NOW + timedelta(seconds=60),
    ]


def test_activation_residual_is_claimed_without_business_ttl_expiry(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    current = pending
    for state in (
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        current = repository.replace_if_state(
            replace(
                current,
                state=state,
                credential_temp_ref=(
                    "b" * 48
                    if state is not EnrollmentState.RUNNING
                    else current.credential_temp_ref
                ),
                remote_instance_id=(
                    "33333333-3333-4333-8333-333333333333"
                    if state is EnrollmentState.VERIFIED
                    else current.remote_instance_id
                ),
                remote_credential_id=(
                    "remote-credential"
                    if state is EnrollmentState.VERIFIED
                    else current.remote_credential_id
                ),
            ),
            expected_state=current.state,
        )
    requested = repository.replace_if_state(
        replace(
            current,
            state=EnrollmentState.ACTIVATION_REQUESTED,
            save_requested=True,
            requested_display_name="Lab 01",
        ),
        expected_state=EnrollmentState.VERIFIED,
    )

    claimed = repository.expire_and_claim_recovery(
        owner="11111111-1111-4111-8111-111111111111",
        now=requested.expires_at + timedelta(hours=1),
        lease_seconds=30,
    )

    assert claimed[0].state is EnrollmentState.ACTIVATION_REQUESTED
    assert repository.get(requested.enrollment_id).state is (
        EnrollmentState.ACTIVATION_REQUESTED
    )


def test_phase_invariants_reject_missing_durable_credential_reference(setup):
    jobs, repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    running = repository.replace_if_state(
        replace(pending, state=EnrollmentState.RUNNING),
        expected_state=EnrollmentState.PENDING,
    )

    with pytest.raises(Exception, match="credential reference"):
        repository.replace_if_state(
            replace(running, state=EnrollmentState.CREDENTIAL_ISSUED),
            expected_state=EnrollmentState.RUNNING,
        )


@pytest.mark.parametrize(
    ("internal", "public"),
    (
        (EnrollmentState.CREDENTIAL_ISSUED, "verifying"),
        (EnrollmentState.ACTIVATION_REQUESTED, "running"),
        (EnrollmentState.ACTIVATED, "running"),
    ),
)
def test_public_projection_folds_internal_states_and_exposes_only_safe_error(
    setup, internal, public
):
    jobs, _repository, _engine = setup
    pending = jobs.create(ssh_request(), now=NOW)
    job = replace(
        pending,
        state=internal,
        credential_temp_ref="a" * 48,
        save_requested=internal
        in {EnrollmentState.ACTIVATION_REQUESTED, EnrollmentState.ACTIVATED},
        requested_display_name="Lab 01",
        remote_instance_id="33333333-3333-4333-8333-333333333333",
        remote_credential_id="remote-credential",
        last_error_code="agent_network_error",
        recovery_owner="11111111-1111-4111-8111-111111111111",
        recovery_lease_until=NOW + timedelta(seconds=30),
    )

    result = EnrollmentPublicResult(job).to_public_dict()

    assert result["state"] == public
    assert result["last_error_code"] == "agent_network_error"
    serialized = repr(result)
    assert internal.value not in serialized
    assert "recovery_owner" not in serialized
    assert "recovery_lease_until" not in serialized


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

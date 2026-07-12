from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.agent_client import (
    EnrollmentAgentClient,
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.credential_store import CredentialStore
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest, EnrollmentJobs
from ic_env_guard.enrollment.orchestrator import (
    EnrollmentOrchestrator,
    LegacyValidationRequest,
)
from ic_env_guard.fleet.models import EnrollmentMethod, EnrollmentState
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def setup_services(tmp_path, client):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    journal = EnrollmentJournalRepository(engine)
    registry = ManagerRegistryRepository(engine)
    store = CredentialStore(tmp_path / "credentials")
    jobs = EnrollmentJobs(
        journal,
        manager_id=str(registry.get_or_create_manager_id()),
        pending_ttl_seconds=600,
        max_active=16,
    )
    orchestrator = EnrollmentOrchestrator(
        jobs=jobs,
        journal=journal,
        credential_store=store,
        agent_client=client,
        registry=registry,
    )
    return orchestrator, jobs, journal, registry, store, engine


def validation():
    return EnrollmentValidation(
        normalized_endpoint="https://10.20.30.40:8765",
        api_version="1",
        agent_version="0.2.0",
        capabilities=("services.v1",),
        instance_id=None,
        summary=None,
        readiness_warning="legacy_readiness_unavailable",
    )


@pytest.mark.integration
async def test_legacy_credential_is_journaled_before_network_validation(tmp_path):
    observations = []

    class Client:
        def prepare(self, endpoint, _profile):
            class Target:
                normalized_endpoint = endpoint

            return Target()

        async def validate_legacy(self, _target, token):
            job = journal.list_non_terminal()[0]
            observations.append(
                (job.state, job.credential_temp_ref, store.read(job.credential_temp_ref))
            )
            assert token == b"legacy-never-serialized"
            return validation()

    client = Client()
    orchestrator, _jobs, journal, _registry, store, engine = setup_services(
        tmp_path, client
    )
    try:
        result = await orchestrator.validate_legacy(
            LegacyValidationRequest("https://10.20.30.40:8765", "system-tls"),
            "legacy-never-serialized",
        )
        assert result.job.state is EnrollmentState.VERIFIED
        assert observations[0][0] is EnrollmentState.VERIFYING
        assert observations[0][1]
        assert observations[0][2] == b"legacy-never-serialized"
        assert "legacy-never-serialized" not in journal.dump_serialized_rows()
        orchestrator._validation_cache.clear()
        await orchestrator.recover()
        assert orchestrator.get(result.job.enrollment_id).to_public_dict()["preview"][
            "agent"
        ]["api_version"] == "1"
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_startup_recovery_resumes_verification_before_orphan_cleanup(tmp_path):
    calls = []

    class Client:
        def prepare(self, endpoint, _profile):
            calls.append("prepare")

            class Target:
                normalized_endpoint = endpoint

            return Target()

        async def validate_legacy(self, _target, token):
            calls.append("validate")
            assert token == b"recover-me"
            return validation()

    orchestrator, jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            ),
            now=NOW,
        )
        reference = store.put(b"recover-me")
        running = journal.replace_if_state(
            replace(pending, state=EnrollmentState.RUNNING),
            expected_state=EnrollmentState.PENDING,
        )
        journal.replace_if_state(
            replace(
                running,
                state=EnrollmentState.CREDENTIAL_ISSUED,
                credential_temp_ref=reference,
            ),
            expected_state=EnrollmentState.RUNNING,
        )
        orphan = store.put(b"delete-after-recovery")

        await orchestrator.recover_and_cleanup()

        assert calls == ["prepare", "validate"]
        assert journal.get(pending.enrollment_id).state is EnrollmentState.VERIFIED
        assert store.read(reference) == b"recover-me"
        with pytest.raises(Exception, match="credential not found"):
            store.read(orphan)
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_recovery_finishes_activated_local_commit_and_consumes_once(tmp_path):
    orchestrator, jobs, journal, registry, store, engine = setup_services(
        tmp_path, client=None
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                display_name="Lab 01",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            ),
            now=NOW,
        )
        reference = store.put(b"legacy-token")
        current = pending
        for state in (
            EnrollmentState.RUNNING,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
            EnrollmentState.VERIFIED,
        ):
            updated = replace(
                current,
                state=state,
                credential_temp_ref=(
                    reference
                    if state
                    in {
                        EnrollmentState.CREDENTIAL_ISSUED,
                        EnrollmentState.VERIFYING,
                        EnrollmentState.VERIFIED,
                    }
                    else current.credential_temp_ref
                ),
            )
            current = journal.replace_if_state(updated, expected_state=current.state)
        requested = journal.replace_if_state(
            replace(
                current,
                state=EnrollmentState.ACTIVATION_REQUESTED,
                save_requested=True,
                requested_display_name="Lab 01",
            ),
            expected_state=EnrollmentState.VERIFIED,
        )
        journal.replace_if_state(
            replace(requested, state=EnrollmentState.ACTIVATED),
            expected_state=EnrollmentState.ACTIVATION_REQUESTED,
        )
        recoverable = journal.list_non_terminal()
        assert len(recoverable) == 1
        assert recoverable[0].state is EnrollmentState.ACTIVATED
        assert recoverable[0].save_requested is True
        assert recoverable[0].credential_temp_ref == reference

        await orchestrator.recover_and_cleanup()

        saved = registry.get(pending.enrollment_id)
        assert saved is not None
        assert saved.instance_id is None
        assert saved.credential_ref == reference
        consumed = journal.get(pending.enrollment_id)
        assert consumed.state is EnrollmentState.CONSUMED
        assert consumed.credential_temp_ref is None
        await orchestrator.recover_and_cleanup()
        assert registry.get(pending.enrollment_id) == saved
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_pending_validation_rejects_helper_http_identity_mismatch():
    class Response:
        status_code = 200

        def json(self):
            return {
                "api_version": "2",
                "agent_version": "0.2.0",
                "instance_id": "http-instance",
                "capabilities": ["manager-enrollment.v1"],
            }

    class HttpClient:
        async def request(self, *_args, **_kwargs):
            return Response()

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(EnrollmentValidationError) as caught:
        await client.validate_pending(
            object(),  # type: ignore[arg-type]
            b"pending-secret",
            helper_instance_id="helper-instance",
        )

    assert caught.value.code == "agent_identity_mismatch"
    assert caught.value.dispatch_state == "dispatched"


@pytest.mark.integration
async def test_recovery_transport_failure_retains_visible_credential_residual(tmp_path):
    class Client:
        def prepare(self, *_args):
            raise EnrollmentValidationError(
                "agent_network_error", dispatch_state="not_dispatched"
            )

    orchestrator, jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            ),
            now=NOW,
        )
        reference = store.put(b"retain-on-network-failure")
        running = journal.replace_if_state(
            replace(pending, state=EnrollmentState.RUNNING),
            expected_state=EnrollmentState.PENDING,
        )
        journal.replace_if_state(
            replace(
                running,
                state=EnrollmentState.CREDENTIAL_ISSUED,
                credential_temp_ref=reference,
            ),
            expected_state=EnrollmentState.RUNNING,
        )

        await orchestrator.recover_and_cleanup()

        residual = journal.get(pending.enrollment_id)
        assert residual.state is EnrollmentState.VERIFYING
        assert residual.credential_temp_ref == reference
        assert store.read(reference) == b"retain-on-network-failure"
    finally:
        engine.dispose()

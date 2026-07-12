import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ic_env_guard.agents.client import AgentClientError
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.agent_client import (
    EnrollmentAgentClient,
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.enrollment.jobs import (
    EnrollmentJobRequest,
    EnrollmentJobs,
    job_input_fingerprint,
)
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    EnrollmentOrchestrator,
    LegacyValidationRequest,
)
from ic_env_guard.fleet.models import EnrollmentMethod, EnrollmentState
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository
from ic_env_guard.summary.service import (
    AgentSummary,
    LogCounts,
    ObservationCounts,
    ServiceCounts,
    TerminalCounts,
)

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
        clock=lambda: NOW,
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
        recovered = orchestrator.get(result.job.enrollment_id)
        assert recovered.job.state is EnrollmentState.FAILED
        assert recovered.job.last_error_code == "enrollment_recovery_unavailable"
        assert recovered.job.credential_temp_ref is None
        assert len(observations) == 1
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

        assert calls == []
        residual = journal.get(pending.enrollment_id)
        assert residual.state is EnrollmentState.FAILED
        assert residual.last_error_code == "enrollment_recovery_unavailable"
        assert residual.credential_temp_ref is None
        with pytest.raises(Exception, match="credential not found"):
            store.read(reference)
        with pytest.raises(Exception, match="credential not found"):
            store.read(orphan)
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_ssh_recovery_uses_durable_pin_without_prepare_or_dns(tmp_path):
    calls = []

    class Client:
        def prepare(self, *_args):
            raise AssertionError("recovery must not resolve DNS")

        def prepare_pinned(self, endpoint, profile, stored_ip):
            calls.append(("pinned", endpoint, profile, stored_ip))
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_pending(self, _target, token, *, helper_instance_id):
            calls.append(("validate", token, helper_instance_id))
            return validation()

    orchestrator, jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://agent.example:8765",
                transport_profile_id="system-tls",
                ssh_user="edaops",
                ssh_host="agent.example",
                ssh_port=22,
                enrollment_method=EnrollmentMethod.SSH_AUTO,
            ),
            now=NOW,
        )
        reference = store.put(b"durably-pinned")
        running = journal.replace_if_state(
            replace(pending, state=EnrollmentState.RUNNING),
            expected_state=EnrollmentState.PENDING,
        )
        journal.replace_if_state(
            replace(
                running,
                state=EnrollmentState.CREDENTIAL_ISSUED,
                credential_temp_ref=reference,
                remote_instance_id="33333333-3333-4333-8333-333333333333",
                remote_credential_id="remote-credential",
                validated_http_address="10.20.30.40",
            ),
            expected_state=EnrollmentState.RUNNING,
        )

        await orchestrator.recover()

        recovered = journal.get(pending.enrollment_id)
        assert recovered.state is EnrollmentState.VERIFIED
        assert recovered.validated_http_address == "10.20.30.40"
        assert calls == [
            (
                "pinned",
                "https://agent.example:8765",
                "system-tls",
                "10.20.30.40",
            ),
            (
                "validate",
                b"durably-pinned",
                "33333333-3333-4333-8333-333333333333",
            ),
        ]
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
@pytest.mark.parametrize(
    "method", (EnrollmentMethod.SSH_AUTO, EnrollmentMethod.SSH_CLI)
)
async def test_ssh_activated_recovery_commits_registry_and_atomically_clears_ref_and_pin(
    tmp_path, method
):
    calls = []

    class Client:
        def prepare_pinned(self, endpoint, profile, stored_ip):
            calls.append((endpoint, profile, stored_ip))
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def activate(self, *_args, **_kwargs):
            raise AssertionError("already activated residual must not dispatch")

        def prepare_cli_target(
            self, endpoint, _profile, *, ssh_host, ssh_port, pinned_address
        ):
            assert (ssh_host, ssh_port) == ("agent.example", 22)
            return SimpleNamespace(
                normalized_endpoint=endpoint,
                pinned_address=pinned_address,
                profile=SimpleNamespace(),
            )

    client = Client()
    orchestrator, jobs, journal, registry, store, engine = setup_services(
        tmp_path, client
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://agent.example:8765",
                transport_profile_id="system-tls",
                display_name="Lab SSH",
                ssh_user="edaops",
                ssh_host="agent.example",
                ssh_port=22,
                enrollment_method=method,
            ),
            now=NOW,
        )
        reference = store.put(b"registry-owned")
        current = pending
        cli_nonce = "11111111-1111-4111-8111-111111111111"
        cli_fingerprint = job_input_fingerprint(pending)
        for state in (
            EnrollmentState.RUNNING,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
            EnrollmentState.VERIFIED,
            EnrollmentState.ACTIVATION_REQUESTED,
            EnrollmentState.ACTIVATED,
        ):
            current = journal.replace_if_state(
                replace(
                    current,
                    state=state,
                    credential_temp_ref=(
                        reference
                        if state is not EnrollmentState.RUNNING
                        else current.credential_temp_ref
                    ),
                    validated_http_address=(
                        "10.20.30.40"
                        if state is not EnrollmentState.RUNNING
                        else current.validated_http_address
                    ),
                    remote_instance_id=(
                        "33333333-3333-4333-8333-333333333333"
                        if state is not EnrollmentState.RUNNING
                        else current.remote_instance_id
                    ),
                    remote_credential_id=(
                        "remote-credential"
                        if state is not EnrollmentState.RUNNING
                        else current.remote_credential_id
                    ),
                    save_requested=state
                    in {
                        EnrollmentState.ACTIVATION_REQUESTED,
                        EnrollmentState.ACTIVATED,
                    },
                    requested_display_name="Lab SSH",
                    cli_resume_nonce=(
                        cli_nonce if method is EnrollmentMethod.SSH_CLI else None
                    ),
                    cli_peer_uid=501 if method is EnrollmentMethod.SSH_CLI else None,
                    cli_input_fingerprint=(
                        cli_fingerprint if method is EnrollmentMethod.SSH_CLI else None
                    ),
                    cli_pinned_address=(
                        "10.20.30.40" if method is EnrollmentMethod.SSH_CLI else None
                    ),
                ),
                expected_state=current.state,
            )

        await orchestrator.recover_and_cleanup()

        saved = registry.get(pending.enrollment_id)
        assert saved is not None
        assert saved.credential_ref == reference
        assert saved.instance_id == "33333333-3333-4333-8333-333333333333"
        consumed = journal.get(pending.enrollment_id)
        assert consumed.state is EnrollmentState.CONSUMED
        assert consumed.credential_temp_ref is None
        assert consumed.validated_http_address is None
        if method is EnrollmentMethod.SSH_CLI:
            assert consumed.cli_resume_nonce is None
            assert consumed.cli_peer_uid is None
            assert consumed.cli_input_fingerprint is None
            assert consumed.cli_pinned_address is None
            assert consumed.cli_accept_receipt is not None
            assert consumed.cli_accept_receipt not in journal.dump_serialized_rows()
            assert "cli_accept_receipt" not in journal.dump_serialized_rows()
        assert store.read(reference) == b"registry-owned"
        await orchestrator.recover_and_cleanup()
        assert registry.get(pending.enrollment_id) == saved
        assert calls == [
            ("https://agent.example:8765", "system-tls", "10.20.30.40")
        ]
        if method is EnrollmentMethod.SSH_CLI:
            class Audit:
                def record_cli_intent(self, *_args):
                    return 1

                def record_outcome(self, *_args, **_kwargs):
                    return None

            restarted = EnrollmentOrchestrator(
                jobs=jobs,
                journal=journal,
                credential_store=store,
                agent_client=client,
                registry=registry,
                auto_audit=Audit(),
                clock=lambda: NOW,
            )
            accepted = restarted.begin_cli_submission(
                enrollment_id=pending.enrollment_id,
                ssh_user="edaops",
                ssh_host="agent.example",
                ssh_port=22,
                pinned_address="10.20.30.40",
                peer_uid=501,
                resume_nonce=cli_nonce,
                context=AutoEnrollmentAuditContext(None, None, None),
            )
            assert accepted.already_accepted is True
            for peer_uid, nonce, pin in (
                (502, cli_nonce, "10.20.30.40"),
                (501, "22222222-2222-4222-8222-222222222222", "10.20.30.40"),
                (501, cli_nonce, "10.20.30.41"),
            ):
                with pytest.raises(EnrollmentValidationError, match="conflict"):
                    restarted.begin_cli_submission(
                        enrollment_id=pending.enrollment_id,
                        ssh_user="edaops",
                        ssh_host="agent.example",
                        ssh_port=22,
                        pinned_address=pin,
                        peer_uid=peer_uid,
                        resume_nonce=nonce,
                        context=AutoEnrollmentAuditContext(None, None, None),
                    )
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_registry_commit_then_consumed_fence_fault_converges_on_restart(
    tmp_path, monkeypatch
):
    class Client:
        def prepare_pinned(self, endpoint, _profile, _stored_ip):
            return SimpleNamespace(normalized_endpoint=endpoint)

    orchestrator, jobs, journal, registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://agent.example:8765",
                transport_profile_id="system-tls",
                display_name="Lab SSH",
                ssh_user="edaops",
                ssh_host="agent.example",
                ssh_port=22,
                enrollment_method=EnrollmentMethod.SSH_AUTO,
            ),
            now=NOW,
        )
        reference = store.put(b"registry-owned")
        current = pending
        for state in (
            EnrollmentState.RUNNING,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
            EnrollmentState.VERIFIED,
            EnrollmentState.ACTIVATION_REQUESTED,
            EnrollmentState.ACTIVATED,
        ):
            current = journal.replace_if_state(
                replace(
                    current,
                    state=state,
                    credential_temp_ref=(
                        reference
                        if state is not EnrollmentState.RUNNING
                        else current.credential_temp_ref
                    ),
                    validated_http_address=(
                        "10.20.30.40"
                        if state is not EnrollmentState.RUNNING
                        else current.validated_http_address
                    ),
                    remote_instance_id="33333333-3333-4333-8333-333333333333",
                    remote_credential_id="remote-credential",
                    save_requested=state
                    in {
                        EnrollmentState.ACTIVATION_REQUESTED,
                        EnrollmentState.ACTIVATED,
                    },
                    requested_display_name="Lab SSH",
                ),
                expected_state=current.state,
            )
        real_transition = orchestrator._transition_claimed
        monkeypatch.setattr(orchestrator, "_transition_claimed", lambda *_a, **_k: None)
        await orchestrator.recover()
        assert registry.get(pending.enrollment_id) is not None
        lost = journal.get(pending.enrollment_id)
        assert lost.state is EnrollmentState.ACTIVATED
        monkeypatch.setattr(orchestrator, "_transition_claimed", real_transition)
        orchestrator._clock = lambda: lost.recovery_lease_until + timedelta(seconds=1)

        await orchestrator.recover()

        assert journal.get(pending.enrollment_id).state is EnrollmentState.CONSUMED
        assert store.read(reference) == b"registry-owned"
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
                "instance_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "name": "Lab Agent",
                "capabilities": ["manager-enrollment.v1", "summary.v2"],
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
            SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
            b"pending-secret",
            helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )

    assert caught.value.code == "agent_identity_mismatch"
    assert caught.value.dispatch_state == "dispatched"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "code"),
    (
        ({"instance_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}, "agent_protocol_error"),
        ({"agent_version": "bad version"}, "agent_version_unsupported"),
        ({"capabilities": ["summary.v2"]}, "missing_capabilities"),
    ),
)
async def test_pending_validation_rejects_unsupported_identity_version_and_caps(
    overrides, code
):
    payload = {
        "api_version": "2",
        "agent_version": "0.2.0",
        "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "name": "Lab Agent",
        "capabilities": ["manager-enrollment.v1", "summary.v2"],
    }
    payload.update(overrides)

    class Response:
        status_code = 200

        def json(self):
            return payload

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
            SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
            b"pending-secret",
            helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
    assert caught.value.code == code


@pytest.mark.integration
async def test_pending_validation_accepts_exact_summary_and_warns_on_unhealthy_counts():
    real_summary = AgentSummary(
        observed_at=NOW,
        observations=ObservationCounts(total=1, warning=0, critical=1, stale=0),
        logs=LogCounts(total=0, stale=0),
        services=ServiceCounts(total=1, running=0, unhealthy=1),
        terminals=TerminalCounts(active=0),
    ).to_dict()
    responses = iter(
        (
            {
                "api_version": "2",
                "agent_version": "0.2.0",
                "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "name": "Lab Agent",
                "capabilities": ["manager-enrollment.v1", "summary.v2"],
            },
            real_summary,
        )
    )

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class HttpClient:
        async def request(self, *_args, **_kwargs):
            return Response(next(responses))

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )
    result = await client.validate_pending(
        SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
        b"pending-secret",
        helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert result.summary["services"]["unhealthy"] == 1
    assert result.readiness_warning == "agent_readiness_unhealthy"


@pytest.mark.integration
async def test_pending_validation_treats_malformed_summary_as_warning_only():
    responses = iter(
        (
            {
                "api_version": "2",
                "agent_version": "0.2.0",
                "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "name": "Lab Agent",
                "capabilities": ["manager-enrollment.v1", "summary.v2"],
            },
            {"arbitrary": "dict"},
        )
    )

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class HttpClient:
        async def request(self, *_args, **_kwargs):
            return Response(next(responses))

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )
    result = await client.validate_pending(
        SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
        b"pending-secret",
        helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert result.summary is None
    assert result.readiness_warning == "agent_readiness_unavailable"


@pytest.mark.integration
async def test_pending_validation_treats_oversized_summary_as_warning_only():
    capabilities = {
        "api_version": "2",
        "agent_version": "0.3.0",
        "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "name": "Lab Agent",
        "capabilities": ["manager-enrollment.v1", "summary.v2"],
    }

    class Response:
        status_code = 200

        def json(self):
            return capabilities

    class HttpClient:
        calls = 0

        async def request(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise AgentClientError(
                    "agent_protocol_error",
                    "agent response is too large",
                    dispatch_state="dispatched",
                )
            return Response()

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )
    result = await client.validate_pending(
        SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
        b"pending-secret",
        helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert result.summary is None
    assert result.readiness_warning == "agent_readiness_unavailable"


@pytest.mark.integration
async def test_pending_validation_accepts_rolling_agent_build_version():
    responses = iter(
        (
            {
                "api_version": "2",
                "agent_version": "0.3.0",
                "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "name": "Lab Agent",
                "capabilities": ["manager-enrollment.v1", "summary.v2"],
            },
            AgentSummary(
                observed_at=NOW,
                observations=ObservationCounts(total=0, warning=0, critical=0, stale=0),
                logs=LogCounts(total=0, stale=0),
                services=ServiceCounts(total=0, running=0, unhealthy=0),
                terminals=TerminalCounts(active=0),
            ).to_dict(),
        )
    )

    class Response:
        status_code = 200

        def json(self):
            return next(responses)

    class HttpClient:
        async def request(self, *_args, **_kwargs):
            return Response()

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )
    result = await client.validate_pending(
        SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
        b"pending-secret",
        helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert result.agent_version == "0.3.0"


@pytest.mark.integration
async def test_pending_summary_auth_failure_remains_a_gate():
    capabilities = {
        "api_version": "2",
        "agent_version": "0.2.0",
        "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "name": "Lab Agent",
        "capabilities": ["manager-enrollment.v1", "summary.v2"],
    }

    class Response:
        status_code = 200

        def json(self):
            return capabilities

    class HttpClient:
        calls = 0

        async def request(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return Response()
            raise AgentClientError(
                "agent_auth_error", "pending credential rejected", dispatch_state="dispatched"
            )

    client = EnrollmentAgentClient(
        target_policy=None,  # type: ignore[arg-type]
        transport_profiles=(),
        client=HttpClient(),  # type: ignore[arg-type]
    )
    with pytest.raises(EnrollmentValidationError) as caught:
        await client.validate_pending(
            SimpleNamespace(normalized_endpoint="https://10.20.30.40:8765"),  # type: ignore[arg-type]
            b"pending-secret",
            helper_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
    assert caught.value.code == "agent_auth_error"


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
        assert residual.state is EnrollmentState.FAILED
        assert residual.last_error_code == "enrollment_recovery_unavailable"
        assert residual.credential_temp_ref is None
        with pytest.raises(Exception, match="credential not found"):
            store.read(reference)
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_concurrent_recovery_claim_dispatches_network_once(tmp_path):
    calls = 0

    class Client:
        def prepare(self, endpoint, _profile):
            class Target:
                normalized_endpoint = endpoint

            return Target()

        async def validate_legacy(self, _target, _token):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return validation()

    client = Client()
    first, jobs, journal, registry, store, engine = setup_services(tmp_path, client)
    second = EnrollmentOrchestrator(
        jobs=jobs,
        journal=journal,
        credential_store=CredentialStore(store.directory),
        agent_client=client,
        registry=registry,
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            )
        )
        reference = store.put(b"single-dispatch")
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

        await asyncio.gather(first.recover(), second.recover())

        assert calls == 0
        assert journal.get(pending.enrollment_id).last_error_code == (
            "enrollment_recovery_unavailable"
        )
        assert journal.get(pending.enrollment_id).state is EnrollmentState.FAILED
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_recovery_renews_one_second_lease_during_slow_network_call(tmp_path):
    calls = 0

    class Client:
        def prepare(self, endpoint, _profile):
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, _target, _token):
            nonlocal calls
            calls += 1
            await asyncio.sleep(1.4)
            return validation()

    first, jobs, journal, registry, store, engine = setup_services(tmp_path, Client())
    monotonic_origin = asyncio.get_running_loop().time()
    wall_origin = datetime.now(UTC)

    def clock():
        elapsed = asyncio.get_running_loop().time() - monotonic_origin
        return wall_origin + timedelta(seconds=elapsed)

    first._clock = clock
    second = EnrollmentOrchestrator(
        jobs=jobs,
        journal=journal,
        credential_store=CredentialStore(store.directory),
        agent_client=first.agent_client,
        registry=registry,
        clock=clock,
    )
    first._recovery_lease_seconds = second._recovery_lease_seconds = 1
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            )
        )
        reference = store.put(b"renewed-single-dispatch")
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

        first_recovery = asyncio.create_task(first.recover())
        await asyncio.sleep(1.05)
        await second.recover()
        await first_recovery

        assert calls == 0
        assert journal.get(pending.enrollment_id).state is EnrollmentState.FAILED
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_recovery_discards_network_result_when_renewal_fence_is_lost(
    tmp_path, monkeypatch
):
    cancelled = False

    class Client:
        def prepare(self, endpoint, _profile):
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, _target, _token):
            nonlocal cancelled
            try:
                await asyncio.sleep(1.4)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return validation()

    orchestrator, jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    orchestrator._recovery_lease_seconds = 1
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            )
        )
        reference = store.put(b"discard-stale-result")
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
        monkeypatch.setattr(journal, "renew_recovery_claim", lambda *_args, **_kwargs: None)

        await orchestrator.recover()

        current = journal.get(pending.enrollment_id)
        assert cancelled is False
        assert current.state is EnrollmentState.FAILED
        assert pending.enrollment_id not in orchestrator._validation_cache
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_terminal_cleanup_preserves_business_validation_error(tmp_path):
    class Client:
        def prepare(self, endpoint, _profile):
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, *_args):
            raise EnrollmentValidationError(
                "agent_auth_error", dispatch_state="dispatched"
            )

    orchestrator, _jobs, journal, _registry, _store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        with pytest.raises(EnrollmentValidationError, match="agent_auth_error"):
            await orchestrator.validate_legacy(
                LegacyValidationRequest("https://10.20.30.40:8765", "system-tls"),
                "business-error-survives-cleanup",
            )
        with engine.connect() as connection:
            enrollment_id = connection.exec_driver_sql(
                "SELECT enrollment_id FROM agent_enrollment_jobs"
            ).scalar_one()
        failed = journal.get(enrollment_id)
        public = orchestrator.get(enrollment_id).to_public_dict()
        assert failed.state is EnrollmentState.FAILED
        assert failed.credential_temp_ref is None
        assert failed.last_error_code == "agent_auth_error"
        assert public["last_error_code"] == "agent_auth_error"
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_recovery_expires_before_any_network_dispatch(tmp_path):
    class Client:
        def prepare(self, *_args):
            raise AssertionError("expired enrollment must not dispatch")

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
            now=datetime(2020, 1, 1, tzinfo=UTC),
        )
        reference = store.put(b"expired")
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

        await orchestrator.recover()

        assert journal.get(pending.enrollment_id).state is EnrollmentState.EXPIRED
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_cancelled_verified_enrollment_deletes_secret_then_clears_reference(tmp_path):
    class Client:
        def prepare(self, endpoint, _profile):
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, *_args):
            return validation()

    orchestrator, _jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        verified = await orchestrator.validate_legacy(
            LegacyValidationRequest("https://10.20.30.40:8765", "system-tls"),
            "delete-on-cancel",
        )
        reference = verified.job.credential_temp_ref

        cancelled = await orchestrator.cancel(verified.job.enrollment_id)

        assert cancelled.job.state is EnrollmentState.CANCELLED
        assert journal.get(verified.job.enrollment_id).credential_temp_ref is None
        with pytest.raises(Exception, match="credential not found"):
            store.read(reference)
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_terminal_delete_failure_keeps_visible_retryable_residual(
    tmp_path, monkeypatch
):
    class Client:
        def prepare(self, endpoint, _profile):
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, *_args):
            return validation()

    orchestrator, _jobs, journal, _registry, store, engine = setup_services(
        tmp_path, Client()
    )
    try:
        verified = await orchestrator.validate_legacy(
            LegacyValidationRequest("https://10.20.30.40:8765", "system-tls"),
            "retain-on-delete-failure",
        )
        reference = verified.job.credential_temp_ref
        real_delete = store.delete_if_exists
        monkeypatch.setattr(
            store,
            "delete_if_exists",
            lambda _reference: (_ for _ in ()).throw(
                CredentialStoreError("disk failure")
            ),
        )

        residual = await orchestrator.cancel(verified.job.enrollment_id)

        assert residual.job.credential_temp_ref == reference
        assert residual.job.last_error_code == "credential_cleanup_failed"
        assert store.read(reference) == b"retain-on-delete-failure"
        monkeypatch.setattr(store, "delete_if_exists", real_delete)
        await orchestrator.recover_and_cleanup()
        assert journal.get(verified.job.enrollment_id).credential_temp_ref is None
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_startup_recovers_crash_after_delete_before_reference_clear(tmp_path):
    orchestrator, jobs, journal, _registry, store, engine = setup_services(
        tmp_path, client=None
    )
    try:
        pending = jobs.create(
            EnrollmentJobRequest(
                normalized_endpoint="https://10.20.30.40:8765",
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            ),
            now=datetime(2020, 1, 1, tzinfo=UTC),
        )
        reference = store.put(b"deleted-before-clear")
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
        store.delete(reference)

        await orchestrator.recover_and_cleanup()

        expired = journal.get(pending.enrollment_id)
        assert expired.state is EnrollmentState.EXPIRED
        assert expired.credential_temp_ref is None
    finally:
        engine.dispose()

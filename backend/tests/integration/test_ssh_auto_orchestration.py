import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.db.control_plane_audit import ControlPlaneAuditEvent
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_session_factory, create_sqlite_engine
from ic_env_guard.enrollment.agent_client import (
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.audit import ManagerAutoEnrollmentAudit
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest, EnrollmentJobs
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    EnrollmentOrchestrator,
)
from ic_env_guard.enrollment.ssh import (
    EnrollmentHelperResult,
    SshEnrollmentError,
)
from ic_env_guard.fleet.models import EnrollmentMethod, EnrollmentState, RegistryError
from ic_env_guard.fleet.transport import TrustedLanHttpProfile
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
ENROLLMENT_ID = "22222222-2222-4222-8222-222222222222"
INSTANCE_ID = "33333333-3333-4333-8333-333333333333"
CREDENTIAL_ID = "44444444-4444-4444-8444-444444444444"
TOKEN = b"eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
PROFILE = TrustedLanHttpProfile(id="lab-http", allowed_cidrs=["10.0.0.0/8"])
VALIDATION_TARGET = type(
    "ValidationTarget", (), {"normalized_endpoint": "http://10.20.30.40:8765"}
)()
AUDIT_CONTEXT = AutoEnrollmentAuditContext(
    actor_id="local-admin",
    source_addr="127.0.0.1",
    correlation_id="corr-ssh-auto",
)


class Audit:
    def __init__(self, *, fail_intent=False, fail_outcome=False):
        self.fail_intent = fail_intent
        self.fail_outcome = fail_outcome
        self.events = []

    def record_intent(self, enrollment_id, context):
        self.events.append(("intent", enrollment_id, context))
        if self.fail_intent:
            raise RuntimeError("audit unavailable")
        return 17

    def record_outcome(self, event_id, *, result, dispatch_state, failure_category=None):
        self.events.append(
            ("outcome", event_id, result, dispatch_state, failure_category)
        )
        if self.fail_outcome:
            raise RuntimeError("outcome storage unavailable")


class AgentClient:
    def __init__(self):
        self.calls = 0

    def prepare(self, endpoint, _profile_id):
        raise AssertionError("auto enrollment must not resolve the Agent target again")

    async def validate_pending(self, _target, token, *, helper_instance_id):
        self.calls += 1
        assert token == TOKEN
        assert helper_instance_id == INSTANCE_ID
        return EnrollmentValidation(
            normalized_endpoint="http://10.20.30.40:8765",
            api_version="2",
            agent_version="0.3.0",
            capabilities=("manager-enrollment.v1", "summary.v2"),
            instance_id=INSTANCE_ID,
            summary=None,
            readiness_warning="agent_readiness_unavailable",
        )


class Adapter:
    healthy = True

    def __init__(self, outcome=None):
        self.outcome = outcome or EnrollmentHelperResult(
            instance_id=INSTANCE_ID,
            credential_id=CREDENTIAL_ID,
            token=TOKEN,
            expires_at=NOW + timedelta(minutes=5),
            validation_target=VALIDATION_TARGET,
        )
        self.calls = 0

    async def issue(self, request, profile):
        self.calls += 1
        assert request.manager_id
        assert request.enrollment_id
        assert profile == PROFILE
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if callable(self.outcome):
            return await self.outcome()
        return self.outcome


def request():
    return EnrollmentJobRequest(
        normalized_endpoint="http://10.20.30.40:8765",
        transport_profile_id="lab-http",
        display_name="Lab 01",
        ssh_user="edaops",
        ssh_host="agent.lab.example",
        ssh_port=2222,
        enrollment_method=EnrollmentMethod.SSH_AUTO,
    )


def setup_services(
    tmp_path, adapter, *, audit=None, store=None, shutdown_timeout_seconds=1.0
):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    journal = EnrollmentJournalRepository(engine)
    registry = ManagerRegistryRepository(engine)
    jobs = EnrollmentJobs(
        journal,
        manager_id=str(registry.get_or_create_manager_id()),
        pending_ttl_seconds=600,
        max_active=16,
    )
    credential_store = store or CredentialStore(tmp_path / "credentials")
    client = AgentClient()
    orchestrator = EnrollmentOrchestrator(
        jobs=jobs,
        journal=journal,
        credential_store=credential_store,
        agent_client=client,
        registry=registry,
        ssh_adapter=adapter,
        transport_profiles=(PROFILE,),
        auto_audit=audit or Audit(),
        clock=lambda: NOW,
        background_shutdown_timeout_seconds=shutdown_timeout_seconds,
    )
    return orchestrator, jobs, journal, credential_store, client, engine


@pytest.mark.integration
async def test_create_auto_returns_running_then_background_persists_and_verifies(tmp_path):
    adapter = Adapter()
    audit = Audit()
    orchestrator, _jobs, journal, store, client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)

        assert created.job.state is EnrollmentState.RUNNING
        await orchestrator.wait_for_background()
        verified = journal.get(created.job.enrollment_id)
        assert verified.state is EnrollmentState.VERIFIED
        assert verified.remote_instance_id == INSTANCE_ID
        assert verified.remote_credential_id == CREDENTIAL_ID
        assert store.read(verified.credential_temp_ref) == TOKEN
        assert client.calls == 1
        assert [event[0] for event in audit.events] == ["intent", "outcome"]
        assert audit.events[-1][2:4] == ("success", "dispatched")
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "state", "code"),
    (
        (
            SshEnrollmentError("ssh_interaction_required", dispatch_state="dispatched"),
            EnrollmentState.AWAITING_CLI,
            "ssh_interaction_required",
        ),
        (
            SshEnrollmentError("ssh_host_key_changed", dispatch_state="dispatched"),
            EnrollmentState.FAILED,
            "ssh_host_key_changed",
        ),
        (
            SshEnrollmentError("ssh_unavailable", dispatch_state="not_dispatched"),
            EnrollmentState.FAILED,
            "ssh_unavailable",
        ),
    ),
)
async def test_auto_error_converges_to_cli_or_failed_state(tmp_path, error, state, code):
    audit = Audit()
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, Adapter(error), audit=audit
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        current = journal.get(created.job.enrollment_id)
        assert current.state is state
        assert current.last_error_code == code
        assert audit.events[-1][3] == error.dispatch_state
        assert audit.events[-1][4] == code
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_unhealthy_adapter_immediately_returns_awaiting_cli_without_task(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, _journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        assert created.job.state is EnrollmentState.AWAITING_CLI
        assert created.job.last_error_code == "ssh_unavailable"
        assert adapter.calls == 0
        assert orchestrator.background_task_count == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_auto_audit_intent_failure_is_fail_closed_before_ssh(tmp_path):
    adapter = Adapter()
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter, audit=Audit(fail_intent=True)
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        current = journal.get(created.job.enrollment_id)
        assert current.state is EnrollmentState.FAILED
        assert current.last_error_code == "audit_unavailable"
        assert adapter.calls == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_shutdown_cancels_and_awaits_background_auto_task(tmp_path):
    started = asyncio.Event()
    cancelled = False

    async def blocked():
        nonlocal cancelled
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, Adapter(blocked)
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await started.wait()
        await orchestrator.shutdown()
        assert cancelled is True
        assert orchestrator.background_task_count == 0
        assert journal.get(created.job.enrollment_id).state is EnrollmentState.RUNNING
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_shutdown_is_bounded_when_adapter_swallows_cancel_and_late_result_cannot_publish(
    tmp_path,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def ignores_cancel():
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return EnrollmentHelperResult(
            instance_id=INSTANCE_ID,
            credential_id=CREDENTIAL_ID,
            token=TOKEN,
            expires_at=NOW + timedelta(minutes=5),
            validation_target=VALIDATION_TARGET,
        )

    class CountingStore(CredentialStore):
        puts = 0

        def put(self, secret):
            self.puts += 1
            return super().put(secret)

    audit = Audit()
    store = CountingStore(tmp_path / "credentials")
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path,
        Adapter(ignores_cancel),
        audit=audit,
        store=store,
        shutdown_timeout_seconds=0.02,
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await started.wait()
        background = next(iter(orchestrator._background_tasks.values()))

        await asyncio.wait_for(orchestrator.shutdown(), timeout=0.2)

        assert orchestrator.background_task_count == 0
        assert [event[0] for event in audit.events] == ["intent"]
        release.set()
        await asyncio.gather(background, return_exceptions=True)
        assert store.puts == 0
        current = journal.get(created.job.enrollment_id)
        assert current.state is EnrollmentState.RUNNING
        assert current.credential_temp_ref is None
        assert [event[0] for event in audit.events] == ["intent"]
    finally:
        release.set()
        engine.dispose()


@pytest.mark.integration
async def test_cancel_fences_background_before_helper_result_can_write_credential(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return EnrollmentHelperResult(
            instance_id=INSTANCE_ID,
            credential_id=CREDENTIAL_ID,
            token=TOKEN,
            expires_at=NOW + timedelta(minutes=5),
            validation_target=VALIDATION_TARGET,
        )

    class CountingStore(CredentialStore):
        puts = 0

        def put(self, secret):
            self.puts += 1
            return super().put(secret)

    store = CountingStore(tmp_path / "credentials")
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, Adapter(blocked), store=store
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await started.wait()

        cancelled = await orchestrator.cancel(created.job.enrollment_id)
        release.set()
        await orchestrator.wait_for_background()

        current = journal.get(created.job.enrollment_id)
        assert cancelled.job.state is EnrollmentState.CANCELLED
        assert current.state is EnrollmentState.CANCELLED
        assert current.credential_temp_ref is None
        assert store.puts == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_startup_does_not_redispatch_running_job_with_unknown_helper_result(tmp_path):
    adapter = Adapter()
    orchestrator, jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        pending = jobs.create(request(), now=NOW)
        journal.replace_if_state(
            replace(pending, state=EnrollmentState.RUNNING),
            expected_state=EnrollmentState.PENDING,
        )

        await orchestrator.recover_and_cleanup()

        current = journal.get(pending.enrollment_id)
        assert current.state is EnrollmentState.AWAITING_CLI
        assert current.last_error_code == "ssh_interaction_required"
        assert adapter.calls == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_startup_schedules_pending_auto_job_exactly_once(tmp_path):
    adapter = Adapter()
    orchestrator, jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        pending = jobs.create(request(), now=NOW)

        await orchestrator.recover_and_cleanup()
        await orchestrator.recover_and_cleanup()
        await orchestrator.wait_for_background()

        assert adapter.calls == 1
        assert journal.get(pending.enrollment_id).state is EnrollmentState.VERIFIED
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_startup_atomically_expires_pending_auto_before_scheduling(tmp_path):
    adapter = Adapter()
    orchestrator, jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        pending = jobs.create(request(), now=NOW - timedelta(minutes=10))

        await orchestrator.recover_and_cleanup()

        assert journal.get(pending.enrollment_id).state is EnrollmentState.EXPIRED
        assert adapter.calls == 0
        assert orchestrator.background_task_count == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_cancel_after_audit_intent_is_rechecked_before_ssh_dispatch(tmp_path):
    adapter = Adapter()
    audit = Audit()
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )

    def cancel_during_intent(enrollment_id, context):
        event_id = Audit.record_intent(audit, enrollment_id, context)
        current = journal.get(enrollment_id)
        journal.replace_if_state(
            replace(current, state=EnrollmentState.CANCELLED, updated_at=NOW),
            expected_state=EnrollmentState.RUNNING,
        )
        return event_id

    audit.record_intent = cancel_during_intent
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()

        assert journal.get(created.job.enrollment_id).state is EnrollmentState.CANCELLED
        assert adapter.calls == 0
        assert audit.events[-1][3] == "not_dispatched"
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_credential_store_failure_after_helper_is_visible_and_secret_safe(tmp_path):
    class FailingStore(CredentialStore):
        def put(self, _secret):
            raise CredentialStoreError("path and token must stay private")

    store = FailingStore(tmp_path / "credentials")
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, Adapter(), store=store
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        current = journal.get(created.job.enrollment_id)
        assert current.state is EnrollmentState.FAILED
        assert current.last_error_code == "credential_store_unavailable"
        assert TOKEN.decode() not in journal.dump_serialized_rows()
        assert "path" not in journal.dump_serialized_rows()
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
def test_auto_audit_uses_independent_durable_intent_and_outcome(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    audit = ManagerAutoEnrollmentAudit(sessions)
    try:
        event_id = audit.record_intent(ENROLLMENT_ID, AUDIT_CONTEXT)
        audit.record_outcome(
            event_id,
            result="failure",
            dispatch_state="dispatched",
            failure_category="ssh_auth_failed",
        )

        with sessions() as session:
            row = session.get(ControlPlaneAuditEvent, event_id)
            assert row.operation == "agent-enrollment.ssh-auto"
            assert row.target == f"enrollment:{ENROLLMENT_ID}"
            assert row.actor_id == "local-admin"
            assert row.source_addr == "127.0.0.1"
            assert row.correlation_id == "corr-ssh-auto"
            assert row.result == "failure"
            assert row.dispatch_state == "dispatched"
            assert row.failure_category == "ssh_auth_failed"
            assert TOKEN.decode() not in repr(row.to_safe_dict())
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_audit_outcome_failure_leaves_diagnostic_pending_without_redispatch(tmp_path):
    adapter = Adapter()
    audit = Audit(fail_outcome=True)
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        assert adapter.calls == 1
        assert journal.get(created.job.enrollment_id).state is EnrollmentState.VERIFIED
        assert orchestrator.background_task_count == 0
        assert [event[0] for event in audit.events] == ["intent", "outcome"]
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_journal_failure_after_secret_put_deletes_orphan_and_is_visible(
    tmp_path, monkeypatch
):
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, Adapter()
    )
    real_replace = journal.replace_if_state
    failed_once = False

    def fail_credential_issue(job, **kwargs):
        nonlocal failed_once
        if job.state is EnrollmentState.CREDENTIAL_ISSUED and not failed_once:
            failed_once = True
            raise RegistryError("journal unavailable")
        return real_replace(job, **kwargs)

    monkeypatch.setattr(journal, "replace_if_state", fail_credential_issue)
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        current = journal.get(created.job.enrollment_id)
        assert current.state is EnrollmentState.FAILED
        assert current.last_error_code == "storage_unavailable"
        assert current.credential_temp_ref is None
        assert [path.name for path in store.directory.iterdir()] == [
            ".credential-store.lock"
        ]
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_http_validation_failure_keeps_recoverable_secret_and_exact_audit_code(
    tmp_path, monkeypatch
):
    audit = Audit()
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, Adapter(), audit=audit
    )

    async def fail_validation(*_args, **_kwargs):
        raise EnrollmentValidationError(
            "agent_auth_error", dispatch_state="dispatched"
        )

    monkeypatch.setattr(orchestrator.agent_client, "validate_pending", fail_validation)
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        current = journal.get(created.job.enrollment_id)
        assert current.state is EnrollmentState.VERIFYING
        assert current.credential_temp_ref is not None
        assert store.read(current.credential_temp_ref) == TOKEN
        assert audit.events[-1][2:] == (
            "failure",
            "dispatched",
            "agent_auth_error",
        )
    finally:
        await orchestrator.shutdown()
        engine.dispose()

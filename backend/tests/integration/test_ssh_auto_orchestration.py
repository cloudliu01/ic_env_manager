import asyncio
import io
import json
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditEvent
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_session_factory, create_sqlite_engine
from ic_env_guard.enrollment.agent_client import (
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.audit import ManagerAutoEnrollmentAudit
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.enrollment.helper import run_helper
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest, EnrollmentJobs
from ic_env_guard.enrollment.manager_socket import ManagerEnrollmentSocket
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    EnrollmentOrchestrator,
    _cli_accept_receipt,
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
    "ValidationTarget",
    (),
    {
        "normalized_endpoint": "http://10.20.30.40:8765",
        "pinned_address": "10.20.30.40",
        "profile": PROFILE,
    },
)()
AUDIT_CONTEXT = AutoEnrollmentAuditContext(
    actor_id="local-admin",
    source_addr="127.0.0.1",
    correlation_id="corr-ssh-auto",
)


def test_cli_accept_receipt_is_domain_separated_and_binds_every_field():
    values = {
        "enrollment_id": ENROLLMENT_ID,
        "nonce": "11111111-1111-4111-8111-111111111111",
        "peer_uid": 501,
        "input_fingerprint": "a" * 64,
        "pinned_address": "10.20.30.40",
    }
    receipt = _cli_accept_receipt(**values)

    for key, replacement in (
        ("enrollment_id", "different-enrollment"),
        ("nonce", "22222222-2222-4222-8222-222222222222"),
        ("peer_uid", 502),
        ("input_fingerprint", "b" * 64),
        ("pinned_address", "10.20.30.41"),
    ):
        assert _cli_accept_receipt(**{**values, key: replacement}) != receipt


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

    def prepare_cli_target(
        self, endpoint, _profile_id, *, ssh_host, ssh_port, pinned_address
    ):
        assert (ssh_host, ssh_port, pinned_address) == (
            "agent.lab.example",
            2222,
            "10.20.30.40",
        )
        assert endpoint == "http://10.20.30.40:8765"
        return VALIDATION_TARGET

    def prepare_pinned(self, endpoint, _profile_id, stored_ip):
        assert endpoint == "http://10.20.30.40:8765"
        assert stored_ip == "10.20.30.40"
        return VALIDATION_TARGET

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
    tmp_path,
    adapter,
    *,
    audit=None,
    store=None,
    clock=None,
    service_key_adapter=None,
    service_key_configured=False,
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
        service_key_adapter=service_key_adapter,
        service_key_configured=service_key_configured,
        transport_profiles=(PROFILE,),
        auto_audit=audit or Audit(),
        clock=clock or (lambda: NOW),
    )
    return orchestrator, jobs, journal, credential_store, client, engine


async def _socket_object(
    reader: asyncio.StreamReader,
) -> dict[str, object]:
    value = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert isinstance(value, dict)
    return value


async def _open_manager_cli(
    path: Path, header: dict[str, object]
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, dict[str, object]]:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(json.dumps(header, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    return reader, writer, await _socket_object(reader)


@pytest.mark.integration
async def test_real_cli_socket_resumes_after_manager_restart_and_returns_acceptance(
    tmp_path,
):
    now = datetime.now(UTC)
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter, clock=lambda: now
    )
    runtime = Path(tempfile.mkdtemp(prefix="ieg-r-", dir="/tmp"))
    runtime.chmod(0o700)
    manager_path = runtime / "m.sock"
    agent_path = runtime / "a.sock"
    server: ManagerEnrollmentSocket | None = None
    restarted_server: ManagerEnrollmentSocket | None = None
    restarted_orchestrator: EnrollmentOrchestrator | None = None
    restarted_engine = None
    agent_container = None
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()
        assert journal.get(created.job.enrollment_id).state is EnrollmentState.AWAITING_CLI

        server = ManagerEnrollmentSocket(
            path=manager_path,
            mode=0o600,
            orchestrator=orchestrator,
            allowed_uid=os.geteuid(),
            peer_credentials=lambda _socket: (
                os.getpid(),
                os.geteuid(),
                os.getegid(),
            ),
        )
        await server.start()
        header = {
            "protocol": "manager-cli-enrollment.header.v1",
            "enrollment_id": created.job.enrollment_id,
            "ssh": "edaops@agent.lab.example:2222",
            "pinned_address": "10.20.30.40",
        }
        first_reader, first_writer, ready = await _open_manager_cli(manager_path, header)
        assert ready.get("protocol") == "manager-cli-enrollment.ready.v1", ready
        resume_nonce = ready["nonce"]
        assert isinstance(resume_nonce, str)
        first_writer.close()
        await first_writer.wait_closed()
        assert await asyncio.wait_for(first_reader.read(), timeout=2) == b""
        await server.stop()
        server = None
        await orchestrator.shutdown()
        engine.dispose()

        restarted_adapter = Adapter()
        restarted_adapter.healthy = False
        (
            restarted_orchestrator,
            _restarted_jobs,
            restarted_journal,
            restarted_store,
            restarted_client,
            restarted_engine,
        ) = setup_services(tmp_path, restarted_adapter, clock=lambda: now)

        async def accept_real_helper(_target, token, *, helper_instance_id):
            assert token
            return EnrollmentValidation(
                normalized_endpoint="http://10.20.30.40:8765",
                api_version="2",
                agent_version="0.3.0",
                capabilities=("manager-enrollment.v1", "summary.v2"),
                instance_id=helper_instance_id,
                summary=None,
                readiness_warning="agent_readiness_unavailable",
            )

        restarted_client.validate_pending = accept_real_helper
        await restarted_orchestrator.recover_and_cleanup()
        assert restarted_journal.get(created.job.enrollment_id).state is EnrollmentState.RUNNING
        restarted_server = ManagerEnrollmentSocket(
            path=manager_path,
            mode=0o600,
            orchestrator=restarted_orchestrator,
            allowed_uid=os.geteuid(),
            peer_credentials=lambda _socket: (
                os.getpid(),
                os.geteuid(),
                os.getegid(),
            ),
        )
        await restarted_server.start()

        resume_header = {**header, "resume_nonce": resume_nonce}
        resumed_reader, resumed_writer, resumed_ready = await _open_manager_cli(
            manager_path, resume_header
        )
        assert resumed_ready == ready

        token_file = tmp_path / "agent-admin.token"
        token_file.write_text("agent-admin\n", encoding="utf-8")
        token_file.chmod(0o600)
        agent_config = AppConfig.model_validate(
            {
                "mode": "agent",
                "auth": {"token_file": token_file},
                "enrollment": {"socket_path": agent_path, "socket_mode": "0600"},
            }
        )
        agent_container = build_agent_container(
            agent_config,
            tmp_path / "agent.db",
            tmp_path / "agent-instance-id",
        )
        assert agent_container.enrollment_socket_server is not None
        agent_container.enrollment_socket_server.start()
        helper_stdout, helper_stderr = io.BytesIO(), io.StringIO()
        helper_request = json.dumps(
            {
                "protocol": "manager-enrollment.v1",
                "manager_id": resumed_ready["manager_id"],
                "enrollment_id": resumed_ready["enrollment_id"],
            },
            separators=(",", ":"),
        ).encode()
        assert run_helper(
            agent_path,
            io.BytesIO(helper_request),
            helper_stdout,
            helper_stderr,
        ) == 0, helper_stderr.getvalue()
        helper = json.loads(helper_stdout.getvalue())
        secret = helper["token"]
        result = {
            "protocol": "manager-cli-enrollment.result.v1",
            "input_fingerprint": resumed_ready["input_fingerprint"],
            "nonce": resumed_ready["nonce"],
            "helper": helper,
        }
        resumed_writer.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
        await resumed_writer.drain()
        resumed_writer.write_eof()
        verified_bytes = await asyncio.wait_for(resumed_reader.readline(), timeout=2)
        assert json.loads(verified_bytes) == {
            "status": "verified",
            "enrollment_id": created.job.enrollment_id,
        }
        assert secret.encode() not in verified_bytes
        resumed_writer.close()
        await resumed_writer.wait_closed()

        accepted_reader, accepted_writer, accepted = await _open_manager_cli(
            manager_path, resume_header
        )
        accepted_bytes = json.dumps(accepted, separators=(",", ":")).encode()
        assert accepted == {
            "protocol": "manager-cli-enrollment.accepted.v1",
            "status": "already_accepted",
            "enrollment_id": created.job.enrollment_id,
        }
        assert secret.encode() not in accepted_bytes
        assert await asyncio.wait_for(accepted_reader.read(), timeout=2) == b""
        accepted_writer.close()
        await accepted_writer.wait_closed()
        persisted = restarted_journal.get(created.job.enrollment_id)
        assert persisted.state is EnrollmentState.VERIFIED
        assert persisted.cli_resume_nonce == resume_nonce
        assert secret not in restarted_journal.dump_serialized_rows()
        assert restarted_store.read(persisted.credential_temp_ref).decode() == secret
    finally:
        if restarted_server is not None:
            await restarted_server.stop()
        if server is not None:
            await server.stop()
        if restarted_orchestrator is not None:
            await restarted_orchestrator.shutdown()
        await orchestrator.shutdown()
        if agent_container is not None:
            if agent_container.enrollment_socket_server is not None:
                agent_container.enrollment_socket_server.stop()
            agent_container.database_engine.dispose()
        if restarted_engine is not None:
            restarted_engine.dispose()
        engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


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
        assert verified.validated_http_address == "10.20.30.40"
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
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await started.wait()
        background = next(iter(orchestrator._background_tasks.values()))

        asyncio.get_running_loop().call_later(0.05, release.set)
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

        try:
            cancelled = await orchestrator.cancel(created.job.enrollment_id)
        finally:
            release.set()
        await orchestrator.wait_for_background()

        current = journal.get(created.job.enrollment_id)
        assert cancelled.job.state is EnrollmentState.CANCELLED
        assert current.state is EnrollmentState.CANCELLED
        assert current.credential_temp_ref is None
        assert store.puts == 0
    finally:
        release.set()
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
async def test_injected_clock_controls_public_create_get_and_cancel(tmp_path):
    orchestrator, _jobs, _journal, _store, _client, engine = setup_services(
        tmp_path, Adapter()
    )
    try:
        created = orchestrator.create(request())
        assert created.job.created_at == NOW
        assert created.job.expires_at == NOW + timedelta(minutes=10)

        loaded = orchestrator.get(created.job.enrollment_id)
        assert loaded.job.state is EnrollmentState.PENDING

        cancelled = await orchestrator.cancel(created.job.enrollment_id)
        assert cancelled.job.state is EnrollmentState.CANCELLED
        assert cancelled.job.updated_at == NOW
    finally:
        await orchestrator.shutdown()
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
        assert current.validated_http_address == "10.20.30.40"
        assert store.read(current.credential_temp_ref) == TOKEN
        assert audit.events[-1][2:] == (
            "failure",
            "dispatched",
            "agent_auth_error",
        )
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_cli_claim_nonce_publication_and_replay_are_fenced(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    audit = Audit()
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        assert claim.job.state is EnrollmentState.RUNNING
        assert claim.job.enrollment_method is EnrollmentMethod.SSH_CLI
        assert claim.job.recovery_owner == claim.nonce
        assert claim.job.cli_resume_nonce == claim.nonce
        assert claim.job.cli_peer_uid == 501
        assert claim.job.cli_input_fingerprint == claim.input_fingerprint
        assert claim.job.cli_pinned_address == "10.20.30.40"
        serialized = journal.dump_serialized_rows()
        assert claim.nonce not in serialized
        assert claim.input_fingerprint not in serialized
        assert '"cli_peer_uid"' not in serialized
        assert '"cli_pinned_address"' not in serialized
        payload = (
            b'{"protocol":"manager-enrollment.v1",'
            b'"instance_id":"33333333-3333-4333-8333-333333333333",'
            b'"credential_id":"44444444-4444-4444-8444-444444444444",'
            b'"token":"eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",'
            b'"expires_at":"2026-07-12T12:05:00Z"}'
        )
        completed = await orchestrator.complete_cli_submission(
            claim,
            helper_payload=payload,
            input_fingerprint=claim.input_fingerprint,
            nonce=claim.nonce,
        )

        assert completed.job.state is EnrollmentState.VERIFIED
        assert completed.job.validated_http_address == "10.20.30.40"
        assert completed.job.recovery_owner is None
        assert completed.job.cli_resume_nonce == claim.nonce
        assert store.read(completed.job.credential_temp_ref) == TOKEN
        with pytest.raises(EnrollmentValidationError, match="agent_enrollment_conflict"):
            orchestrator.begin_cli_submission(
                enrollment_id=awaiting.enrollment_id,
                ssh_user="edaops",
                ssh_host="agent.lab.example",
                ssh_port=2222,
                pinned_address="10.20.30.40",
                peer_uid=501,
                context=AUDIT_CONTEXT,
            )
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_cli_wrong_nonce_aborts_to_awaiting_without_storing_token(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    audit = Audit()
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        with pytest.raises(EnrollmentValidationError, match="input_changed"):
            await orchestrator.complete_cli_submission(
                claim,
                helper_payload=b"must-not-be-parsed",
                input_fingerprint=claim.input_fingerprint,
                nonce="stale-nonce",
            )

        current = journal.get(awaiting.enrollment_id)
        assert current.state is EnrollmentState.RUNNING
        assert current.recovery_owner == claim.nonce
        assert not [
            path for path in store.directory.iterdir() if not path.name.startswith(".")
        ]
        assert audit.events[-1][2:] == (
            "failure",
            "dispatched",
            "agent_enrollment_input_changed",
        )
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_cli_result_atomically_expires_before_helper_token_is_parsed(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    now = [NOW]
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, adapter, clock=lambda: now[0]
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        now[0] = awaiting.expires_at

        with pytest.raises(EnrollmentValidationError, match="expired"):
            await orchestrator.complete_cli_submission(
                claim,
                helper_payload=b"must-not-be-parsed",
                input_fingerprint=claim.input_fingerprint,
                nonce=claim.nonce,
            )

        current = journal.get(awaiting.enrollment_id)
        assert current.state is EnrollmentState.EXPIRED
        assert current.recovery_owner is None
        assert current.cli_resume_nonce is None
        assert current.cli_peer_uid is None
        assert current.cli_input_fingerprint is None
        assert current.cli_pinned_address is None
        assert not [
            path for path in store.directory.iterdir() if not path.name.startswith(".")
        ]
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_cli_disconnect_abort_fences_late_result_handler(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    audit = Audit()
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, adapter, audit=audit
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        orchestrator.release_cli_connection(
            claim, result_received=False, code="cli_submission_interrupted"
        )

        resumed = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            resume_nonce=claim.nonce,
            context=AUDIT_CONTEXT,
        )
        assert resumed.nonce == claim.nonce
        assert resumed.input_fingerprint == claim.input_fingerprint
        assert resumed.job.recovery_revision == claim.job.recovery_revision
        assert resumed.job.state is EnrollmentState.RUNNING
        assert audit.events[1][2:] == (
            "failure",
            "unknown",
            "cli_submission_interrupted",
        )
        assert not [
            path for path in store.directory.iterdir() if not path.name.startswith(".")
        ]
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(("peer_uid", "nonce"), ((502, None), (501, "wrong-nonce")))
def test_cli_resume_rejects_wrong_peer_or_nonce_without_unlocking_claim(
    tmp_path, peer_uid, nonce
):
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        with pytest.raises(EnrollmentValidationError, match="conflict"):
            orchestrator.begin_cli_submission(
                enrollment_id=awaiting.enrollment_id,
                ssh_user="edaops",
                ssh_host="agent.lab.example",
                ssh_port=2222,
                pinned_address="10.20.30.40",
                peer_uid=peer_uid,
                resume_nonce=nonce or claim.nonce,
                context=AUDIT_CONTEXT,
            )
        current = journal.get(awaiting.enrollment_id)
        assert current.state is EnrollmentState.RUNNING
        assert current.cli_resume_nonce == claim.nonce
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_cli_resume_after_publication_returns_already_accepted_without_token(tmp_path):
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, _journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        payload = (
            b'{"protocol":"manager-enrollment.v1",'
            b'"instance_id":"33333333-3333-4333-8333-333333333333",'
            b'"credential_id":"44444444-4444-4444-8444-444444444444",'
            b'"token":"eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",'
            b'"expires_at":"2026-07-12T12:05:00Z"}'
        )
        await orchestrator.complete_cli_submission(
            claim,
            helper_payload=payload,
            input_fingerprint=claim.input_fingerprint,
            nonce=claim.nonce,
        )

        resumed = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            resume_nonce=claim.nonce,
            context=AUDIT_CONTEXT,
        )

        assert resumed.already_accepted is True
        assert resumed.job.state is EnrollmentState.VERIFIED
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_cli_result_read_before_put_failure_can_resume_and_publish_once(
    tmp_path, monkeypatch
):
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, journal, store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        payload = (
            b'{"protocol":"manager-enrollment.v1",'
            b'"instance_id":"33333333-3333-4333-8333-333333333333",'
            b'"credential_id":"44444444-4444-4444-8444-444444444444",'
            b'"token":"eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",'
            b'"expires_at":"2026-07-12T12:05:00Z"}'
        )
        publish = orchestrator._publish_cli_helper

        async def crash_before_put(*_args, **_kwargs):
            raise RuntimeError("manager crashed before put")

        monkeypatch.setattr(orchestrator, "_publish_cli_helper", crash_before_put)
        with pytest.raises(RuntimeError, match="before put"):
            await orchestrator.complete_cli_submission(
                claim,
                helper_payload=payload,
                input_fingerprint=claim.input_fingerprint,
                nonce=claim.nonce,
            )
        assert journal.get(awaiting.enrollment_id).state is EnrollmentState.RUNNING
        assert not [
            path for path in store.directory.iterdir() if not path.name.startswith(".")
        ]

        resumed = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            resume_nonce=claim.nonce,
            context=AUDIT_CONTEXT,
        )
        monkeypatch.setattr(orchestrator, "_publish_cli_helper", publish)
        completed = await orchestrator.complete_cli_submission(
            resumed,
            helper_payload=payload,
            input_fingerprint=resumed.input_fingerprint,
            nonce=resumed.nonce,
        )
        assert completed.job.state is EnrollmentState.VERIFIED
        assert store.read(completed.job.credential_temp_ref) == TOKEN
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_cli_running_claim_survives_startup_recovery_and_cancel_clears_identity(
    tmp_path,
):
    adapter = Adapter()
    adapter.healthy = False
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path, adapter
    )
    try:
        awaiting = orchestrator.create_auto(request(), AUDIT_CONTEXT).job
        claim = orchestrator.begin_cli_submission(
            enrollment_id=awaiting.enrollment_id,
            ssh_user="edaops",
            ssh_host="agent.lab.example",
            ssh_port=2222,
            pinned_address="10.20.30.40",
            peer_uid=501,
            context=AUDIT_CONTEXT,
        )
        await orchestrator.recover_and_cleanup()
        recovered = journal.get(awaiting.enrollment_id)
        assert recovered.state is EnrollmentState.RUNNING
        assert recovered.cli_resume_nonce == claim.nonce

        cancelled = await orchestrator.cancel(awaiting.enrollment_id)
        assert cancelled.job.state is EnrollmentState.CANCELLED
        assert cancelled.job.cli_resume_nonce is None
        assert cancelled.job.cli_peer_uid is None
        assert cancelled.job.cli_input_fingerprint is None
        assert cancelled.job.cli_pinned_address is None
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_configured_invalid_service_key_falls_to_cli_not_personal_auto(tmp_path):
    personal = Adapter()
    orchestrator, _jobs, journal, _store, _client, engine = setup_services(
        tmp_path,
        personal,
        service_key_configured=True,
        service_key_adapter=None,
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)

        assert created.job.state is EnrollmentState.AWAITING_CLI
        assert created.job.enrollment_method is EnrollmentMethod.SSH_CLI
        assert personal.calls == 0
        assert journal.get(created.job.enrollment_id).state is EnrollmentState.AWAITING_CLI
    finally:
        await orchestrator.shutdown()
        engine.dispose()


@pytest.mark.integration
async def test_valid_service_key_adapter_has_priority_over_personal_auto(tmp_path):
    personal = Adapter()
    service = Adapter()
    orchestrator, _jobs, _journal, _store, _client, engine = setup_services(
        tmp_path,
        personal,
        service_key_configured=True,
        service_key_adapter=service,
    )
    try:
        created = orchestrator.create_auto(request(), AUDIT_CONTEXT)
        await orchestrator.wait_for_background()

        assert created.job.enrollment_method is EnrollmentMethod.SSH_SERVICE_KEY
        assert service.calls == 1
        assert personal.calls == 0
    finally:
        await orchestrator.shutdown()
        engine.dispose()

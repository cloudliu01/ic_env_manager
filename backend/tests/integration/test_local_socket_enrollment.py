import asyncio
import hashlib
import hmac
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.bootstrap.composition import build_agent_container, build_manager_container
from ic_env_guard.config.models import AppConfig
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.local_socket import (
    LocalEnrollmentSocketClient,
    LocalEnrollmentSocketError,
)
from ic_env_guard.enrollment.models import CredentialState
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    LocalBootstrapRequest,
)
from ic_env_guard.fleet.models import (
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RegistryError,
)
from ic_env_guard.main import create_public_app


def _token_file(path: Path, value: str) -> Path:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _containers(tmp_path: Path):
    runtime = Path(tempfile.mkdtemp(prefix="ieg-l-", dir="/private/tmp"))
    runtime.chmod(0o700)
    agent_admin_token = "agent-admin-token-that-must-never-be-managed"
    agent_config = AppConfig.model_validate(
        {
            "mode": "agent",
            "server": {"bind": "127.0.0.1", "port": 8766},
            "ingest": {"port": 8767},
            "auth": {
                "token_file": _token_file(
                    tmp_path / "agent-admin.token", agent_admin_token
                )
            },
            "enrollment": {"socket_path": runtime / "agent.sock"},
        }
    )
    manager_config = AppConfig.model_validate(
        {
            "mode": "control-plane",
            "server": {"bind": "127.0.0.1", "port": 8765},
            "auth": {
                "token_file": _token_file(
                    tmp_path / "manager-admin.token", "manager-admin-token-value"
                )
            },
            "development": {
                "allow_insecure_http": True,
                "local_agent_bootstrap": True,
            },
            "enrollment": {"manager_socket_path": runtime / "manager.sock"},
            "control_plane": {
                "audit_database": tmp_path / "manager.db",
                "credential_directory": tmp_path / "manager-credentials",
                "allowed_agent_cidrs": ["127.0.0.0/8"],
                "transport_profiles": [
                    {
                        "id": "local-loopback-http",
                        "type": "trusted_lan_http",
                        "allowed_cidrs": ["127.0.0.0/8"],
                    },
                    {
                        "id": "alternate-loopback-http",
                        "type": "trusted_lan_http",
                        "allowed_cidrs": ["127.0.0.0/8"],
                    },
                ],
            },
        }
    )
    agent = build_agent_container(
        agent_config, tmp_path / "agent.db", tmp_path / "agent-instance-id"
    )
    manager = build_manager_container(manager_config)
    return agent, manager, agent_config, agent_admin_token.encode(), runtime


def _request(agent_config, *, profile_id="local-loopback-http"):
    return LocalBootstrapRequest(
        agent_id="local-agent",
        display_name="Local development agent",
        base_url="http://127.0.0.1:8766",
        transport_profile_id=profile_id,
        agent_socket_path=agent_config.enrollment.socket_path,
    )


def _context():
    return AutoEnrollmentAuditContext(
        actor_id=f"local-cli:{os.geteuid()}",
        source_addr="local-unix",
        correlation_id=None,
    )


def _assert_distinct_secret(left: bytes, right: bytes) -> None:
    left_hash = hashlib.sha256(left).digest()
    right_hash = hashlib.sha256(right).digest()
    if hmac.compare_digest(left_hash, right_hash):
        pytest.fail("managed and admin credentials must differ")


def _assert_secret_absent(secret: bytes, serialized: str) -> None:
    if secret.decode("ascii") in serialized:
        pytest.fail("credential bytes were serialized")


class _LoseFirstActivationResponse(httpx.AsyncBaseTransport):
    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self.activation_lost = False

    async def handle_async_request(self, request):
        response = await self._inner.handle_async_request(request)
        if request.url.path.endswith("/activate") and not self.activation_lost:
            self.activation_lost = True
            await response.aread()
            raise httpx.ReadError("activation response was lost", request=request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self.paths: list[str] = []

    async def handle_async_request(self, request):
        self.paths.append(request.url.path)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.mark.integration
async def test_local_socket_bootstrap_uses_managed_credential_saga(tmp_path):
    agent, manager, agent_config, agent_admin_token, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport = _RecordingTransport(create_public_app(agent))
    transport_client = AgentHttpClient(transport=transport)
    manager.enrollment_orchestrator.agent_client._client = transport_client
    real_socket_client = LocalEnrollmentSocketClient(runtime)

    class CountingSocketClient:
        calls = 0

        async def issue(self, **kwargs):
            self.calls += 1
            return await real_socket_client.issue(**kwargs)

    socket_client = CountingSocketClient()
    manager.enrollment_orchestrator._local_socket_client = socket_client
    manager.enrollment_orchestrator._local_bootstrap_enabled = True
    try:
        record = await manager.enrollment_orchestrator.bootstrap_local(
            _request(agent_config),
            _context(),
        )

        assert record.agent_id == "local-agent"
        assert record.enrollment_method is EnrollmentMethod.LOCAL_SOCKET
        assert record.source == "local_dev_bootstrap"
        assert record.transport_profile_id == "local-loopback-http"
        assert record.remote_credential_id is not None
        manager_token = manager.credential_store.read(record.credential_ref)
        _assert_distinct_secret(manager_token, agent_admin_token)
        _assert_secret_absent(
            manager_token,
            manager.enrollment_journal_repository.dump_serialized_rows(),
        )
        assert (
            manager.enrollment_journal_repository.get("local-agent").state
            is EnrollmentState.CONSUMED
        )
        authenticated = agent.enrollment_service.authenticate(manager_token.decode("ascii"))
        assert authenticated is not None
        assert authenticated.state is CredentialState.ACTIVE

        repeated = await manager.enrollment_orchestrator.bootstrap_local(
            _request(agent_config), _context()
        )
        assert repeated == record
        assert socket_client.calls == 1
        assert len(agent.enrollment_service.repository.list_all()) == 1

        assert manager.fleet_probe_service is not None
        manager.fleet_probe_service._client = transport_client
        probe = await manager.fleet_probe_service.probe("local-agent")
        assert probe.status.connection_status == "ready"
        assert "/api/v2/capabilities" in transport.paths
        assert "/api/v2/summary" in transport.paths
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_local_socket_bootstrap_rejects_non_exact_profile_before_dispatch(tmp_path):
    _agent, manager, agent_config, _admin, runtime = _containers(tmp_path)

    class SocketClient:
        calls = 0

        async def issue(self, **_kwargs):
            self.calls += 1
            raise AssertionError("alternate profile must not dispatch")

    socket_client = SocketClient()
    orchestrator = manager.enrollment_orchestrator
    orchestrator._local_socket_client = socket_client
    orchestrator._local_bootstrap_enabled = True
    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(
                _request(agent_config, profile_id="alternate-loopback-http"),
                _context(),
            )

        assert caught.value.code == "local_bootstrap_profile_invalid"
        assert caught.value.dispatch_state == "not_dispatched"
        assert socket_client.calls == 0
        assert manager.enrollment_journal_repository.get("local-agent") is None
        assert manager.registry_repository.get("local-agent") is None
    finally:
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_local_socket_lost_response_retries_only_after_bounded_expiry(tmp_path):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    clock = [datetime.now(UTC)]
    orchestrator._clock = lambda: clock[0]
    real_socket_client = LocalEnrollmentSocketClient(runtime)

    class LostFirstResponseClient:
        calls = 0

        async def issue(self, **kwargs):
            self.calls += 1
            helper = await real_socket_client.issue(**kwargs)
            if self.calls == 1:
                raise LocalEnrollmentSocketError("local_socket_unavailable")
            return helper

    socket_client = LostFirstResponseClient()
    orchestrator._local_socket_client = socket_client
    orchestrator._local_bootstrap_enabled = True
    try:
        with pytest.raises(EnrollmentValidationError) as lost:
            await orchestrator.bootstrap_local(_request(agent_config), _context())
        assert lost.value.code == "local_socket_unavailable"

        with pytest.raises(EnrollmentValidationError) as pending:
            await orchestrator.bootstrap_local(_request(agent_config), _context())
        assert pending.value.code == "local_bootstrap_retry_pending"
        assert pending.value.dispatch_state == "not_dispatched"
        assert socket_client.calls == 1

        clock[0] += timedelta(seconds=601)
        with agent.database_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE manager_credentials SET pending_expires_at=? "
                "WHERE enrollment_id='local-agent' AND state='pending'",
                ("2000-01-01T00:00:00.000000Z",),
            )

        record = await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert record.agent_id == "local-agent"
        assert record.enrollment_method is EnrollmentMethod.LOCAL_SOCKET
        assert socket_client.calls == 2
        credentials = agent.enrollment_service.repository.list_all()
        assert len(credentials) == 1
        assert credentials[0].state is CredentialState.ACTIVE
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_local_socket_pre_dispatch_failure_retries_when_agent_row_is_missing(
    tmp_path,
):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    clock = [datetime.now(UTC)]
    orchestrator._clock = lambda: clock[0]
    real_socket_client = LocalEnrollmentSocketClient(runtime)

    class FailBeforeFirstIssueClient:
        calls = 0

        async def issue(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise LocalEnrollmentSocketError("local_socket_unavailable")
            return await real_socket_client.issue(**kwargs)

    socket_client = FailBeforeFirstIssueClient()
    orchestrator._local_socket_client = socket_client
    orchestrator._local_bootstrap_enabled = True
    try:
        with pytest.raises(EnrollmentValidationError) as first:
            await orchestrator.bootstrap_local(_request(agent_config), _context())
        assert first.value.code == "local_socket_unavailable"
        assert agent.enrollment_service.repository.list_all() == ()
        failed = manager.enrollment_journal_repository.get("local-agent")
        assert failed is not None
        assert failed.state is EnrollmentState.FAILED
        assert failed.credential_temp_ref is None

        clock[0] += timedelta(seconds=601)
        record = await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert record.agent_id == "local-agent"
        assert socket_client.calls == 2
        credentials = agent.enrollment_service.repository.list_all()
        assert len(credentials) == 1
        assert credentials[0].state is CredentialState.ACTIVE
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_local_activation_lost_response_retains_durable_recovery_reference(tmp_path):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport = _LoseFirstActivationResponse(create_public_app(agent))
    transport_client = AgentHttpClient(transport=transport)
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    orchestrator._local_socket_client = LocalEnrollmentSocketClient(runtime)
    orchestrator._local_bootstrap_enabled = True
    clock = [datetime.now(UTC)]
    orchestrator._clock = lambda: clock[0]
    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert caught.value.code == "agent_network_error"
        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        assert residual.state is EnrollmentState.ACTIVATION_REQUESTED
        assert not residual.state.terminal
        assert residual.credential_temp_ref is not None
        retained = manager.credential_store.read(residual.credential_temp_ref)
        assert hashlib.sha256(retained).digest()
        remote = agent.enrollment_service.repository.get(
            residual.remote_credential_id
        )
        assert remote is not None
        assert remote.state is CredentialState.ACTIVE
        assert manager.registry_repository.get("local-agent") is None

        await orchestrator.recover()

        record = manager.registry_repository.get("local-agent")
        assert record is not None
        assert record.agent_id == "local-agent"
        assert (
            manager.enrollment_journal_repository.get("local-agent").state
            is EnrollmentState.CONSUMED
        )
        assert record.credential_ref == residual.credential_temp_ref
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_local_transient_compensation_revoke_failure_recovers_after_restart(
    tmp_path, monkeypatch
):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    orchestrator._local_socket_client = LocalEnrollmentSocketClient(runtime)
    orchestrator._local_bootstrap_enabled = True
    clock = [datetime.now(UTC)]
    orchestrator._clock = lambda: clock[0]
    real_revoke = orchestrator.agent_client.revoke
    real_commit = manager.registry_repository.commit_activated_enrollment
    revoke_calls = 0

    async def fail_first_revoke(*args, **kwargs):
        nonlocal revoke_calls
        revoke_calls += 1
        if revoke_calls == 1:
            raise EnrollmentValidationError(
                "agent_network_error", dispatch_state="unknown"
            )
        return await real_revoke(*args, **kwargs)

    def conflict_commit(*_args, **_kwargs):
        raise RegistryConflict("injected commit conflict")

    monkeypatch.setattr(orchestrator.agent_client, "revoke", fail_first_revoke)
    monkeypatch.setattr(
        manager.registry_repository, "commit_activated_enrollment", conflict_commit
    )
    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(_request(agent_config), _context())
        assert caught.value.code == "agent_network_error"

        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        assert residual.state is EnrollmentState.ACTIVATED
        assert not residual.state.terminal
        assert residual.credential_temp_ref is not None
        retained = manager.credential_store.read(residual.credential_temp_ref)
        assert hashlib.sha256(retained).digest()
        assert manager.registry_repository.get("local-agent") is None
        assert revoke_calls == 1

        await orchestrator.recover()

        failed = manager.enrollment_journal_repository.get("local-agent")
        assert failed is not None
        assert failed.state is EnrollmentState.FAILED
        assert failed.credential_temp_ref is None
        remote = agent.enrollment_service.repository.get(failed.remote_credential_id)
        assert remote is not None
        assert remote.state is CredentialState.REVOKED
        assert revoke_calls == 2

        clock[0] += timedelta(seconds=601)
        monkeypatch.setattr(
            manager.registry_repository,
            "commit_activated_enrollment",
            real_commit,
        )
        retried = await orchestrator.bootstrap_local(
            _request(agent_config), _context()
        )
        assert retried.agent_id == "local-agent"
        replacement = agent.enrollment_service.repository.list_all()
        assert len(replacement) == 1
        assert replacement[0].state is CredentialState.ACTIVE
        assert replacement[0].credential_id != remote.credential_id
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize("compensation_failure", ("journal_get", "cleanup"))
async def test_local_failure_keeps_stable_error_and_audits_once_when_compensation_fails(
    tmp_path, monkeypatch, compensation_failure
):
    _agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    orchestrator = manager.enrollment_orchestrator

    class Audit:
        outcomes = []

        def record_intent(self, _enrollment_id, _context):
            return 7

        def record_outcome(self, event_id, **outcome):
            self.outcomes.append((event_id, outcome))

    class SocketClient:
        async def issue(self, **_kwargs):
            if compensation_failure == "cleanup":
                raise LocalEnrollmentSocketError("local_socket_unavailable")
            raise RuntimeError("private implementation detail")

    audit = Audit()
    orchestrator._auto_audit = audit
    orchestrator._local_socket_client = SocketClient()
    orchestrator._local_bootstrap_enabled = True
    if compensation_failure == "journal_get":
        real_get = orchestrator.journal.get
        calls = 0

        def fail_compensation_get(enrollment_id):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RegistryError("private journal failure")
            return real_get(enrollment_id)

        monkeypatch.setattr(orchestrator.journal, "get", fail_compensation_get)
        expected_code = "local_bootstrap_failed"
    else:

        def fail_cleanup(_job):
            raise RegistryError("private cleanup failure")

        monkeypatch.setattr(orchestrator, "_cleanup_terminal", fail_cleanup)
        expected_code = "local_socket_unavailable"

    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert caught.value.code == expected_code
        assert caught.value.args == (expected_code,)
        assert len(audit.outcomes) == 1
        assert audit.outcomes[0][0] == 7
        assert audit.outcomes[0][1]["failure_category"] == expected_code
    finally:
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure_type", "expected_code"),
    (
        ("credential", "credential_store_unavailable"),
        ("registry", "storage_unavailable"),
    ),
)
async def test_local_storage_errors_are_normalized(
    tmp_path, monkeypatch, failure_type, expected_code
):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    orchestrator._local_socket_client = LocalEnrollmentSocketClient(runtime)
    orchestrator._local_bootstrap_enabled = True
    if failure_type == "credential":
        monkeypatch.setattr(
            manager.credential_store,
            "put",
            lambda _token: (_ for _ in ()).throw(
                CredentialStoreError("private credential failure")
            ),
        )
    else:
        real_replace = orchestrator.journal.replace_if_state

        def fail_publication(job, **kwargs):
            if job.state is EnrollmentState.CREDENTIAL_ISSUED:
                raise RegistryError("private Registry failure")
            return real_replace(job, **kwargs)

        monkeypatch.setattr(orchestrator.journal, "replace_if_state", fail_publication)
    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert caught.value.code == expected_code
        assert caught.value.args == (expected_code,)
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_shutdown_cancels_blocked_local_socket_issue_and_waits_for_cleanup(tmp_path):
    _agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    orchestrator = manager.enrollment_orchestrator
    started = asyncio.Event()

    class BlockingSocketClient:
        async def issue(self, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    orchestrator._local_socket_client = BlockingSocketClient()
    orchestrator._local_bootstrap_enabled = True
    task = asyncio.create_task(
        orchestrator.bootstrap_local(_request(agent_config), _context())
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(orchestrator.shutdown(), timeout=2)

        assert task.done()
        assert task.cancelled()
        assert manager.registry_repository.get("local-agent") is None
        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        assert residual.credential_temp_ref is None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
async def test_closing_after_local_issue_never_publishes_or_activates(tmp_path):
    agent, manager, agent_config, _admin, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    real_socket_client = LocalEnrollmentSocketClient(runtime)

    class CloseAfterIssueClient:
        helper = None

        async def issue(self, **kwargs):
            self.helper = await real_socket_client.issue(**kwargs)
            await orchestrator.shutdown()
            return self.helper

    socket_client = CloseAfterIssueClient()
    orchestrator._local_socket_client = socket_client
    orchestrator._local_bootstrap_enabled = True
    try:
        with pytest.raises(EnrollmentValidationError) as caught:
            await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert caught.value.code == "local_bootstrap_cancelled"
        assert manager.registry_repository.get("local-agent") is None
        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        assert residual.credential_temp_ref is None
        assert socket_client.helper is not None
        remote = agent.enrollment_service.repository.get(
            socket_client.helper.credential_id
        )
        assert remote is not None
        assert remote.state is CredentialState.PENDING
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure_point",
    ("after_issue", "after_storage", "validation", "registry_commit"),
)
async def test_local_socket_bootstrap_compensates_partial_saga(
    tmp_path, monkeypatch, failure_point
):
    agent, manager, agent_config, _agent_admin_token, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    orchestrator = manager.enrollment_orchestrator
    orchestrator.agent_client._client = transport_client
    real_socket_client = LocalEnrollmentSocketClient(runtime)

    class CapturingSocketClient:
        helper = None

        async def issue(self, **kwargs):
            self.helper = await real_socket_client.issue(**kwargs)
            if failure_point == "after_issue":
                raise LocalEnrollmentSocketError("local_socket_unavailable")
            return self.helper

    socket_client = CapturingSocketClient()
    orchestrator._local_socket_client = socket_client
    orchestrator._local_bootstrap_enabled = True

    if failure_point == "after_storage":
        replace_if_state = orchestrator.journal.replace_if_state

        def fail_issued(job, **kwargs):
            if job.state is EnrollmentState.CREDENTIAL_ISSUED:
                raise RegistryError("injected journal failure")
            return replace_if_state(job, **kwargs)

        monkeypatch.setattr(orchestrator.journal, "replace_if_state", fail_issued)
    elif failure_point == "validation":

        async def fail_validation(*_args, **_kwargs):
            raise EnrollmentValidationError(
                "agent_network_error", dispatch_state="dispatched"
            )

        monkeypatch.setattr(orchestrator.agent_client, "validate_pending", fail_validation)
    elif failure_point == "registry_commit":
        real_commit = manager.registry_repository.commit_activated_enrollment

        def fail_commit(*_args, **_kwargs):
            raise RegistryError("injected Registry failure")

        monkeypatch.setattr(
            manager.registry_repository, "commit_activated_enrollment", fail_commit
        )

    try:
        with pytest.raises(EnrollmentValidationError):
            await orchestrator.bootstrap_local(_request(agent_config), _context())

        assert manager.registry_repository.get("local-agent") is None
        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        if residual.credential_temp_ref is not None:
            assert not residual.state.terminal
            retained = manager.credential_store.read(residual.credential_temp_ref)
            assert hashlib.sha256(retained).digest()
        helper = socket_client.helper
        assert helper is not None
        _assert_secret_absent(
            helper.token,
            manager.enrollment_journal_repository.dump_serialized_rows(),
        )
        remote = agent.enrollment_service.repository.get(helper.credential_id)
        assert remote is not None
        expected_state = (
            CredentialState.ACTIVE
            if failure_point == "registry_commit"
            else CredentialState.PENDING
        )
        assert remote.state is expected_state
        if failure_point == "registry_commit":
            monkeypatch.setattr(
                manager.registry_repository,
                "commit_activated_enrollment",
                real_commit,
            )
            recovery_time = datetime.now(UTC) + timedelta(
                seconds=orchestrator._recovery_lease_seconds + 1
            )
            orchestrator._clock = lambda: recovery_time
            await orchestrator.recover()
            assert manager.registry_repository.get("local-agent") is not None
            assert (
                manager.enrollment_journal_repository.get("local-agent").state
                is EnrollmentState.CONSUMED
            )
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)

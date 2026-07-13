import os
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.bootstrap.composition import build_agent_container, build_manager_container
from ic_env_guard.config.models import AppConfig
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.local_socket import (
    LocalEnrollmentSocketClient,
    LocalEnrollmentSocketError,
)
from ic_env_guard.enrollment.models import CredentialState
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    LocalBootstrapRequest,
    MutationSagaError,
)
from ic_env_guard.fleet.models import EnrollmentMethod, EnrollmentState, RegistryError
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
                    }
                ],
            },
        }
    )
    agent = build_agent_container(
        agent_config, tmp_path / "agent.db", tmp_path / "agent-instance-id"
    )
    manager = build_manager_container(manager_config)
    return agent, manager, agent_config, agent_admin_token.encode(), runtime


@pytest.mark.integration
async def test_local_socket_bootstrap_uses_managed_credential_saga(tmp_path):
    agent, manager, agent_config, agent_admin_token, runtime = _containers(tmp_path)
    assert agent.enrollment_socket_server is not None
    agent.enrollment_socket_server.start()
    transport_client = AgentHttpClient(
        transport=httpx.ASGITransport(app=create_public_app(agent))
    )
    manager.enrollment_orchestrator.agent_client._client = transport_client
    manager.enrollment_orchestrator._local_socket_client = LocalEnrollmentSocketClient(
        runtime
    )
    manager.enrollment_orchestrator._local_bootstrap_enabled = True
    try:
        record = await manager.enrollment_orchestrator.bootstrap_local(
            LocalBootstrapRequest(
                agent_id="local-agent",
                display_name="Local development agent",
                base_url="http://127.0.0.1:8766",
                transport_profile_id="local-loopback-http",
                agent_socket_path=agent_config.enrollment.socket_path,
            ),
            AutoEnrollmentAuditContext(
                actor_id=f"local-cli:{os.geteuid()}",
                source_addr="local-unix",
                correlation_id=None,
            ),
        )

        assert record.agent_id == "local-agent"
        assert record.enrollment_method is EnrollmentMethod.LOCAL_SOCKET
        assert record.source == "local_dev_bootstrap"
        assert record.transport_profile_id == "local-loopback-http"
        assert record.remote_credential_id is not None
        manager_token = manager.credential_store.read(record.credential_ref)
        assert manager_token != agent_admin_token
        assert (
            manager_token.decode("ascii")
            not in manager.enrollment_journal_repository.dump_serialized_rows()
        )
        assert (
            manager.enrollment_journal_repository.get("local-agent").state
            is EnrollmentState.CONSUMED
        )
        authenticated = agent.enrollment_service.authenticate(manager_token.decode("ascii"))
        assert authenticated is not None
        assert authenticated.state is CredentialState.ACTIVE
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure_point",
    ("after_issue", "after_storage", "validation", "activation", "registry_commit"),
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
    elif failure_point == "activation":

        async def fail_activation(*_args, **_kwargs):
            raise EnrollmentValidationError(
                "agent_network_error", dispatch_state="dispatched"
            )

        monkeypatch.setattr(orchestrator.agent_client, "activate", fail_activation)
    elif failure_point == "registry_commit":

        def fail_commit(*_args, **_kwargs):
            raise RegistryError("injected Registry failure")

        monkeypatch.setattr(
            manager.registry_repository, "commit_activated_enrollment", fail_commit
        )

    try:
        with pytest.raises(
            (EnrollmentValidationError, MutationSagaError, RegistryError)
        ):
            await orchestrator.bootstrap_local(
                LocalBootstrapRequest(
                    agent_id="local-agent",
                    display_name="Local development agent",
                    base_url="http://127.0.0.1:8766",
                    transport_profile_id="local-loopback-http",
                    agent_socket_path=agent_config.enrollment.socket_path,
                ),
                AutoEnrollmentAuditContext(
                    actor_id=f"local-cli:{os.geteuid()}",
                    source_addr="local-unix",
                    correlation_id=None,
                ),
            )

        assert manager.registry_repository.get("local-agent") is None
        residual = manager.enrollment_journal_repository.get("local-agent")
        assert residual is not None
        assert residual.credential_temp_ref is None or not residual.state.terminal
        helper = socket_client.helper
        assert helper is not None
        assert (
            helper.token.decode("ascii")
            not in manager.enrollment_journal_repository.dump_serialized_rows()
        )
        remote = agent.enrollment_service.repository.get(helper.credential_id)
        assert remote is not None
        expected_state = (
            CredentialState.REVOKED
            if failure_point == "registry_commit"
            else CredentialState.PENDING
        )
        assert remote.state is expected_state
    finally:
        await transport_client.aclose()
        agent.enrollment_socket_server.stop()
        agent.database_engine.dispose()
        manager.database_engine.dispose()
        shutil.rmtree(runtime, ignore_errors=True)

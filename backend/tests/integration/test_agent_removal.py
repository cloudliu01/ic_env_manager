from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.agents.terminal_proxy import GatewayTicketStore
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEvent,
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.jobs import EnrollmentConflict
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod, RegistryConflict
from ic_env_guard.main import create_app
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


@pytest.mark.integration
def test_terminal_usage_gate_covers_reservation_ticket_and_active_websocket():
    clock = [NOW]
    store = GatewayTicketStore(clock=lambda: clock[0])

    reservation = store.reserve("alpha")
    assert reservation is not None
    assert store.begin_removal("alpha") is False
    store.release_reservation(reservation)

    reservation = store.reserve("alpha")
    ticket = store.commit(
        reservation,
        actor_id="operator",
        agent_id="alpha",
        terminal_id="terminal-1",
        intended_ws_path="/ws/agents/alpha/terminals/terminal-1",
        upstream_ticket="upstream",
        expires_at=NOW + timedelta(seconds=30),
    )
    assert store.begin_removal("alpha") is False

    status, consumed = store.consume_for_websocket(
        ticket.ticket,
        actor_id="operator",
        agent_id="alpha",
        terminal_id="terminal-1",
        intended_ws_path="/ws/agents/alpha/terminals/terminal-1",
    )
    assert status == "ok"
    assert consumed == ticket
    assert store.begin_removal("alpha") is False

    store.release_active(ticket)
    assert store.begin_removal("alpha") is True
    assert store.reserve("alpha") is None
    assert store.reserve("beta") is not None
    store.abort_removal("alpha")
    assert store.reserve("alpha") is not None


@pytest.mark.integration
def test_expired_ticket_does_not_permanently_block_removal():
    clock = [NOW]
    store = GatewayTicketStore(clock=lambda: clock[0])
    reservation = store.reserve("alpha")
    ticket = store.commit(
        reservation,
        actor_id="operator",
        agent_id="alpha",
        terminal_id="terminal-1",
        intended_ws_path="/ws/agents/alpha/terminals/terminal-1",
        upstream_ticket="upstream",
        expires_at=NOW + timedelta(seconds=1),
    )

    assert ticket is not None
    clock[0] += timedelta(seconds=2)
    assert store.begin_removal("alpha") is True


@pytest.mark.integration
def test_removal_journal_snapshots_target_and_registry_delete_is_cas(tmp_path):
    from ic_env_guard.storage.removal_journal import AgentRemovalRepository

    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
        )
    )
    container = app.state.container
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"remove-token")
    record = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=reference,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=True,
        source="manual",
        revision=2,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)
    removals = AgentRemovalRepository(container.database_engine)

    with pytest.raises(RegistryConflict, match="audit"):
        removals.create_for_agent(
            "alpha", audit_event_id=7, local_only=False, now=NOW
        )
    with container.control_plane_session_factory() as session:
        audit = ControlPlaneAuditRepository(session).record_intent(
            ControlPlaneAuditEventCreate(
                actor_id="operator",
                source_addr="127.0.0.1",
                agent_id="alpha",
                operation="agents.v2.remove",
                target="agent:alpha",
                correlation_id="correlation",
            )
        )
        session.commit()
        audit_id = audit.id
    job = removals.create_for_agent(
        "alpha", audit_event_id=audit_id, local_only=False, now=NOW
    )

    assert job.phase == "pending"
    assert job.captured_revision == 2
    assert job.credential_ref == reference
    assert job.normalized_endpoint == record.normalized_endpoint
    assert removals.list_recoverable() == (job,)
    second_engine = create_sqlite_engine(tmp_path / "manager.db")
    second_registry = ManagerRegistryRepository(second_engine)
    try:
        with pytest.raises(RegistryConflict, match="agent_mutation_in_progress"):
            second_registry.update_if_revision(
                replace(record, display_name="Concurrent rename"),
                expected_revision=record.revision,
            )
    finally:
        second_engine.dispose()
    assert container.registry_repository.delete_if_revision_and_credential(
        "alpha",
        expected_revision=1,
        expected_credential_ref=reference,
        owner_removal_id=job.removal_id,
    ) is False
    assert container.registry_repository.get("alpha") == record
    assert container.registry_repository.delete_if_revision_and_credential(
        "alpha",
        expected_revision=2,
        expected_credential_ref=reference,
        owner_removal_id=job.removal_id,
    ) is True


@pytest.mark.integration
async def test_online_remove_revokes_before_registry_and_local_credential_delete(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(
                audit_database=tmp_path / "manager.db",
                allowed_agent_cidrs=["10.0.0.0/8"],
            ),
        )
    )
    container = app.state.container
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"remove-token")
    record = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=reference,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=True,
        source="manual",
        revision=2,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)

    class Client:
        calls = []
        fail_revoke = True

        def prepare(self, endpoint, profile):
            self.calls.append(("prepare", endpoint, profile))
            return object()

        async def revoke(self, _target, token, *, credential_id):
            assert container.registry_repository.get("alpha") is not None
            assert container.credential_store.read(reference) == b"remove-token"
            self.calls.append(("revoke", token, credential_id))
            if self.fail_revoke:
                raise EnrollmentValidationError(
                    "agent_network_error", dispatch_state="unknown"
                )

    orchestrator = container.enrollment_orchestrator
    orchestrator.agent_client = Client()
    orchestrator._clock = lambda: NOW
    with container.control_plane_session_factory() as session:
        audit = ControlPlaneAuditRepository(session).record_intent(
            ControlPlaneAuditEventCreate(
                actor_id="operator",
                source_addr="127.0.0.1",
                agent_id="alpha",
                operation="agents.v2.remove",
                target="agent:alpha",
                correlation_id="correlation",
            )
        )
        session.commit()
        audit_id = audit.id

    assert hasattr(orchestrator, "remove_agent")
    with pytest.raises(EnrollmentConflict, match="agent_network_error"):
        await orchestrator.remove_agent(
            "alpha", audit_event_id=audit_id, local_only=False
        )
    assert container.registry_repository.get("alpha") == record
    assert container.credential_store.read(reference) == b"remove-token"
    residual = container.removal_repository.list_recoverable()
    assert len(residual) == 1
    assert residual[0].phase == "residual"
    assert container.gateway_ticket_store.reserve("alpha") is None
    with container.control_plane_session_factory() as session:
        pending = session.get(ControlPlaneAuditEvent, audit_id)
        assert pending.result == "pending"

    orchestrator.agent_client.fail_revoke = False
    await orchestrator.recover_removals()

    assert container.registry_repository.get("alpha") is None
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(reference)
    assert orchestrator.agent_client.calls[-1] == (
        "revoke",
        b"remove-token",
        record.remote_credential_id,
    )
    assert container.removal_repository.list_recoverable() == ()
    assert container.gateway_ticket_store.reserve("alpha") is not None


@pytest.mark.integration
def test_registry_delete_never_detaches_a_nonterminal_rotation(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
        )
    )
    container = app.state.container
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"remove-token")
    record = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=reference,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=True,
        source="manual",
        revision=2,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)
    rotation = container.enrollment_orchestrator.start_rotation(
        "alpha", ssh_user="edaops", ssh_host="10.0.0.11", ssh_port=22
    ).job

    with pytest.raises(RegistryConflict, match="agent_mutation_in_progress"):
        container.registry_repository.delete_if_revision_and_credential(
            "alpha",
            expected_revision=record.revision,
            expected_credential_ref=reference,
        )

    assert container.registry_repository.get("alpha") == record
    with container.database_engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT replace_agent_id, replace_agent_tombstone "
            "FROM agent_enrollment_jobs WHERE enrollment_id=?",
            (rotation.enrollment_id,),
        ).one()
    assert tuple(row) == ("alpha", None)


@pytest.mark.integration
async def test_startup_finalizes_remove_intent_crash_before_journal_create(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
        )
    )
    container = app.state.container
    with container.control_plane_session_factory() as session:
        event = ControlPlaneAuditRepository(session).record_intent(
            ControlPlaneAuditEventCreate(
                actor_id="operator",
                source_addr="127.0.0.1",
                agent_id="alpha",
                operation="agents.v2.remove",
                target="agent:alpha",
                correlation_id="correlation",
            )
        )
        session.commit()
        event_id = event.id

    await container.enrollment_orchestrator.recover_removals()

    with container.control_plane_session_factory() as session:
        recovered = session.get(ControlPlaneAuditEvent, event_id)
        assert recovered.result == "failed"
        assert recovered.dispatch_state == "not_dispatched"
        assert recovered.failure_category == "agent_removal_interrupted"

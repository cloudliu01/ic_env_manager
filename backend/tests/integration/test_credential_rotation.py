import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.jobs import EnrollmentConflict
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod, EnrollmentState
from ic_env_guard.main import create_app

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


@pytest.mark.integration
def test_rotation_snapshot_and_removal_journal_schema_are_durable(tmp_path):
    database = tmp_path / "manager.db"
    run_control_plane_migrations(database)

    connection = sqlite3.connect(database)
    try:
        enrollment_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_enrollment_jobs)")
        }
        removal_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_removal_jobs)")
        }
        removal_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_removal_jobs'"
        ).fetchone()
    finally:
        connection.close()

    assert removal_row is not None
    removal_sql = removal_row[0]
    assert {
        "old_normalized_endpoint",
        "old_transport_profile_id",
        "old_instance_id",
        "old_registry_revision",
        "old_enrollment_method",
        "old_source",
        "old_enabled",
        "old_display_name",
    } <= enrollment_columns
    assert {
        "removal_id",
        "agent_id",
        "captured_revision",
        "credential_ref",
        "remote_credential_id",
        "normalized_endpoint",
        "transport_profile_id",
        "enrollment_method",
        "phase",
        "local_only",
        "audit_event_id",
        "last_error_code",
    } <= removal_columns
    for phase in (
        "pending",
        "revoking",
        "revoked",
        "registry_deleted",
        "credential_deleted",
        "completed",
        "residual",
    ):
        assert f"'{phase}'" in removal_sql


@pytest.mark.integration
def test_rotation_start_atomically_captures_complete_old_registry_snapshot(tmp_path):
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
        old_ref = container.credential_store.put(b"old-token")
    old = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha Lab",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=old_ref,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=False,
        source="manual",
        revision=3,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(old)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW

    assert hasattr(orchestrator, "start_rotation")
    result = orchestrator.start_rotation(
        "alpha", ssh_user="edaops", ssh_host="10.0.0.11", ssh_port=22
    )

    job = result.job
    assert job.state is EnrollmentState.AWAITING_CLI
    assert job.replace_agent_id == "alpha"
    assert job.old_credential_ref == old_ref
    assert job.old_remote_credential_id == old.remote_credential_id
    assert job.old_normalized_endpoint == old.normalized_endpoint
    assert job.old_transport_profile_id == old.transport_profile_id
    assert job.old_instance_id == old.instance_id
    assert job.old_registry_revision == old.revision
    assert job.old_enrollment_method is old.enrollment_method
    assert job.old_source == old.source
    assert job.old_enabled is old.enabled
    assert job.old_display_name == old.display_name


@pytest.mark.integration
async def test_rotation_consume_swaps_then_revokes_and_deletes_old_credential(tmp_path):
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
        old_ref = container.credential_store.put(b"old-token")
        new_ref = container.credential_store.put(b"new-token")
    old = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha Lab",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=old_ref,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=False,
        source="manual",
        revision=3,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(old)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW
    started = orchestrator.start_rotation(
        "alpha", ssh_user="edaops", ssh_host="10.0.0.11", ssh_port=22
    ).job
    current = started
    for state in (
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        current = container.enrollment_journal_repository.replace_if_state(
            replace(
                current,
                state=state,
                remote_instance_id=old.instance_id,
                remote_credential_id="55555555-5555-4555-8555-555555555555",
                credential_temp_ref=new_ref,
                validated_http_address="10.0.0.11",
                updated_at=NOW,
            ),
            expected_state=current.state,
        )

    class Client:
        calls = []
        fail_revoke = True

        def prepare_pinned(self, endpoint, profile, stored_ip):
            return SimpleNamespace(
                normalized_endpoint=endpoint,
                profile=profile,
                pinned_address=stored_ip,
            )

        async def activate(self, _target, token, **kwargs):
            self.calls.append(("activate", token, kwargs["credential_id"]))

        async def revoke(self, _target, token, *, credential_id):
            self.calls.append(("revoke", token, credential_id))
            if self.fail_revoke:
                raise EnrollmentValidationError(
                    "agent_network_error", dispatch_state="unknown"
                )

    client = Client()
    orchestrator.agent_client = client

    assert hasattr(orchestrator, "consume_rotation")
    with pytest.raises(EnrollmentConflict, match="agent_network_error"):
        await orchestrator.consume_rotation("alpha", started.enrollment_id)

    residual = container.enrollment_journal_repository.get(started.enrollment_id)
    assert residual.state is EnrollmentState.ACTIVATED
    assert residual.last_error_code == "agent_network_error"
    assert container.registry_repository.get("alpha").credential_ref == new_ref
    assert container.credential_store.read(old_ref) == b"old-token"

    client.fail_revoke = False
    await orchestrator.recover()
    rotated = container.registry_repository.get("alpha")

    assert rotated.agent_id == "alpha"
    assert rotated.revision == 4
    assert rotated.credential_ref == new_ref
    assert rotated.display_name == old.display_name
    assert rotated.enabled is old.enabled
    assert rotated.source == old.source
    assert client.calls[0] == (
        "activate",
        b"new-token",
        "55555555-5555-4555-8555-555555555555",
    )
    assert client.calls[-1] == ("revoke", b"new-token", old.remote_credential_id)
    assert container.enrollment_journal_repository.get(
        started.enrollment_id
    ).state is EnrollmentState.CONSUMED
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(old_ref)
    assert container.credential_store.read(new_ref) == b"new-token"


@pytest.mark.integration
async def test_rotation_consume_rejects_corrupt_mismatched_captured_identity(tmp_path):
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
        old_ref = container.credential_store.put(b"old-token")
        new_ref = container.credential_store.put(b"new-token")
    old = AgentRecord(
        agent_id="alpha",
        instance_id="33333333-3333-4333-8333-333333333333",
        display_name="Alpha",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=old_ref,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=True,
        source="manual",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(old)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW
    current = orchestrator.start_rotation(
        "alpha", ssh_user="edaops", ssh_host="10.0.0.11", ssh_port=22
    ).job
    for state in (
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        current = container.enrollment_journal_repository.replace_if_state(
            replace(
                current,
                state=state,
                remote_instance_id=old.instance_id,
                remote_credential_id="55555555-5555-4555-8555-555555555555",
                credential_temp_ref=new_ref,
                validated_http_address="10.0.0.11",
                updated_at=NOW,
            ),
            expected_state=current.state,
        )
    with container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE agent_enrollment_jobs SET remote_instance_id=? WHERE enrollment_id=?",
            ("66666666-6666-4666-8666-666666666666", current.enrollment_id),
        )

    with pytest.raises(EnrollmentConflict, match="agent_identity_changed"):
        await orchestrator.consume_rotation("alpha", current.enrollment_id)

    assert container.registry_repository.get("alpha") == old
    assert container.credential_store.read(old_ref) == b"old-token"
    assert container.credential_store.read(new_ref) == b"new-token"

    with container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE agent_enrollment_jobs SET state='activation_requested', "
            "save_requested=1, requested_display_name='Alpha' WHERE enrollment_id=?",
            (current.enrollment_id,),
        )

    class NoDispatchClient:
        def prepare_pinned(self, *_args):
            raise AssertionError("identity mismatch must be fenced before dispatch")

        async def activate(self, *_args, **_kwargs):
            raise AssertionError("identity mismatch must not activate")

    orchestrator.agent_client = NoDispatchClient()
    await orchestrator.recover()

    startup_residual = container.enrollment_journal_repository.get(current.enrollment_id)
    assert startup_residual.state is EnrollmentState.ACTIVATION_REQUESTED
    assert startup_residual.last_error_code == "agent_identity_changed"
    assert container.registry_repository.get("alpha") == old

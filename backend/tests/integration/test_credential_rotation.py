import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from types import SimpleNamespace

import pytest

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.jobs import (
    EnrollmentConflict,
    EnrollmentJobRequest,
    EnrollmentJobs,
)
from ic_env_guard.enrollment.orchestrator import MutationSagaError
from ic_env_guard.fleet.models import (
    AgentRecord,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RevisionConflict,
)
from ic_env_guard.main import create_app
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.manager_registry import ManagerRegistryRepository

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
                last_error_code=None,
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

    second_engine = create_sqlite_engine(tmp_path / "manager.db")
    second_registry = ManagerRegistryRepository(second_engine)
    try:
        with pytest.raises(RegistryConflict, match="agent_mutation_in_progress"):
            second_registry.update_if_revision(
                replace(old, display_name="Concurrent rename"),
                expected_revision=old.revision,
            )
    finally:
        second_engine.dispose()

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


@pytest.mark.integration
async def test_post_activate_swap_mismatch_revokes_new_and_preserves_concurrent_registry(
    tmp_path,
):
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
        concurrent_ref = container.credential_store.put(b"concurrent-token")
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
        revision=3,
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
                last_error_code=None,
                updated_at=NOW,
            ),
            expected_state=current.state,
        )

    class Client:
        calls = []
        fail_revoke = True

        def prepare_pinned(self, *_args):
            return object()

        async def activate(self, _target, token, **_kwargs):
            self.calls.append(("activate", token))

        async def revoke(self, _target, token, *, credential_id):
            self.calls.append(("revoke", token, credential_id))
            if self.fail_revoke:
                raise EnrollmentValidationError(
                    "agent_network_error", dispatch_state="unknown"
                )

    client = Client()
    orchestrator.agent_client = client
    base_registry = orchestrator.registry

    class RegistryRace:
        raced = False

        def __getattr__(self, name):
            return getattr(base_registry, name)

        def get(self, agent_id):
            registered = base_registry.get(agent_id)
            job = container.enrollment_journal_repository.get(current.enrollment_id)
            if job.state is EnrollmentState.ACTIVATED and not self.raced:
                self.raced = True
                with container.database_engine.begin() as connection:
                    connection.exec_driver_sql(
                        "UPDATE agents SET credential_ref=?, remote_credential_id=?, "
                        "revision=4 WHERE agent_id='alpha'",
                        (
                            concurrent_ref,
                            "77777777-7777-4777-8777-777777777777",
                        ),
                    )
            return registered

    orchestrator.registry = RegistryRace()

    with pytest.raises(MutationSagaError, match="agent_network_error") as caught:
        await orchestrator.consume_rotation("alpha", current.enrollment_id)
    assert caught.value.dispatch_state == "unknown"

    uncertain = container.enrollment_journal_repository.get(current.enrollment_id)
    assert uncertain.state is EnrollmentState.ACTIVATED
    assert uncertain.last_error_code == "agent_changed"
    assert uncertain.credential_temp_ref == new_ref
    assert uncertain.validated_http_address == "10.0.0.11"
    assert container.credential_store.read(new_ref) == b"new-token"

    client.fail_revoke = False
    await orchestrator.recover()

    residual = container.enrollment_journal_repository.get(current.enrollment_id)
    assert residual.state is EnrollmentState.FAILED
    assert residual.last_error_code == "agent_changed"
    assert residual.credential_temp_ref is None
    assert residual.old_credential_ref is None
    registered = container.registry_repository.get("alpha")
    assert registered.credential_ref == concurrent_ref
    assert container.credential_store.read(concurrent_ref) == b"concurrent-token"
    assert container.credential_store.read(old_ref) == b"old-token"
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(new_ref)
    assert client.calls[-1] == (
        "revoke",
        b"new-token",
        "55555555-5555-4555-8555-555555555555",
    )


@pytest.mark.integration
async def test_rotation_consume_and_registry_update_are_one_atomic_winner(tmp_path):
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
                last_error_code=None,
                updated_at=NOW,
            ),
            expected_state=current.state,
        )

    second_engine = create_sqlite_engine(tmp_path / "manager.db")
    second_journal = EnrollmentJournalRepository(second_engine)
    barrier = Barrier(2)

    def consume():
        barrier.wait()
        try:
            second_journal.consume_rotation(
                current.enrollment_id,
                agent_id="alpha",
                display_name="Alpha",
                now=NOW,
            )
            return "consume_ok"
        except (RegistryConflict, RevisionConflict) as exc:
            return str(exc)

    def update():
        barrier.wait()
        try:
            container.registry_repository.update_if_revision(
                replace(old, display_name="Concurrent rename"),
                expected_revision=old.revision,
            )
            return "update_ok"
        except (RegistryConflict, RevisionConflict) as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            consume_future = pool.submit(consume)
            update_future = pool.submit(update)
            results = {consume_future.result(), update_future.result()}
    finally:
        second_engine.dispose()

    assert results in (
        {"consume_ok", "agent_mutation_in_progress"},
        {"update_ok", "agent_changed"},
    )


@pytest.mark.integration
def test_two_engines_start_only_one_rotation_for_the_same_agent(tmp_path):
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
        old_ref = container.credential_store.put(b"old-token")
    container.registry_repository.create(
        AgentRecord(
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
    )
    first_jobs = container.enrollment_jobs
    second_engine = create_sqlite_engine(tmp_path / "manager.db")
    second_jobs = EnrollmentJobs(
        EnrollmentJournalRepository(second_engine),
        manager_id=first_jobs.manager_id,
        pending_ttl_seconds=first_jobs.pending_ttl_seconds,
        max_active=first_jobs.max_active,
    )
    request = EnrollmentJobRequest(
        normalized_endpoint="rotation-captured",
        transport_profile_id="rotation-captured",
        ssh_user="edaops",
        ssh_host="10.0.0.11",
        ssh_port=22,
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        replace_agent_id="alpha",
    )
    barrier = Barrier(2)

    def start(jobs):
        barrier.wait()
        try:
            jobs.create_rotation(request, now=NOW)
            return "created"
        except EnrollmentConflict as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(start, first_jobs)
            second = pool.submit(start, second_jobs)
            results = [first.result(), second.result()]
    finally:
        second_engine.dispose()

    assert results.count("created") == 1
    assert results.count("agent_mutation_in_progress") == 1


@pytest.mark.integration
async def test_put_before_rotation_consume_fails_before_remote_activate(tmp_path):
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
                last_error_code=None,
                updated_at=NOW,
            ),
            expected_state=current.state,
        )

    renamed = container.registry_repository.update_if_revision(
        replace(old, display_name="Renamed"), expected_revision=old.revision
    )
    assert renamed.revision == 2

    class NoDispatchClient:
        def prepare_pinned(self, *_args):
            raise AssertionError("captured mismatch must fail before remote preparation")

        async def activate(self, *_args, **_kwargs):
            raise AssertionError("captured mismatch must fail before remote activation")

    orchestrator.agent_client = NoDispatchClient()
    with pytest.raises(EnrollmentConflict, match="agent_changed"):
        await orchestrator.consume_rotation("alpha", current.enrollment_id)

    untouched = container.enrollment_journal_repository.get(current.enrollment_id)
    assert untouched.state is EnrollmentState.VERIFIED
    assert untouched.save_requested is False
    assert container.registry_repository.get("alpha") == renamed

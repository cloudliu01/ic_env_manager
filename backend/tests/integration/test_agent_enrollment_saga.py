from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.enrollment.agent_client import (
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.jobs import (
    EnrollmentConflict,
    EnrollmentJobRequest,
    job_input_fingerprint,
)
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod, EnrollmentState
from ic_env_guard.main import create_app

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def manager(tmp_path):
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
    return app.state.container


@pytest.mark.integration
async def test_consume_verified_enrollment_atomically_creates_registry_status_and_consumes(
    tmp_path,
):
    container = manager(tmp_path)
    jobs = container.enrollment_jobs
    journal = container.enrollment_journal_repository
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW
    job = jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint="https://10.0.0.11:8765",
            transport_profile_id="system-tls",
            ssh_user="edaops",
            ssh_host="10.0.0.11",
            ssh_port=22,
            enrollment_method=EnrollmentMethod.SSH_AUTO,
        ),
        now=NOW,
    )
    with container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE agent_enrollment_jobs SET discovery_result_id='bound-result' "
            "WHERE enrollment_id=?",
            (job.enrollment_id,),
        )
    job = journal.get(job.enrollment_id)
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"new-manager-token")
    current = job
    for state in (
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        credential_phase = state is not EnrollmentState.RUNNING
        current = journal.replace_if_state(
            replace(
                current,
                state=state,
                remote_instance_id="33333333-3333-4333-8333-333333333333",
                remote_credential_id="44444444-4444-4444-8444-444444444444",
                credential_temp_ref=reference if credential_phase else None,
                validated_http_address="10.0.0.11" if credential_phase else None,
                updated_at=NOW,
            ),
            expected_state=current.state,
        )

    class Client:
        def prepare_pinned(self, endpoint, profile, stored_ip):
            return SimpleNamespace(
                normalized_endpoint=endpoint,
                profile=profile,
                pinned_address=stored_ip,
            )

        async def activate(self, *_args, **_kwargs):
            return None

        async def revoke(self, *_args, **_kwargs):
            raise AssertionError("successful Registry commit must not compensate")

    orchestrator.agent_client = Client()

    assert hasattr(orchestrator, "consume")
    record = await orchestrator.consume(
        job.enrollment_id,
        display_name="EDA Host 01",
        input_fingerprint=job_input_fingerprint(current),
    )

    assert record.display_name == "EDA Host 01"
    assert record.source == "discovery"
    assert journal.get(job.enrollment_id).state is EnrollmentState.CONSUMED
    status = container.status_repository.get(record.agent_id)
    assert status is not None
    assert status.connection_status == "unknown"
    assert status.target_revision == 1


def verified_job(container, *, endpoint, instance_id, token):
    job = container.enrollment_jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint=endpoint,
            transport_profile_id="system-tls",
            ssh_user="edaops",
            ssh_host="10.0.0.11",
            ssh_port=22,
            enrollment_method=EnrollmentMethod.SSH_AUTO,
        ),
        now=NOW,
    )
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(token)
    current = job
    for state in (
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        credential_phase = state is not EnrollmentState.RUNNING
        current = container.enrollment_journal_repository.replace_if_state(
            replace(
                current,
                state=state,
                remote_instance_id=instance_id,
                remote_credential_id="55555555-5555-4555-8555-555555555555",
                credential_temp_ref=reference if credential_phase else None,
                validated_http_address="10.0.0.11" if credential_phase else None,
                updated_at=NOW,
            ),
            expected_state=current.state,
        )
    return current, reference


@pytest.mark.integration
@pytest.mark.parametrize("revoke_fails_once", [False, True])
async def test_add_unique_race_revokes_before_cleanup_or_retains_restart_residual(
    tmp_path, monkeypatch, revoke_fails_once
):
    container = manager(tmp_path)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW
    instance_id = "33333333-3333-4333-8333-333333333333"
    endpoint = "https://10.0.0.11:8765"
    with container.credential_store.lifecycle_lease():
        existing_ref = container.credential_store.put(b"existing-token")
    existing = AgentRecord(
        agent_id="existing",
        instance_id=instance_id,
        display_name="Existing",
        normalized_endpoint=endpoint,
        credential_ref=existing_ref,
        remote_credential_id="44444444-4444-4444-8444-444444444444",
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.SSH_AUTO,
        enabled=True,
        source="manual",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(existing)
    current, new_ref = verified_job(
        container, endpoint=endpoint, instance_id=instance_id, token=b"new-token"
    )
    monkeypatch.setattr(container.registry_repository, "find_duplicate", lambda **_kw: None)

    class Client:
        fail = revoke_fails_once
        calls = []

        def prepare_pinned(self, *_args):
            return object()

        async def activate(self, _target, token, **_kwargs):
            self.calls.append(("activate", token))

        async def revoke(self, _target, token, **_kwargs):
            self.calls.append(("revoke", token))
            if self.fail:
                raise EnrollmentValidationError(
                    "agent_network_error", dispatch_state="unknown"
                )

    client = Client()
    orchestrator.agent_client = client
    expected = "agent_network_error" if revoke_fails_once else "agent_already_registered"
    with pytest.raises(EnrollmentConflict, match=expected):
        await orchestrator.consume(
            current.enrollment_id,
            display_name="Racing",
            input_fingerprint=job_input_fingerprint(current),
        )

    residual = container.enrollment_journal_repository.get(current.enrollment_id)
    assert container.registry_repository.get("existing") == existing
    if revoke_fails_once:
        assert residual.state is EnrollmentState.ACTIVATED
        assert container.credential_store.read(new_ref) == b"new-token"
        client.fail = False
        await orchestrator.recover()
        residual = container.enrollment_journal_repository.get(current.enrollment_id)
    assert residual.state is EnrollmentState.FAILED
    assert residual.last_error_code == "agent_already_registered"
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(new_ref)
    assert client.calls[0] == ("activate", b"new-token")
    assert client.calls[-1] == ("revoke", b"new-token")


@pytest.mark.integration
async def test_local_edit_uses_revision_cas_and_resets_status(tmp_path):
    container = manager(tmp_path)
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"existing-token")
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
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW

    assert hasattr(orchestrator, "update_agent")
    updated = await orchestrator.update_agent(
        "alpha", display_name="Renamed", enabled=False
    )

    assert updated.display_name == "Renamed"
    assert updated.enabled is False
    assert updated.revision == 2
    status = container.status_repository.get("alpha")
    assert status is not None
    assert status.connection_status == "disabled"
    assert status.target_revision == 2


@pytest.mark.integration
async def test_legacy_target_edit_requires_and_rotates_validated_token(tmp_path):
    container = manager(tmp_path)
    with container.credential_store.lifecycle_lease():
        old_reference = container.credential_store.put(b"old-token")
    record = AgentRecord(
        agent_id="legacy",
        instance_id=None,
        display_name="Legacy",
        normalized_endpoint="https://10.0.0.11:8765",
        credential_ref=old_reference,
        remote_credential_id=None,
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        enabled=True,
        source="manual",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)
    orchestrator = container.enrollment_orchestrator
    orchestrator._clock = lambda: NOW

    with pytest.raises(EnrollmentConflict, match="legacy_revalidation_required"):
        await orchestrator.update_agent(
            "legacy", base_url="https://10.0.0.12:8765"
        )

    class Client:
        def prepare(self, endpoint, profile):
            assert profile == "system-tls"
            return SimpleNamespace(normalized_endpoint=endpoint)

        async def validate_legacy(self, target, token):
            assert token == b"replacement-token"
            return EnrollmentValidation(
                normalized_endpoint=target.normalized_endpoint,
                api_version="1",
                agent_version="1.2.3",
                capabilities=("terminal",),
                instance_id=None,
                summary=None,
                readiness_warning="legacy_readiness_unavailable",
            )

    orchestrator.agent_client = Client()
    updated = await orchestrator.update_agent(
        "legacy",
        base_url="https://10.0.0.12:8765",
        legacy_token="replacement-token",
    )

    assert updated.normalized_endpoint == "https://10.0.0.12:8765"
    assert updated.credential_ref != old_reference
    assert container.credential_store.read(updated.credential_ref) == b"replacement-token"
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(old_reference)

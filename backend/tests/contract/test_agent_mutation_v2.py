import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.enrollment.credential_store import CredentialStoreError
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest
from ic_env_guard.enrollment.orchestrator import MutationSagaError
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod, EnrollmentState
from ic_env_guard.main import create_app

AUTH = {"Authorization": "Bearer manager-secret"}


def manager_client(tmp_path) -> TestClient:
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
    return TestClient(app)


def add_managed_agent(client: TestClient, agent_id: str = "alpha") -> str:
    container = client.app.state.container
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"managed-token")
    now = datetime.now(UTC)
    container.registry_repository.create(
        AgentRecord(
            agent_id=agent_id,
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
            created_at=now,
            updated_at=now,
        )
    )
    return reference


@pytest.mark.contract
def test_add_agent_accepts_only_verified_enrollment_contract(tmp_path):
    client = manager_client(tmp_path)

    response = client.post(
        "/api/v2/agents",
        headers=AUTH,
        json={"enrollment_id": "missing", "display_name": "EDA Host 01"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_enrollment_not_found"


@pytest.mark.contract
def test_add_agent_consumes_verified_enrollment_once_and_returns_safe_agent(tmp_path):
    client = manager_client(tmp_path)
    container = client.app.state.container
    job = container.enrollment_jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint="https://10.0.0.12:8765",
            transport_profile_id="system-tls",
            enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        )
    )
    with container.credential_store.lifecycle_lease():
        reference = container.credential_store.put(b"write-only-token")
    current = job
    for state in (
        EnrollmentState.RUNNING,
        EnrollmentState.CREDENTIAL_ISSUED,
        EnrollmentState.VERIFYING,
        EnrollmentState.VERIFIED,
    ):
        current = container.enrollment_journal_repository.replace_if_state(
            replace(
                current,
                state=state,
                credential_temp_ref=(
                    reference if state is not EnrollmentState.RUNNING else None
                ),
                updated_at=datetime.now(UTC),
            ),
            expected_state=current.state,
        )

    body = {"enrollment_id": job.enrollment_id, "display_name": "Imported"}
    created = client.post("/api/v2/agents", headers=AUTH, json=body)
    repeated = client.post("/api/v2/agents", headers=AUTH, json=body)

    assert created.status_code == 201
    assert created.json()["agent"]["display_name"] == "Imported"
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "agent_enrollment_consumed"
    for forbidden in ("write-only-token", "credential_ref", reference):
        assert forbidden not in created.text + repeated.text


@pytest.mark.contract
def test_update_agent_rejects_unknown_agent_with_stable_error(tmp_path):
    response = manager_client(tmp_path).put(
        "/api/v2/agents/missing",
        headers=AUTH,
        json={"display_name": "Renamed"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_not_found"


@pytest.mark.contract
def test_rotation_uses_explicit_start_or_consume_action(tmp_path):
    response = manager_client(tmp_path).post(
        "/api/v2/agents/missing/credential-rotation",
        headers=AUTH,
        json={
            "action": "start",
            "ssh": {"user": "edaops", "host": "10.0.0.11", "port": 22},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_not_found"


@pytest.mark.contract
def test_local_only_delete_requires_query_and_body_confirmation(tmp_path):
    client = manager_client(tmp_path)
    response = client.request(
        "DELETE",
        "/api/v2/agents/missing?local_only=true",
        headers=AUTH,
        json={"confirm_remote_residual": False},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "local_only_confirmation_required"
    events = client.get("/api/control-plane/audit?limit=10", headers=AUTH).json()[
        "events"
    ]
    removal = next(event for event in events if event["operation"] == "agents.v2.remove")
    assert removal["result"] == "failed"
    assert removal["dispatch_state"] == "not_dispatched"
    assert removal["failure_category"] == "local_only_confirmation_required"


@pytest.mark.contract
def test_local_only_delete_is_204_and_records_remote_residual(tmp_path):
    client = manager_client(tmp_path)
    reference = add_managed_agent(client)

    response = client.request(
        "DELETE",
        "/api/v2/agents/alpha?local_only=true",
        headers=AUTH,
        json={"confirm_remote_residual": True},
    )

    assert response.status_code == 204
    container = client.app.state.container
    assert container.registry_repository.get("alpha") is None
    with pytest.raises(CredentialStoreError):
        container.credential_store.read(reference)
    events = client.get("/api/control-plane/audit?limit=10", headers=AUTH).json()[
        "events"
    ]
    removal = next(event for event in events if event["operation"] == "agents.v2.remove")
    assert removal["result"] == "success"
    assert removal["failure_category"] == "remote_credential_residual"


@pytest.mark.contract
def test_delete_returns_agent_in_use_and_releases_transient_gate(tmp_path):
    client = manager_client(tmp_path)
    add_managed_agent(client)
    tickets = client.app.state.container.gateway_ticket_store
    reservation = tickets.reserve("alpha")
    try:
        response = client.request(
            "DELETE",
            "/api/v2/agents/alpha?local_only=true",
            headers=AUTH,
            json={"confirm_remote_residual": True},
        )
    finally:
        tickets.release_reservation(reservation)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_in_use"
    assert tickets.begin_removal("alpha") is True
    tickets.abort_removal("alpha")


@pytest.mark.contract
def test_remote_revoke_fences_all_registry_mutations_until_delete_cas(tmp_path):
    client = manager_client(tmp_path)
    add_managed_agent(client)
    container = client.app.state.container
    entered = Event()
    release = Event()

    class BlockingClient:
        def prepare(self, *_args):
            return object()

        async def revoke(self, *_args, **_kwargs):
            entered.set()
            await asyncio.to_thread(release.wait)

    container.enrollment_orchestrator.agent_client = BlockingClient()

    def remove():
        return client.delete("/api/v2/agents/alpha", headers=AUTH)

    with ThreadPoolExecutor(max_workers=1) as pool:
        removal = pool.submit(remove)
        assert entered.wait(timeout=5)
        try:
            renamed = client.put(
                "/api/v2/agents/alpha",
                headers=AUTH,
                json={"display_name": "Concurrent rename"},
            )
            disabled = client.post(
                "/api/v2/agents/alpha/enabled",
                headers=AUTH,
                json={"enabled": False},
            )
            rotation = client.post(
                "/api/v2/agents/alpha/credential-rotation",
                headers=AUTH,
                json={
                    "action": "start",
                    "ssh": {"user": "edaops", "host": "10.0.0.11", "port": 22},
                },
            )
            assert {
                renamed.json()["error"]["code"],
                disabled.json()["error"]["code"],
                rotation.json()["error"]["code"],
            } == {"agent_mutation_in_progress"}
            assert renamed.status_code == disabled.status_code == rotation.status_code == 409
            current = container.registry_repository.get("alpha")
            assert current.display_name == "Alpha"
            assert current.enabled is True
            assert current.revision == 1
        finally:
            release.set()
        removed = removal.result(timeout=5)

    assert removed.status_code == 204
    assert container.registry_repository.get("alpha") is None


@pytest.mark.contract
def test_add_unique_race_audits_actual_dispatched_state(tmp_path):
    client = manager_client(tmp_path)
    container = client.app.state.container
    job = container.enrollment_jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint="https://10.0.0.12:8765",
            transport_profile_id="system-tls",
            enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        )
    )

    async def conflict(*_args, **_kwargs):
        raise MutationSagaError(
            "agent_already_registered", dispatch_state="dispatched"
        )

    container.enrollment_orchestrator.consume = conflict
    response = client.post(
        "/api/v2/agents",
        headers=AUTH,
        json={"enrollment_id": job.enrollment_id, "display_name": "Alpha"},
    )

    assert response.status_code == 409
    events = client.get("/api/control-plane/audit?limit=10", headers=AUTH).json()[
        "events"
    ]
    event = next(item for item in events if item["operation"] == "agents.v2.create")
    assert event["failure_category"] == "agent_already_registered"
    assert event["dispatch_state"] == "dispatched"


@pytest.mark.contract
@pytest.mark.parametrize(
    ("code", "dispatch_state", "status"),
    [
        ("agent_changed", "dispatched", 409),
        ("agent_identity_changed", "not_dispatched", 409),
        ("agent_network_error", "unknown", 503),
    ],
)
def test_rotation_audit_uses_saga_dispatch_state(
    tmp_path, code, dispatch_state, status
):
    client = manager_client(tmp_path)
    container = client.app.state.container

    async def fail(*_args, **_kwargs):
        raise MutationSagaError(code, dispatch_state=dispatch_state)

    container.enrollment_orchestrator.consume_rotation = fail
    response = client.post(
        "/api/v2/agents/alpha/credential-rotation",
        headers=AUTH,
        json={"action": "consume", "enrollment_id": "rotation-1"},
    )

    assert response.status_code == status
    events = client.get("/api/control-plane/audit?limit=10", headers=AUTH).json()[
        "events"
    ]
    event = next(
        item for item in events if item["operation"] == "agents.v2.credential-rotation"
    )
    assert event["failure_category"] == code
    assert event["dispatch_state"] == dispatch_state

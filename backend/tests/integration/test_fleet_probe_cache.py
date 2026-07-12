import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psutil
import pytest

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.bootstrap.composition import _manager_self_targets
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, AgentStatus, EnrollmentMethod
from ic_env_guard.fleet.probes import AgentProbeError, FleetProbeService
from ic_env_guard.main import create_app

NOW = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_legacy_probe_cannot_overwrite_manager_v2_status(tmp_path):
    token = tmp_path / "agent.token"
    token.write_text("agent-secret\n", encoding="utf-8")
    token.chmod(0o600)
    registry = AgentRegistry(
        [
            AgentConfig(
                id="alpha",
                name="Alpha",
                base_url="https://10.0.0.11:8765",
                token_file=token,
            )
        ]
    )

    class LegacyClient:
        async def request(self, *_args, **_kwargs):
            return Response(
                {
                    "api_version": "1",
                    "agent_version": "0.2.0",
                    "capabilities": [
                        "services.v1",
                        "terminals.v1",
                        "audit.v1",
                        "monitoring.snapshot.v1",
                    ],
                }
            )

    class Statuses:
        def get(self, _agent_id):
            return None

        def update_if_target_revision(self, *_args, **_kwargs):
            raise AssertionError("legacy probe must not overwrite v2 status")

    service = AgentAvailabilityService(
        registry,
        LegacyClient(),
        status_repository=Statuses(),
        persist_probe_status=False,
    )

    await service.probe("alpha")


def test_manager_self_targets_include_every_interface_on_wildcard_bind(monkeypatch):
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [
                type("Addr", (), {"family": socket.AF_INET, "address": "10.20.0.5"})()
            ],
            "eth1": [
                type(
                    "Addr", (), {"family": socket.AF_INET6, "address": "fd20::5%eth1"}
                )()
            ],
        },
    )

    assert set(_manager_self_targets("0.0.0.0", 8765)) >= {
        ("10.20.0.5", 8765),
        ("fd20::5", 8765),
        ("127.0.0.1", 8765),
        ("::1", 8765),
    }


class TargetPolicy:
    def __init__(self):
        self.calls = []

    def resolve(self, endpoint, profile):
        self.calls.append((endpoint, profile.id))
        return object()


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class Client:
    def __init__(self, payloads, *, hook=None):
        self.payloads = list(payloads)
        self.calls = []
        self.hook = hook

    async def request(self, target, credential, method, path, **_kwargs):
        self.calls.append((credential, method, path))
        if self.hook is not None:
            self.hook(path)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return Response(payload)


def _manager(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "manager.db",
            allowed_agent_cidrs=["10.0.0.0/8"],
        ),
    )
    app = create_app(config=config)
    return app.state.container


def _add(container, agent_id, endpoint, *, instance_id=None):
    with container.credential_store.lifecycle_lease():
        credential_ref = container.credential_store.put(f"secret-{agent_id}".encode())
    record = AgentRecord(
        agent_id=agent_id,
        instance_id=instance_id,
        display_name=agent_id,
        normalized_endpoint=endpoint,
        credential_ref=credential_ref,
        remote_credential_id=None,
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        enabled=True,
        source="config_import",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    container.registry_repository.create(record)
    return record


def _service(container, client, policy=None):
    return FleetProbeService(
        registry_repository=container.registry_repository,
        status_repository=container.status_repository,
        credential_store=container.credential_store,
        target_policy=policy or TargetPolicy(),
        transport_profiles=container.config.control_plane.transport_profiles,
        client=client,
        stale_after_seconds=30,
        max_parallel_probes=2,
        probe_jitter_seconds=0,
        clock=lambda: NOW,
    )


def _capabilities(instance_id):
    return {
        "instance_id": instance_id,
        "name": "Lab",
        "api_version": "2",
        "agent_version": "0.2.0",
        "capabilities": ["runtime.v2", "summary.v2", "logs.v2"],
    }


def _summary(*, critical=0):
    return {
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "observations": {"total": 1, "warning": 0, "critical": critical, "stale": 0},
        "logs": {"total": 0, "stale": 0},
        "services": {"total": 1, "running": 1, "unhealthy": 0},
        "terminals": {"active": 0},
    }


@pytest.mark.integration
async def test_probe_uses_policy_credential_and_v2_capabilities_then_summary(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    policy = TargetPolicy()
    client = Client([_capabilities("11111111-1111-4111-8111-111111111111"), _summary()])

    status = await _service(container, client, policy).probe("alpha")

    assert policy.calls == [("https://10.0.0.11:8765", "system-tls")]
    assert [call[2] for call in client.calls] == [
        "/api/v2/capabilities",
        "/api/v2/summary",
    ]
    assert all(call[0] == b"secret-alpha" for call in client.calls)
    assert status.connection_status == "ready"
    assert status.workload_status == "healthy"
    assert container.status_repository.get("alpha") == status


@pytest.mark.integration
async def test_late_probe_cannot_write_status_after_target_revision_changes(tmp_path):
    container = _manager(tmp_path)
    original = _add(container, "alpha", "https://10.0.0.11:8765")

    def mutate(path):
        if path == "/api/v2/summary":
            current = container.registry_repository.get("alpha")
            container.registry_repository.update_if_revision(
                replace(current, display_name="changed", updated_at=NOW + timedelta(seconds=1)),
                expected_revision=current.revision,
            )

    client = Client(
        [_capabilities("11111111-1111-4111-8111-111111111111"), _summary()], hook=mutate
    )
    service = _service(container, client)

    with pytest.raises(AgentProbeError, match="agent_target_changed"):
        await service.probe("alpha")

    assert container.status_repository.get("alpha") is None
    assert container.registry_repository.get("alpha").revision > original.revision


@pytest.mark.integration
async def test_probe_failure_preserves_last_known_summary_and_does_not_block_fleet(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")
    old = AgentStatus(
        agent_id="alpha",
        target_revision=1,
        connection_status="ready",
        workload_status="critical",
        observed_at=NOW - timedelta(minutes=1),
        stale_after=NOW + timedelta(seconds=20),
        api_version="2",
        agent_version="0.1.0",
        capabilities=("summary.v2",),
        summary=_summary(critical=1),
        last_error_code=None,
        updated_at=NOW - timedelta(minutes=1),
    )
    container.status_repository.update_if_target_revision(old, expected_revision=1)
    client = Client(
        [
            AgentClientError("agent_network_error", "timeout"),
            _capabilities("22222222-2222-4222-8222-222222222222"),
            _summary(),
        ]
    )

    results = await _service(container, client).probe_all()

    assert set(results) == {"alpha", "beta"}
    failed = container.status_repository.get("alpha")
    assert failed.connection_status == "unavailable"
    assert failed.workload_status == "stale"
    assert failed.summary == old.summary
    assert container.status_repository.get("beta").connection_status == "ready"


@pytest.mark.integration
async def test_duplicate_reported_identity_marks_all_conflicts_and_blocks_routing(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")
    identity = "11111111-1111-4111-8111-111111111111"
    client = Client(
        [_capabilities(identity), _summary(), _capabilities(identity), _summary()]
    )
    service = _service(container, client)

    await service.probe("alpha")
    with pytest.raises(AgentProbeError, match="agent_identity_conflict"):
        await service.probe("beta")

    for agent_id in ("alpha", "beta"):
        status = container.status_repository.get(agent_id)
        assert status.connection_status == "unavailable"
        assert status.last_error_code == "agent_identity_conflict"
        agent = container.agent_registry.get(agent_id)
        assert agent.enabled is True
        with pytest.raises(AgentClientError) as blocked:
            await container.agent_client.request(agent, "GET", "/api/capabilities")
        assert blocked.value.dispatch_state == "not_dispatched"

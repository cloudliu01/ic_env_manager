import asyncio
import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psutil
import pytest

from ic_env_guard.agents.availability import AgentAvailabilityService, AgentObservation
from ic_env_guard.agents.client import AgentClientError
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.bootstrap.composition import _manager_self_targets
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, AgentStatus, EnrollmentMethod
from ic_env_guard.fleet.probes import AgentProbeError, FleetProbeService
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
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


@pytest.mark.integration
async def test_legacy_capability_reads_durable_status_and_ensure_uses_fleet_delegate(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    calls = []

    async def fleet_probe(agent_id):
        calls.append(agent_id)
        status = AgentStatus(
            agent_id=agent_id,
            target_revision=1,
            connection_status="ready",
            workload_status="healthy",
            observed_at=datetime.now(UTC),
            stale_after=datetime.now(UTC) + timedelta(minutes=1),
            api_version="2",
            agent_version="0.2.0",
            capabilities=("services.v1",),
            summary=_summary(),
            last_error_code=None,
            updated_at=datetime.now(UTC),
        )
        container.status_repository.update_if_target_revision(status, 1)

    container.agent_availability.set_probe_delegate(fleet_probe)

    assert not container.agent_availability.has_capability("alpha", "services.v1")
    assert await container.agent_availability.ensure_capability("alpha", "services.v1")
    assert calls == ["alpha"]
    assert container.agent_availability.has_capability("alpha", "services.v1")


@pytest.mark.integration
def test_manager_lifecycle_starts_only_the_fleet_probe_scheduler(tmp_path):
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

    from fastapi.testclient import TestClient

    with TestClient(app):
        assert app.state.fleet_probe_task
        assert not hasattr(app.state, "agent_availability_probe_task")


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

    def validate_safety(self, endpoint):
        return endpoint

    def resolve_validated(self, endpoint, profile):
        self.calls.append((endpoint, profile.id))
        return object()


class Response:
    status_code = 200

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
        if isinstance(payload, Response):
            return payload
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


def _service(
    container,
    client,
    policy=None,
    legacy=None,
    max_parallel=2,
    allow_import_without_dynamic_allowlist=True,
    clock=lambda: NOW,
):
    return FleetProbeService(
        registry_repository=container.registry_repository,
        status_repository=container.status_repository,
        credential_store=container.credential_store,
        target_policy=policy or TargetPolicy(),
        transport_profiles=container.config.control_plane.transport_profiles,
        client=client,
        stale_after_seconds=30,
        max_parallel_probes=max_parallel,
        probe_jitter_seconds=0,
        clock=clock,
        legacy_availability=legacy,
        allow_import_without_dynamic_allowlist=allow_import_without_dynamic_allowlist,
    )


class LegacyAdapter:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()
        self.release.set()

    async def probe_legacy(self, agent_id):
        self.calls.append(agent_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await self.release.wait()
        self.active -= 1
        return AgentObservation(
            status="ready",
            observed_at=NOW,
            stale_after=NOW + timedelta(seconds=30),
            api_version="1",
            agent_version="0.1.0",
            capabilities=("services.v1",),
            dispatch_state="dispatched",
        )


class RejectingPolicy:
    def validate_safety(self, endpoint):
        return endpoint

    def resolve_validated(self, _endpoint, _profile):
        raise TargetPolicyError("target_address_not_allowed", "not allowed")


@pytest.mark.integration
async def test_imported_legacy_agent_falls_back_when_dynamic_allowlist_is_missing(tmp_path):
    container = _manager(tmp_path)
    _add(container, "legacy", "https://10.0.0.11:8765")
    legacy = LegacyAdapter()
    result = await _service(
        container,
        Client([]),
        policy=RejectingPolicy(),
        legacy=legacy,
    ).probe("legacy")

    assert legacy.calls == ["legacy"]
    assert result.status.connection_status == "degraded"
    assert result.status.api_version == "1"
    assert result.status.last_error_code == "legacy_identity_unavailable"
    assert result.dispatch_state == "dispatched"


@pytest.mark.integration
async def test_imported_legacy_http_marker_and_v2_not_found_use_narrow_fallback(tmp_path):
    container = _manager(tmp_path)
    marker = _add(container, "marker", "http://10.0.0.11:8765")
    container.registry_repository.update_if_revision(
        replace(marker, transport_profile_id="legacy-config-http", updated_at=NOW),
        expected_revision=1,
    )
    identity = "11111111-1111-4111-8111-111111111111"
    _add(
        container,
        "not-found",
        "https://10.0.0.12:8765",
        instance_id=identity,
    )

    class NotFound(Response):
        status_code = 404

    legacy = LegacyAdapter()
    marker_result = await _service(
        container, Client([]), policy=TargetPolicy(), legacy=legacy
    ).probe("marker")
    not_found_result = await _service(
        container,
        Client([NotFound({"error": "not found"})]),
        policy=TargetPolicy(),
        legacy=legacy,
    ).probe("not-found")

    assert marker_result.status.connection_status == "degraded"
    assert not_found_result.status.connection_status == "degraded"
    assert legacy.calls == ["marker", "not-found"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("endpoint", "expected_code", "self_targets"),
    [
        ("http://10.0.0.11:8765", "target_is_manager", [("10.0.0.11", 8765)]),
        ("http://169.254.169.254:80", "target_address_forbidden", [("10.0.0.1", 8765)]),
    ],
)
async def test_legacy_http_marker_cannot_bypass_safety_gate(
    tmp_path, endpoint, expected_code, self_targets
):
    container = _manager(tmp_path)
    marker = _add(container, "marker", endpoint)
    container.registry_repository.update_if_revision(
        replace(marker, transport_profile_id="legacy-config-http", updated_at=NOW),
        expected_revision=1,
    )
    legacy = LegacyAdapter()
    policy = AgentTargetPolicy(allowed_agent_cidrs=[], self_targets=self_targets)

    result = await _service(
        container, Client([]), policy=policy, legacy=legacy
    ).probe("marker")

    assert result.status.last_error_code == expected_code
    assert result.dispatch_state == "not_dispatched"
    assert legacy.calls == []


@pytest.mark.integration
async def test_import_with_missing_stored_profile_never_falls_back(tmp_path):
    container = _manager(tmp_path)
    original = _add(container, "legacy", "https://10.0.0.11:8765")
    container.registry_repository.update_if_revision(
        replace(original, transport_profile_id="missing-profile", updated_at=NOW),
        expected_revision=1,
    )
    legacy = LegacyAdapter()

    result = await _service(
        container, Client([]), policy=TargetPolicy(), legacy=legacy
    ).probe("legacy")

    assert result.status.last_error_code == "transport_profile_invalid"
    assert result.dispatch_state == "not_dispatched"
    assert legacy.calls == []


@pytest.mark.integration
async def test_manual_agent_never_uses_import_legacy_fallback(tmp_path):
    container = _manager(tmp_path)
    identity = "11111111-1111-4111-8111-111111111111"
    original = _add(
        container,
        "manual",
        "https://10.0.0.11:8765",
        instance_id=identity,
    )
    container.registry_repository.update_if_revision(
        replace(original, source="manual", updated_at=NOW),
        expected_revision=1,
    )
    legacy = LegacyAdapter()

    result = await _service(
        container,
        Client([]),
        policy=RejectingPolicy(),
        legacy=legacy,
    ).probe("manual")

    assert legacy.calls == []
    assert result.status.connection_status == "unavailable"
    assert result.status.last_error_code == "target_address_not_allowed"
    assert result.dispatch_state == "not_dispatched"


@pytest.mark.integration
async def test_configured_dynamic_allowlist_rejection_never_falls_back(tmp_path):
    container = _manager(tmp_path)
    _add(container, "legacy", "https://10.0.0.11:8765")
    legacy = LegacyAdapter()

    result = await _service(
        container,
        Client([]),
        policy=RejectingPolicy(),
        legacy=legacy,
        allow_import_without_dynamic_allowlist=False,
    ).probe("legacy")

    assert result.status.last_error_code == "target_address_not_allowed"
    assert legacy.calls == []


@pytest.mark.integration
async def test_import_fallback_never_bypasses_forbidden_or_manager_self_target(tmp_path):
    container = _manager(tmp_path)
    _add(container, "legacy", "https://10.0.0.11:8765")
    legacy = LegacyAdapter()

    for code in ("target_address_forbidden", "target_is_manager"):
        class ForbiddenPolicy:
            def __init__(self, error_code):
                self.error_code = error_code

            def validate_safety(self, _endpoint):
                raise TargetPolicyError(self.error_code, "forbidden")

            def resolve_validated(self, _endpoint, _profile):
                raise AssertionError("forbidden target must not reach profile validation")

        result = await _service(
            container,
            Client([]),
            policy=ForbiddenPolicy(code),
            legacy=legacy,
        ).probe("legacy")
        assert result.status.last_error_code == code

    assert legacy.calls == []


@pytest.mark.integration
async def test_legacy_fallback_respects_global_probe_limit(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")
    legacy = LegacyAdapter()
    legacy.release.clear()
    service = _service(
        container,
        Client([]),
        policy=RejectingPolicy(),
        legacy=legacy,
        max_parallel=1,
    )

    first = asyncio.create_task(service.probe("alpha"))
    second = asyncio.create_task(service.probe("beta"))
    while legacy.active == 0:
        await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert legacy.max_active == 1
    assert len(legacy.calls) == 1
    legacy.release.set()
    await asyncio.gather(first, second)
    assert legacy.max_active == 1


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

    result = await _service(container, client, policy).probe("alpha")
    status = result.status

    assert policy.calls == [("https://10.0.0.11:8765", "system-tls")]
    assert [call[2] for call in client.calls] == [
        "/api/v2/capabilities",
        "/api/v2/summary",
    ]
    assert all(call[0] == b"secret-alpha" for call in client.calls)
    assert status.connection_status == "ready"
    assert status.workload_status == "healthy"
    assert result.dispatch_state == "dispatched"
    assert container.status_repository.get("alpha") == status


@pytest.mark.integration
async def test_probe_discards_unapproved_remote_summary_and_capability_fields(tmp_path):
    container = _manager(tmp_path)
    identity = "11111111-1111-4111-8111-111111111111"
    _add(container, "alpha", "https://10.0.0.11:8765", instance_id=identity)
    capabilities = _capabilities(identity)
    capabilities.update(
        {"Authorization": "Bearer remote-secret", "ssh_user": "root"}
    )
    summary = _summary()
    summary.update(
        {"Authorization": "Bearer remote-secret", "details": {"private_key": "secret"}}
    )
    summary["observations"]["details"] = {"password": "secret"}
    summary["services"]["ssh_user"] = "root"

    await _service(container, Client([capabilities, summary])).probe("alpha")

    stored = container.status_repository.get("alpha")
    assert set(stored.summary) == {
        "observed_at",
        "observations",
        "logs",
        "services",
        "terminals",
    }
    assert set(stored.summary["observations"]) == {
        "total",
        "warning",
        "critical",
        "stale",
    }
    assert set(stored.summary["services"]) == {"total", "running", "unhealthy"}
    serialized = str(stored)
    assert "remote-secret" not in serialized
    assert "private_key" not in serialized
    assert "ssh_user" not in serialized


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability", "Authorization:Bearer-secret"),
        ("capability", "logs.v2\nprivate-key"),
        ("capability", "x" * 129),
        ("agent_version", "v1\nAuthorization"),
        ("agent_version", "v" * 65),
    ],
)
async def test_probe_rejects_unbounded_or_control_remote_metadata(
    tmp_path, field, value
):
    container = _manager(tmp_path)
    identity = "11111111-1111-4111-8111-111111111111"
    _add(container, "alpha", "https://10.0.0.11:8765", instance_id=identity)
    capabilities = _capabilities(identity)
    if field == "capability":
        capabilities["capabilities"].append(value)
    else:
        capabilities["agent_version"] = value

    result = await _service(container, Client([capabilities])).probe("alpha")

    assert result.status.connection_status == "unavailable"
    assert result.status.last_error_code == "agent_protocol_error"
    assert result.dispatch_state == "dispatched"
    assert value not in str(container.status_repository.get("alpha"))


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
async def test_same_agent_probes_are_single_flight_and_newer_result_wins(tmp_path):
    container = _manager(tmp_path)
    identity = "11111111-1111-4111-8111-111111111111"
    _add(
        container,
        "alpha",
        "https://10.0.0.11:8765",
        instance_id=identity,
    )

    old_capabilities = _capabilities(identity)
    old_capabilities["agent_version"] = "0.1.0"
    new_capabilities = _capabilities(identity)
    new_capabilities["agent_version"] = "0.2.0"

    class BlockingClient(Client):
        def __init__(self):
            super().__init__(
                [old_capabilities, _summary(critical=1), new_capabilities, _summary()]
            )
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, *args, **kwargs):
            if not self.calls:
                self.calls.append((args[1], args[2], args[3]))
                self.entered.set()
                await self.release.wait()
                return Response(self.payloads.pop(0))
            return await super().request(*args, **kwargs)

    client = BlockingClient()
    service = _service(container, client)

    older = asyncio.create_task(service.probe("alpha"))
    await client.entered.wait()
    newer = asyncio.create_task(service.probe("alpha"))
    await asyncio.sleep(0)

    assert len(client.calls) == 1
    client.release.set()
    await asyncio.gather(older, newer)

    final = container.status_repository.get("alpha")
    assert final.agent_version == "0.2.0"
    assert final.workload_status == "healthy"


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

    restarted_client = Client([_capabilities(identity), _summary()])
    restarted = _service(container, restarted_client)
    with pytest.raises(AgentProbeError, match="agent_identity_conflict") as sticky:
        await restarted.probe("alpha")
    assert sticky.value.dispatch_state == "not_dispatched"
    assert restarted_client.calls == []


@pytest.mark.integration
async def test_changed_reported_identity_is_sticky_and_blocks_credential_loading(tmp_path):
    container = _manager(tmp_path)
    original_id = "11111111-1111-4111-8111-111111111111"
    _add(
        container,
        "alpha",
        "https://10.0.0.11:8765",
        instance_id=original_id,
    )
    changed_id = "22222222-2222-4222-8222-222222222222"
    service = _service(container, Client([_capabilities(changed_id), _summary()]))

    with pytest.raises(AgentProbeError, match="agent_identity_changed") as changed:
        await service.probe("alpha")

    assert changed.value.dispatch_state == "dispatched"
    assert container.status_repository.get("alpha").last_error_code == "agent_identity_changed"
    agent = container.agent_registry.get("alpha")
    with pytest.raises(AgentClientError) as blocked:
        await container.agent_client.request(agent, "GET", "/api/capabilities")
    assert blocked.value.dispatch_state == "not_dispatched"


@pytest.mark.integration
def test_identity_conflict_bulk_status_write_rolls_back_if_second_upsert_fails(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")
    statuses = tuple(
        AgentStatus(
            agent_id=agent_id,
            target_revision=1,
            connection_status="unavailable",
            workload_status="unknown",
            observed_at=NOW,
            stale_after=NOW + timedelta(seconds=30),
            api_version=None,
            agent_version=None,
            capabilities=(),
            summary={},
            last_error_code="agent_identity_conflict",
            updated_at=NOW,
        )
        for agent_id in ("alpha", "beta")
    )
    with container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_beta_status BEFORE INSERT ON agent_status "
            "WHEN NEW.agent_id = 'beta' BEGIN SELECT RAISE(ABORT, 'fail beta'); END"
        )

    with pytest.raises(Exception, match="status storage"):
        container.status_repository.update_many_if_target_revisions(statuses)

    assert container.status_repository.get("alpha") is None
    assert container.status_repository.get("beta") is None


@pytest.mark.integration
def test_bulk_status_revision_mismatch_writes_no_members(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")
    statuses = tuple(
        AgentStatus(
            agent_id=agent_id,
            target_revision=revision,
            connection_status="unavailable",
            workload_status="unknown",
            observed_at=NOW,
            stale_after=NOW + timedelta(seconds=30),
            api_version=None,
            agent_version=None,
            capabilities=(),
            summary={},
            last_error_code="agent_identity_conflict",
            updated_at=NOW,
        )
        for agent_id, revision in (("alpha", 1), ("beta", 2))
    )

    assert not container.status_repository.update_many_if_target_revisions(statuses)
    assert container.status_repository.get("alpha") is None
    assert container.status_repository.get("beta") is None


@pytest.mark.integration
def test_bulk_status_stale_member_rolls_back_every_candidate(tmp_path):
    container = _manager(tmp_path)
    _add(container, "alpha", "https://10.0.0.11:8765")
    _add(container, "beta", "https://10.0.0.12:8765")

    def status(agent_id, observed_at, code):
        return AgentStatus(
            agent_id=agent_id,
            target_revision=1,
            connection_status="unavailable",
            workload_status="unknown",
            observed_at=observed_at,
            stale_after=observed_at + timedelta(seconds=30),
            api_version=None,
            agent_version=None,
            capabilities=(),
            summary={},
            last_error_code=code,
            updated_at=observed_at,
        )

    newer = NOW + timedelta(seconds=10)
    container.status_repository.update_many_if_target_revisions(
        (status("alpha", NOW, "old-alpha"), status("beta", newer, "new-beta"))
    )

    assert not container.status_repository.update_many_if_target_revisions(
        (
            status("alpha", newer + timedelta(seconds=1), "candidate-alpha"),
            status("beta", NOW, "stale-beta"),
        )
    )
    assert container.status_repository.get("alpha").last_error_code == "old-alpha"
    assert container.status_repository.get("beta").last_error_code == "new-beta"


@pytest.mark.integration
async def test_cross_process_older_probe_cannot_overwrite_newer_status(tmp_path):
    container = _manager(tmp_path)
    identity = "11111111-1111-4111-8111-111111111111"
    _add(
        container,
        "alpha",
        "https://10.0.0.11:8765",
        instance_id=identity,
    )
    old_capabilities = _capabilities(identity)
    old_capabilities["agent_version"] = "0.1.0"
    new_capabilities = _capabilities(identity)
    new_capabilities["agent_version"] = "0.2.0"

    class SlowClient(Client):
        def __init__(self):
            super().__init__([old_capabilities, _summary(critical=1)])
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, *args, **kwargs):
            if not self.calls:
                self.entered.set()
                await self.release.wait()
            return await super().request(*args, **kwargs)

    slow_client = SlowClient()
    old_service = _service(container, slow_client, clock=lambda: NOW)
    new_service = _service(
        container,
        Client([new_capabilities, _summary()]),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    old_probe = asyncio.create_task(old_service.probe("alpha"))
    await slow_client.entered.wait()
    await new_service.probe("alpha")
    slow_client.release.set()

    with pytest.raises(AgentProbeError):
        await old_probe
    final = container.status_repository.get("alpha")
    assert final.agent_version == "0.2.0"
    assert final.workload_status == "healthy"


@pytest.mark.integration
async def test_dispatch_state_aggregates_v2_dispatch_before_local_legacy_failure(tmp_path):
    container = _manager(tmp_path)
    _add(container, "legacy", "https://10.0.0.11:8765")

    class NotFound(Response):
        status_code = 404

    class LocalLegacyFailure(LegacyAdapter):
        async def probe_legacy(self, agent_id):
            self.calls.append(agent_id)
            return AgentObservation(
                status="unavailable",
                observed_at=NOW,
                stale_after=NOW + timedelta(seconds=30),
                last_error="agent_auth_error",
                dispatch_state="not_dispatched",
            )

    result = await _service(
        container,
        Client([NotFound({"error": "not found"})]),
        legacy=LocalLegacyFailure(),
    ).probe("legacy")

    assert result.status.last_error_code == "agent_auth_error"
    assert result.dispatch_state == "dispatched"


@pytest.mark.integration
async def test_failure_never_reuses_summary_from_an_old_target_revision(tmp_path):
    container = _manager(tmp_path)
    original = _add(container, "alpha", "https://10.0.0.11:8765")
    container.status_repository.update_if_target_revision(
        AgentStatus(
            agent_id="alpha",
            target_revision=1,
            connection_status="ready",
            workload_status="critical",
            observed_at=NOW,
            stale_after=NOW + timedelta(seconds=30),
            api_version="2",
            agent_version="0.1.0",
            capabilities=("summary.v2",),
            summary=_summary(critical=1),
            last_error_code=None,
            updated_at=NOW,
        ),
        1,
    )
    container.registry_repository.update_if_revision(
        replace(original, transport_profile_id="missing-profile", updated_at=NOW),
        expected_revision=1,
    )

    result = await _service(container, Client([])).probe("alpha")

    assert result.status.target_revision == 2
    assert result.status.summary == {}
    assert result.status.capabilities == ()

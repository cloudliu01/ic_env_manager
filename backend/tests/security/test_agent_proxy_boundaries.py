from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.agents.availability import CapabilityCheck
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import SYSTEM_TLS_PROFILE, TrustedLanHttpProfile
from ic_env_guard.main import create_app
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError


def manager_client(tmp_path):
    token = tmp_path / "manager.token"
    token.write_text("manager-secret\n", encoding="utf-8")
    token.chmod(0o600)
    return TestClient(
        create_app(
            config=AppConfig(
                mode="control-plane",
                auth=AuthConfig(token_file=token),
                control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
            )
        )
    )


@pytest.mark.security
def test_v2_service_and_audit_proxy_routes_are_explicit(tmp_path):
    with manager_client(tmp_path) as client:
        headers = {"Authorization": "Bearer manager-secret"}
        services = client.get("/api/v2/agents/missing/services", headers=headers)
        audit = client.get("/api/v2/agents/missing/audit?limit=10", headers=headers)

    assert services.status_code == audit.status_code == 404
    assert services.json()["error"]["code"] == "agent_not_found"
    assert audit.json()["error"]["code"] == "agent_not_found"


@pytest.mark.security
def test_v2_proxy_rejects_noncanonical_path_id_before_dispatch(tmp_path):
    calls = []

    class Proxy:
        async def get_json(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("invalid path must not dispatch")

    client = manager_client(tmp_path)
    client.app.dependency_overrides[get_agent_http_proxy] = lambda: Proxy()
    with client:
        response = client.get(
            "/api/v2/agents/lab/services/%2e",
            headers={"Authorization": "Bearer manager-secret"},
        )

    assert response.status_code == 422
    assert calls == []


def _record(agent_id, credential_ref):
    now = datetime.now(UTC)
    return AgentRecord(
        agent_id,
        f"instance-{agent_id}",
        agent_id,
        f"https://10.20.30.{1 if agent_id == 'a' else 2}:8765",
        credential_ref,
        "remote",
        "system-tls",
        EnrollmentMethod.SSH_AUTO,
        True,
        "manual",
        1,
        now,
        now,
    )


def _local_record(**changes):
    now = datetime.now(UTC)
    record = AgentRecord(
        "local-agent",
        "instance-local",
        "Local Agent",
        "http://127.0.0.1:8766",
        "c" * 48,
        "remote-local",
        "local-loopback-http",
        EnrollmentMethod.LOCAL_SOCKET,
        True,
        "local_dev_bootstrap",
        1,
        now,
        now,
    )
    return replace(record, **changes)


def _local_profiles():
    return (
        TrustedLanHttpProfile(
            id="local-loopback-http", allowed_cidrs=["127.0.0.0/8"]
        ),
        TrustedLanHttpProfile(
            id="alternate-loopback-http", allowed_cidrs=["127.0.0.0/8"]
        ),
    )


@pytest.mark.security
@pytest.mark.parametrize(
    ("capability", "path"),
    [
        ("observations.v2", "/api/v2/observations"),
        ("logs.v2", "/api/v2/logs"),
        ("services.v1", "/api/services"),
        ("audit.v1", "/api/audit"),
        ("terminals.v1", "/api/terminals"),
    ],
)
async def test_proxy_dispatches_scoped_requests_for_committed_local_record(
    capability, path
):
    record = _local_record()

    class Registry:
        def get(self, _agent_id):
            return record

    class Availability:
        async def check_capability(self, _agent_id, _capability):
            return CapabilityCheck(True, "not_dispatched")

    class Credentials:
        def lifecycle_lease(self):
            return nullcontext()

        def read(self, reference):
            assert reference == record.credential_ref
            return b"managed-secret"

    class Client:
        calls = []

        async def request(self, target, credential, method, upstream_path, **_kwargs):
            self.calls.append((str(target.pinned_address), credential, method, upstream_path))
            return httpx.Response(200, json={"items": []})

    proxy = AgentHttpProxy(
        registry=Registry(),
        availability=Availability(),
        credential_store=Credentials(),
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["127.0.0.0/8"],
            self_targets=[("127.0.0.1", 8765)],
        ),
        transport_profiles=_local_profiles(),
        client=Client(),
        local_bootstrap_enabled=True,
    )

    response = await proxy.request_json(
        agent_id="local-agent",
        capability=capability,
        method="GET",
        upstream_path=path,
        query={},
        correlation_id=None,
    )

    assert response.status_code == 200
    assert Client.calls == [("127.0.0.1", b"managed-secret", "GET", path)]


@pytest.mark.security
@pytest.mark.parametrize(
    ("record", "gate"),
    [
        (_local_record(), False),
        (_local_record(source="manual"), True),
        (_local_record(enrollment_method=EnrollmentMethod.SSH_AUTO), True),
        (_local_record(transport_profile_id="alternate-loopback-http"), True),
    ],
    ids=["gate-disabled", "source-changed", "method-changed", "profile-changed"],
)
async def test_proxy_rejects_invalid_local_authority_before_credential_read(record, gate):
    reads = []
    dispatches = []

    class Registry:
        def get(self, _agent_id):
            return record

    class Availability:
        async def check_capability(self, *_args):
            raise AssertionError("invalid local authority must fail before availability")

    class Credentials:
        def lifecycle_lease(self):
            return nullcontext()

        def read(self, reference):
            reads.append(reference)
            raise AssertionError("invalid local authority must fail before credential read")

    class Client:
        async def request(self, *_args, **_kwargs):
            dispatches.append(True)
            raise AssertionError("invalid local authority must fail before dispatch")

    proxy = AgentHttpProxy(
        registry=Registry(),
        availability=Availability(),
        credential_store=Credentials(),
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["127.0.0.0/8"],
            self_targets=[("127.0.0.1", 8765)],
        ),
        transport_profiles=_local_profiles(),
        client=Client(),
        local_bootstrap_enabled=gate,
    )

    with pytest.raises(AgentProxyError) as caught:
        await proxy.get_json(
            agent_id="local-agent",
            capability="logs.v2",
            upstream_path="/api/v2/logs",
            query={},
            correlation_id=None,
        )

    assert caught.value.code == "target_address_forbidden"
    assert reads == []
    assert dispatches == []


@pytest.mark.security
async def test_proxy_isolates_credentials_and_rejects_late_revision_or_invalid_schema():
    records = {"a": _record("a", "a" * 48), "b": _record("b", "b" * 48)}

    class Registry:
        def get(self, agent_id):
            return records.get(agent_id)

    class Availability:
        async def check_capability(self, _agent_id, _capability):
            return CapabilityCheck(True, "not_dispatched")

    class Credentials:
        def lifecycle_lease(self):
            return nullcontext()

        def read(self, reference):
            return reference.encode()

    class Policy:
        def resolve(self, endpoint, profile):
            return type("Target", (), {"endpoint": endpoint, "profile": profile})()

    class Client:
        calls = []

        async def request(self, target, credential, method, path, **_kwargs):
            self.calls.append((target.endpoint, credential, method, path))
            if path == "/invalid":
                return httpx.Response(200, json=[])
            if path == "/late":
                records["a"] = replace(records["a"], revision=2)
            return httpx.Response(200, json={"items": []})

    proxy = AgentHttpProxy(
        registry=Registry(),
        availability=Availability(),
        credential_store=Credentials(),
        target_policy=Policy(),
        transport_profiles=(SYSTEM_TLS_PROFILE,),
        client=Client(),
    )
    await proxy.get_json(
        agent_id="a",
        capability="observations.v2",
        upstream_path="/ok",
        query={},
        correlation_id="corr-a",
    )
    await proxy.get_json(
        agent_id="b",
        capability="logs.v2",
        upstream_path="/ok",
        query={},
        correlation_id="corr-b",
    )
    assert Client.calls[0][0:2] != Client.calls[1][0:2]
    with pytest.raises(AgentProxyError, match="agent_protocol_error"):
        await proxy.get_json(
            agent_id="b",
            capability="logs.v2",
            upstream_path="/invalid",
            query={},
            correlation_id=None,
        )
    with pytest.raises(AgentProxyError, match="agent_target_changed"):
        await proxy.get_json(
            agent_id="a",
            capability="observations.v2",
            upstream_path="/late",
            query={},
            correlation_id=None,
        )


@pytest.mark.security
async def test_proxy_preserves_late_probe_dispatch_for_capability_and_client_failures():
    records = {"a": _record("a", "a" * 48)}

    class Registry:
        def get(self, _agent_id):
            return records["a"]

    class Credentials:
        def lifecycle_lease(self):
            return nullcontext()

        def read(self, reference):
            return reference.encode()

    class Policy:
        def resolve(self, endpoint, profile):
            return type("Target", (), {"endpoint": endpoint, "profile": profile})()

    class MissingAvailability:
        async def check_capability(self, _agent_id, _capability):
            return CapabilityCheck(False, "dispatched")

    class ReadyAvailability:
        async def check_capability(self, _agent_id, _capability):
            return CapabilityCheck(True, "dispatched")

    class ChangingAvailability:
        async def check_capability(self, _agent_id, _capability):
            records["a"] = replace(records["a"], revision=2)
            return CapabilityCheck(True, "dispatched")

    class FailingClient:
        async def request(self, *_args, **_kwargs):
            raise AgentClientError("agent_network_error", "unavailable")

    class ForbiddenClient:
        async def request(self, *_args, **_kwargs):
            raise AssertionError("changed target must fail before request dispatch")

    def proxy(availability, client=None):
        return AgentHttpProxy(
            registry=Registry(),
            availability=availability,
            credential_store=Credentials(),
            target_policy=Policy(),
            transport_profiles=(SYSTEM_TLS_PROFILE,),
            client=client or FailingClient(),
        )

    with pytest.raises(AgentProxyError) as missing:
        await proxy(MissingAvailability()).get_json(
            agent_id="a",
            capability="logs.v2",
            upstream_path="/api/v2/logs",
            query={},
            correlation_id=None,
        )
    assert missing.value.code == "agent_capability_missing"
    assert missing.value.dispatch_state == "dispatched"

    with pytest.raises(AgentProxyError) as failed:
        await proxy(ReadyAvailability()).get_json(
            agent_id="a",
            capability="logs.v2",
            upstream_path="/api/v2/logs",
            query={},
            correlation_id=None,
        )
    assert failed.value.code == "agent_network_error"
    assert failed.value.dispatch_state == "dispatched"

    with pytest.raises(AgentProxyError) as changed:
        await proxy(ChangingAvailability(), ForbiddenClient()).get_json(
            agent_id="a",
            capability="logs.v2",
            upstream_path="/api/v2/logs",
            query={},
            correlation_id=None,
        )
    assert changed.value.code == "agent_target_changed"
    assert changed.value.dispatch_state == "dispatched"


@pytest.mark.security
async def test_proxy_rejects_disabled_agent_before_credential_or_upstream_access():
    disabled = replace(_record("a", "a" * 48), enabled=False)

    class Registry:
        def get(self, _agent_id):
            return disabled

    class ForbiddenDependency:
        def __getattr__(self, _name):
            raise AssertionError("disabled Agent must fail before proxy dependencies")

    proxy = AgentHttpProxy(
        registry=Registry(),
        availability=ForbiddenDependency(),
        credential_store=ForbiddenDependency(),
        target_policy=ForbiddenDependency(),
        transport_profiles=(SYSTEM_TLS_PROFILE,),
        client=ForbiddenDependency(),
    )
    with pytest.raises(AgentProxyError) as error:
        await proxy.get_json(
            agent_id="a",
            capability="logs.v2",
            upstream_path="/api/v2/logs",
            query={},
            correlation_id=None,
        )
    assert error.value.code == "agent_disabled"
    assert error.value.dispatch_state == "not_dispatched"


@pytest.mark.security
async def test_proxy_enforces_agent_client_response_limit():
    record = _record("a", "a" * 48)

    class Registry:
        def get(self, _agent_id):
            return record

    class Availability:
        async def check_capability(self, _agent_id, _capability):
            return CapabilityCheck(True, "not_dispatched")

    class Credentials:
        def lifecycle_lease(self):
            return nullcontext()

        def read(self, _reference):
            return b"stored-secret"

    async def handler(_request):
        return httpx.Response(200, json={"value": "x" * (1024 * 1024)})

    client = AgentHttpClient(transport=httpx.MockTransport(handler))
    proxy = AgentHttpProxy(
        registry=Registry(),
        availability=Availability(),
        credential_store=Credentials(),
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.20.30.0/24"],
            self_targets=[("10.20.30.254", 8765)],
        ),
        transport_profiles=(SYSTEM_TLS_PROFILE,),
        client=client,
    )
    with pytest.raises(AgentProxyError) as error:
        await proxy.get_json(
            agent_id="a",
            capability="observations.v2",
            upstream_path="/api/v2/observations",
            query={},
            correlation_id=None,
        )
    await client.aclose()
    assert error.value.code == "agent_protocol_error"
    assert error.value.dispatch_state == "dispatched"

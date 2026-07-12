from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import (
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
    ServerConfig,
    TrustedLanHttpServerConfig,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile
from ic_env_guard.main import create_app


def _token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret-token\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.contract
def test_agent_runtime_is_unauthenticated_and_capabilities_are_safe(tmp_path):
    client = TestClient(create_app(token_file=_token_file(tmp_path)))

    response = client.get("/api/v2/runtime")

    assert response.status_code == 200
    assert response.json() == {"mode": "agent", "capabilities": ["runtime.v2"]}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Correlation-ID"]


@pytest.mark.contract
def test_agent_runtime_serializes_configured_capability_without_cidrs(tmp_path):
    token_file = _token_file(tmp_path)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        server=ServerConfig(
            bind="192.168.40.10",
            remote_bind_enabled=True,
            trusted_lan_http=TrustedLanHttpServerConfig(
                enabled=True, client_cidrs=["192.168.40.0/24"]
            ),
        ),
    )
    client = TestClient(
        create_app(
            config=config,
            state_database=tmp_path / "state.db",
            instance_id_path=tmp_path / "instance-id",
        ),
        client=("192.168.40.20", 50000),
    )

    response = client.get("/api/v2/runtime")

    assert response.json() == {
        "mode": "agent",
        "capabilities": ["runtime.v2", "trusted-lan-http.v1"],
    }
    assert "192.168.40.0/24" not in response.text


@pytest.mark.contract
def test_v2_capabilities_are_authenticated_and_include_stable_identity(tmp_path):
    client = TestClient(create_app(token_file=_token_file(tmp_path)))

    missing = client.get("/api/v2/capabilities")
    first = client.get(
        "/api/v2/capabilities", headers={"Authorization": "Bearer secret-token"}
    )
    second = client.get(
        "/api/v2/capabilities", headers={"Authorization": "Bearer secret-token"}
    )

    assert missing.status_code == 401
    assert first.status_code == 200
    body = first.json()
    assert UUID(body["instance_id"])
    assert body["instance_id"] == second.json()["instance_id"]
    assert body["name"]
    assert body["api_version"] == "2"
    assert body["agent_version"]
    assert "runtime.v2" in body["capabilities"]
    assert "services.v1" in body["capabilities"]
    assert first.headers["Cache-Control"] == "no-store"


@pytest.mark.contract
def test_manager_runtime_exposes_only_healthy_local_capabilities(tmp_path):
    token_file = _token_file(tmp_path)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
    )
    client = TestClient(create_app(config=config))

    response = client.get("/api/v2/runtime")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "manager",
        "capabilities": ["fleet.v2", "agent-registry.v2"],
    }
    assert response.headers["Cache-Control"] == "no-store"
    for forbidden in (
        str(tmp_path),
        "username",
        "allowed_agent_cidrs",
        "transport_profiles",
        "agents",
    ):
        assert forbidden not in response.text


@pytest.mark.contract
def test_manager_runtime_reports_configured_trusted_lan_adapter_without_policy_details(
    tmp_path,
):
    token_file = _token_file(tmp_path)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "control-plane.db",
            allowed_agent_cidrs=["10.0.0.0/8"],
            transport_profiles=(
                TrustedLanHttpProfile(id="lab-http", allowed_cidrs=["10.1.0.0/16"]),
            ),
        ),
    )

    response = TestClient(create_app(config=config)).get("/api/v2/runtime")

    assert response.json()["capabilities"] == [
        "fleet.v2",
        "agent-registry.v2",
        "trusted-lan-http.v1",
    ]
    assert "lab-http" not in response.text
    assert "10.1.0.0/16" not in response.text


@pytest.mark.contract
def test_manager_runtime_does_not_claim_v2_when_self_target_inventory_fails(
    tmp_path, monkeypatch
):
    import psutil

    monkeypatch.setattr(
        psutil, "net_if_addrs", lambda: (_ for _ in ()).throw(OSError("unavailable"))
    )
    token_file = _token_file(tmp_path)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=True),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
    )

    response = TestClient(create_app(config=config)).get("/api/v2/runtime")

    assert response.json() == {"mode": "manager", "capabilities": []}

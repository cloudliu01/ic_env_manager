import sqlite3

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app
from ic_env_guard.proxy.http import AgentProxyError, AgentProxyResponse


def _client(tmp_path):
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


@pytest.mark.contract
def test_agent_observation_proxy_is_explicit_and_authenticated(tmp_path):
    with _client(tmp_path) as client:
        response = client.get(
            "/api/v2/agents/missing/observations?include_stale=true",
            headers={"Authorization": "Bearer manager-secret"},
        )
        generic = client.post(
            "/api/v2/agents/missing/proxy",
            headers={"Authorization": "Bearer manager-secret"},
            json={"url": "http://127.0.0.1:22"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_not_found"
    assert generic.status_code == 404


@pytest.mark.contract
def test_agent_observation_proxy_forwards_only_fixed_path_and_validated_query(tmp_path):
    calls = []

    class Proxy:
        async def get_json(self, **kwargs):
            with sqlite3.connect(tmp_path / "manager.db") as connection:
                pending = connection.execute(
                    "SELECT operation,target,result FROM control_plane_audit_events "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            assert pending == ("observations.list", "observations", "pending")
            calls.append(kwargs)
            return AgentProxyResponse(200, {"items": [], "next_cursor": None})

    client = _client(tmp_path)
    client.app.dependency_overrides[get_agent_http_proxy] = lambda: Proxy()
    with client:
        response = client.get(
            "/api/v2/agents/lab-01/observations",
            headers={"Authorization": "Bearer manager-secret"},
            params={
                "namespace": "eda",
                "status": "warning",
                "include_stale": "true",
                "limit": "25",
            },
        )
        duplicate = client.get(
            "/api/v2/agents/lab-01/observations?limit=1&limit=2",
            headers={"Authorization": "Bearer manager-secret"},
        )

    assert response.status_code == 200
    assert calls[0]["upstream_path"] == "/api/v2/observations"
    assert calls[0]["capability"] == "observations.v2"
    assert calls[0]["query"] == {
        "namespace": "eda",
        "status": "warning",
        "include_stale": "true",
        "limit": 25,
    }
    assert duplicate.status_code == 422
    assert len(calls) == 1
    with sqlite3.connect(tmp_path / "manager.db") as connection:
        outcome = connection.execute(
            "SELECT result,dispatch_state,upstream_status FROM "
            "control_plane_audit_events WHERE operation='observations.list'"
        ).fetchone()
    assert outcome == ("success", "dispatched", 200)


@pytest.mark.contract
def test_agent_observation_proxy_commits_intent_before_late_probe_failure(tmp_path):
    class Proxy:
        async def get_json(self, **_kwargs):
            with sqlite3.connect(tmp_path / "manager.db") as connection:
                pending = connection.execute(
                    "SELECT result FROM control_plane_audit_events "
                    "WHERE operation='observations.list' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            assert pending == ("pending",)
            raise AgentProxyError(
                "agent_capability_missing", 409, dispatch_state="dispatched"
            )

    client = _client(tmp_path)
    client.app.dependency_overrides[get_agent_http_proxy] = lambda: Proxy()
    with client:
        response = client.get(
            "/api/v2/agents/lab-01/observations",
            headers={"Authorization": "Bearer manager-secret"},
        )

    assert response.status_code == 409
    with sqlite3.connect(tmp_path / "manager.db") as connection:
        outcome = connection.execute(
            "SELECT result,dispatch_state,failure_category FROM "
            "control_plane_audit_events WHERE operation='observations.list'"
        ).fetchone()
    assert outcome == ("failed", "dispatched", "agent_capability_missing")

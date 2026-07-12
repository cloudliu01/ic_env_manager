import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app
from ic_env_guard.proxy.http import AgentProxyResponse


def _client(tmp_path):
    token = tmp_path / "manager.token"
    token.write_text("manager-secret\n", encoding="utf-8")
    token.chmod(0o600)
    return TestClient(
        create_app(
            config=AppConfig(
                mode="control-plane",
                auth=AuthConfig(token_file=token),
                control_plane=ControlPlaneConfig(
                    audit_database=tmp_path / "manager.db"
                ),
            )
        )
    )


@pytest.mark.contract
def test_agent_log_proxy_has_explicit_list_detail_and_tail_routes(tmp_path):
    with _client(tmp_path) as client:
        headers = {"Authorization": "Bearer manager-secret"}
        responses = [
            client.get("/api/v2/agents/missing/logs", headers=headers),
            client.get("/api/v2/agents/missing/logs/app", headers=headers),
            client.get(
                "/api/v2/agents/missing/logs/app/tail?lines=100", headers=headers
            ),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert all(
        response.json()["error"]["code"] == "agent_not_found"
        for response in responses
    )


@pytest.mark.contract
def test_agent_log_proxy_forwards_only_fixed_routes_and_validated_tail_query(tmp_path):
    calls = []

    class Proxy:
        async def get_json(self, **kwargs):
            calls.append(kwargs)
            return AgentProxyResponse(200, {"content": "line\n"})

    client = _client(tmp_path)
    client.app.dependency_overrides[get_agent_http_proxy] = lambda: Proxy()
    with client:
        headers = {"Authorization": "Bearer manager-secret"}
        tail = client.get(
            "/api/v2/agents/lab-01/logs/app/tail?lines=25", headers=headers
        )
        invalid_id = client.get(
            "/api/v2/agents/lab-01/logs/%2e/tail?lines=25", headers=headers
        )
        duplicate = client.get(
            "/api/v2/agents/lab-01/logs/app/tail?lines=1&lines=2", headers=headers
        )

    assert tail.status_code == 200
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "lab-01"
    assert calls[0]["capability"] == "logs.v2"
    assert calls[0]["upstream_path"] == "/api/v2/logs/app/tail"
    assert calls[0]["query"] == {"lines": 25}
    assert calls[0]["tail"] is True
    assert invalid_id.status_code == duplicate.status_code == 422

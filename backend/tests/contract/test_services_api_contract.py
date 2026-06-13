import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.services import get_service_manager
from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.main import create_app
from ic_env_guard.services.manager import ServiceManager


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    manager = ServiceManager(
        [ServiceRuntime(id="demo", name="Demo", command="python -c 'import time; time.sleep(5)'")]
    )
    app = create_app(token_file=token_file)
    app.dependency_overrides[get_service_manager] = lambda: manager
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


@pytest.mark.contract
def test_service_list_detail_start_stop_restart_events_logs_contract(client, auth_headers):
    listed = client.get("/api/services", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["services"][0]["id"] == "demo"

    detail = client.get("/api/services/demo", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == "demo"

    started = client.post("/api/services/demo/start", headers=auth_headers)
    assert started.status_code == 202
    assert started.json()["result"] in {"success", "already_in_state"}

    restarted = client.post("/api/services/demo/restart", headers=auth_headers)
    assert restarted.status_code == 202
    assert restarted.json()["operation"] == "restart"

    stopped = client.post("/api/services/demo/stop", headers=auth_headers)
    assert stopped.status_code == 202
    assert stopped.json()["result"] in {"success", "already_in_state"}

    events = client.get("/api/services/demo/events", headers=auth_headers)
    assert events.status_code == 200
    assert "events" in events.json()

    logs = client.get("/api/services/demo/logs", headers=auth_headers)
    assert logs.status_code == 200
    assert logs.json()["service_id"] == "demo"


@pytest.mark.contract
def test_service_unknown_returns_404(client, auth_headers):
    response = client.get("/api/services/missing", headers=auth_headers)
    assert response.status_code == 404

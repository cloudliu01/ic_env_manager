import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import AgentContainer
from ic_env_guard.config.loader import ConfigLoadError, load_config
from ic_env_guard.main import create_app


@pytest.mark.integration
def test_agent_starts_with_valid_token_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)

    app = create_app(token_file=token_file)
    client = TestClient(app)

    assert client.get("/readyz").status_code == 200
    assert isinstance(app.state.container, AgentContainer)


@pytest.mark.integration
def test_agent_loads_configured_services_from_config_path(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
server:
  bind: 127.0.0.1
  port: 8765
auth:
  mode: bearer_token
  token_file: {token_file}
metrics:
  enabled: true
  collect_interval_seconds: 10
services:
  - id: demo
    name: Demo Service
    command: python -c 'import time; time.sleep(5)'
    cwd: {tmp_path}
    env:
      DEMO_FLAG: enabled
    allowed_operations: [start, stop, restart, status]
    restart: on-failure
    start_timeout_seconds: 7
    stop_timeout_seconds: 3
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path=config_path))
    headers = {"Authorization": "Bearer secret-token"}

    listed = client.get("/api/services", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["services"][0]["id"] == "demo"
    assert listed.json()["services"][0]["name"] == "Demo Service"

    detail = client.get("/api/services/demo", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["restart_policy"] == "on-failure"


@pytest.mark.integration
def test_agent_fails_closed_with_insecure_token_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o644)

    with pytest.raises(ValueError, match="group/other"):
        create_app(token_file=token_file)


@pytest.mark.integration
def test_invalid_service_config_reports_actionable_diagnostics(tmp_path):
    config_path = tmp_path / "config.yaml"
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    config_path.write_text(
        f"""
server:
  bind: 127.0.0.1
  port: 8765
auth:
  mode: bearer_token
  token_file: {token_file}
metrics:
  enabled: true
  collect_interval_seconds: 10
services:
  - id: broken
    name: Broken
    allowed_operations: []
    restart: never
    start_timeout_seconds: 1
    stop_timeout_seconds: 1
    logs:
      capture: true
      max_tail_lines: 100
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError, match="allowed_operations"):
        load_config(config_path)

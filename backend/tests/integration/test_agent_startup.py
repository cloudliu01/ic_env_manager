import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.loader import ConfigLoadError, load_config
from ic_env_guard.main import create_app


@pytest.mark.integration
def test_agent_starts_with_valid_token_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)

    client = TestClient(create_app(token_file=token_file))

    assert client.get("/readyz").status_code == 200


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

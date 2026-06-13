import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from ic_env_guard.main import create_app


@pytest.mark.contract
def test_metrics_endpoint_exposes_required_metric_families(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    client = TestClient(create_app(token_file=token_file))

    response = client.get("/metrics")

    assert response.status_code == 200
    families = {family.name for family in text_string_to_metric_families(response.text)}
    assert "ic_env_guard_build_info" in families
    assert "ic_env_guard_terminal_sessions" in families
    assert "ic_env_guard_host_cpu_percent" in families

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.metrics import MetricsAccessPolicy, get_metrics_access_policy
from ic_env_guard.main import create_app


@pytest.fixture
def app(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return create_app(token_file=token_file)


@pytest.mark.integration
@pytest.mark.security
def test_metrics_allows_localhost_by_default(app):
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.security
def test_metrics_rejects_remote_source_outside_allowlist(app):
    app.dependency_overrides[get_metrics_access_policy] = lambda: MetricsAccessPolicy(
        remote_network_allowlist=["10.0.0.0/8"]
    )
    client = TestClient(app)

    response = client.get("/metrics", headers={"X-Forwarded-For": "192.0.2.10"})

    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.security
def test_metrics_accepts_remote_source_inside_allowlist(app):
    app.dependency_overrides[get_metrics_access_policy] = lambda: MetricsAccessPolicy(
        remote_network_allowlist=["192.0.2.0/24", "2001:db8::/32"]
    )
    client = TestClient(app)

    response = client.get("/metrics", headers={"X-Forwarded-For": "192.0.2.10"})

    assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.security
def test_metrics_allowlist_can_be_loaded_from_config(tmp_path):
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
  remote_network_allowlist:
    - 192.0.2.0/24
services: []
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path=config_path))

    accepted = client.get("/metrics", headers={"X-Forwarded-For": "192.0.2.10"})
    rejected = client.get("/metrics", headers={"X-Forwarded-For": "198.51.100.10"})

    assert accepted.status_code == 200
    assert rejected.status_code == 403

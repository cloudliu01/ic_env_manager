import pytest
from pydantic import ValidationError

from ic_env_guard.api.risk import RouteRisk, classify_route
from ic_env_guard.config.models import AppConfig, AuthConfig, ServerConfig


@pytest.mark.unit
@pytest.mark.security
def test_route_risk_classifies_privileged_terminal_and_service_control():
    assert classify_route("/api/terminals", "POST") == RouteRisk.PRIVILEGED_TERMINAL
    assert classify_route("/ws/terminals/abc", "GET") == RouteRisk.PRIVILEGED_TERMINAL
    assert (
        classify_route("/api/services/demo/start", "POST") == RouteRisk.PRIVILEGED_SERVICE_CONTROL
    )
    assert classify_route("/metrics", "GET") == RouteRisk.METRICS


@pytest.mark.unit
@pytest.mark.security
def test_remote_bind_requires_explicit_enablement_and_auth(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")

    with pytest.raises(ValidationError, match="remote_bind_enabled"):
        AppConfig(
            server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=False),
            auth=AuthConfig(token_file=token_file),
        )

    config = AppConfig(
        server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=True),
        auth=AuthConfig(token_file=token_file),
    )
    assert config.server.remote_bind_enabled

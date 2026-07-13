import pytest
from pydantic import ValidationError

from ic_env_guard.api.risk import RouteRisk, classify_route
from ic_env_guard.config.models import (
    AgentConfig,
    AgentTlsConfig,
    AppConfig,
    AuthConfig,
    DevelopmentConfig,
    ServerConfig,
)


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


def _token_file(tmp_path):
    token_file = tmp_path / "agent-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


@pytest.mark.unit
@pytest.mark.security
def test_agent_ids_must_be_unique(tmp_path):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValidationError, match="unique"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url="https://a.example",
                    token_file=token_file,
                ),
                AgentConfig(
                    id="lab-01",
                    name="Lab 01 again",
                    base_url="https://b.example",
                    token_file=token_file,
                ),
            ],
        )


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    "agent_url",
    [
        "https://user@example.com",
        "https://example.com/agent",
        "https://example.com?x=1",
        "https://example.com#fragment",
    ],
)
def test_agent_base_url_rejects_unsupported_shapes(tmp_path, agent_url):
    with pytest.raises(ValidationError):
        AgentConfig(
            id="lab-01",
            name="Lab 01",
            base_url=agent_url,
            token_file=_token_file(tmp_path),
        )


@pytest.mark.unit
@pytest.mark.security
def test_non_loopback_agents_require_https_and_verified_tls(tmp_path):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValidationError, match="HTTPS"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url="http://example.com",
                    token_file=token_file,
                )
            ],
        )

    with pytest.raises(ValidationError, match="TLS"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url="https://example.com",
                    token_file=token_file,
                    tls=AgentTlsConfig(verify=False),
                )
            ],
        )


@pytest.mark.unit
@pytest.mark.security
def test_loopback_http_requires_development_exception_and_local_bind(tmp_path):
    token_file = _token_file(tmp_path)
    agent = AgentConfig(
        id="local-agent",
        name="Local Agent",
        base_url="http://127.0.0.1:8766",
        token_file=token_file,
    )

    with pytest.raises(ValidationError, match="insecure HTTP"):
        AppConfig(auth=AuthConfig(token_file=token_file), agents=[agent])

    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        development=DevelopmentConfig(allow_insecure_http=True),
        agents=[agent],
    )
    assert config.agents[0].base_url == "http://127.0.0.1:8766"

    with pytest.raises(ValidationError, match="local-only"):
        AppConfig(
            server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=True),
            auth=AuthConfig(token_file=token_file),
            development=DevelopmentConfig(allow_insecure_http=True),
            agents=[agent],
        )


@pytest.mark.unit
def test_local_bootstrap_requires_local_manager_and_insecure_dev_opt_in(tmp_path):
    token = _token_file(tmp_path)
    with pytest.raises(ValueError, match="local Agent bootstrap"):
        AppConfig(
            mode="control-plane",
            server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=True),
            auth=AuthConfig(token_file=token),
            development=DevelopmentConfig(
                allow_insecure_http=True, local_agent_bootstrap=True
            ),
        )


@pytest.mark.unit
@pytest.mark.security
def test_enabled_agents_require_owner_only_token_file(tmp_path):
    token_file = _token_file(tmp_path)
    token_file.chmod(0o640)

    with pytest.raises(ValidationError, match="permissions"):
        AgentConfig(
            id="lab-01", name="Lab 01", base_url="https://example.com", token_file=token_file
        )


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    "agent_url",
    [
        "https://169.254.169.254",
        "https://224.0.0.1",
        "https://0.0.0.0",
        "https://240.0.0.1",
    ],
)
def test_agent_base_url_rejects_forbidden_address_ranges(tmp_path, agent_url):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValidationError, match="forbidden address"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url=agent_url,
                    token_file=token_file,
                )
            ],
        )


@pytest.mark.unit
@pytest.mark.security
def test_agent_base_url_rejects_control_plane_self_target(tmp_path):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValidationError, match="control plane itself"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            server=ServerConfig(bind="127.0.0.1", port=8765),
            development=DevelopmentConfig(allow_insecure_http=True),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url="http://127.0.0.1:8765",
                    token_file=token_file,
                )
            ],
        )


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    ("server_bind", "agent_url"),
    [
        ("0.0.0.0", "https://127.0.0.1:8765"),
        ("0.0.0.0", "https://localhost:8765"),
        ("::", "https://[::1]:8765"),
    ],
)
def test_agent_base_url_rejects_wildcard_bind_self_target(tmp_path, server_bind, agent_url):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValidationError, match="control plane itself"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            server=ServerConfig(bind=server_bind, port=8765, remote_bind_enabled=True),
            agents=[
                AgentConfig(
                    id="lab-01",
                    name="Lab 01",
                    base_url=agent_url,
                    token_file=token_file,
                )
            ],
        )

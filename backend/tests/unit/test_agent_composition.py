from pathlib import Path

import pytest

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.bootstrap.composition import (
    AgentContainer,
    ManagerContainer,
    build_agent_container,
    build_manager_container,
    configured_agent_capabilities,
)
from ic_env_guard.config.models import (
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
    EnrollmentConfig,
    IngestConfig,
    LogsConfig,
    ObservationConfig,
    ServerConfig,
    TrustedLanHttpServerConfig,
)
from ic_env_guard.enrollment.manager_socket import ManagerEnrollmentSocket
from ic_env_guard.enrollment.ssh import SshEnrollmentAdapter
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager


def _token_file(tmp_path: Path) -> Path:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


@pytest.mark.unit
def test_ingest_listener_rejects_non_loopback_and_public_port_collision(tmp_path):
    token_file = _token_file(tmp_path)

    with pytest.raises(ValueError, match="ingest bind must be loopback"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            ingest=IngestConfig(bind="0.0.0.0", port=8766),
        )

    with pytest.raises(ValueError, match="public and ingest ports must differ"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            server=ServerConfig(port=8765),
            ingest=IngestConfig(port=8765),
        )


@pytest.mark.unit
def test_new_agent_configuration_models_enforce_documented_bounds():
    assert ObservationConfig().expired_retention_seconds == 86400
    assert LogsConfig().default_tail_lines == 100
    assert EnrollmentConfig().pending_ttl_seconds == 600
    assert EnrollmentConfig(pending_ttl_seconds=60).pending_ttl_seconds == 60
    assert EnrollmentConfig(pending_ttl_seconds=900).pending_ttl_seconds == 900
    assert "supplementary" in (
        EnrollmentConfig.model_fields["manager_socket_gid"].description or ""
    ).lower()

    with pytest.raises(ValueError):
        ObservationConfig(cleanup_interval_seconds=0)
    with pytest.raises(ValueError, match="absolute paths"):
        LogsConfig(allowed_roots=[Path("relative")])
    with pytest.raises(ValueError, match="default_tail_lines"):
        LogsConfig(default_tail_lines=101, max_tail_lines=100)
    with pytest.raises(ValueError):
        EnrollmentConfig(max_pending=129)
    with pytest.raises(ValueError):
        EnrollmentConfig(pending_ttl_seconds=59)
    with pytest.raises(ValueError):
        EnrollmentConfig(pending_ttl_seconds=901)

@pytest.mark.unit
def test_trusted_lan_capability_is_config_derived_without_disclosing_cidrs(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        server=ServerConfig(
            bind="10.20.30.10",
            remote_bind_enabled=True,
            trusted_lan_http=TrustedLanHttpServerConfig(
                enabled=True,
                client_cidrs=["10.20.30.0/24"],
            ),
        ),
    )

    capabilities = configured_agent_capabilities(config)

    assert "trusted-lan-http.v1" in capabilities
    assert "10.20.30.0/24" not in repr(capabilities)
    assert configured_agent_capabilities(
        AppConfig(auth=AuthConfig(token_file=_token_file(tmp_path)))
    ) == ()


@pytest.mark.unit
def test_build_agent_container_constructs_agent_dependencies(tmp_path):
    config = AppConfig(auth=AuthConfig(token_file=_token_file(tmp_path)))
    state_database = tmp_path / "state.db"

    container = build_agent_container(config, state_database)

    assert isinstance(container, AgentContainer)
    assert container.config is config
    assert isinstance(container.terminal_manager, TerminalManager)
    assert isinstance(container.service_manager, ServiceManager)
    assert container.session_factory.kw["bind"] is container.database_engine
    assert state_database.exists()
    container.database_engine.dispose()


@pytest.mark.unit
def test_build_manager_container_constructs_control_plane_dependencies(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
    )

    container = build_manager_container(config)

    assert isinstance(container, ManagerContainer)
    assert container.config is config


@pytest.mark.unit
def test_manager_submission_socket_is_opt_in_and_composed_without_starting(tmp_path):
    socket_dir = tmp_path / "run"
    socket_dir.mkdir(mode=0o700)
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        enrollment=EnrollmentConfig(manager_socket_path=socket_dir / "manager.sock"),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
    )

    container = build_manager_container(config)

    assert isinstance(container.manager_enrollment_socket, ManagerEnrollmentSocket)
    assert container.manager_enrollment_socket.path == socket_dir / "manager.sock"
    assert not container.manager_enrollment_socket.healthy
    container.database_engine.dispose()
    assert isinstance(container.agent_registry, AgentRegistry)
    assert isinstance(container.agent_availability, AgentAvailabilityService)
    assert isinstance(container.ssh_enrollment_adapter, SshEnrollmentAdapter)
    assert container.control_plane_session_factory.kw["bind"] is container.database_engine
    assert config.control_plane.audit_database.exists()
    container.database_engine.dispose()

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
import uvicorn

from ic_env_guard import main
from ic_env_guard.config.models import AppConfig


class _Socket:
    def __init__(self) -> None:
        self.close = Mock()


class _Config:
    def __init__(self, app, *, host, port, **kwargs):
        self.app = app
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.listener = _Socket()

    def bind_socket(self):
        return self.listener


class _Server:
    behaviors: dict[int, str] = {}
    instances: list["_Server"] = []

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.started = False
        self.__class__.instances.append(self)

    async def serve(self, sockets):
        behavior = self.behaviors.get(self.config.port, "wait")
        if behavior == "startup_failure":
            self.should_exit = True
            return
        self.started = True
        if behavior == "unexpected_return":
            return
        if behavior == "cancelled":
            raise asyncio.CancelledError
        if behavior == "graceful":
            self.should_exit = True
            return
        while not self.should_exit:
            await asyncio.sleep(0)


def _config(tmp_path, *, mode="agent") -> AppConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    token = tmp_path / f"{mode}.token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    return AppConfig.model_validate(
        {
            "mode": mode,
            "server": {"port": 19065},
            "ingest": {"port": 19066},
            "auth": {"token_file": token},
            "state_database": tmp_path / "state.db",
            "control_plane": {"audit_database": tmp_path / "manager.db"},
        }
    )


@pytest.fixture
def fake_uvicorn(monkeypatch):
    _Server.instances = []
    _Server.behaviors = {}
    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    return _Server


@pytest.mark.parametrize("failed_port", [19065, 19066])
@pytest.mark.asyncio
async def test_listener_startup_failure_stops_peer_and_raises(
    tmp_path, fake_uvicorn, failed_port
):
    fake_uvicorn.behaviors[failed_port] = "startup_failure"

    with pytest.raises(RuntimeError, match="failed to start"):
        await main.serve_config(_config(tmp_path))

    assert len(fake_uvicorn.instances) == 2
    assert all(server.should_exit for server in fake_uvicorn.instances)
    assert all(server.config.listener.close.call_count == 1 for server in fake_uvicorn.instances)


@pytest.mark.parametrize("behavior", ["unexpected_return", "cancelled"])
@pytest.mark.asyncio
async def test_unexpected_listener_completion_stops_peer_and_raises(
    tmp_path, fake_uvicorn, behavior
):
    fake_uvicorn.behaviors[19066] = behavior

    with pytest.raises(RuntimeError, match="listener task"):
        await main.serve_config(_config(tmp_path))

    assert all(server.should_exit for server in fake_uvicorn.instances)


@pytest.mark.asyncio
async def test_explicit_and_server_graceful_shutdown_are_normal(tmp_path, fake_uvicorn):
    shutdown = asyncio.Event()
    shutdown.set()
    await main.serve_config(_config(tmp_path), shutdown_event=shutdown)

    fake_uvicorn.behaviors = {19065: "graceful"}
    await main.serve_config(_config(tmp_path / "signal"))


@pytest.mark.parametrize("failure", ["public_app", "ingest_app", "config", "prebind"])
@pytest.mark.asyncio
async def test_setup_failure_closes_agent_container_once(
    tmp_path, monkeypatch, fake_uvicorn, failure
):
    built = []
    original_builder = main.build_agent_container

    def builder(*args, **kwargs):
        container = original_builder(*args, **kwargs)
        container.database_engine.dispose = Mock(wraps=container.database_engine.dispose)
        built.append(container)
        return container

    monkeypatch.setattr(main, "build_agent_container", builder)
    if failure == "public_app":
        monkeypatch.setattr(main, "create_public_app", Mock(side_effect=RuntimeError(failure)))
    elif failure == "ingest_app":
        monkeypatch.setattr(main, "create_ingest_app", Mock(side_effect=RuntimeError(failure)))
    elif failure == "config":
        monkeypatch.setattr(uvicorn, "Config", Mock(side_effect=RuntimeError(failure)))
    else:
        monkeypatch.setattr(_Config, "bind_socket", Mock(side_effect=RuntimeError(failure)))

    with pytest.raises(RuntimeError, match=failure):
        await main.serve_config(_config(tmp_path))

    built[0].database_engine.dispose.assert_called_once_with()


@pytest.mark.asyncio
async def test_manager_setup_failure_closes_engine_and_http_client_once(
    tmp_path, monkeypatch, fake_uvicorn
):
    built = []
    original_builder = main.build_manager_container

    def builder(*args, **kwargs):
        container = original_builder(*args, **kwargs)
        container.database_engine.dispose = Mock(wraps=container.database_engine.dispose)
        container.agent_client.aclose = AsyncMock(wraps=container.agent_client.aclose)
        built.append(container)
        return container

    monkeypatch.setattr(main, "build_manager_container", builder)
    monkeypatch.setattr(main, "create_public_app", Mock(side_effect=RuntimeError("public_app")))

    with pytest.raises(RuntimeError, match="public_app"):
        await main.serve_config(_config(tmp_path, mode="control-plane"))

    built[0].database_engine.dispose.assert_called_once_with()
    built[0].agent_client.aclose.assert_awaited_once_with()


@pytest.mark.parametrize("explicit_state", [False, True])
@pytest.mark.asyncio
async def test_runtime_instance_identity_path_preserves_config_location_rule(
    tmp_path, monkeypatch, explicit_state
):
    config = _config(tmp_path)
    config.state_database = tmp_path / "state" / "agent.db" if explicit_state else None
    config_path = tmp_path / "config" / "agent.yaml"
    captured = {}

    def builder(_config, state_database, instance_id_path):
        captured["state_database"] = state_database
        captured["instance_id_path"] = instance_id_path
        raise RuntimeError("captured")

    monkeypatch.setattr(main, "build_agent_container", builder)

    with pytest.raises(RuntimeError, match="captured"):
        await main.serve_config(config, config_path=config_path)

    expected = (
        config.state_database.with_name("instance-id")
        if explicit_state
        else config_path.with_name("instance-id")
    )
    assert captured["instance_id_path"] == expected

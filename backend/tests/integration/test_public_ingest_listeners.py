import asyncio
import socket
from contextlib import closing
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest
import uvicorn

from ic_env_guard import main
from ic_env_guard.config.models import AppConfig
from ic_env_guard.main import serve_config


def _free_port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _config(tmp_path, *, mode: str = "agent") -> AppConfig:
    token_file = tmp_path / f"{mode}.token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    public_port = _free_port()
    ingest_port = _free_port()
    return AppConfig.model_validate(
        {
            "mode": mode,
            "server": {"bind": "127.0.0.1", "port": public_port},
            "ingest": {"bind": "127.0.0.1", "port": ingest_port},
            "auth": {"token_file": token_file},
            "state_database": tmp_path / f"{mode}.db",
            "control_plane": {"audit_database": tmp_path / "manager.db"},
        }
    )


async def _wait_for_status(url: str, expected: int = 200) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(url)).status_code == expected:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.02)
    raise AssertionError(f"listener did not become ready: {url}")


def _observation_payload() -> dict[str, object]:
    return {
        "namespace": "eda",
        "name": "license_alive",
        "kind": "gauge",
        "value": 1,
        "status": "ok",
        "labels": {"server": "license01"},
        "details": {"pid": 1234},
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_real_listeners_are_isolated_and_close_together(tmp_path, monkeypatch):
    proxy_header_settings = []
    built_containers = []
    original_init = uvicorn.Config.__init__
    original_builder = main.build_agent_container

    def recording_init(self, *args, **kwargs):
        proxy_header_settings.append(kwargs.get("proxy_headers"))
        original_init(self, *args, **kwargs)

    def recording_builder(*args, **kwargs):
        container = original_builder(*args, **kwargs)
        container.database_engine.dispose = Mock(wraps=container.database_engine.dispose)
        built_containers.append(container)
        return container

    monkeypatch.setattr(uvicorn.Config, "__init__", recording_init)
    monkeypatch.setattr(main, "build_agent_container", recording_builder)
    config = _config(tmp_path)
    shutdown = asyncio.Event()
    task = asyncio.create_task(serve_config(config, shutdown_event=shutdown))
    public_url = f"http://127.0.0.1:{config.server.port}"
    ingest_url = f"http://127.0.0.1:{config.ingest.port}"
    try:
        await _wait_for_status(f"{public_url}/healthz")
        await _wait_for_status(f"{ingest_url}/api/v2/runtime", expected=404)
        async with (
            httpx.AsyncClient(base_url=public_url) as public,
            httpx.AsyncClient(base_url=ingest_url) as ingest,
        ):
            assert (await public.get("/api/v2/runtime")).status_code == 200
            assert (
                await public.put("/api/v2/observations", json=_observation_payload())
            ).status_code == 404
            assert (
                await ingest.put("/api/v2/observations", json=_observation_payload())
            ).status_code == 201
            assert (await ingest.get("/api/v2/runtime")).status_code == 404
        assert proxy_header_settings == [False, False]
    finally:
        shutdown.set()
        await asyncio.wait_for(task, timeout=5)

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.TransportError):
            await client.get(f"{public_url}/healthz")
        with pytest.raises(httpx.TransportError):
            await client.get(f"{ingest_url}/api/v2/runtime")
    built_containers[0].database_engine.dispose.assert_called_once_with()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_never_binds_ingest_listener(tmp_path):
    config = _config(tmp_path, mode="control-plane")
    shutdown = asyncio.Event()
    task = asyncio.create_task(serve_config(config, shutdown_event=shutdown))
    public_url = f"http://127.0.0.1:{config.server.port}"
    try:
        await _wait_for_status(f"{public_url}/healthz")
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.TransportError):
                await client.get(f"http://127.0.0.1:{config.ingest.port}/api/v2/runtime")
    finally:
        shutdown.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_listener_bind_failure_stops_public_listener(tmp_path):
    config = _config(tmp_path)
    blocker = socket.socket()
    blocker.bind((config.ingest.bind, config.ingest.port))
    blocker.listen()
    try:
        with pytest.raises(SystemExit):
            await serve_config(config)
    finally:
        blocker.close()

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.TransportError):
            await client.get(f"http://127.0.0.1:{config.server.port}/healthz")

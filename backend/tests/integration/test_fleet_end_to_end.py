import asyncio
import socket
from contextlib import closing
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

import httpx
import psutil
import pytest

from ic_env_guard.config.models import AppConfig
from ic_env_guard.main import serve_config


def _free_port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _private_host_address() -> str:
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            candidate = ip_address(address.address)
            if candidate.is_private and not candidate.is_loopback:
                return str(candidate)
    pytest.skip(
        "live Fleet E2E requires a non-loopback private host address; SSRF policy remains enabled"
    )


async def _wait_for_health(base_url: str) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(f"{base_url}/healthz")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.02)
    raise AssertionError(f"listener did not become ready: {base_url}")


def _token(path, value: str) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_manager_agent_enrollment_probe_proxy_restart_and_online_removal(tmp_path):
    address = _private_host_address()
    agent_port, ingest_port, manager_port = _free_port(), _free_port(), _free_port()
    agent_token, manager_token = tmp_path / "agent.token", tmp_path / "manager.token"
    _token(agent_token, "agent-e2e-secret")
    _token(manager_token, "manager-e2e-secret")
    socket_dir = Path(mkdtemp(prefix="ieg-e2e-", dir="/tmp"))
    socket_dir.chmod(0o700)
    cidr = f"{address}/32"
    agent = AppConfig.model_validate(
        {
            "mode": "agent",
            "server": {"bind": "0.0.0.0", "port": agent_port, "remote_bind_enabled": True},
            "ingest": {"bind": "127.0.0.1", "port": ingest_port},
            "auth": {"token_file": agent_token},
            "state_database": tmp_path / "agent.db",
            "enrollment": {"socket_path": socket_dir / "agent.sock", "socket_mode": "0600"},
        }
    )
    manager = AppConfig.model_validate(
        {
            "mode": "control-plane",
            "server": {"bind": "127.0.0.1", "port": manager_port},
            "auth": {"token_file": manager_token},
            "enrollment": {
                "manager_socket_path": socket_dir / "manager.sock",
                "manager_socket_mode": "0600",
            },
            "control_plane": {
                "audit_database": tmp_path / "manager.db",
                "credential_directory": tmp_path / "manager-credentials",
                "allowed_agent_cidrs": [cidr],
                "transport_profiles": [
                    {"id": "e2e-http", "type": "trusted_lan_http", "allowed_cidrs": [cidr]}
                ],
            },
        }
    )
    agent_shutdown, manager_shutdown = asyncio.Event(), asyncio.Event()
    agent_task = asyncio.create_task(serve_config(agent, shutdown_event=agent_shutdown))
    manager_task = None
    agent_url, manager_url = f"http://{address}:{agent_port}", f"http://127.0.0.1:{manager_port}"
    manager_headers = {"Authorization": "Bearer manager-e2e-secret"}
    try:
        await _wait_for_health(agent_url)
        async with httpx.AsyncClient() as client:
            ingested = await client.put(
                f"http://127.0.0.1:{ingest_port}/api/v2/observations",
                json={
                    "namespace": "eda",
                    "name": "license_alive",
                    "kind": "gauge",
                    "value": 1,
                    "status": "ok",
                    "labels": {"server": "e2e"},
                    "details": {},
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "ttl_seconds": 120,
                },
            )
            assert ingested.status_code == 201
        manager_task = asyncio.create_task(serve_config(manager, shutdown_event=manager_shutdown))
        await _wait_for_health(manager_url)
        async with httpx.AsyncClient(base_url=manager_url, headers=manager_headers) as client:
            validated = await client.post(
                "/api/v2/agents/validate",
                json={
                    "base_url": agent_url,
                    "transport_profile_id": "e2e-http",
                    "token": "agent-e2e-secret",
                },
            )
            assert validated.status_code == 200
            enrollment_id = validated.json()["enrollment_id"]
        manager_shutdown.set()
        await asyncio.wait_for(manager_task, timeout=5)
        manager_shutdown = asyncio.Event()
        manager_task = asyncio.create_task(serve_config(manager, shutdown_event=manager_shutdown))
        await _wait_for_health(manager_url)
        async with httpx.AsyncClient(base_url=manager_url, headers=manager_headers) as client:
            added = await client.post(
                "/api/v2/agents", json={"enrollment_id": enrollment_id, "display_name": "E2E Agent"}
            )
            assert added.status_code == 201, added.text
            agent_id = added.json()["agent"]["agent_id"]
            probed = await client.post(f"/api/v2/agents/{agent_id}/probe")
            assert probed.status_code == 200
            observations = await client.get(f"/api/v2/agents/{agent_id}/observations")
            assert observations.status_code == 200
            assert observations.json()["items"][0]["name"] == "license_alive"
            removed = await client.delete(f"/api/v2/agents/{agent_id}")
            assert removed.status_code == 204
    finally:
        if manager_task is not None:
            manager_shutdown.set()
            await asyncio.wait_for(manager_task, timeout=5)
        agent_shutdown.set()
        await asyncio.wait_for(agent_task, timeout=5)
        rmtree(socket_dir, ignore_errors=True)

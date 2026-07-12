import asyncio
import base64
import io
import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

import httpx
import psutil
import pytest
import websockets

from ic_env_guard.config.models import AppConfig
from ic_env_guard.enrollment.cli import CliSshRunner, run_cli_enrollment
from ic_env_guard.enrollment.helper import run_helper
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


class _LocalAgentHelperRunner(CliSshRunner):
    """Exercise the real Agent helper socket in place of an unavailable test sshd."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.responses: list[dict[str, object]] = []

    async def run(self, _argv, request: bytes, **_route) -> bytes:
        stdout = io.BytesIO()
        stderr = io.StringIO()
        assert run_helper(self.socket_path, io.BytesIO(request), stdout, stderr) == 0, (
            stderr.getvalue()
        )
        payload = stdout.getvalue()
        self.responses.append(json.loads(payload))
        return payload


async def _run_real_cli_enrollment(
    manager_socket: Path,
    agent_socket: Path,
    enrollment_id: str,
    address: str,
) -> _LocalAgentHelperRunner:
    runner = _LocalAgentHelperRunner(agent_socket)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = await asyncio.to_thread(
        run_cli_enrollment,
        manager_socket=manager_socket,
        enrollment_id=enrollment_id,
        ssh=f"edaops@{address}",
        stdout=stdout,
        stderr=stderr,
        runner=runner,
    )
    assert result == 0, f"{stderr.getvalue()} helper_responses={runner.responses!r}"
    assert stdout.getvalue() == "Enrollment verified.\n"
    assert stderr.getvalue() == ""
    assert len(runner.responses) == 1
    return runner


async def _wait_for_enrollment_state(
    client: httpx.AsyncClient, enrollment_id: str, expected: str
) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/api/v2/agent-enrollments/{enrollment_id}")
        assert response.status_code == 200
        enrollment = response.json()
        if enrollment["state"] == expected:
            return enrollment
        await asyncio.sleep(0.05)
    raise AssertionError(f"enrollment {enrollment_id} did not reach {expected}: {enrollment}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_manager_agent_enrollment_probe_proxy_restart_and_online_removal(tmp_path):
    address = _private_host_address()
    agent_port, second_agent_port, ingest_port, second_ingest_port, manager_port = (
        _free_port(),
        _free_port(),
        _free_port(),
        _free_port(),
        _free_port(),
    )
    agent_token = tmp_path / "agent.token"
    second_agent_token = tmp_path / "second-agent.token"
    manager_token = tmp_path / "manager.token"
    _token(agent_token, "agent-e2e-secret")
    _token(second_agent_token, "second-agent-e2e-secret")
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
    second_agent = AppConfig.model_validate(
        {
            "mode": "agent",
            "server": {
                "bind": "0.0.0.0",
                "port": second_agent_port,
                "remote_bind_enabled": True,
            },
            "ingest": {"bind": "127.0.0.1", "port": second_ingest_port},
            "auth": {"token_file": second_agent_token},
            "state_database": tmp_path / "second-agent.db",
            "enrollment": {
                "socket_path": socket_dir / "second-agent.sock",
                "socket_mode": "0600",
            },
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
                "ssh_connect_timeout_seconds": 1,
                "ssh_total_timeout_seconds": 2,
            },
            "control_plane": {
                "audit_database": tmp_path / "manager.db",
                "credential_directory": tmp_path / "manager-credentials",
                "allowed_agent_cidrs": [cidr],
                "transport_profiles": [
                    {"id": "e2e-http", "type": "trusted_lan_http", "allowed_cidrs": [cidr]}
                ],
                "discovery": {
                    "scopes": [
                        {
                            "id": "e2e-agent",
                            "name": "E2E Agent",
                            "cidr": cidr,
                            "endpoints": [{"port": agent_port, "transport_profile_id": "e2e-http"}],
                        }
                    ]
                },
            },
        }
    )
    agent_shutdown, second_agent_shutdown, manager_shutdown = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    agent_task = asyncio.create_task(serve_config(agent, shutdown_event=agent_shutdown))
    second_agent_task = asyncio.create_task(
        serve_config(second_agent, shutdown_event=second_agent_shutdown)
    )
    manager_task = None
    agent_url = f"http://{address}:{agent_port}"
    second_agent_url = f"http://{address}:{second_agent_port}"
    manager_url = f"http://127.0.0.1:{manager_port}"
    manager_headers = {"Authorization": "Bearer manager-e2e-secret"}
    try:
        await _wait_for_health(agent_url)
        await _wait_for_health(second_agent_url)
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
            discovery = await client.post("/api/v2/discovery/jobs", json={"scope_id": "e2e-agent"})
            assert discovery.status_code == 201
            discovery_id = discovery.json()["job"]["job_id"]
            for _ in range(50):
                job = await client.get(f"/api/v2/discovery/jobs/{discovery_id}")
                assert job.status_code == 200
                if job.json()["job"]["state"] == "completed":
                    break
                await asyncio.sleep(0.05)
            results = await client.get(f"/api/v2/discovery/jobs/{discovery_id}/results")
            assert results.status_code == 200
            discovery_result = results.json()["results"][0]
            cli_fallback = await client.post(
                "/api/v2/agent-enrollments",
                json={
                    "base_url": agent_url,
                    "transport_profile_id": "e2e-http",
                    "ssh": {"user": "edaops", "host": address, "port": 22},
                    "discovery_result_id": discovery_result["result_id"],
                },
            )
            assert cli_fallback.status_code == 201
            enrollment_id = cli_fallback.json()["enrollment_id"]
            await _wait_for_enrollment_state(client, enrollment_id, "awaiting_cli")
            initial_cli = await _run_real_cli_enrollment(
                manager.enrollment.manager_socket_path,
                agent.enrollment.socket_path,
                enrollment_id,
                address,
            )
            await _wait_for_enrollment_state(client, enrollment_id, "verified")
            initial_credential = initial_cli.responses[0]
            assert initial_credential["token"] not in cli_fallback.text

            second_validated = await client.post(
                "/api/v2/agents/validate",
                json={
                    "base_url": second_agent_url,
                    "transport_profile_id": "e2e-http",
                    "token": "second-agent-e2e-secret",
                },
            )
            assert second_validated.status_code == 200
            second_enrollment_id = second_validated.json()["enrollment_id"]
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
            second_added = await client.post(
                "/api/v2/agents",
                json={"enrollment_id": second_enrollment_id, "display_name": "Failing Agent"},
            )
            assert second_added.status_code == 201
            second_agent_id = second_added.json()["agent"]["agent_id"]
            online_removed = await client.delete(f"/api/v2/agents/{second_agent_id}")
            assert online_removed.status_code == 204, online_removed.text
            second_revalidated = await client.post(
                "/api/v2/agents/validate",
                json={
                    "base_url": second_agent_url,
                    "transport_profile_id": "e2e-http",
                    "token": "second-agent-e2e-secret",
                },
            )
            assert second_revalidated.status_code == 200
            second_readded = await client.post(
                "/api/v2/agents",
                json={
                    "enrollment_id": second_revalidated.json()["enrollment_id"],
                    "display_name": "Failing Agent",
                },
            )
            assert second_readded.status_code == 201
            second_agent_id = second_readded.json()["agent"]["agent_id"]

            rotation = await client.post(
                f"/api/v2/agents/{agent_id}/credential-rotation",
                json={
                    "action": "start",
                    "ssh": {"user": "edaops", "host": address, "port": 22},
                },
            )
            assert rotation.status_code == 201
            rotation_id = rotation.json()["rotation"]["enrollment_id"]
            await _wait_for_enrollment_state(client, rotation_id, "awaiting_cli")
            rotation_cli = await _run_real_cli_enrollment(
                manager.enrollment.manager_socket_path,
                agent.enrollment.socket_path,
                rotation_id,
                address,
            )
            await _wait_for_enrollment_state(client, rotation_id, "verified")
            rotated_credential = rotation_cli.responses[0]
            manager_shutdown.set()
            await asyncio.wait_for(manager_task, timeout=5)
            manager_shutdown = asyncio.Event()
            manager_task = asyncio.create_task(
                serve_config(manager, shutdown_event=manager_shutdown)
            )
            await _wait_for_health(manager_url)
            recovered_rotation = await client.get(
                f"/api/v2/agent-enrollments/{rotation_id}"
            )
            assert recovered_rotation.status_code == 200
            assert recovered_rotation.json()["state"] == "verified"
            assert rotated_credential["token"] not in recovered_rotation.text
            applied = await client.post(
                f"/api/v2/agents/{agent_id}/credential-rotation",
                json={"action": "consume", "enrollment_id": rotation_id},
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["rotation"]["state"] == "consumed"
            secret_values = {
                str(initial_credential["token"]),
                str(rotated_credential["token"]),
            }
            assert all(secret not in applied.text for secret in secret_values)
            manager_shutdown.set()
            await asyncio.wait_for(manager_task, timeout=5)
            manager_shutdown = asyncio.Event()
            manager_task = asyncio.create_task(
                serve_config(manager, shutdown_event=manager_shutdown)
            )
            await _wait_for_health(manager_url)
            async with httpx.AsyncClient(base_url=agent_url) as agent_client:
                old_auth = {
                    "Authorization": f"Bearer {initial_credential['token']}"
                }
                new_auth = {
                    "Authorization": f"Bearer {rotated_credential['token']}"
                }
                assert (
                    await agent_client.get("/api/v2/capabilities", headers=old_auth)
                ).status_code == 401
                assert (
                    await agent_client.get("/api/v2/capabilities", headers=new_auth)
                ).status_code == 200
                credentials = await agent_client.get(
                    "/api/v2/manager-credentials",
                    headers={"Authorization": "Bearer agent-e2e-secret"},
                )
                states = {
                    item["credential_id"]: item["state"]
                    for item in credentials.json()["credentials"]
                }
                assert states[initial_credential["credential_id"]] == "revoked"
                assert states[rotated_credential["credential_id"]] == "active"

            terminal = await client.post(
                f"/api/agents/{agent_id}/terminals",
                json={"title": "Task16 E2E", "rows": 24, "cols": 80},
            )
            assert terminal.status_code == 201, terminal.text
            terminal_id = terminal.json()["id"]
            async with httpx.AsyncClient(base_url=agent_url, headers=new_auth) as agent_client:
                direct_token = await agent_client.post(
                    f"/api/terminals/{terminal_id}/connect-token"
                )
            assert direct_token.status_code == 201
            direct_url = (
                f"ws://{address}:{agent_port}/ws/terminals/{terminal_id}"
                f"?ticket={direct_token.json()['ticket']}&cursor=0"
            )
            async with websockets.connect(
                direct_url,
                additional_headers=new_auth,
                proxy=None,
            ) as direct_websocket:
                await direct_websocket.send("printf 'direct-agent-ws-ok\\n'\n")
                direct_output = ""
                for _ in range(30):
                    direct_output += await asyncio.wait_for(direct_websocket.recv(), timeout=2)
                    if "direct-agent-ws-ok" in direct_output:
                        break
            assert "direct-agent-ws-ok" in direct_output
            connect_token = await client.post(
                f"/api/agents/{agent_id}/terminals/{terminal_id}/connect-token"
            )
            assert connect_token.status_code == 201, connect_token.text
            gateway_ticket = connect_token.json()["ticket"]
            assert all(secret not in connect_token.text for secret in secret_values)
            protocol = "bearer." + base64.urlsafe_b64encode(
                b"manager-e2e-secret"
            ).decode().rstrip("=")
            ws_url = (
                f"ws://127.0.0.1:{manager_port}/ws/agents/{agent_id}/terminals/"
                f"{terminal_id}?ticket={gateway_ticket}&cursor=0"
            )
            output = ""
            try:
                async with websockets.connect(ws_url, subprotocols=[protocol]) as websocket:
                    await websocket.send("printf 'task16-terminal-ok\\n'\n")
                    for _ in range(30):
                        output += await asyncio.wait_for(websocket.recv(), timeout=2)
                        if "task16-terminal-ok" in output:
                            break
            except Exception as exc:
                audit = await client.get(
                    "/api/control-plane/audit",
                    params={"agent_id": agent_id, "operation": "terminals.attach"},
                )
                raise AssertionError(audit.text) from exc
            assert "task16-terminal-ok" in output
            assert all(secret not in output for secret in secret_values)

            second_agent_shutdown.set()
            await asyncio.wait_for(second_agent_task, timeout=5)
            good_probe, bad_probe = await asyncio.wait_for(
                asyncio.gather(
                    client.post(f"/api/v2/agents/{agent_id}/probe"),
                    client.post(f"/api/v2/agents/{second_agent_id}/probe"),
                ),
                timeout=8,
            )
            assert good_probe.status_code == 200
            assert good_probe.json()["agent"]["connection_status"] in {"ready", "degraded"}
            assert bad_probe.status_code == 200
            assert bad_probe.json()["agent"]["connection_status"] == "unavailable"
            overview = await client.get("/api/v2/fleet/overview")
            fleet_status = {
                item["agent_id"]: item["connection_status"]
                for item in overview.json()["agents"]
            }
            assert fleet_status[agent_id] in {"ready", "degraded"}
            assert fleet_status[second_agent_id] == "unavailable"
            still_routable = await client.get(f"/api/v2/agents/{agent_id}/observations")
            assert still_routable.status_code == 200
        async with httpx.AsyncClient(base_url=manager_url, headers=manager_headers) as client:
            local_only = await client.request(
                "DELETE",
                f"/api/v2/agents/{second_agent_id}?local_only=true",
                json={"confirm_remote_residual": True},
            )
            assert local_only.status_code == 204
            assert (
                await client.get(f"/api/v2/agents/{second_agent_id}")
            ).status_code == 404
            rotated_removed = await client.delete(f"/api/v2/agents/{agent_id}")
            assert rotated_removed.status_code == 204, rotated_removed.text
            assert (await client.get(f"/api/v2/agents/{agent_id}")).status_code == 404
        with sqlite3.connect(tmp_path / "manager.db") as connection:
            assert connection.execute(
                "SELECT replace_agent_id, replace_agent_tombstone "
                "FROM agent_enrollment_jobs WHERE enrollment_id=?",
                (rotation_id,),
            ).fetchone() == (None, agent_id)
    finally:
        if manager_task is not None:
            manager_shutdown.set()
            await asyncio.wait_for(manager_task, timeout=5)
        if not agent_task.done():
            agent_shutdown.set()
            await asyncio.wait_for(agent_task, timeout=5)
        if not second_agent_task.done():
            second_agent_shutdown.set()
            await asyncio.wait_for(second_agent_task, timeout=5)
        rmtree(socket_dir, ignore_errors=True)

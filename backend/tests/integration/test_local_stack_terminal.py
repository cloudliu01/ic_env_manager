import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
import websockets
from websockets.exceptions import WebSocketException

from ic_env_guard.development import readiness

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANAGER_TOKEN = "local-stack-manager-token"
AGENT_TOKEN = "local-stack-agent-token"


def _reserve_ports() -> tuple[dict[str, int], list[socket.socket]]:
    reservations: list[socket.socket] = []
    ports: dict[str, int] = {}
    try:
        for name in ("manager", "agent", "ingest", "frontend"):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            reservations.append(listener)
            ports[name] = listener.getsockname()[1]
    except Exception:
        for listener in reservations:
            listener.close()
        raise
    return ports, reservations


def _launcher_environment(
    dev_dir: Path,
) -> tuple[dict[str, str], dict[str, int], list[socket.socket]]:
    executable_dir = dev_dir / "bin"
    executable_dir.mkdir()
    python = executable_dir / "python"
    python.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n", encoding="utf-8")
    python.chmod(0o700)
    npm = executable_dir / "npm"
    npm.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
    npm.chmod(0o700)

    ports, reservations = _reserve_ports()
    environment = os.environ | {
        "CONDA_DEFAULT_ENV": "venv312",
        "SKIP_INSTALL": "1",
        "IC_ENV_GUARD_DEV_DIR": str(dev_dir),
        "IC_ENV_GUARD_PORT": str(ports["manager"]),
        "IC_ENV_GUARD_AGENT_PORT": str(ports["agent"]),
        "IC_ENV_GUARD_AGENT_INGEST_PORT": str(ports["ingest"]),
        "IC_ENV_GUARD_FRONTEND_PORT": str(ports["frontend"]),
        "PATH": f"{executable_dir}:{os.environ['PATH']}",
    }
    return environment, ports, reservations


def test_launcher_environment_reserves_four_distinct_ports(tmp_path):
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir(mode=0o700)

    _environment, ports, reservations = _launcher_environment(dev_dir)
    try:
        assert len(set(ports.values())) == 4
        assert len(reservations) == 4
        assert all(listener.fileno() >= 0 for listener in reservations)
    finally:
        for listener in reservations:
            listener.close()


def test_full_stack_cleanup_removes_dev_dir_when_stop_fails(tmp_path, monkeypatch):
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    monkeypatch.setattr(
        sys.modules[__name__],
        "_stop_all",
        lambda _process: (_ for _ in ()).throw(RuntimeError("stop failed")),
    )

    with pytest.raises(RuntimeError, match="stop failed"):
        _cleanup_full_stack(object(), dev_dir, "")

    assert not dev_dir.exists()


def _start_all(
    environment: dict[str, str], reservations: list[socket.socket]
) -> subprocess.Popen:
    for listener in reservations:
        listener.close()
    return subprocess.Popen(
        [str(PROJECT_ROOT / "start.sh"), "all"],
        cwd=PROJECT_ROOT,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _wait_for_readiness(process: subprocess.Popen, timeout: float = 30) -> str:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output += process.stdout.read()
            pytest.fail(f"launcher exited before Terminal readiness:\n{output}")
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if not ready:
            continue
        line = process.stdout.readline()
        output += line
        if "Local Terminal proxy ready." in line:
            return output
        if "Starting frontend" in line:
            pytest.fail(f"frontend started before Terminal readiness:\n{output}")
    pytest.fail(f"timed out waiting for Terminal readiness:\n{output}")


def _stop_all(process: subprocess.Popen) -> str:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        return process.communicate(timeout=25)[0]
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        return process.communicate(timeout=5)[0]


def _cleanup_full_stack(
    process: subprocess.Popen | None, dev_dir: Path, startup_output: str
) -> None:
    try:
        if process is None:
            return
        output = startup_output + _stop_all(process)
        assert MANAGER_TOKEN not in output
        assert AGENT_TOKEN not in output
        assert process.poll() is not None
        assert not (dev_dir / "agent.pid").exists()
        assert not (dev_dir / "control-plane.pid").exists()
        assert not (dev_dir / "agent-enrollment.sock").exists()
        assert not (dev_dir / "manager-enrollment.sock").exists()
    finally:
        rmtree(dev_dir)
        assert not dev_dir.exists()


def _request(
    manager_url: str,
    token: str,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object] | None]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{manager_url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            response_body = response.read()
            return response.status, json.loads(response_body) if response_body else None
    except HTTPError as error:
        response_body = error.read()
        return error.code, json.loads(response_body) if response_body else None


def _json(
    manager_url: str,
    token: str,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    expected_status: int = 200,
) -> dict[str, object]:
    status, response = _request(manager_url, token, method, path, payload=payload)
    assert status == expected_status, response
    assert response is not None
    return response


async def _terminal_round_trip(
    manager_url: str, token: str, terminal_id: str, ticket: str
) -> None:
    deadline = time.monotonic() + 5
    websocket_url = manager_url.replace("http://", "ws://", 1)
    query = urlencode({"ticket": ticket, "cursor": "0"})
    url = f"{websocket_url}/ws/agents/local-agent/terminals/{terminal_id}?{query}"
    async with websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=max(0.01, deadline - time.monotonic()),
        close_timeout=2,
    ) as websocket:
        await asyncio.wait_for(
            websocket.send("printf '__LOCAL_V2_TERMINAL_OK__\\n'\r"),
            timeout=max(0.01, deadline - time.monotonic()),
        )
        received = ""
        while "__LOCAL_V2_TERMINAL_OK__" not in received:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Terminal sentinel timed out")
            received += await asyncio.wait_for(websocket.recv(), timeout=remaining)


async def _reuse_ticket(
    manager_url: str, token: str, terminal_id: str, ticket: str
) -> None:
    deadline = time.monotonic() + 5
    websocket_url = manager_url.replace("http://", "ws://", 1)
    query = urlencode({"ticket": ticket, "cursor": "0"})
    url = f"{websocket_url}/ws/agents/local-agent/terminals/{terminal_id}?{query}"
    async with websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=max(0.01, deadline - time.monotonic()),
        close_timeout=2,
    ) as websocket:
        await asyncio.wait_for(
            websocket.recv(), timeout=max(0.01, deadline - time.monotonic())
        )


def test_readiness_http_client_disables_environment_proxy(monkeypatch):
    captured: dict[str, object] = {}

    def client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(readiness.httpx, "AsyncClient", client)
    token = "manager-header-secret"

    readiness._manager_client("http://127.0.0.1:8765", token)

    assert captured["trust_env"] is False
    assert captured["headers"] == {"Authorization": f"Bearer {token}"}
    assert token not in captured["base_url"]


def test_readiness_websocket_disables_proxy_redirects_and_keeps_token_in_header(
    monkeypatch,
):
    captured: dict[str, object] = {}

    class FakeWebSocket:
        async def send(self, message):
            captured["message"] = message

        async def recv(self):
            return readiness.SENTINEL

        async def close(self):
            captured["closed"] = True

    class FakeConnection:
        process_redirect = "redirects-enabled"

        def __await__(self):
            async def connect():
                return FakeWebSocket()

            return connect().__await__()

    connection = FakeConnection()

    def connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return connection

    monkeypatch.setattr(readiness.websockets, "connect", connect)
    token = "manager-header-secret"

    asyncio.run(
        readiness._verify_websocket(
            "ws://127.0.0.1:8765",
            token,
            "local-agent",
            "terminal-1",
            "gateway-ticket",
            time.monotonic() + 1,
        )
    )

    assert captured["kwargs"]["proxy"] is None
    assert captured["kwargs"]["additional_headers"] == {
        "Authorization": f"Bearer {token}"
    }
    assert token not in captured["url"]
    assert "gateway-ticket" in captured["url"]
    assert connection.process_redirect(Exception("redirect")) is not None
    assert captured["closed"] is True


@pytest.mark.integration
def test_start_all_verifies_and_exposes_manager_terminal_proxy():
    dev_dir = Path(mkdtemp(prefix="ieg-terminal-stack-", dir="/tmp")).resolve()
    dev_dir.chmod(0o700)
    for name, token in (
        ("control-plane.token", MANAGER_TOKEN),
        ("agent.token", AGENT_TOKEN),
    ):
        token_file = dev_dir / name
        token_file.write_text(f"{token}\n", encoding="utf-8")
        token_file.chmod(0o600)

    environment, ports, reservations = _launcher_environment(dev_dir)
    process = None
    startup_output = ""
    manager_url = f"http://127.0.0.1:{ports['manager']}"
    try:
        process = _start_all(environment, reservations)
        startup_output = _wait_for_readiness(process)
        assert "Starting frontend" not in startup_output

        agents = _json(manager_url, MANAGER_TOKEN, "GET", "/api/v2/agents")
        local = next(
            item for item in agents["agents"] if item["agent_id"] == "local-agent"
        )
        assert local["transport_profile_id"] == "local-loopback-http"

        terminal_path = "/api/agents/local-agent/terminals"
        assert _json(manager_url, MANAGER_TOKEN, "GET", terminal_path) == {"terminals": []}
        terminal = _json(
            manager_url,
            MANAGER_TOKEN,
            "POST",
            terminal_path,
            payload={"title": "Full-stack verification", "rows": 24, "cols": 80},
            expected_status=201,
        )
        terminal_id = terminal["id"]
        status, response = _request(
            manager_url,
            MANAGER_TOKEN,
            "POST",
            f"{terminal_path}/{terminal_id}/resize",
            payload={"rows": 30, "cols": 100},
        )
        assert (status, response) == (204, None)
        connect_token = _json(
            manager_url,
            MANAGER_TOKEN,
            "POST",
            f"{terminal_path}/{terminal_id}/connect-token",
            expected_status=201,
        )
        ticket = connect_token["ticket"]
        asyncio.run(_terminal_round_trip(manager_url, MANAGER_TOKEN, terminal_id, ticket))

        closed = _json(
            manager_url,
            MANAGER_TOKEN,
            "DELETE",
            f"{terminal_path}/{terminal_id}",
            expected_status=202,
        )
        assert closed["status"] in {"closed", "exited"}
        with pytest.raises(WebSocketException):
            asyncio.run(_reuse_ticket(manager_url, MANAGER_TOKEN, terminal_id, ticket))
        assert _json(manager_url, MANAGER_TOKEN, "GET", terminal_path) == {"terminals": []}
    finally:
        for listener in reservations:
            listener.close()
        _cleanup_full_stack(process, dev_dir, startup_output)

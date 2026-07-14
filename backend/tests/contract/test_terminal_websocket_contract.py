import asyncio
import time

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from ic_env_guard.api.terminal_ws import _pump_terminal_output
from ic_env_guard.main import create_app
from ic_env_guard.terminal.manager import TerminalManager


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


def receive_until(
    websocket: WebSocketTestSession, needle: str, timeout_seconds: float = 5
) -> str:
    deadline = time.monotonic() + timeout_seconds
    received = ""

    while needle not in received:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"timed out after {timeout_seconds}s waiting for {needle!r}")

        async def receive_message(receive_timeout: float = remaining):
            with anyio.fail_after(receive_timeout):
                return await websocket._send_rx.receive()

        try:
            message = websocket.portal.call(receive_message)
        except TimeoutError:
            pytest.fail(f"timed out after {timeout_seconds}s waiting for {needle!r}")
        websocket._raise_on_close(message)
        received += message["text"]

    return received


@pytest.mark.contract
def test_terminal_websocket_output_pump_stops_when_session_disappears_during_read(
    monkeypatch,
):
    manager = TerminalManager(shell="/bin/sh", exited_retention_minutes=0)
    session = manager.create_terminal(title="read-teardown-race")

    def purge_during_read(terminal_id, _timeout):
        manager.close(terminal_id)
        manager.get(terminal_id)

    monkeypatch.setattr(manager, "read_available", purge_during_read)

    asyncio.run(_pump_terminal_output(object(), manager, session.id))

    assert session.id not in manager.sessions


@pytest.mark.contract
def test_terminal_websocket_output_pump_propagates_unrelated_key_error(monkeypatch):
    manager = TerminalManager(shell="/bin/sh")
    session = manager.create_terminal(title="replay-buffer-error")

    def raise_replay_buffer_error(_terminal_id, _timeout):
        raise KeyError("replay-buffer-missing")

    monkeypatch.setattr(manager, "read_available", raise_replay_buffer_error)

    try:
        with pytest.raises(KeyError, match="replay-buffer-missing"):
            asyncio.run(_pump_terminal_output(object(), manager, session.id))
        assert session.id in manager.sessions
    finally:
        manager.close(session.id)


@pytest.mark.contract
def test_terminal_websocket_output_pump_stops_when_closed_session_is_purged(
    monkeypatch,
):
    manager = TerminalManager(shell="/bin/sh", exited_retention_minutes=0)
    session = manager.create_terminal(title="teardown-race")

    def close_during_read(terminal_id, _timeout):
        manager.close(terminal_id)
        return ""

    monkeypatch.setattr(manager, "read_available", close_during_read)

    asyncio.run(_pump_terminal_output(object(), manager, session.id))

    assert session.id not in manager.sessions


@pytest.mark.contract
def test_terminal_websocket_rejects_missing_ticket(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()

    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/terminals/{terminal['id']}?cursor=0"):
            pass


@pytest.mark.contract
def test_terminal_websocket_streams_initial_prompt_without_input(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()
    ticket = client.post(
        f"/api/terminals/{terminal['id']}/connect-token", headers=auth_headers
    ).json()["ticket"]

    with client.websocket_connect(f"/ws/terminals/{terminal['id']}?ticket={ticket}&cursor=0") as ws:
        received = ""
        for _ in range(20):
            received += ws.receive_text()
            if "$ " in received or "# " in received:
                break
        assert "$ " in received or "# " in received


@pytest.mark.contract
def test_terminal_websocket_accepts_one_use_ticket_and_streams_text(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()
    ticket = client.post(
        f"/api/terminals/{terminal['id']}/connect-token", headers=auth_headers
    ).json()["ticket"]

    with client.websocket_connect(f"/ws/terminals/{terminal['id']}?ticket={ticket}&cursor=0") as ws:
        ws.send_text("printf ws-contract\\n\r")
        received = ""
        for _ in range(20):
            received += ws.receive_text()
            if "ws-contract" in received:
                break
        assert "ws-contract" in received


@pytest.mark.contract
def test_terminal_websocket_ticket_is_one_use(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()
    ticket = client.post(
        f"/api/terminals/{terminal['id']}/connect-token", headers=auth_headers
    ).json()["ticket"]

    with client.websocket_connect(f"/ws/terminals/{terminal['id']}?ticket={ticket}&cursor=0"):
        pass

    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/terminals/{terminal['id']}?ticket={ticket}&cursor=0"):
            pass


@pytest.mark.contract
def test_terminal_websocket_reconnect_replays_retained_output(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()
    terminal_id = terminal["id"]

    try:
        ticket = client.post(
            f"/api/terminals/{terminal_id}/connect-token", headers=auth_headers
        ).json()["ticket"]
        with client.websocket_connect(
            f"/ws/terminals/{terminal_id}?ticket={ticket}&cursor=0"
        ) as ws:
            ws.send_text("printf '__WS_''RECONNECT_OK__\\n'\r")
            assert "__WS_RECONNECT_OK__" in receive_until(ws, "__WS_RECONNECT_OK__")

        reconnect_ticket = client.post(
            f"/api/terminals/{terminal_id}/connect-token", headers=auth_headers
        ).json()["ticket"]
        with client.websocket_connect(
            f"/ws/terminals/{terminal_id}?ticket={reconnect_ticket}&cursor=0"
        ) as ws:
            assert "__WS_RECONNECT_OK__" in receive_until(ws, "__WS_RECONNECT_OK__")
    finally:
        closed = client.delete(f"/api/terminals/{terminal_id}", headers=auth_headers)
        assert closed.status_code == 202
        assert closed.json()["status"] in {"closed", "exited"}

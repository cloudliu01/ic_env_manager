import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


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
    ticket = client.post(
        f"/api/terminals/{terminal_id}/connect-token", headers=auth_headers
    ).json()["ticket"]

    with client.websocket_connect(
        f"/ws/terminals/{terminal_id}?ticket={ticket}&cursor=0"
    ) as ws:
        ws.send_text("printf '__WS_''RECONNECT_OK__\\n'\r")
        received = ""
        for _ in range(20):
            received += ws.receive_text()
            if "__WS_RECONNECT_OK__" in received:
                break
        assert "__WS_RECONNECT_OK__" in received

    reconnect_ticket = client.post(
        f"/api/terminals/{terminal_id}/connect-token", headers=auth_headers
    ).json()["ticket"]
    with client.websocket_connect(
        f"/ws/terminals/{terminal_id}?ticket={reconnect_ticket}&cursor=0"
    ) as ws:
        assert "__WS_RECONNECT_OK__" in ws.receive_text()

    client.delete(f"/api/terminals/{terminal_id}", headers=auth_headers)

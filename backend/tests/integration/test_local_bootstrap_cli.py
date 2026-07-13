import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from ic_env_guard.systemd.cli import ctl_main


def _read_line(connection):
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = connection.recv(1)
        if not chunk:
            break
        payload.extend(chunk)
    return json.loads(payload)


def _run_manager(response):
    socket_dir = Path(tempfile.mkdtemp(prefix="ieg-local-cli-", dir="/tmp"))
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "manager.sock"
    ready = threading.Event()
    received = []

    def manager():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            listener.listen(1)
            ready.set()
            with listener.accept()[0] as connection:
                received.append(_read_line(connection))
                assert connection.recv(1) == b""
                connection.sendall(
                    json.dumps(response, separators=(",", ":")).encode() + b"\n"
                )

    thread = threading.Thread(target=manager)
    thread.start()
    assert ready.wait(2)
    return socket_dir, socket_path, thread, received


def _finish_manager(socket_path, thread):
    if thread.is_alive():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(b"{}\n")
            client.shutdown(socket.SHUT_WR)
            client.recv(4096)
    thread.join(2)


def _arguments(manager_socket, agent_socket):
    return [
        "agent",
        "bootstrap-local",
        "--manager-socket",
        str(manager_socket),
        "--agent-socket",
        str(agent_socket),
        "--base-url",
        "http://127.0.0.1:8766",
        "--transport-profile",
        "local-loopback-http",
        "--agent-id",
        "local-agent",
        "--display-name",
        "Local development agent",
    ]


@pytest.mark.integration
def test_guardctl_local_bootstrap_uses_token_free_unix_exchange(capsys):
    response = {
        "protocol": "manager-local-bootstrap.result.v1",
        "status": "enrolled",
        "agent_id": "local-agent",
        "revision": 1,
    }
    socket_dir, manager_socket, thread, received = _run_manager(response)
    try:
        assert ctl_main(_arguments(manager_socket, socket_dir / "agent.sock")) == 0
        thread.join(2)
        assert not thread.is_alive()
        output = capsys.readouterr()
        assert output.out == "Local Agent enrolled.\n"
        assert output.err == ""
        assert received == [
            {
                "protocol": "manager-local-bootstrap.request.v1",
                "agent_id": "local-agent",
                "display_name": "Local development agent",
                "base_url": "http://127.0.0.1:8766",
                "transport_profile_id": "local-loopback-http",
                "agent_socket_path": str(socket_dir / "agent.sock"),
            }
        ]
        assert all("token" not in key for key in received[0])
    finally:
        _finish_manager(manager_socket, thread)
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.mark.integration
def test_guardctl_local_bootstrap_normalizes_server_error(capsys):
    socket_dir, manager_socket, thread, received = _run_manager(
        {"error": "enrollment_rejected"}
    )
    try:
        assert ctl_main(_arguments(manager_socket, socket_dir / "agent.sock")) == 1
        thread.join(2)
        assert not thread.is_alive()
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err == "ic-env-guardctl: local bootstrap failed\n"
        assert len(received) == 1
    finally:
        _finish_manager(manager_socket, thread)
        shutil.rmtree(socket_dir, ignore_errors=True)

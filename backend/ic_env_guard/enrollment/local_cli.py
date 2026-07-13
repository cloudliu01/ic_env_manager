import json
import math
import socket
from pathlib import Path
from time import monotonic
from typing import Any, TextIO

from ic_env_guard.enrollment.manager_socket import (
    DEFAULT_MANAGER_OPERATION_TIMEOUT_SECONDS,
)

MAX_LOCAL_FRAME_BYTES = 2048
LOCAL_BOOTSTRAP_TIMEOUT_SECONDS = DEFAULT_MANAGER_OPERATION_TIMEOUT_SECONDS + 5.0


class LocalBootstrapCliError(Exception):
    pass


def run_local_bootstrap(
    *,
    manager_socket: Path,
    agent_socket: Path,
    base_url: str,
    transport_profile: str,
    agent_id: str,
    display_name: str,
    stdout: TextIO,
    stderr: TextIO,
    timeout_seconds: float = LOCAL_BOOTSTRAP_TIMEOUT_SECONDS,
) -> int:
    try:
        request = {
            "protocol": "manager-local-bootstrap.request.v1",
            "agent_id": agent_id,
            "display_name": display_name,
            "base_url": base_url,
            "transport_profile_id": transport_profile,
            "agent_socket_path": str(agent_socket),
        }
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_LOCAL_FRAME_BYTES:
            raise LocalBootstrapCliError
        response = _exchange(manager_socket, payload, timeout_seconds)
        if set(response) != {
            "protocol",
            "status",
            "agent_id",
            "revision",
        }:
            raise LocalBootstrapCliError
        if (
            response["protocol"] != "manager-local-bootstrap.result.v1"
            or response["status"] != "enrolled"
            or response["agent_id"] != agent_id
            or type(response["revision"]) is not int
            or response["revision"] < 1
        ):
            raise LocalBootstrapCliError
    except Exception:
        stderr.write("ic-env-guardctl: local bootstrap failed\n")
        stderr.flush()
        return 1
    stdout.write("Local Agent enrolled.\n")
    stdout.flush()
    return 0


def _exchange(
    manager_socket: Path, payload: bytes, timeout_seconds: float
) -> dict[str, Any]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise LocalBootstrapCliError
    deadline = monotonic() + timeout_seconds
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        _set_remaining_timeout(client, deadline)
        client.connect(str(manager_socket))
        _set_remaining_timeout(client, deadline)
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while len(response) <= MAX_LOCAL_FRAME_BYTES:
            _set_remaining_timeout(client, deadline)
            chunk = client.recv(MAX_LOCAL_FRAME_BYTES + 1 - len(response))
            if not chunk:
                break
            response.extend(chunk)
    if len(response) > MAX_LOCAL_FRAME_BYTES or not response.endswith(b"\n"):
        raise LocalBootstrapCliError
    try:
        parsed = json.loads(response, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError):
        raise LocalBootstrapCliError from None
    if not isinstance(parsed, dict):
        raise LocalBootstrapCliError
    return parsed


def _set_remaining_timeout(client: socket.socket, deadline: float) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    client.settimeout(remaining)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

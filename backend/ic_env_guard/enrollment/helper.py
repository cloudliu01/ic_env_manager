import io
import json
import socket
from pathlib import Path
from typing import BinaryIO, TextIO

from ic_env_guard.enrollment.protocol import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    EnrollmentProtocolError,
    parse_request,
    parse_response,
)

MAX_STDERR_BYTES = 1024


def run_helper(
    socket_path: Path,
    stdin: BinaryIO,
    stdout: BinaryIO,
    stderr: TextIO,
) -> int:
    try:
        request_payload = stdin.read(MAX_REQUEST_BYTES + 1)
        parse_request(request_payload)
        response_payload = _exchange(socket_path, request_payload)
        parse_response(response_payload)
        stdout.write(response_payload)
        stdout.flush()
        return 0
    except EnrollmentProtocolError as exc:
        _safe_error(stderr, str(exc))
    except OSError:
        _safe_error(stderr, "enrollment helper unavailable")
    return 1


def _exchange(socket_path: Path, payload: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3.0)
        client.connect(str(socket_path))
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        response = io.BytesIO()
        while response.tell() <= MAX_RESPONSE_BYTES:
            chunk = client.recv(MAX_RESPONSE_BYTES + 1 - response.tell())
            if not chunk:
                break
            response.write(chunk)
        result = response.getvalue()
        if len(result) > MAX_RESPONSE_BYTES:
            raise EnrollmentProtocolError("enrollment response exceeds 8192 bytes")
        if result.startswith(b'{"error":'):
            try:
                code = json.loads(result)["error"]
            except (ValueError, KeyError, TypeError) as exc:
                raise EnrollmentProtocolError("invalid enrollment response") from exc
            allowed = {
                "unauthorized_peer",
                "invalid_request",
                "enrollment_rejected",
                "enrollment_unavailable",
            }
            if code not in allowed:
                raise EnrollmentProtocolError("invalid enrollment response")
            raise EnrollmentProtocolError(f"enrollment failed: {code}")
        return result


def _safe_error(stderr: TextIO, message: str) -> None:
    safe = message.replace("\r", " ").replace("\n", " ")
    encoded = f"ic-env-guard: {safe}\n".encode()[:MAX_STDERR_BYTES]
    stderr.write(encoded.decode("utf-8", errors="ignore"))
    stderr.flush()

import asyncio
import math
import os
import socket
import stat
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from pydantic import ValidationError

from ic_env_guard.enrollment.protocol import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    EnrollmentProtocolError,
    EnrollmentRequest,
    parse_response,
)
from ic_env_guard.enrollment.ssh import EnrollmentHelperResult
from ic_env_guard.fleet.target_policy import ValidatedTarget


class LocalEnrollmentSocketError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LocalEnrollmentSocketClient:
    def __init__(self, allowed_root: Path, timeout_seconds: float = 3.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        resolved_root = None
        try:
            resolved_root = allowed_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            pass
        if resolved_root is None:
            raise LocalEnrollmentSocketError("local_socket_path_rejected")
        self._allowed_root = resolved_root
        self._timeout_seconds = timeout_seconds

    async def issue(
        self,
        *,
        socket_path: Path,
        manager_id: str,
        enrollment_id: str,
        validation_target: ValidatedTarget,
    ) -> EnrollmentHelperResult:
        request = None
        try:
            request = EnrollmentRequest(
                protocol="manager-enrollment.v1",
                manager_id=manager_id,
                enrollment_id=enrollment_id,
            )
        except (ValidationError, ValueError):
            pass
        if request is None:
            raise LocalEnrollmentSocketError("local_enrollment_request_invalid")
        payload = request.model_dump_json().encode("ascii")
        if len(payload) > MAX_REQUEST_BYTES:
            raise LocalEnrollmentSocketError("local_enrollment_request_invalid")

        response = await asyncio.to_thread(self._exchange, socket_path, payload)
        parsed = None
        token = None
        try:
            parsed = parse_response(response)
            token = parsed.token.encode("ascii")
        except (EnrollmentProtocolError, UnicodeEncodeError):
            pass
        if parsed is None or token is None:
            raise LocalEnrollmentSocketError("local_enrollment_protocol_error")
        if parsed.expires_at <= datetime.now(UTC):
            raise LocalEnrollmentSocketError("local_credential_expired")
        return EnrollmentHelperResult(
            instance_id=str(parsed.instance_id),
            credential_id=str(parsed.credential_id),
            token=token,
            expires_at=parsed.expires_at,
            validation_target=validation_target,
        )

    def _exchange(self, socket_path: Path, payload: bytes) -> bytes:
        self._validate_socket_path(socket_path)
        deadline = monotonic() + self._timeout_seconds
        timed_out = False
        unavailable = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                self._set_remaining_timeout(client, deadline)
                client.connect(str(socket_path))
                self._set_remaining_timeout(client, deadline)
                client.sendall(payload)
                self._set_remaining_timeout(client, deadline)
                client.shutdown(socket.SHUT_WR)
                response = bytearray()
                while len(response) <= MAX_RESPONSE_BYTES:
                    self._set_remaining_timeout(client, deadline)
                    chunk = client.recv(MAX_RESPONSE_BYTES + 1 - len(response))
                    if not chunk:
                        break
                    response.extend(chunk)
        except TimeoutError:
            timed_out = True
        except OSError:
            unavailable = True
        if timed_out:
            raise LocalEnrollmentSocketError("local_socket_timeout")
        if unavailable:
            raise LocalEnrollmentSocketError("local_socket_unavailable")
        if len(response) > MAX_RESPONSE_BYTES:
            raise LocalEnrollmentSocketError("local_socket_response_too_large")
        return bytes(response)

    @staticmethod
    def _set_remaining_timeout(client: socket.socket, deadline: float) -> None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        timeout_invalid = False
        try:
            client.settimeout(remaining)
        except (OverflowError, ValueError):
            timeout_invalid = True
        if timeout_invalid:
            raise TimeoutError

    def _validate_socket_path(self, socket_path: Path) -> None:
        path_error = False
        try:
            parent = socket_path.parent.resolve(strict=True)
            root_metadata = self._allowed_root.lstat()
            socket_metadata = socket_path.lstat()
        except (OSError, RuntimeError, ValueError):
            path_error = True
        if path_error:
            raise LocalEnrollmentSocketError("local_socket_path_rejected")
        if parent != self._allowed_root:
            raise LocalEnrollmentSocketError("local_socket_path_rejected")
        if not _is_owner_only(root_metadata, stat.S_ISDIR):
            raise LocalEnrollmentSocketError("local_socket_path_rejected")
        if not _is_owner_only(socket_metadata, stat.S_ISSOCK):
            raise LocalEnrollmentSocketError("local_socket_path_rejected")


def _is_owner_only(metadata: os.stat_result, expected_type) -> bool:
    return (
        expected_type(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) & 0o077 == 0
    )

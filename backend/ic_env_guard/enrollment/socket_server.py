import ctypes
import json
import os
import socket
import stat
import struct
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from ic_env_guard.enrollment.models import EnrollmentError
from ic_env_guard.enrollment.protocol import (
    MAX_REQUEST_BYTES,
    EnrollmentProtocolError,
    EnrollmentResponse,
    encode_response,
    parse_request,
)
from ic_env_guard.enrollment.service import EnrollmentService


class SocketSecurityError(RuntimeError):
    pass


PeerCredentials = Callable[[socket.socket], tuple[int, int]]


def peer_credentials(connection: socket.socket) -> tuple[int, int]:
    if sys.platform.startswith("linux"):
        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            raise SocketSecurityError("peer credentials are unavailable")
        value = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        _, uid, gid = struct.unpack("3i", value)
        return uid, gid
    if sys.platform == "darwin":
        uid = ctypes.c_uint()
        gid = ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        getpeereid = libc.getpeereid
        getpeereid.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        getpeereid.restype = ctypes.c_int
        if getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise SocketSecurityError("peer credentials are unavailable")
        return uid.value, gid.value
    raise SocketSecurityError("peer credentials are unsupported on this platform")


class EnrollmentSocketServer:
    def __init__(
        self,
        path: Path,
        mode: int,
        instance_id: UUID,
        service: EnrollmentService,
        *,
        peer_credentials: PeerCredentials = peer_credentials,
    ) -> None:
        self.path = path
        self.mode = mode
        self.instance_id = instance_id
        self.service = service
        self._peer_credentials = peer_credentials
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._identity: tuple[int, int] | None = None
        self._healthy = False

    @property
    def healthy(self) -> bool:
        return self._healthy

    def start(self) -> None:
        if self._socket is not None:
            raise SocketSecurityError("enrollment socket is already running")
        self._validate_parent()
        if self.path.exists() or self.path.is_symlink():
            raise SocketSecurityError("enrollment socket path already exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, self.mode)
            metadata = os.lstat(self.path)
            self._identity = (metadata.st_dev, metadata.st_ino)
            if not stat.S_ISSOCK(metadata.st_mode):
                raise SocketSecurityError("enrollment socket is not a socket")
            if stat.S_IMODE(metadata.st_mode) & ~self.mode:
                raise SocketSecurityError("enrollment socket permissions are too broad")
            if metadata.st_uid != os.geteuid():
                raise SocketSecurityError("enrollment socket owner is unsafe")
            listener.listen(8)
            listener.settimeout(0.1)
        except Exception:
            listener.close()
            self._remove_if_owned()
            raise
        self._socket = listener
        self._healthy = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._healthy = False
        listener, self._socket = self._socket, None
        if listener is not None:
            listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._remove_if_owned()
        self._identity = None

    def _validate_parent(self) -> None:
        try:
            metadata = self.path.parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise SocketSecurityError("enrollment socket directory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or self.path.parent.is_symlink():
            raise SocketSecurityError("enrollment socket parent is unsafe")
        if metadata.st_uid != os.geteuid():
            raise SocketSecurityError("enrollment socket directory owner is unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise SocketSecurityError("enrollment socket directory permissions are unsafe")

    def _serve(self) -> None:
        try:
            while self._healthy:
                listener = self._socket
                if listener is None:
                    return
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                with connection:
                    connection.settimeout(2.0)
                    self._handle(connection)
        finally:
            self._healthy = False

    def _handle(self, connection: socket.socket) -> None:
        try:
            uid, _ = self._peer_credentials(connection)
            if uid != os.geteuid():
                self._send_error(connection, "unauthorized_peer")
                return
            payload = self._read_request(connection)
            request = parse_request(payload)
            issued = self.service.issue_pending(str(request.manager_id), request.enrollment_id)
            response = EnrollmentResponse(
                protocol="manager-enrollment.v1",
                instance_id=self.instance_id,
                credential_id=issued.credential_id,
                token=issued.token,
                expires_at=issued.credential.pending_expires_at,
            )
            connection.sendall(encode_response(response))
        except EnrollmentProtocolError:
            self._send_error(connection, "invalid_request")
        except EnrollmentError:
            self._send_error(connection, "enrollment_rejected")
        except Exception:
            # Adapter and audit failures are deliberately reduced to one safe code.
            self._send_error(connection, "enrollment_unavailable")

    @staticmethod
    def _read_request(connection: socket.socket) -> bytes:
        chunks: list[bytes] = []
        remaining = MAX_REQUEST_BYTES + 1
        while remaining:
            chunk = connection.recv(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_REQUEST_BYTES:
            raise EnrollmentProtocolError("stdin exceeds 4096 bytes")
        return payload

    @staticmethod
    def _send_error(connection: socket.socket, code: str) -> None:
        payload = json.dumps({"error": code}, separators=(",", ":")).encode("ascii") + b"\n"
        try:
            connection.sendall(payload)
        except OSError:
            return

    def _remove_if_owned(self) -> None:
        if self._identity is None:
            return
        try:
            metadata = os.lstat(self.path)
        except FileNotFoundError:
            return
        if (metadata.st_dev, metadata.st_ino) == self._identity and stat.S_ISSOCK(metadata.st_mode):
            self.path.unlink()

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
    LOCAL_RETRY_PROTOCOL,
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
        self._thread_started = False
        self._identity: tuple[int, int] | None = None
        self._temporary_path: Path | None = None
        self._owned_paths: dict[Path, tuple[int, int]] = {}
        self._healthy = False
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            return (
                self._healthy
                and self._thread_started
                and self._thread is not None
                and self._thread.is_alive()
            )

    def start(self) -> None:
        if self._socket is not None:
            raise SocketSecurityError("enrollment socket is already running")
        self._validate_parent()
        self._cleanup_owned_paths()
        if self._owned_paths:
            raise SocketSecurityError("previous enrollment socket cleanup is pending")
        if self.path.exists() or self.path.is_symlink():
            raise SocketSecurityError("enrollment socket path already exists")
        listener, temporary_path = self._bind_temporary_socket()
        self._temporary_path = temporary_path
        try:
            metadata = self._bound_path_metadata(temporary_path)
            self._identity = (metadata.st_dev, metadata.st_ino)
            self._owned_paths[temporary_path] = self._identity
            self._validate_bound_path(metadata)
            os.chmod(temporary_path, self.mode)
            metadata = self._bound_path_metadata(temporary_path)
            if (metadata.st_dev, metadata.st_ino) != self._identity:
                raise SocketSecurityError("enrollment socket path changed during startup")
            if stat.S_IMODE(metadata.st_mode) & ~self.mode:
                raise SocketSecurityError("enrollment socket permissions are too broad")
            listener.listen(8)
            listener.settimeout(0.1)
            os.link(temporary_path, self.path, follow_symlinks=False)
            self._owned_paths[self.path] = self._identity
            if self._remove_path_if_owned(temporary_path):
                self._temporary_path = None
        except Exception:
            listener.close()
            self._remove_if_owned()
            self._remove_temporary_if_owned()
            self._sync_identity()
            raise
        thread = threading.Thread(target=self._serve, daemon=True)
        with self._state_lock:
            self._stop_event.clear()
            self._socket = listener
            self._thread = thread
            self._thread_started = False
            self._healthy = False
        try:
            thread.start()
        except Exception:
            self._rollback_start(listener)
            raise
        with self._state_lock:
            if not thread.is_alive():
                startup_failed = True
            else:
                self._thread_started = True
                self._healthy = True
                startup_failed = False
        if startup_failed:
            thread.join(timeout=2)
            self._rollback_start(listener)
            raise SocketSecurityError("enrollment socket thread failed to start")

    def stop(self) -> None:
        with self._state_lock:
            self._healthy = False
            self._stop_event.set()
            listener, self._socket = self._socket, None
            thread, self._thread = self._thread, None
            thread_started, self._thread_started = self._thread_started, False
        if listener is not None:
            listener.close()
        if thread is not None and thread_started:
            thread.join(timeout=2)
        self._remove_if_owned()
        self._remove_temporary_if_owned()
        self._sync_identity()

    def _rollback_start(self, listener: socket.socket) -> None:
        with self._state_lock:
            self._healthy = False
            self._stop_event.set()
            self._socket = None
            self._thread = None
            self._thread_started = False
        listener.close()
        self._remove_if_owned()
        self._remove_temporary_if_owned()
        self._sync_identity()

    def _bind_temporary_socket(self) -> tuple[socket.socket, Path]:
        basename_bytes = len(os.fsencode(self.path.name))
        if basename_bytes < 1:
            raise SocketSecurityError("enrollment socket filename is invalid")
        names = ("_" * basename_bytes, "-" * basename_bytes, "~" * basename_bytes)
        temporary_path = next(
            self.path.parent / name
            for name in names
            if os.fsencode(name) != os.fsencode(self.path.name)
        )
        self._prepare_reserved_temporary_path(temporary_path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(temporary_path))
        except OSError as exc:
            listener.close()
            raise SocketSecurityError("reserved temporary socket is unavailable") from exc
        return listener, temporary_path

    def _prepare_reserved_temporary_path(self, path: Path) -> None:
        try:
            metadata = self._bound_path_metadata(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SocketSecurityError("reserved temporary socket metadata is unavailable") from exc
        self._validate_parent()
        try:
            self._validate_bound_path(metadata)
        except SocketSecurityError as exc:
            raise SocketSecurityError("reserved temporary socket is unsafe") from exc
        self._identity = (metadata.st_dev, metadata.st_ino)
        self._owned_paths[path] = self._identity
        self._temporary_path = path
        if not self._remove_path_if_owned(path):
            raise SocketSecurityError("reserved temporary socket cleanup is pending")
        self._temporary_path = None
        self._sync_identity()

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

    def _bound_path_metadata(self, path: Path) -> os.stat_result:
        last_error: OSError | None = None
        for _ in range(3):
            try:
                return os.lstat(path)
            except FileNotFoundError:
                raise
            except OSError as exc:
                last_error = exc
        try:
            return os.stat(path, follow_symlinks=False)
        except OSError as exc:
            if last_error is not None:
                raise last_error from exc
            raise

    @staticmethod
    def _validate_bound_path(metadata: os.stat_result) -> None:
        if not stat.S_ISSOCK(metadata.st_mode):
            raise SocketSecurityError("enrollment socket is not a socket")
        if metadata.st_uid != os.geteuid():
            raise SocketSecurityError("enrollment socket owner is unsafe")

    def _serve(self) -> None:
        try:
            while not self._stop_event.is_set():
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
            with self._state_lock:
                self._healthy = False

    def _handle(self, connection: socket.socket) -> None:
        try:
            uid, _ = self._peer_credentials(connection)
            if uid != os.geteuid():
                self._send_error(connection, "unauthorized_peer")
                return
            payload = self._read_request(connection)
            request = parse_request(payload)
            if request.protocol == LOCAL_RETRY_PROTOCOL:
                issued = self.service.reissue_expired_pending(
                    str(request.manager_id), request.enrollment_id
                )
            else:
                issued = self.service.issue_pending(
                    str(request.manager_id), request.enrollment_id
                )
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
        self._remove_path_if_owned(self.path)

    def _remove_temporary_if_owned(self) -> None:
        if self._temporary_path is not None and self._remove_path_if_owned(self._temporary_path):
            self._temporary_path = None

    def _remove_path_if_owned(self, path: Path) -> bool:
        identity = self._owned_paths.get(path)
        if identity is None:
            return False
        try:
            metadata = self._bound_path_metadata(path)
        except FileNotFoundError:
            self._owned_paths.pop(path, None)
            return True
        except OSError:
            return False
        if (metadata.st_dev, metadata.st_ino) != identity or not stat.S_ISSOCK(metadata.st_mode):
            self._owned_paths.pop(path, None)
            return True
        try:
            path.unlink()
        except OSError:
            return False
        self._owned_paths.pop(path, None)
        return True

    def _cleanup_owned_paths(self) -> None:
        for path in tuple(self._owned_paths):
            self._remove_path_if_owned(path)
        self._sync_identity()

    def _sync_identity(self) -> None:
        self._identity = next(iter(self._owned_paths.values()), None)

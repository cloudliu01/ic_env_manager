import asyncio
import json
import os
import socket
import stat
import struct
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ic_env_guard.enrollment.cli import CliEnrollmentError, parse_ssh_argument
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    CliSubmissionClaim,
    EnrollmentOrchestrator,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile

MAX_HEADER_BYTES = 2048
MAX_RESULT_BYTES = 12 * 1024


class ManagerSocketError(RuntimeError):
    pass


PeerAuthorizer = Callable[[socket.socket], bool]


class ManagerEnrollmentSocket:
    def __init__(
        self,
        *,
        path: Path,
        mode: int,
        orchestrator: EnrollmentOrchestrator,
        allowed_uid: int,
        allowed_gid: int | None = None,
        peer_authorizer: PeerAuthorizer | None = None,
        max_concurrency: int = 4,
        io_timeout_seconds: float = 3.0,
        result_timeout_seconds: float = 120.0,
    ) -> None:
        self.path = path
        self.mode = mode
        self.orchestrator = orchestrator
        self.allowed_uid = allowed_uid
        self.allowed_gid = allowed_gid
        self._peer_authorizer = peer_authorizer or self._authorize_peer
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._io_timeout_seconds = io_timeout_seconds
        self._result_timeout_seconds = result_timeout_seconds
        self._server: asyncio.AbstractServer | None = None
        self._identity: tuple[int, int] | None = None
        self._handlers: set[asyncio.Task[None]] = set()
        self.healthy = False

    async def start(self) -> None:
        if self._server is not None:
            raise ManagerSocketError("manager enrollment socket is already running")
        self._validate_parent()
        if self.path.exists() or self.path.is_symlink():
            raise ManagerSocketError("manager enrollment socket path already exists")
        temporary = self.path.with_name(f".{self.path.name}.new")
        if temporary.exists() or temporary.is_symlink():
            raise ManagerSocketError("manager enrollment temporary socket exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(temporary))
            os.chmod(temporary, self.mode)
            if self.allowed_gid is not None:
                os.chown(temporary, os.geteuid(), self.allowed_gid)
            metadata = os.lstat(temporary)
            self._validate_socket(metadata)
            identity = (metadata.st_dev, metadata.st_ino)
            self._identity = identity
            listener.listen(16)
            listener.setblocking(False)
            os.link(temporary, self.path, follow_symlinks=False)
            final = os.lstat(self.path)
            if (final.st_dev, final.st_ino) != identity:
                raise ManagerSocketError("manager enrollment socket publish changed")
            temporary.unlink()
            self._server = await asyncio.start_unix_server(
                self._connected, sock=listener
            )
            self.healthy = True
        except Exception:
            listener.close()
            self._unlink_if_owned(temporary)
            self._unlink_if_owned(self.path)
            self._identity = None
            raise

    async def stop(self) -> None:
        self.healthy = False
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        if self._handlers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(self._handlers), return_exceptions=True),
                    timeout=self._io_timeout_seconds,
                )
            except TimeoutError:
                for task in tuple(self._handlers):
                    task.cancel()
                await asyncio.gather(*tuple(self._handlers), return_exceptions=True)
        self._unlink_if_owned(self.path)
        self._identity = None

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._handle(reader, writer))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        claim: CliSubmissionClaim | None = None
        try:
            raw_socket = writer.get_extra_info("socket")
            if raw_socket is None or not self._peer_authorizer(raw_socket):
                await self._send(writer, {"error": "unauthorized_peer"})
                return
            async with self._semaphore:
                header = await self._read_object(reader, MAX_HEADER_BYTES)
                if set(header) != {
                    "protocol",
                    "enrollment_id",
                    "ssh",
                    "pinned_address",
                } or header["protocol"] != "manager-cli-enrollment.header.v1":
                    raise ManagerSocketError("invalid_request")
                user, host, port = parse_ssh_argument(header["ssh"])
                claim = self.orchestrator.begin_cli_submission(
                    enrollment_id=header["enrollment_id"],
                    ssh_user=user,
                    ssh_host=host,
                    ssh_port=port,
                    pinned_address=header["pinned_address"],
                    context=AutoEnrollmentAuditContext(
                        actor_id=f"local-cli:{self.allowed_uid}",
                        source_addr="local-unix",
                        correlation_id=None,
                    ),
                )
                await self._send(
                    writer,
                    {
                        "protocol": "manager-cli-enrollment.ready.v1",
                        "manager_id": claim.job.manager_id,
                        "enrollment_id": claim.job.enrollment_id,
                        "input_fingerprint": claim.input_fingerprint,
                        "nonce": claim.nonce,
                        "expires_at": claim.job.expires_at.isoformat(),
                        "host_key_policy": (
                            "accept-new"
                            if isinstance(claim.target.profile, TrustedLanHttpProfile)
                            else "ask"
                        ),
                    },
                )
                remaining = max(
                    0.001,
                    (claim.job.expires_at - datetime.now(UTC)).total_seconds(),
                )
                result = await self._read_object(
                    reader,
                    MAX_RESULT_BYTES,
                    timeout=min(remaining, self._result_timeout_seconds),
                )
                if set(result) != {
                    "protocol",
                    "input_fingerprint",
                    "nonce",
                    "helper",
                } or result[
                    "protocol"
                ] != "manager-cli-enrollment.result.v1":
                    raise ManagerSocketError("invalid_result")
                helper_payload = json.dumps(
                    result["helper"], separators=(",", ":"), sort_keys=True
                ).encode()
                if await asyncio.wait_for(
                    reader.read(1), timeout=self._io_timeout_seconds
                ) != b"":
                    raise ManagerSocketError("trailing_request_data")
                completed = await self.orchestrator.complete_cli_submission(
                    claim,
                    helper_payload=helper_payload,
                    input_fingerprint=result["input_fingerprint"],
                    nonce=result["nonce"],
                )
                await self._send(
                    writer,
                    {"status": "verified", "enrollment_id": completed.job.enrollment_id},
                )
                claim = None
        except (ManagerSocketError, CliEnrollmentError, TypeError, ValueError):
            await self._send(writer, {"error": "invalid_request"})
        except Exception:
            await self._send(writer, {"error": "enrollment_rejected"})
        finally:
            if claim is not None:
                self.orchestrator.abort_cli_submission(claim, "cli_submission_interrupted")
            writer.close()
            await writer.wait_closed()

    async def _read_object(
        self,
        reader: asyncio.StreamReader,
        limit: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            payload = await asyncio.wait_for(
                reader.readline(), timeout or self._io_timeout_seconds
            )
        except (TimeoutError, asyncio.LimitOverrunError):
            raise ManagerSocketError("invalid_request") from None
        if not payload.endswith(b"\n") or len(payload) > limit:
            raise ManagerSocketError("invalid_request")
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError):
            raise ManagerSocketError("invalid_request") from None
        if not isinstance(value, dict):
            raise ManagerSocketError("invalid_request")
        return value

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
        writer.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        try:
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _authorize_peer(self, connection: socket.socket) -> bool:
        pid, uid, gid = _peer_credentials(connection)
        if uid == self.allowed_uid or (
            self.allowed_gid is not None and gid == self.allowed_gid
        ):
            return True
        return self.allowed_gid is not None and _linux_supplementary_group(
            pid, uid, self.allowed_gid
        )

    def _validate_parent(self) -> None:
        metadata = os.lstat(self.path.parent)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ManagerSocketError("manager enrollment socket parent is unsafe")

    def _validate_socket(self, metadata: os.stat_result) -> None:
        if (
            not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & ~self.mode
            or (self.allowed_gid is not None and metadata.st_gid != self.allowed_gid)
        ):
            raise ManagerSocketError("manager enrollment socket metadata is unsafe")

    def _unlink_if_owned(self, path: Path) -> None:
        if self._identity is None:
            return
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return
        if (metadata.st_dev, metadata.st_ino) == self._identity and stat.S_ISSOCK(
            metadata.st_mode
        ):
            path.unlink()


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    if sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED"):
        value = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        return struct.unpack("3i", value)
    if sys.platform == "darwin" and hasattr(socket, "LOCAL_PEERCRED"):
        value = connection.getsockopt(0, socket.LOCAL_PEERCRED, 76)
        if len(value) < 16:
            raise ManagerSocketError("peer credentials are unavailable")
        _version, uid, group_count, primary_gid = struct.unpack("4I", value[:16])
        if group_count < 1:
            raise ManagerSocketError("peer credentials are unavailable")
        return -1, uid, primary_gid
    raise ManagerSocketError("peer credentials are unavailable")


def _linux_supplementary_group(pid: int, uid: int, allowed_gid: int) -> bool:
    if not sys.platform.startswith("linux") or pid < 1:
        return False
    directory_fd = -1
    status_fd = -1
    try:
        directory_fd = os.open(
            f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        if os.fstat(directory_fd).st_uid != uid:
            return False
        status_fd = os.open("status", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        payload = os.read(status_fd, 16 * 1024)
        if len(payload) == 16 * 1024 or os.read(status_fd, 1):
            return False
        for line in payload.decode("ascii").splitlines():
            if line.startswith("Groups:"):
                return allowed_gid in {int(value) for value in line[7:].split()}
    except (OSError, UnicodeError, ValueError):
        return False
    finally:
        if status_fd >= 0:
            os.close(status_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
    return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

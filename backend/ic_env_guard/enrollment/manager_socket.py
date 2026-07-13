import asyncio
import json
import os
import socket
import stat
import struct
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from ic_env_guard.enrollment.cli import CliEnrollmentError, parse_ssh_argument
from ic_env_guard.enrollment.local_socket import (
    LocalEnrollmentSocketClient,
    LocalEnrollmentSocketError,
)
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    CliSubmissionClaim,
    EnrollmentOrchestrator,
    LocalBootstrapRequest,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile

MAX_HEADER_BYTES = 2048
MAX_RESULT_BYTES = 12 * 1024
DEFAULT_MANAGER_OPERATION_TIMEOUT_SECONDS = 120.0
DEFAULT_CANCELLATION_CLEANUP_SECONDS = 1.0

T = TypeVar("T")


class ManagerSocketError(RuntimeError):
    pass


class _OperationDeadlineExpired(Exception):
    pass


PeerAuthorizer = Callable[[socket.socket], bool]
PeerCredentials = Callable[[socket.socket], tuple[int, int, int]]


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
        peer_credentials: PeerCredentials | None = None,
        max_concurrency: int = 4,
        io_timeout_seconds: float = 3.0,
        result_timeout_seconds: float = DEFAULT_MANAGER_OPERATION_TIMEOUT_SECONDS,
        cancellation_cleanup_seconds: float = DEFAULT_CANCELLATION_CLEANUP_SECONDS,
        local_bootstrap_enabled: bool = False,
        local_socket_client: LocalEnrollmentSocketClient | None = None,
    ) -> None:
        self.path = path
        self.mode = mode
        self.orchestrator = orchestrator
        self.allowed_uid = allowed_uid
        self.allowed_gid = allowed_gid
        self._peer_authorizer = peer_authorizer or self._authorize_peer
        self._peer_credentials = peer_credentials or _peer_credentials
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._io_timeout_seconds = io_timeout_seconds
        self._operation_timeout_seconds = result_timeout_seconds
        self._cancellation_cleanup_seconds = cancellation_cleanup_seconds
        self._local_bootstrap_enabled = local_bootstrap_enabled
        self._local_socket_client = local_socket_client
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
        if self._handlers:
            handlers = tuple(self._handlers)
            _done, pending = await asyncio.wait(
                handlers, timeout=self._io_timeout_seconds
            )
            for task in pending:
                task.cancel()
            if pending:
                _done, pending = await asyncio.wait(
                    pending,
                    timeout=(
                        self._io_timeout_seconds
                        + self._cancellation_cleanup_seconds
                    ),
                )
                for task in pending:
                    task.add_done_callback(_consume_task_result)
        if server is not None:
            close_task = asyncio.create_task(server.wait_closed())
            _done, pending = await asyncio.wait(
                {close_task}, timeout=self._io_timeout_seconds
            )
            if pending:
                close_task.cancel()
                close_task.add_done_callback(_consume_task_result)
        self._unlink_if_owned(self.path)
        self._identity = None

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._handle(reader, writer))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        deadline = asyncio.get_running_loop().time() + self._operation_timeout_seconds
        acquired = False
        try:
            raw_socket = writer.get_extra_info("socket")
            if raw_socket is None or not self._peer_authorizer(raw_socket):
                await self._send(writer, {"error": "unauthorized_peer"}, deadline)
                return
            _pid, peer_uid, _gid = self._peer_credentials(raw_socket)
            await self._await_with_deadline(self._semaphore.acquire(), deadline)
            acquired = True
            header = await self._read_object(reader, MAX_HEADER_BYTES, deadline)
            protocol = header.get("protocol")
            if protocol == "manager-local-bootstrap.request.v1":
                await self._handle_local_bootstrap(
                    header, writer, peer_uid, deadline
                )
                return
            if protocol != "manager-cli-enrollment.header.v1":
                raise ManagerSocketError("invalid_request")
            await self._handle_ssh_cli(
                header, reader, writer, peer_uid, deadline
            )
        except _OperationDeadlineExpired:
            pass
        except (ManagerSocketError, CliEnrollmentError, TypeError, ValueError):
            await self._send(writer, {"error": "invalid_request"}, deadline)
        except Exception:
            await self._send(writer, {"error": "enrollment_rejected"}, deadline)
        finally:
            if acquired:
                self._semaphore.release()
            writer.close()
            await self._wait_for_close(writer, deadline)

    async def _handle_local_bootstrap(
        self,
        header: dict[str, Any],
        writer: asyncio.StreamWriter,
        peer_uid: int,
        deadline: float,
    ) -> None:
        if peer_uid != self.allowed_uid:
            await self._send(writer, {"error": "unauthorized_peer"}, deadline)
            return
        expected_keys = {
            "protocol",
            "agent_id",
            "display_name",
            "base_url",
            "transport_profile_id",
            "agent_socket_path",
        }
        if not self._local_bootstrap_enabled or set(header) != expected_keys:
            raise ManagerSocketError("invalid_request")
        if any(type(header[key]) is not str for key in expected_keys):
            raise ManagerSocketError("invalid_request")
        if (
            header["agent_id"] != "local-agent"
            or not header["display_name"]
            or header["transport_profile_id"] != "local-loopback-http"
            or not _is_local_bootstrap_url(header["base_url"])
            or self._local_socket_client is None
        ):
            raise ManagerSocketError("invalid_request")
        agent_socket_path = Path(header["agent_socket_path"])
        try:
            self._local_socket_client.preflight(agent_socket_path)
        except LocalEnrollmentSocketError:
            raise ManagerSocketError("invalid_request") from None
        bootstrap_task = asyncio.create_task(
            self.orchestrator.bootstrap_local(
                LocalBootstrapRequest(
                    agent_id=header["agent_id"],
                    display_name=header["display_name"],
                    base_url=header["base_url"],
                    transport_profile_id=header["transport_profile_id"],
                    agent_socket_path=agent_socket_path,
                ),
                AutoEnrollmentAuditContext(
                    actor_id=f"local-cli:{peer_uid}",
                    source_addr="local-unix",
                    correlation_id=None,
                ),
            ),
        )
        record = await self._await_with_deadline(bootstrap_task, deadline)
        await self._send(
            writer,
            {
                "protocol": "manager-local-bootstrap.result.v1",
                "status": "enrolled",
                "agent_id": record.agent_id,
                "revision": record.revision,
            },
            deadline,
        )

    async def _handle_ssh_cli(
        self,
        header: dict[str, Any],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_uid: int,
        deadline: float,
    ) -> None:
        claim: CliSubmissionClaim | None = None
        result_received = False
        result_handled = False
        try:
            initial_keys = {
                "protocol",
                "enrollment_id",
                "ssh",
                "pinned_address",
            }
            resume_keys = {*initial_keys, "resume_nonce"}
            key_sets = {frozenset(initial_keys), frozenset(resume_keys)}
            if frozenset(header) not in key_sets:
                raise ManagerSocketError("invalid_request")
            user, host, port = parse_ssh_argument(header["ssh"])
            claim = self.orchestrator.begin_cli_submission(
                enrollment_id=header["enrollment_id"],
                ssh_user=user,
                ssh_host=host,
                ssh_port=port,
                pinned_address=header["pinned_address"],
                peer_uid=peer_uid,
                resume_nonce=header.get("resume_nonce"),
                context=AutoEnrollmentAuditContext(
                    actor_id=f"local-cli:{peer_uid}",
                    source_addr="local-unix",
                    correlation_id=None,
                ),
            )
            if claim.already_accepted:
                await self._send(
                    writer,
                    {
                        "protocol": "manager-cli-enrollment.accepted.v1",
                        "status": "already_accepted",
                        "enrollment_id": claim.job.enrollment_id,
                    },
                    deadline,
                )
                self.orchestrator.release_cli_connection(
                    claim, result_received=True, code="already_accepted"
                )
                claim = None
                return
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
                deadline,
            )
            remaining = max(
                0.001,
                (claim.job.expires_at - datetime.now(UTC)).total_seconds(),
            )
            result = await self._read_object(
                reader,
                MAX_RESULT_BYTES,
                deadline,
                first_byte_timeout=remaining,
            )
            result_received = True
            if set(result) != {
                "protocol",
                "input_fingerprint",
                "nonce",
                "helper",
            } or result["protocol"] != "manager-cli-enrollment.result.v1":
                raise ManagerSocketError("invalid_result")
            helper_payload = json.dumps(
                result["helper"], separators=(",", ":"), sort_keys=True
            ).encode()
            if await self._await_with_deadline(
                reader.read(1), deadline, cap=self._io_timeout_seconds
            ) != b"":
                raise ManagerSocketError("trailing_request_data")
            try:
                completed = await self._await_with_deadline(
                    self.orchestrator.complete_cli_submission(
                        claim,
                        helper_payload=helper_payload,
                        input_fingerprint=result["input_fingerprint"],
                        nonce=result["nonce"],
                    ),
                    deadline,
                )
            finally:
                result_handled = True
            await self._send(
                writer,
                {"status": "verified", "enrollment_id": completed.job.enrollment_id},
                deadline,
            )
            claim = None
        finally:
            if claim is not None and not result_handled:
                self.orchestrator.release_cli_connection(
                    claim,
                    result_received=result_received,
                    code=(
                        "cli_result_rejected"
                        if result_received
                        else "cli_submission_interrupted"
                    ),
                )

    async def _read_object(
        self,
        reader: asyncio.StreamReader,
        limit: int,
        deadline: float,
        *,
        first_byte_timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            if first_byte_timeout is None:
                payload = await self._await_with_deadline(
                    reader.readline(), deadline, cap=self._io_timeout_seconds
                )
            else:
                first = await self._await_with_deadline(
                    reader.readexactly(1), deadline, cap=first_byte_timeout
                )
                rest = await self._await_with_deadline(
                    reader.readline(), deadline, cap=self._io_timeout_seconds
                )
                payload = first + rest
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
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

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        value: dict[str, Any],
        deadline: float,
    ) -> None:
        if self._remaining(deadline) <= 0:
            return
        try:
            writer.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
            await self._await_with_deadline(
                writer.drain(), deadline, cap=self._io_timeout_seconds
            )
        except (
            BrokenPipeError,
            ConnectionResetError,
            _OperationDeadlineExpired,
        ):
            return

    async def _await_with_deadline(
        self,
        awaitable: Awaitable[T],
        deadline: float,
        *,
        cap: float | None = None,
    ) -> T:
        task = asyncio.ensure_future(awaitable)
        timeout = self._remaining(deadline)
        if cap is not None:
            timeout = min(timeout, cap)
        try:
            if timeout > 0:
                done, _pending = await asyncio.wait({task}, timeout=timeout)
                if task in done:
                    return task.result()
        except asyncio.CancelledError:
            task.cancel()
            await self._settle_cancelled_task(task)
            raise
        task.cancel()
        await self._settle_cancelled_task(task)
        raise _OperationDeadlineExpired

    async def _settle_cancelled_task(self, task: asyncio.Future[Any]) -> None:
        done, _pending = await asyncio.wait(
            {task}, timeout=self._cancellation_cleanup_seconds
        )
        if task not in done:
            task.add_done_callback(_consume_task_result)
            return
        _consume_task_result(task)

    async def _wait_for_close(
        self, writer: asyncio.StreamWriter, deadline: float
    ) -> None:
        try:
            await self._await_with_deadline(
                writer.wait_closed(), deadline, cap=self._io_timeout_seconds
            )
        except (
            BrokenPipeError,
            ConnectionResetError,
            _OperationDeadlineExpired,
        ):
            return

    @staticmethod
    def _remaining(deadline: float) -> float:
        return deadline - asyncio.get_running_loop().time()

    def _authorize_peer(self, connection: socket.socket) -> bool:
        _pid, uid, gid = self._peer_credentials(connection)
        return uid == self.allowed_uid or (
            self.allowed_gid is not None and gid == self.allowed_gid
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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _consume_task_result(task: asyncio.Future[Any]) -> None:
    try:
        task.result()
    except BaseException:
        pass


def _is_local_bootstrap_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        type(port) is int
        and 1 <= port <= 65535
        and value == f"http://127.0.0.1:{port}"
    )

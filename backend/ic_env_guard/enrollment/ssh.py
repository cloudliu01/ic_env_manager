import asyncio
import json
import os
import signal
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ic_env_guard.enrollment.ssh_config import (
    MAX_EFFECTIVE_CONFIG_BYTES,
    SshConfigError,
    SshEffectiveTarget,
    build_ssh_argv,
    build_ssh_preflight_argv,
    host_key_alias,
    validate_ssh_destination,
    verify_effective_config,
)
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile

MAX_HELPER_STDIN_BYTES = 4 * 1024
MAX_HELPER_STDOUT_BYTES = 8 * 1024
MAX_HELPER_STDERR_BYTES = 8 * 1024
_PROTOCOL = "manager-enrollment.v1"


class SshEnrollmentError(Exception):
    def __init__(self, code: str, *, dispatch_state: str = "not_dispatched") -> None:
        super().__init__(code)
        self.code = code
        self.dispatch_state = dispatch_state


@dataclass(frozen=True)
class SshEnrollmentRequest:
    manager_id: str
    enrollment_id: str
    ssh_user: str
    ssh_host: str
    ssh_port: int
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class EnrollmentHelperResult:
    instance_id: str
    credential_id: str
    token: bytes
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            "EnrollmentHelperResult("
            f"instance_id={self.instance_id!r}, credential_id={self.credential_id!r}, "
            f"expires_at={self.expires_at!r})"
        )


class _StreamLimitExceeded(Exception):
    pass


class _ProcessTimedOut(Exception):
    pass


class SshEnrollmentAdapter:
    def __init__(
        self,
        *,
        target_policy: AgentTargetPolicy,
        executable: Path = Path("/usr/bin/ssh"),
        connect_timeout_seconds: int = 10,
        total_timeout_seconds: float = 15,
        clock=None,
        termination_grace_seconds: float = 0.5,
    ) -> None:
        self._target_policy = target_policy
        self._executable = executable
        self._connect_timeout_seconds = connect_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._termination_grace_seconds = termination_grace_seconds
        self.healthy = False

    async def check_available(self) -> bool:
        self.healthy = False
        try:
            _validate_executable(self._executable)
            profile = TrustedLanHttpProfile(
                id="ssh-runtime-check", allowed_cidrs=["10.0.0.0/8"]
            )
            actual = build_ssh_argv(
                executable=self._executable,
                pinned_address=_parse_ip("10.0.0.2"),
                user="ic_env_guard_probe",
                host="runtime-check.invalid",
                port=22,
                profile=profile,
                connect_timeout_seconds=self._connect_timeout_seconds,
                batch_mode=True,
            )
            output, _stderr, returncode = await self._execute(
                build_ssh_preflight_argv(actual),
                stdin_payload=b"",
                stdout_limit=MAX_EFFECTIVE_CONFIG_BYTES,
                stderr_limit=MAX_HELPER_STDERR_BYTES,
                timeout=min(self._total_timeout_seconds, 5.0),
            )
            if returncode != 0:
                return False
            verify_effective_config(
                output,
                SshEffectiveTarget(
                    pinned_address="10.0.0.2",
                    user="ic_env_guard_probe",
                    port=22,
                    host_key_alias="[runtime-check.invalid]:22",
                    strict_host_key_checking="accept-new",
                    batch_mode=True,
                    connect_timeout_seconds=self._connect_timeout_seconds,
                ),
            )
        except (OSError, SshConfigError, _StreamLimitExceeded, _ProcessTimedOut):
            return False
        self.healthy = True
        return True

    async def issue(
        self, request: SshEnrollmentRequest, profile: TransportProfile
    ) -> EnrollmentHelperResult:
        try:
            user, host, port = validate_ssh_destination(
                user=request.ssh_user,
                host=request.ssh_host,
                port=request.ssh_port,
            )
            _canonical_uuid(request.manager_id)
            _canonical_uuid(request.enrollment_id)
            scheme = "http" if isinstance(profile, TrustedLanHttpProfile) else "https"
            url_host = f"[{host}]" if ":" in host else host
            target = self._target_policy.resolve(f"{scheme}://{url_host}:{port}", profile)
            actual = build_ssh_argv(
                executable=self._executable,
                pinned_address=target.pinned_address,
                user=user,
                host=host,
                port=port,
                profile=profile,
                connect_timeout_seconds=self._connect_timeout_seconds,
                batch_mode=True,
            )
            preflight, _stderr, returncode = await self._execute(
                build_ssh_preflight_argv(actual),
                stdin_payload=b"",
                stdout_limit=MAX_EFFECTIVE_CONFIG_BYTES,
                stderr_limit=MAX_HELPER_STDERR_BYTES,
                timeout=min(self._total_timeout_seconds, 5.0),
            )
            if returncode != 0:
                raise SshEnrollmentError("ssh_unavailable")
            strict = "accept-new" if isinstance(profile, TrustedLanHttpProfile) else "yes"
            verify_effective_config(
                preflight,
                SshEffectiveTarget(
                    pinned_address=str(target.pinned_address),
                    user=user,
                    port=port,
                    host_key_alias=host_key_alias(host, port),
                    strict_host_key_checking=strict,
                    batch_mode=True,
                    connect_timeout_seconds=self._connect_timeout_seconds,
                ),
            )
            stdin_payload = _request_payload(request)
        except (
            SshConfigError,
            TargetPolicyError,
            OSError,
            ValueError,
            _StreamLimitExceeded,
            _ProcessTimedOut,
        ):
            raise SshEnrollmentError("ssh_unavailable") from None
        try:
            stdout, stderr, returncode = await self._execute(
                actual,
                stdin_payload=stdin_payload,
                stdout_limit=MAX_HELPER_STDOUT_BYTES,
                stderr_limit=MAX_HELPER_STDERR_BYTES,
                timeout=self._total_timeout_seconds,
            )
        except (OSError, _StreamLimitExceeded, _ProcessTimedOut):
            raise SshEnrollmentError(
                "ssh_remote_command_failed", dispatch_state="dispatched"
            ) from None
        if returncode != 0:
            raise SshEnrollmentError(
                _classify_failure(stderr), dispatch_state="dispatched"
            )
        try:
            return _parse_helper_result(stdout, request=request, now=self._clock())
        except (TypeError, ValueError):
            raise SshEnrollmentError(
                "ssh_remote_command_failed", dispatch_state="dispatched"
            ) from None

    async def _execute(
        self,
        argv: tuple[str, ...],
        *,
        stdin_payload: bytes,
        stdout_limit: int,
        stderr_limit: int,
        timeout: float,
    ) -> tuple[bytes, bytes, int]:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=_ssh_environment(),
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        coroutines = (
            _write_stdin(process.stdin, stdin_payload),
            _read_bounded(process.stdout, stdout_limit),
            _read_bounded(process.stderr, stderr_limit),
            process.wait(),
        )
        tasks: list[asyncio.Task[Any]] = []
        loop = asyncio.get_running_loop()
        try:
            try:
                for coroutine in coroutines:
                    tasks.append(loop.create_task(coroutine))
            except BaseException:
                for coroutine in coroutines[len(tasks) :]:
                    coroutine.close()
                raise
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=timeout
                )
            except TimeoutError:
                raise _ProcessTimedOut from None
            return results[1], results[2], results[3]
        except BaseException:
            await self._stop_process(process)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            if process.returncode is not None:
                # asyncio exposes no public subprocess transport close method. All
                # pipes/tasks are already drained and awaited at this point.
                process._transport.close()  # type: ignore[attr-defined]
                await asyncio.sleep(0)

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        _signal_process_group(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self._termination_grace_seconds
            )
        except TimeoutError:
            _signal_process_group(process.pid, signal.SIGKILL)
            await process.wait()


async def _write_stdin(writer: asyncio.StreamWriter, payload: bytes) -> None:
    try:
        writer.write(payload)
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def _read_bounded(reader: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await reader.read(min(4096, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise _StreamLimitExceeded
        chunks.append(chunk)


def _request_payload(request: SshEnrollmentRequest) -> bytes:
    payload = json.dumps(
        {
            "protocol": _PROTOCOL,
            "manager_id": request.manager_id,
            "enrollment_id": request.enrollment_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(payload) > MAX_HELPER_STDIN_BYTES:
        raise ValueError
    return payload


def _parse_helper_result(
    output: bytes, *, request: SshEnrollmentRequest, now: datetime
) -> EnrollmentHelperResult:
    if len(output) > MAX_HELPER_STDOUT_BYTES:
        raise ValueError
    decoded = output.decode("utf-8")
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(decoded)
    if decoded[end:].strip() or not isinstance(value, dict) or set(value) != {
        "protocol",
        "instance_id",
        "credential_id",
        "token",
        "expires_at",
    }:
        raise ValueError
    if value["protocol"] != _PROTOCOL:
        raise ValueError
    instance_id = _canonical_uuid(value["instance_id"])
    credential_id = _canonical_uuid(value["credential_id"])
    token = value["token"]
    if (
        not isinstance(token, str)
        or not 32 <= len(token.encode()) <= 4096
        or any(ord(character) < 0x20 for character in token)
    ):
        raise ValueError
    expires_at = _parse_time(value["expires_at"])
    if now.tzinfo is None or not now < expires_at <= request.expires_at:
        raise ValueError
    return EnrollmentHelperResult(
        instance_id=instance_id,
        credential_id=credential_id,
        token=token.encode(),
        expires_at=expires_at,
    )


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError
    return value


def _classify_failure(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").lower()
    if "remote host identification has changed" in text or "offending" in text:
        return "ssh_host_key_changed"
    if "host key is known" in text or "host key verification failed" in text:
        return "ssh_host_key_unknown"
    if any(
        marker in text
        for marker in (
            "enter passphrase",
            "password is required",
            "a password is required",
            "keyboard-interactive",
            "read_passphrase",
            "a terminal is required",
        )
    ):
        return "ssh_interaction_required"
    if "permission denied" in text:
        return "ssh_auth_failed"
    return "ssh_remote_command_failed"


def _validate_executable(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise OSError
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise OSError


def _ssh_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in ("HOME", "USER", "LOGNAME", "SSH_AUTH_SOCK", "SSH_SK_PROVIDER"):
        value = os.environ.get(key)
        if value is not None and _safe_environment_value(value):
            environment[key] = value
    return environment


def _safe_environment_value(value: str) -> bool:
    return 0 < len(value.encode("utf-8")) <= 4096 and all(
        ord(character) >= 0x20 and character != "\x7f" for character in value
    )


def _signal_process_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _parse_ip(value: str):
    from ipaddress import ip_address

    return ip_address(value)

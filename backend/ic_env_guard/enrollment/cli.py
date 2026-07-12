import asyncio
import json
import os
import re
import signal
import socket
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any, TextIO

from ic_env_guard.enrollment.protocol import parse_response
from ic_env_guard.enrollment.ssh import (
    MAX_HELPER_STDERR_BYTES,
    MAX_HELPER_STDOUT_BYTES,
    _read_bounded,
    _signal_process_group,
    _write_stdin,
)
from ic_env_guard.enrollment.ssh_config import (
    REMOTE_ENROLLMENT_COMMAND,
    SshConfigError,
    SshEffectiveTarget,
    build_ssh_preflight_argv,
    host_key_alias,
    validate_ssh_destination,
    verify_effective_config,
)
from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile

_BRACKETED = re.compile(r"^(?P<user>[^@]+)@\[(?P<host>[^]]+)](?::(?P<port>[0-9]+))?$")
_PLAIN = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+)(?::(?P<port>[0-9]+))?$")


class CliEnrollmentError(ValueError):
    pass


class CliSshRunner:
    async def run(
        self,
        argv: tuple[str, ...],
        request: bytes,
        **route: object,
    ) -> bytes:
        return await _run_cli_ssh(argv, request, **route)


def parse_ssh_argument(value: str) -> tuple[str, str, int]:
    if not isinstance(value, str) or any(ord(character) < 0x21 for character in value):
        raise CliEnrollmentError("ssh_target_invalid")
    match = _BRACKETED.fullmatch(value) or _PLAIN.fullmatch(value)
    if match is None:
        raise CliEnrollmentError("ssh_target_invalid")
    try:
        port = int(match.group("port") or "22")
        return validate_ssh_destination(
            user=match.group("user"), host=match.group("host"), port=port
        )
    except (ValueError, SshConfigError):
        raise CliEnrollmentError("ssh_target_invalid") from None


def build_cli_ssh_argv(
    *,
    executable: Path,
    pinned_address: IPv4Address | IPv6Address,
    user: str,
    host: str,
    port: int,
    profile: TransportProfile,
    connect_timeout_seconds: int,
    strict_host_key_checking: str | None = None,
) -> tuple[str, ...]:
    try:
        user, host, port = validate_ssh_destination(user=user, host=host, port=port)
    except SshConfigError:
        raise CliEnrollmentError("ssh_target_invalid") from None
    if not executable.is_absolute() or not 1 <= connect_timeout_seconds <= 60:
        raise CliEnrollmentError("ssh_unavailable")
    strict = strict_host_key_checking or (
        "accept-new" if isinstance(profile, TrustedLanHttpProfile) else "ask"
    )
    if strict not in {"ask", "accept-new"}:
        raise CliEnrollmentError("ssh_unavailable")
    options = (
        f"Hostname={pinned_address}",
        f"User={user}",
        f"Port={port}",
        f"HostKeyAlias={host_key_alias(host, port)}",
        f"StrictHostKeyChecking={strict}",
        "BatchMode=no",
        "PreferredAuthentications=publickey",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ChallengeResponseAuthentication=no",
        "NumberOfPasswordPrompts=0",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ProxyUseFdpass=no",
        "ClearAllForwardings=yes",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ForwardX11Trusted=no",
        "RequestTTY=no",
        "PermitLocalCommand=no",
        "LocalCommand=none",
        "RemoteCommand=none",
        "KnownHostsCommand=none",
        "CanonicalizeHostname=no",
        "ControlMaster=no",
        "ControlPath=none",
        "ControlPersist=no",
        "ConnectionAttempts=1",
        f"ConnectTimeout={connect_timeout_seconds}",
        "LogLevel=ERROR",
        "SendEnv=-*",
    )
    argv: list[str] = [str(executable)]
    for option in options:
        argv.extend(("-o", option))
    argv.extend((host, REMOTE_ENROLLMENT_COMMAND))
    return tuple(argv)


def run_cli_enrollment(
    *,
    manager_socket: Path,
    enrollment_id: str,
    ssh: str,
    stdout: TextIO,
    stderr: TextIO,
    executable: Path = Path("/usr/bin/ssh"),
    connect_timeout_seconds: int = 10,
    total_timeout_seconds: float = 120,
    runner: CliSshRunner | None = None,
) -> int:
    try:
        user, host, port = parse_ssh_argument(ssh)
        pinned = _resolve_cli_address(host, port)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(manager_socket))
            _send_object(
                client,
                {
                    "protocol": "manager-cli-enrollment.header.v1",
                    "enrollment_id": enrollment_id,
                    "ssh": ssh,
                    "pinned_address": str(pinned),
                },
            )
            ready = _read_object(client, 4096)
            if set(ready) != {
                "protocol",
                "manager_id",
                "enrollment_id",
                "input_fingerprint",
                "nonce",
                "expires_at",
                "host_key_policy",
            } or ready.get("protocol") != "manager-cli-enrollment.ready.v1":
                raise CliEnrollmentError("enrollment_rejected")
            profile = TrustedLanHttpProfile(
                id="cli", allowed_cidrs=["10.0.0.0/8", "fc00::/7"]
            )
            argv = build_cli_ssh_argv(
                executable=executable,
                pinned_address=pinned,
                user=user,
                host=host,
                port=port,
                profile=profile,
                connect_timeout_seconds=connect_timeout_seconds,
                strict_host_key_checking=ready["host_key_policy"],
            )
            request = json.dumps(
                {
                    "protocol": "manager-enrollment.v1",
                    "manager_id": ready["manager_id"],
                    "enrollment_id": ready["enrollment_id"],
                },
                separators=(",", ":"),
            ).encode()
            helper_payload = asyncio.run(
                (runner or CliSshRunner()).run(
                    argv,
                    request,
                    host=host,
                    user=user,
                    port=port,
                    pinned=str(pinned),
                    strict=ready["host_key_policy"],
                    connect_timeout_seconds=connect_timeout_seconds,
                    total_timeout_seconds=total_timeout_seconds,
                )
            )
            helper = parse_response(helper_payload)
            _send_object(
                client,
                {
                    "protocol": "manager-cli-enrollment.result.v1",
                    "input_fingerprint": ready["input_fingerprint"],
                    "nonce": ready["nonce"],
                    "helper": helper.model_dump(mode="json"),
                },
            )
            client.shutdown(socket.SHUT_WR)
            response = _read_object(client, 4096)
            if response.get("status") != "verified":
                raise CliEnrollmentError("enrollment_rejected")
        stdout.write("Enrollment verified.\n")
        stdout.flush()
        return 0
    except (OSError, ValueError, KeyError, CliEnrollmentError, SshConfigError):
        stderr.write("ic-env-guardctl: enrollment failed\n")
        stderr.flush()
        return 1


async def _run_cli_ssh(
    argv: tuple[str, ...],
    request: bytes,
    *,
    host: str,
    user: str,
    port: int,
    pinned: str,
    strict: str,
    connect_timeout_seconds: int,
    total_timeout_seconds: float,
) -> bytes:
    preflight, _stderr, code = await _run_process(
        build_ssh_preflight_argv(argv), b"", 32 * 1024, 8192, 5
    )
    if code != 0:
        raise CliEnrollmentError("ssh_unavailable")
    verify_effective_config(
        preflight,
        SshEffectiveTarget(
            pinned_address=pinned,
            user=user,
            port=port,
            host_key_alias=host_key_alias(host, port),
            strict_host_key_checking=strict,
            batch_mode=False,
            connect_timeout_seconds=connect_timeout_seconds,
            user_known_hosts_file="",
        ),
    )
    output, _stderr, code = await _run_process(
        argv,
        request,
        MAX_HELPER_STDOUT_BYTES,
        MAX_HELPER_STDERR_BYTES,
        total_timeout_seconds,
    )
    if code != 0:
        raise CliEnrollmentError("ssh_remote_command_failed")
    return output


async def _run_process(
    argv: tuple[str, ...], stdin: bytes, stdout_limit: int, stderr_limit: int, timeout: float
) -> tuple[bytes, bytes, int]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=dict(os.environ),
    )
    assert process.stdin and process.stdout and process.stderr
    tasks = (
        asyncio.create_task(_write_stdin(process.stdin, stdin)),
        asyncio.create_task(_read_bounded(process.stdout, stdout_limit)),
        asyncio.create_task(_read_bounded(process.stderr, stderr_limit)),
        asyncio.create_task(process.wait()),
    )
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        return results[1], results[2], results[3]
    except BaseException:
        if process.returncode is None:
            _signal_process_group(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 0.5)
            except TimeoutError:
                _signal_process_group(process.pid, signal.SIGKILL)
                await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _resolve_cli_address(host: str, port: int) -> IPv4Address | IPv6Address:
    try:
        literal = ip_address(host)
        addresses = {literal}
    except ValueError:
        addresses = {
            ip_address(result[4][0])
            for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    if not addresses:
        raise CliEnrollmentError("ssh_unavailable")
    if any(
        address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        for address in addresses
    ):
        raise CliEnrollmentError("ssh_unavailable")
    return sorted(addresses, key=lambda address: (address.version, int(address)))[0]


def _send_object(client: socket.socket, value: dict[str, object]) -> None:
    client.sendall(json.dumps(value, separators=(",", ":")).encode() + b"\n")


def _read_object(client: socket.socket, limit: int) -> dict[str, object]:
    data = bytearray()
    while len(data) <= limit:
        chunk = client.recv(1)
        if not chunk:
            break
        data += chunk
        if chunk == b"\n":
            break
    if len(data) > limit or not data.endswith(b"\n"):
        raise CliEnrollmentError("invalid_response")
    value = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise CliEnrollmentError("invalid_response")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CliEnrollmentError("invalid_response")
        value[key] = item
    return value

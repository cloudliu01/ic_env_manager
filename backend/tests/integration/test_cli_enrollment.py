import json
import shutil
import socket
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

import pytest

from ic_env_guard.enrollment.cli import (
    CliEnrollmentError,
    CliSshRunner,
    _resolve_cli_address,
    build_cli_ssh_argv,
    parse_ssh_argument,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile
from ic_env_guard.systemd.cli import build_ctl_parser, ctl_main


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("edaops@agent.example", ("edaops", "agent.example", 22)),
        ("edaops@agent.example:2222", ("edaops", "agent.example", 2222)),
        ("edaops@[2001:db8::20]", ("edaops", "2001:db8::20", 22)),
        ("edaops@[2001:db8::20]:2222", ("edaops", "2001:db8::20", 2222)),
    ),
)
def test_cli_ssh_argument_is_exact_and_canonical(value, expected):
    assert parse_ssh_argument(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "-oProxyCommand=x@agent.example",
        "edaops@-Fattacker",
        "edaops@2001:db8::20",
        "edaops@[2001:db8::20]extra",
        "edaops@agent.example:0",
        "edaops@agent.example:65536",
        "edaops@agent.example;touch",
        "edaops@agent.example\ncommand",
        "edaops@agent.example extra-command",
    ),
)
def test_cli_ssh_argument_rejects_options_metacharacters_and_extra_command(value):
    with pytest.raises(CliEnrollmentError, match="ssh_target_invalid"):
        parse_ssh_argument(value)


def test_cli_verified_argv_uses_user_config_but_pins_route_and_remote_command():
    argv = build_cli_ssh_argv(
        executable=Path("/usr/bin/ssh"),
        pinned_address=ip_address("10.20.30.40"),
        user="edaops",
        host="agent.example",
        port=2222,
        profile=VerifiedTlsProfile(id="tls"),
        connect_timeout_seconds=10,
    )

    assert "-F" not in argv
    assert "Hostname=10.20.30.40" in argv
    assert "StrictHostKeyChecking=ask" in argv
    assert "BatchMode=no" in argv
    assert "RequestTTY=no" in argv
    assert argv[-2:] == ("agent.example", "ic-env-guard agent enroll-manager")


def test_cli_trusted_argv_accepts_first_host_key_but_keeps_remote_pty_disabled():
    argv = build_cli_ssh_argv(
        executable=Path("/usr/bin/ssh"),
        pinned_address=ip_address("10.20.30.40"),
        user="edaops",
        host="agent.example",
        port=22,
        profile=TrustedLanHttpProfile(id="lan", allowed_cidrs=["10.0.0.0/8"]),
        connect_timeout_seconds=10,
    )

    assert "StrictHostKeyChecking=accept-new" in argv
    assert "RequestTTY=no" in argv


@pytest.mark.parametrize("host", ("127.0.0.1", "0.0.0.0", "169.254.169.254", "::1"))
def test_cli_pin_selection_rejects_obviously_forbidden_addresses(host):
    with pytest.raises(CliEnrollmentError, match="ssh_unavailable"):
        _resolve_cli_address(host, 22)


def test_guardctl_accepts_only_exact_agent_enroll_shape():
    args = build_ctl_parser().parse_args(
        [
            "agent",
            "enroll",
            "--manager-socket",
            "/run/ic-env-guard/manager-enrollment.sock",
            "--enrollment-id",
            "22222222-2222-4222-8222-222222222222",
            "--ssh",
            "edaops@agent.example:2222",
        ]
    )

    assert args.command == "agent"
    assert args.agent_command == "enroll"
    assert args.ssh == "edaops@agent.example:2222"


def test_guardctl_real_unix_framing_keeps_token_out_of_cli_surfaces(capsys):
    token = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
    socket_dir = Path(tempfile.mkdtemp(prefix="ieg-cli-", dir="/tmp"))
    socket_path = socket_dir / "manager.sock"
    ready = threading.Event()
    received = []

    def manager():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(2)
            ready.set()
            with listener.accept()[0] as connection:
                header = _socket_line(connection)
                received.append(header)
                connection.sendall(
                    json.dumps(
                        {
                            "protocol": "manager-cli-enrollment.ready.v1",
                            "manager_id": "11111111-1111-4111-8111-111111111111",
                            "enrollment_id": header["enrollment_id"],
                            "input_fingerprint": "f" * 64,
                            "nonce": "nonce-1",
                            "expires_at": (
                                datetime.now(UTC) + timedelta(minutes=5)
                            ).isoformat(),
                            "host_key_policy": "ask",
                        }
                    ).encode()
                    + b"\n"
                )
                result = _socket_line(connection)
                received.append(result)
                assert connection.recv(1) == b""
                connection.sendall(b'{"status":"verified"}\n')
            with listener.accept()[0] as connection:
                received.append(_socket_line(connection))
                connection.sendall(b'{"error":"replayed_enrollment"}\n')

    class Runner(CliSshRunner):
        def __init__(self):
            self.argv = ()
            self.request = b""

        async def run(self, argv, request, **_route):
            self.argv = argv
            self.request = request
            return (
                json.dumps(
                    {
                        "protocol": "manager-enrollment.v1",
                        "instance_id": "33333333-3333-4333-8333-333333333333",
                        "credential_id": "44444444-4444-4444-8444-444444444444",
                        "token": token,
                        "expires_at": (
                            datetime.now(UTC) + timedelta(minutes=4)
                        ).isoformat(),
                    },
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )

    thread = threading.Thread(target=manager)
    thread.start()
    assert ready.wait(2)
    runner = Runner()
    arguments = [
        "agent",
        "enroll",
        "--manager-socket",
        str(socket_path),
        "--enrollment-id",
        "01J2A3B4C5D6E7F8G9H0JKMNPQ",
        "--ssh",
        "edaops@10.20.30.40:2222",
    ]

    assert ctl_main(arguments, runner=runner) == 0
    assert ctl_main(arguments, runner=runner) == 1
    thread.join(2)
    assert not thread.is_alive()
    output = capsys.readouterr()
    assert output.out == "Enrollment verified.\n"
    assert token not in output.out
    assert token not in output.err
    assert all(token not in value for value in runner.argv)
    assert token.encode() not in runner.request
    assert received[1]["helper"]["token"] == token
    assert sum(token in json.dumps(value) for value in received) == 1
    shutil.rmtree(socket_dir)


def _socket_line(connection):
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = connection.recv(1)
        if not chunk:
            break
        payload += chunk
    return json.loads(payload)

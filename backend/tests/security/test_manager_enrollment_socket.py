import asyncio
import json
import os
import shutil
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ic_env_guard.enrollment.manager_socket import (
    MAX_HEADER_BYTES,
    ManagerEnrollmentSocket,
    ManagerSocketError,
)
from ic_env_guard.fleet.transport import VerifiedTlsProfile


class Orchestrator:
    def __init__(self):
        self.begins = []
        self.completes = []
        self.aborts = []
        self.local_bootstraps = []

    def begin_cli_submission(self, **kwargs):
        self.begins.append(kwargs)
        job = SimpleNamespace(
            manager_id="11111111-1111-4111-8111-111111111111",
            enrollment_id=kwargs["enrollment_id"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        return SimpleNamespace(
            job=job,
            target=SimpleNamespace(profile=VerifiedTlsProfile(id="tls")),
            input_fingerprint="f" * 64,
            nonce="nonce-1",
            already_accepted=kwargs.get("resume_nonce") == "nonce-1",
        )

    async def complete_cli_submission(self, claim, **kwargs):
        self.completes.append((claim, kwargs))
        return SimpleNamespace(job=claim.job)

    def release_cli_connection(self, claim, *, result_received, code):
        self.aborts.append((claim, result_received, code))

    async def bootstrap_local(self, request, context):
        self.local_bootstraps.append((request, context))
        return SimpleNamespace(agent_id=request.agent_id, revision=1)


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="ieg-manager-", dir="/tmp"))
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def _line(reader):
    return json.loads(await reader.readline())


def _local_request(socket_dir):
    return {
        "protocol": "manager-local-bootstrap.request.v1",
        "agent_id": "local-agent",
        "display_name": "Local development agent",
        "base_url": "http://127.0.0.1:8766",
        "transport_profile_id": "local-loopback-http",
        "agent_socket_path": str(socket_dir / "agent-enrollment.sock"),
    }


async def _send_local_frame(server, payload):
    reader, writer = await asyncio.open_unix_connection(server.path)
    writer.write(payload)
    await writer.drain()
    writer.write_eof()
    response = await _line(reader)
    assert await reader.read() == b""
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.security
async def test_manager_socket_owner_can_bootstrap_local_agent(socket_dir):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
    )
    await server.start()
    request = _local_request(socket_dir)
    try:
        response = await _send_local_frame(
            server,
            json.dumps(request, separators=(",", ":")).encode() + b"\n",
        )
    finally:
        await server.stop()

    assert response == {
        "protocol": "manager-local-bootstrap.result.v1",
        "status": "enrolled",
        "agent_id": "local-agent",
        "revision": 1,
    }
    assert len(orchestrator.local_bootstraps) == 1
    submitted, context = orchestrator.local_bootstraps[0]
    assert vars(submitted) == {
        "agent_id": "local-agent",
        "display_name": "Local development agent",
        "base_url": "http://127.0.0.1:8766",
        "transport_profile_id": "local-loopback-http",
        "agent_socket_path": socket_dir / "agent-enrollment.sock",
    }
    assert context.actor_id == f"local-cli:{os.geteuid()}"
    assert context.source_addr == "local-unix"


@pytest.mark.security
@pytest.mark.parametrize(
    "payload_mutator",
    (
        lambda request, _root: {**request, "unexpected": "value"},
        lambda request, root: {
            **request,
            "agent_socket_path": str(root.parent / "outside" / "agent.sock"),
        },
        lambda request, _root: {
            **request,
            "base_url": "http://192.0.2.10:8766",
        },
    ),
    ids=("extra-key", "outside-socket-root", "non-loopback-url"),
)
async def test_manager_local_bootstrap_rejects_invalid_request_without_dispatch(
    socket_dir, payload_mutator
):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
    )
    await server.start()
    request = payload_mutator(_local_request(socket_dir), socket_dir)
    try:
        response = await _send_local_frame(
            server,
            json.dumps(request, separators=(",", ":")).encode() + b"\n",
        )
    finally:
        await server.stop()

    assert response == {"error": "invalid_request"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
@pytest.mark.parametrize(
    "payload",
    (
        b'{"protocol":"manager-local-bootstrap.request.v1",'
        b'"agent_id":"local-agent","agent_id":"other"}\n',
        b"{" + (b"x" * MAX_HEADER_BYTES) + b"}\n",
    ),
    ids=("duplicate-key", "oversized-frame"),
)
async def test_manager_local_bootstrap_rejects_unsafe_frame_without_dispatch(
    socket_dir, payload
):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
    )
    await server.start()
    try:
        response = await _send_local_frame(server, payload)
    finally:
        await server.stop()

    assert response == {"error": "invalid_request"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
async def test_manager_local_bootstrap_disabled_gate_does_not_dispatch(socket_dir):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=False,
    )
    await server.start()
    try:
        response = await _send_local_frame(
            server,
            json.dumps(_local_request(socket_dir)).encode() + b"\n",
        )
    finally:
        await server.stop()

    assert response == {"error": "invalid_request"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
async def test_manager_local_bootstrap_rejects_group_only_peer(socket_dir):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o660,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        allowed_gid=os.getegid(),
        peer_credentials=lambda _socket: (123, os.geteuid() + 1, os.getegid()),
        local_bootstrap_enabled=True,
    )
    await server.start()
    try:
        response = await _send_local_frame(
            server,
            json.dumps(_local_request(socket_dir)).encode() + b"\n",
        )
    finally:
        await server.stop()

    assert response == {"error": "unauthorized_peer"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
async def test_manager_socket_authenticates_before_header_and_returns_no_secret(socket_dir):
    directory = socket_dir
    path = directory / "enrollment.sock"
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=path,
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(
            json.dumps(
                {
                    "protocol": "manager-cli-enrollment.header.v1",
                    "enrollment_id": "enrollment-1",
                    "ssh": "edaops@agent.example:2222",
                    "pinned_address": "10.20.30.40",
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        ready = await _line(reader)
        assert ready["protocol"] == "manager-cli-enrollment.ready.v1"
        token = "must-never-be-returned"
        writer.write(
            json.dumps(
                {
                    "protocol": "manager-cli-enrollment.result.v1",
                    "input_fingerprint": "f" * 64,
                    "nonce": "nonce-1",
                    "helper": {"token": token},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.write_eof()
        response_bytes = await reader.readline()
        assert json.loads(response_bytes) == {
            "status": "verified",
            "enrollment_id": "enrollment-1",
        }
        assert token.encode() not in response_bytes
        writer.close()
        await writer.wait_closed()
        assert len(orchestrator.completes) == 1
        metadata = os.lstat(path)
        assert stat.S_ISSOCK(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o600
    finally:
        await server.stop()
    assert not path.exists()


@pytest.mark.security
async def test_manager_socket_rejects_peer_before_reading_payload(socket_dir):
    directory = socket_dir
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=directory / "enrollment.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: False,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.path)
        assert await _line(reader) == {"error": "unauthorized_peer"}
        assert orchestrator.begins == []
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.security
async def test_manager_socket_does_not_unlink_replaced_path(socket_dir):
    directory = socket_dir
    path = directory / "enrollment.sock"
    server = ManagerEnrollmentSocket(
        path=path,
        mode=0o600,
        orchestrator=Orchestrator(),
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
    )
    await server.start()
    path.unlink()
    path.write_text("replacement")

    await server.stop()

    assert path.read_text() == "replacement"


@pytest.mark.security
@pytest.mark.parametrize("problem", ("mode", "symlink"))
async def test_manager_socket_parent_fails_closed(tmp_path, problem):
    directory = tmp_path / "manager-runtime"
    directory.mkdir(mode=0o700)
    if problem == "mode":
        directory.chmod(0o770)
        path = directory / "enrollment.sock"
    else:
        real = tmp_path / "real-runtime"
        real.mkdir(mode=0o700)
        directory.rmdir()
        directory.symlink_to(real, target_is_directory=True)
        path = directory / "enrollment.sock"
    server = ManagerEnrollmentSocket(
        path=path,
        mode=0o600,
        orchestrator=Orchestrator(),
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
    )

    with pytest.raises(ManagerSocketError, match="parent is unsafe"):
        await server.start()


@pytest.mark.security
def test_manager_socket_group_authorization_uses_primary_gid_only(tmp_path):
    primary = ManagerEnrollmentSocket(
        path=tmp_path / "primary.sock",
        mode=0o660,
        orchestrator=Orchestrator(),
        allowed_uid=501,
        allowed_gid=700,
        peer_credentials=lambda _socket: (123, 999, 700),
    )
    supplementary_only = ManagerEnrollmentSocket(
        path=tmp_path / "supplementary.sock",
        mode=0o660,
        orchestrator=Orchestrator(),
        allowed_uid=501,
        allowed_gid=700,
        peer_credentials=lambda _socket: (123, 999, 701),
    )

    assert primary._authorize_peer(object()) is True
    assert supplementary_only._authorize_peer(object()) is False


@pytest.mark.security
async def test_manager_socket_resume_returns_accepted_before_reading_result(socket_dir):
    orchestrator = Orchestrator()
    server = ManagerEnrollmentSocket(
        path=socket_dir / "accepted.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.path)
        writer.write(
            json.dumps(
                {
                    "protocol": "manager-cli-enrollment.header.v1",
                    "enrollment_id": "enrollment-1",
                    "ssh": "edaops@agent.example:2222",
                    "pinned_address": "10.20.30.40",
                    "resume_nonce": "nonce-1",
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()

        assert await _line(reader) == {
            "protocol": "manager-cli-enrollment.accepted.v1",
            "status": "already_accepted",
            "enrollment_id": "enrollment-1",
        }
        assert await reader.read() == b""
        assert orchestrator.completes == []
        assert orchestrator.aborts[-1][1:] == (True, "already_accepted")
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()

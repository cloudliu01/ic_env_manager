import asyncio
import json
import os
import shutil
import socket
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

import ic_env_guard.enrollment.local_socket as local_socket_module
from ic_env_guard.enrollment.local_socket import LocalEnrollmentSocketClient
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


def _owner_socket(path, *, mode=0o600):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(mode)
    return listener


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
    agent_listener = _owner_socket(socket_dir / "agent-enrollment.sock")
    local_socket_client = LocalEnrollmentSocketClient(socket_dir)
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
        local_socket_client=local_socket_client,
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
        agent_listener.close()

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
    agent_listener = _owner_socket(socket_dir / "agent-enrollment.sock")
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
        local_socket_client=LocalEnrollmentSocketClient(socket_dir),
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
        agent_listener.close()

    assert response == {"error": "invalid_request"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
@pytest.mark.parametrize(
    "unsafe_leaf",
    ("nul", "symlink", "missing", "non-socket", "unsafe-mode", "foreign-owner"),
)
async def test_manager_local_bootstrap_reuses_exact_socket_preflight_before_dispatch(
    socket_dir, monkeypatch, unsafe_leaf
):
    orchestrator = Orchestrator()
    local_socket_client = LocalEnrollmentSocketClient(socket_dir)
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
        local_socket_client=local_socket_client,
    )
    listener = None
    path = socket_dir / "unsafe-agent.sock"
    if unsafe_leaf == "nul":
        submitted_path = f"{path}\x00tail"
    elif unsafe_leaf == "symlink":
        target = socket_dir / "target"
        target.write_text("not-a-socket", encoding="utf-8")
        path.symlink_to(target)
        submitted_path = str(path)
    elif unsafe_leaf == "missing":
        submitted_path = str(path)
    elif unsafe_leaf == "non-socket":
        path.write_text("not-a-socket", encoding="utf-8")
        path.chmod(0o600)
        submitted_path = str(path)
    else:
        listener = _owner_socket(
            path, mode=0o660 if unsafe_leaf == "unsafe-mode" else 0o600
        )
        submitted_path = str(path)
    await server.start()
    if unsafe_leaf == "foreign-owner":
        owner_uid = os.geteuid()
        effective_uids = iter((owner_uid, owner_uid + 1))
        monkeypatch.setattr(
            local_socket_module.os, "geteuid", lambda: next(effective_uids)
        )
    request = {**_local_request(socket_dir), "agent_socket_path": submitted_path}
    try:
        response = await _send_local_frame(
            server,
            json.dumps(request, separators=(",", ":")).encode() + b"\n",
        )
    finally:
        await server.stop()
        if listener is not None:
            listener.close()

    assert response == {"error": "invalid_request"}
    assert orchestrator.local_bootstraps == []


@pytest.mark.security
@pytest.mark.parametrize(
    "candidate_url",
    (
        "HTTP://127.0.0.1:8766",
        " http://127.0.0.1:8766",
        "\x00http://127.0.0.1:8766",
        "http://127.0.0.1:8766\n",
        "http://127.0.0.1:0",
        "http://127.0.0.1",
        "http://127.0.0.1:8766/",
        "http://127.0.0.1:8766?query=value",
        "http://127.0.0.1:8766#fragment",
        "http://user@127.0.0.1:8766",
    ),
    ids=(
        "uppercase-scheme",
        "leading-space",
        "leading-c0",
        "trailing-newline",
        "port-zero",
        "missing-port",
        "path",
        "query",
        "fragment",
        "userinfo",
    ),
)
async def test_manager_local_bootstrap_requires_exact_canonical_url_before_dispatch(
    socket_dir, candidate_url
):
    orchestrator = Orchestrator()
    agent_listener = _owner_socket(socket_dir / "agent-enrollment.sock")
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        local_bootstrap_enabled=True,
        local_socket_client=LocalEnrollmentSocketClient(socket_dir),
    )
    await server.start()
    request = {**_local_request(socket_dir), "base_url": candidate_url}
    try:
        response = await _send_local_frame(
            server,
            json.dumps(request, separators=(",", ":")).encode() + b"\n",
        )
    finally:
        await server.stop()
        agent_listener.close()

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
async def test_manager_deadline_covers_exhausted_semaphore(socket_dir):
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=Orchestrator(),
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        max_concurrency=1,
        io_timeout_seconds=1.0,
        result_timeout_seconds=0.08,
    )
    await server.start()
    first_reader, first_writer = await asyncio.open_unix_connection(server.path)
    await asyncio.sleep(0.01)
    started = monotonic()
    second_reader, second_writer = await asyncio.open_unix_connection(server.path)
    try:
        assert await asyncio.wait_for(second_reader.read(), timeout=0.25) == b""
        assert monotonic() - started < 0.2
    finally:
        first_writer.close()
        second_writer.close()
        await asyncio.gather(
            first_writer.wait_closed(),
            second_writer.wait_closed(),
            return_exceptions=True,
        )
        await server.stop()
        assert await first_reader.read() == b""


@pytest.mark.security
async def test_manager_deadline_is_not_reset_before_ssh_result_read(socket_dir):
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=Orchestrator(),
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        io_timeout_seconds=0.2,
        result_timeout_seconds=0.15,
    )
    await server.start()
    reader, writer = await asyncio.open_unix_connection(server.path)
    started = monotonic()
    await asyncio.sleep(0.1)
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
    try:
        ready = await asyncio.wait_for(_line(reader), timeout=0.1)
        assert ready["protocol"] == "manager-cli-enrollment.ready.v1"
        assert await asyncio.wait_for(reader.read(), timeout=0.12) == b""
        assert monotonic() - started < 0.21
        assert len(server.orchestrator.begins) == 1
        assert len(server.orchestrator.aborts) == 1
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.security
async def test_manager_local_deadline_bounds_cancellation_cleanup_and_never_completes(
    socket_dir,
):
    class SlowCancellationOrchestrator(Orchestrator):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.completions = 0

        async def bootstrap_local(self, request, context):
            self.local_bootstraps.append((request, context))
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await asyncio.sleep(0.15)
                self.cleaned.set()
                raise
            self.completions += 1
            return SimpleNamespace(agent_id=request.agent_id, revision=1)

    orchestrator = SlowCancellationOrchestrator()
    agent_listener = _owner_socket(socket_dir / "agent-enrollment.sock")
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        io_timeout_seconds=0.2,
        result_timeout_seconds=0.05,
        cancellation_cleanup_seconds=0.02,
        local_bootstrap_enabled=True,
        local_socket_client=LocalEnrollmentSocketClient(socket_dir),
    )
    await server.start()
    reader, writer = await asyncio.open_unix_connection(server.path)
    writer.write(json.dumps(_local_request(socket_dir)).encode() + b"\n")
    writer.write_eof()
    await writer.drain()
    started = monotonic()
    try:
        await asyncio.wait_for(orchestrator.started.wait(), timeout=0.1)
        assert await asyncio.wait_for(reader.read(), timeout=0.15) == b""
        assert monotonic() - started < 0.12
        assert orchestrator.cancelled.is_set()
        assert orchestrator.completions == 0
        await asyncio.wait_for(orchestrator.cleaned.wait(), timeout=0.3)
        assert orchestrator.completions == 0
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
        agent_listener.close()


@pytest.mark.security
async def test_manager_shutdown_cancels_local_orchestration_task(socket_dir):
    class BlockingOrchestrator(Orchestrator):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.completions = 0

        async def bootstrap_local(self, request, context):
            self.local_bootstraps.append((request, context))
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            self.completions += 1
            return SimpleNamespace(agent_id=request.agent_id, revision=1)

    orchestrator = BlockingOrchestrator()
    agent_listener = _owner_socket(socket_dir / "agent-enrollment.sock")
    server = ManagerEnrollmentSocket(
        path=socket_dir / "manager.sock",
        mode=0o600,
        orchestrator=orchestrator,
        allowed_uid=os.geteuid(),
        peer_authorizer=lambda _socket: True,
        io_timeout_seconds=0.05,
        result_timeout_seconds=1.0,
        cancellation_cleanup_seconds=0.02,
        local_bootstrap_enabled=True,
        local_socket_client=LocalEnrollmentSocketClient(socket_dir),
    )
    await server.start()
    reader, writer = await asyncio.open_unix_connection(server.path)
    writer.write(json.dumps(_local_request(socket_dir)).encode() + b"\n")
    writer.write_eof()
    await writer.drain()
    try:
        await asyncio.wait_for(orchestrator.started.wait(), timeout=0.1)
        await asyncio.wait_for(server.stop(), timeout=0.2)
        assert orchestrator.cancelled.is_set()
        assert orchestrator.completions == 0
        assert await reader.read() == b""
    finally:
        writer.close()
        await writer.wait_closed()
        if server.healthy:
            await server.stop()
        agent_listener.close()


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

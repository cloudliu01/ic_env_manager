import os
import socket
import stat
import struct
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import rmtree

import pytest

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.enrollment.protocol import EnrollmentRequest, parse_response
from ic_env_guard.enrollment.socket_server import (
    EnrollmentSocketServer,
    SocketSecurityError,
    peer_credentials,
)

MANAGER_ID = "2b576727-4f36-4f08-b90b-e8cbe98ebc80"
ENROLLMENT_ID = "01J2W4ABCDEFGHJKMNPQRSTVWX"


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="ieg-", dir="/tmp"))
    path.chmod(0o700)
    yield path
    rmtree(path, ignore_errors=True)


def _request(enrollment_id: str = ENROLLMENT_ID) -> bytes:
    return (
        EnrollmentRequest(
            protocol="manager-enrollment.v1",
            manager_id=MANAGER_ID,
            enrollment_id=enrollment_id,
        )
        .model_dump_json()
        .encode()
    )


def _exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        return client.recv(8193)


@pytest.mark.security
def test_socket_fails_closed_for_unsafe_parent_and_existing_path(tmp_path):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    server = EnrollmentSocketServer(
        unsafe / "enroll.sock", 0o600, container.instance_id, container.enrollment_service
    )
    with pytest.raises(SocketSecurityError, match="directory permissions"):
        server.start()

    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    occupied = safe / "enroll.sock"
    occupied.write_text("do not delete", encoding="utf-8")
    server = EnrollmentSocketServer(
        occupied, 0o600, container.instance_id, container.enrollment_service
    )
    with pytest.raises(SocketSecurityError, match="already exists"):
        server.start()
    assert occupied.read_text(encoding="utf-8") == "do not delete"


@pytest.mark.security
def test_socket_mode_peer_authorization_replay_and_expiry(tmp_path, socket_dir):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    server.start()
    try:
        assert server.healthy
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        first = parse_response(_exchange(path, _request()))
        assert first.token
        assert first.instance_id == container.instance_id

        replay = _exchange(path, _request())
        assert b'"error"' in replay
        assert first.token.encode() not in replay

        container.enrollment_service.pending_ttl_seconds = 60
        expiring_id = "01J2W4ABCDEFGHJKMNPQRSTVWY"
        expiring = parse_response(_exchange(path, _request(expiring_id)))
        assert expiring.expires_at <= datetime.now(UTC) + timedelta(seconds=61)

        expired_id = "01J2W4ABCDEFGHJKMNPQRSTVWZ"
        container.enrollment_service.issue_pending(
            MANAGER_ID,
            expired_id,
            now=datetime(2020, 1, 1, tzinfo=UTC),
        )
        expired_replay = _exchange(path, _request(expired_id))
        assert b'"enrollment_rejected"' in expired_replay
    finally:
        server.stop()
    assert not path.exists()


@pytest.mark.security
def test_socket_rejects_unauthorized_peer_without_issuing(tmp_path, socket_dir):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path,
        0o600,
        container.instance_id,
        container.enrollment_service,
        peer_credentials=lambda _: (os.geteuid() + 1, os.getegid()),
    )
    server.start()
    try:
        response = _exchange(path, _request())
    finally:
        server.stop()

    assert b'"unauthorized_peer"' in response
    assert container.manager_credential_repository.list_all() == ()


@pytest.mark.security
def test_stop_never_unlinks_a_replaced_socket(tmp_path, socket_dir):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    server.start()
    original = path.stat()
    path.unlink()
    path.write_text("replacement", encoding="utf-8")
    server.stop()

    assert path.read_text(encoding="utf-8") == "replacement"
    assert original.st_ino != path.stat().st_ino


@pytest.mark.security
def test_linux_peer_credentials_never_degrade_when_kernel_support_is_missing(monkeypatch):
    class Connection:
        def getsockopt(self, level, option, size):
            assert level == socket.SOL_SOCKET
            assert size == struct.calcsize("3i")
            return struct.pack("3i", 123, 456, 789)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    assert peer_credentials(Connection()) == (456, 789)  # type: ignore[arg-type]

    monkeypatch.delattr(socket, "SO_PEERCRED")
    with pytest.raises(SocketSecurityError, match="unavailable"):
        peer_credentials(Connection())  # type: ignore[arg-type]

import os
import secrets
import socket
import stat
import struct
import sys
import tempfile
import threading
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


@pytest.mark.security
def test_thread_start_failure_rolls_back_every_published_resource(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"

    class FailingThread:
        def __init__(self, **_kwargs):
            self.joined = False

        def start(self):
            raise RuntimeError("thread start failed")

        def join(self, **_kwargs):
            self.joined = True
            raise AssertionError("an unstarted thread must not be joined")

    monkeypatch.setattr(threading, "Thread", FailingThread)
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )

    with pytest.raises(RuntimeError, match="thread start failed"):
        server.start()

    assert not server.healthy
    assert server._socket is None
    assert server._thread is None
    assert server._identity is None
    assert not path.exists()
    server.stop()
    server.stop()


@pytest.mark.security
def test_thread_that_exits_before_health_publish_is_rolled_back(tmp_path, socket_dir, monkeypatch):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"

    class ExitedThread:
        def __init__(self, **_kwargs):
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return False

        def join(self, **_kwargs):
            assert self.started

    monkeypatch.setattr(threading, "Thread", ExitedThread)
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )

    with pytest.raises(SocketSecurityError, match="thread failed to start"):
        server.start()

    assert not server.healthy
    assert server._socket is None
    assert server._thread is None
    assert not path.exists()


@pytest.mark.security
def test_post_bind_chmod_failure_removes_only_owned_socket_and_allows_retry(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_chmod = os.chmod
    failed = False

    def fail_first_chmod(target, mode):
        nonlocal failed
        if Path(target).parent == socket_dir and not failed:
            failed = True
            raise PermissionError("chmod failed after bind")
        return real_chmod(target, mode)

    monkeypatch.setattr(os, "chmod", fail_first_chmod)

    with pytest.raises(PermissionError, match="chmod failed after bind"):
        server.start()

    assert not path.exists()
    assert not server.healthy
    assert server._socket is None
    assert server._thread is None
    assert server._identity is None

    server.start()
    assert server.healthy
    server.stop()


@pytest.mark.security
def test_first_post_bind_lstat_failure_is_retried_before_mutation(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_lstat = os.lstat
    failed = False

    def fail_first_lstat(target, *args, **kwargs):
        nonlocal failed
        if Path(target).parent == socket_dir and not failed:
            failed = True
            raise OSError("transient lstat failure")
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fail_first_lstat)

    server.start()
    try:
        assert server.healthy
        assert path.exists()
    finally:
        server.stop()
    assert not path.exists()


@pytest.mark.security
def test_persistent_metadata_failure_never_publishes_final_and_retry_recovers(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_lstat = os.lstat
    real_stat = os.stat
    metadata_unavailable = True

    def is_socket_candidate(target) -> bool:
        candidate = Path(target)
        return candidate.parent == socket_dir and candidate != socket_dir

    def failing_lstat(target, *args, **kwargs):
        if metadata_unavailable and is_socket_candidate(target):
            raise OSError("persistent metadata failure")
        return real_lstat(target, *args, **kwargs)

    def failing_stat(target, *args, **kwargs):
        if metadata_unavailable and is_socket_candidate(target):
            raise OSError("persistent metadata failure")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", failing_lstat)
    monkeypatch.setattr(os, "stat", failing_stat)

    with pytest.raises(OSError, match="persistent metadata failure"):
        server.start()

    assert path.name not in os.listdir(socket_dir)
    assert not server.healthy
    assert server._socket is None
    assert server._thread is None
    assert server._identity is None

    metadata_unavailable = False
    server.start()
    try:
        assert server.healthy
        assert parse_response(_exchange(path, _request())).token
    finally:
        server.stop()
    assert not path.exists()


@pytest.mark.security
def test_atomic_publish_never_overwrites_or_removes_racing_final_path(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_link = os.link

    def replace_before_link(source, destination, *args, **kwargs):
        assert Path(destination) == path
        path.write_text("replacement", encoding="utf-8")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", replace_before_link)

    try:
        with pytest.raises(FileExistsError):
            server.start()
    finally:
        if server.healthy:
            server.stop()

    assert path.read_text(encoding="utf-8") == "replacement"
    assert server._socket is None
    assert server._thread is None
    assert server._identity is None


@pytest.mark.security
@pytest.mark.skipif(sys.platform != "darwin", reason="tests the macOS sun_path limit")
def test_temporary_name_never_breaks_a_legal_near_limit_final_path(tmp_path, socket_dir):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    maximum_path_bytes = 103
    child_length = maximum_path_bytes - len(os.fsencode(socket_dir)) - 3
    parent = socket_dir / ("p" * child_length)
    parent.mkdir(mode=0o700)
    path = parent / "s"

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.bind(str(path))
    path.unlink()

    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    server.start()
    try:
        assert server.healthy
        assert parse_response(_exchange(path, _request())).token
    finally:
        server.stop()
    assert not path.exists()


@pytest.mark.security
def test_post_publish_metadata_outage_retains_ownership_until_retry_cleanup(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_link = os.link
    real_lstat = os.lstat
    real_stat = os.stat
    metadata_unavailable = False
    failed_first_publication = False

    def publish_then_fail_metadata(source, destination, *args, **kwargs):
        nonlocal metadata_unavailable, failed_first_publication
        result = real_link(source, destination, *args, **kwargs)
        if not failed_first_publication:
            failed_first_publication = True
            metadata_unavailable = True
        return result

    def failing_lstat(target, *args, **kwargs):
        if metadata_unavailable and Path(target).parent == socket_dir:
            raise OSError("metadata unavailable after publication")
        return real_lstat(target, *args, **kwargs)

    def failing_stat(target, *args, **kwargs):
        if metadata_unavailable and Path(target).parent == socket_dir:
            raise OSError("metadata unavailable after publication")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "link", publish_then_fail_metadata)
    monkeypatch.setattr(os, "lstat", failing_lstat)
    monkeypatch.setattr(os, "stat", failing_stat)

    server.start()
    assert server.healthy
    server.stop()

    assert not server.healthy
    assert path.name in os.listdir(socket_dir)
    assert server._identity is not None

    metadata_unavailable = False
    server.start()
    try:
        assert server.healthy
        assert parse_response(_exchange(path, _request("01J2W4ABCDEFGHJKMNPQRSTVW0"))).token
    finally:
        server.stop()
    assert not path.exists()


@pytest.mark.security
def test_pending_cleanup_abandons_replaced_final_without_deleting_it(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    server.start()
    path.unlink()
    path.write_text("replacement", encoding="utf-8")
    real_lstat = os.lstat
    real_stat = os.stat
    metadata_unavailable = True

    def failing_lstat(target, *args, **kwargs):
        if metadata_unavailable and Path(target) == path:
            raise OSError("metadata unavailable during stop")
        return real_lstat(target, *args, **kwargs)

    def failing_stat(target, *args, **kwargs):
        if metadata_unavailable and Path(target) == path:
            raise OSError("metadata unavailable during stop")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", failing_lstat)
    monkeypatch.setattr(os, "stat", failing_stat)

    server.stop()
    assert server._identity is not None
    metadata_unavailable = False
    server.stop()

    assert path.read_text(encoding="utf-8") == "replacement"
    assert server._identity is None


@pytest.mark.security
def test_reserved_temp_name_cannot_case_alias_a_single_character_final(
    tmp_path, socket_dir, monkeypatch
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "s"
    monkeypatch.setattr(secrets, "choice", lambda _alphabet: "S")
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )

    server.start()
    try:
        bound_name = Path(server._socket.getsockname()).name
        assert bound_name not in {"s", "S"}
        assert parse_response(_exchange(path, _request())).token
    finally:
        server.stop()


@pytest.mark.security
@pytest.mark.parametrize("reuse_server", [True, False])
def test_preidentity_metadata_outage_uses_one_reserved_temp_and_recovers(
    tmp_path, socket_dir, monkeypatch, reuse_server
):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )
    real_lstat = os.lstat
    real_stat = os.stat
    metadata_unavailable = True

    def is_candidate(target) -> bool:
        candidate = Path(target)
        return (
            candidate.parent == socket_dir
            and candidate != path
            and candidate.name in os.listdir(socket_dir)
        )

    def failing_lstat(target, *args, **kwargs):
        if metadata_unavailable and is_candidate(target):
            raise OSError("reserved temp metadata unavailable")
        return real_lstat(target, *args, **kwargs)

    def failing_stat(target, *args, **kwargs):
        if metadata_unavailable and is_candidate(target):
            raise OSError("reserved temp metadata unavailable")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", failing_lstat)
    monkeypatch.setattr(os, "stat", failing_stat)

    for _ in range(6):
        with pytest.raises((OSError, SocketSecurityError)):
            server.start()
        assert path.name not in os.listdir(socket_dir)
        assert len(os.listdir(socket_dir)) <= 1
        assert len(server._owned_paths) <= 2

    metadata_unavailable = False
    if not reuse_server:
        server = EnrollmentSocketServer(
            path, 0o600, container.instance_id, container.enrollment_service
        )
    server.start()
    try:
        assert server.healthy
        assert parse_response(_exchange(path, _request())).token
    finally:
        server.stop()
    assert os.listdir(socket_dir) == []


@pytest.mark.security
def test_reserved_temp_regular_replacement_is_never_deleted(tmp_path, socket_dir):
    container = build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")
    path = socket_dir / "enroll.sock"
    reserved = socket_dir / ("_" * len(os.fsencode(path.name)))
    reserved.write_text("replacement", encoding="utf-8")
    server = EnrollmentSocketServer(
        path, 0o600, container.instance_id, container.enrollment_service
    )

    with pytest.raises(SocketSecurityError, match="reserved temporary"):
        server.start()

    assert reserved.read_text(encoding="utf-8") == "replacement"
    assert not path.exists()

import json
import socket
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import rmtree

import pytest

import ic_env_guard.enrollment.local_socket as local_socket_module
from ic_env_guard.enrollment.local_socket import (
    LocalEnrollmentSocketClient,
    LocalEnrollmentSocketError,
)
from ic_env_guard.enrollment.protocol import (
    LOCAL_RETRY_PROTOCOL,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    EnrollmentResponse,
    encode_response,
    parse_request,
)
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import TrustedLanHttpProfile

MANAGER_ID = "11111111-1111-4111-8111-111111111111"
INSTANCE_ID = "33333333-3333-4333-8333-333333333333"
CREDENTIAL_ID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="ieg-", dir="/tmp"))
    path.chmod(0o700)
    yield path
    rmtree(path, ignore_errors=True)


def _local_target():
    return AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    ).resolve_local_socket(
        "http://127.0.0.1:8766",
        TrustedLanHttpProfile(
            id="local-loopback-http", allowed_cidrs=["127.0.0.0/8"]
        ),
    )


def _response(**changes):
    values = {
        "protocol": "manager-enrollment.v1",
        "instance_id": INSTANCE_ID,
        "credential_id": CREDENTIAL_ID,
        "token": "managed-secret",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(changes)
    return EnrollmentResponse(**values)


def _one_shot_server(
    socket_dir, response, *, response_delay=0.0, response_interval=0.0
):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.settimeout(2.0)
    socket_path = socket_dir / "enrollment.sock"
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    listener.listen(1)
    received = []

    def serve():
        with listener:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2.0)
                payload = bytearray()
                while len(payload) <= MAX_REQUEST_BYTES:
                    chunk = connection.recv(MAX_REQUEST_BYTES + 1 - len(payload))
                    if not chunk:
                        break
                    payload.extend(chunk)
                received.append(bytes(payload))
                if response_delay:
                    time.sleep(response_delay)
                try:
                    if response_interval:
                        for value in response:
                            connection.sendall(bytes((value,)))
                            time.sleep(response_interval)
                    else:
                        connection.sendall(response)
                except OSError:
                    pass

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    return server, socket_path, received


def _assert_safe_error(error, code, *forbidden):
    assert error.code == code
    assert error.args == (code,)
    assert error.__cause__ is None
    assert error.__context__ is None
    chain = []
    current = error
    while current is not None and id(current) not in {id(item) for item in chain}:
        chain.append(current)
        current = current.__cause__ or current.__context__
    rendered = " ".join(
        part
        for item in chain
        for part in (type(item).__name__, str(item), repr(item.args))
    )
    for value in forbidden:
        assert str(value) not in rendered


@pytest.mark.security
def test_local_client_exposes_synchronous_socket_path_preflight(socket_dir):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_path = socket_dir / "enrollment.sock"
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    client = LocalEnrollmentSocketClient(socket_dir)
    try:
        assert client.preflight(socket_path) is None
        socket_path.chmod(0o660)
        with pytest.raises(LocalEnrollmentSocketError) as caught:
            client.preflight(socket_path)
    finally:
        listener.close()

    _assert_safe_error(caught.value, "local_socket_path_rejected", socket_path)


@pytest.mark.security
@pytest.mark.parametrize("failure", ("missing", "symlink_loop"))
def test_local_client_constructor_maps_root_resolution_failures_without_path_leaks(
    tmp_path, failure
):
    configured_root = tmp_path / "configured-private-root"
    if failure == "symlink_loop":
        other = tmp_path / "configured-private-root-other"
        configured_root.symlink_to(other)
        other.symlink_to(configured_root)

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        LocalEnrollmentSocketClient(configured_root)

    _assert_safe_error(
        caught.value,
        "local_socket_path_rejected",
        configured_root,
        tmp_path,
    )


@pytest.mark.security
def test_local_client_constructor_maps_embedded_nul_without_path_leaks(tmp_path):
    path_fragment = "private-nul-root"
    configured_root = tmp_path / f"{path_fragment}\x00tail"

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        LocalEnrollmentSocketClient(configured_root)

    _assert_safe_error(
        caught.value,
        "local_socket_path_rejected",
        path_fragment,
        tmp_path,
    )


@pytest.mark.security
async def test_local_client_maps_invalid_request_without_retaining_validation_error(
    socket_dir,
):
    invalid_manager_id = "private-invalid-manager-id"

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_dir / "must-not-dispatch.sock",
            manager_id=invalid_manager_id,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )

    _assert_safe_error(
        caught.value,
        "local_enrollment_request_invalid",
        invalid_manager_id,
        socket_dir,
    )


@pytest.mark.security
async def test_local_client_maps_missing_socket_without_retaining_path_error(socket_dir):
    socket_path = socket_dir / "private-missing-socket.sock"

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )

    _assert_safe_error(caught.value, "local_socket_path_rejected", socket_path)


@pytest.mark.security
async def test_local_client_maps_embedded_nul_socket_without_path_leaks(socket_dir):
    path_fragment = "private-nul-socket"
    socket_path = socket_dir / f"{path_fragment}\x00tail"

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )

    _assert_safe_error(caught.value, "local_socket_path_rejected", path_fragment)


@pytest.mark.security
async def test_local_client_exchanges_one_bounded_enrollment_message(socket_dir):
    response = _response()
    server, socket_path, received = _one_shot_server(
        socket_dir, encode_response(response)
    )
    target = _local_target()

    result = await LocalEnrollmentSocketClient(socket_dir).issue(
        socket_path=socket_path,
        manager_id=MANAGER_ID,
        enrollment_id="local-agent",
        validation_target=target,
    )
    server.join(timeout=2)

    assert not server.is_alive()
    request = parse_request(received[0])
    assert request.protocol == "manager-enrollment.v1"
    assert str(request.manager_id) == MANAGER_ID
    assert request.enrollment_id == "local-agent"
    assert len(received[0]) <= MAX_REQUEST_BYTES
    assert result.instance_id == str(response.instance_id)
    assert result.credential_id == str(response.credential_id)
    assert result.token == b"managed-secret"
    assert result.expires_at == response.expires_at.replace(microsecond=0)
    assert result.validation_target is target
    assert "managed-secret" not in repr(result)


@pytest.mark.security
async def test_local_client_uses_distinct_protocol_only_for_expired_retry(socket_dir):
    server, socket_path, received = _one_shot_server(
        socket_dir, encode_response(_response())
    )

    await LocalEnrollmentSocketClient(socket_dir).issue(
        socket_path=socket_path,
        manager_id=MANAGER_ID,
        enrollment_id="local-agent",
        validation_target=_local_target(),
        retry=True,
    )
    server.join(timeout=2)

    assert parse_request(received[0]).protocol == LOCAL_RETRY_PROTOCOL


@pytest.mark.security
@pytest.mark.parametrize("unsafe_path", ("outside", "symlink", "non_socket"))
async def test_local_client_rejects_redirectable_or_non_socket_paths_before_dispatch(
    tmp_path, socket_dir, unsafe_path
):
    if unsafe_path == "outside":
        socket_path = tmp_path / "outside.sock"
        socket_path.write_text("path-secret", encoding="utf-8")
    elif unsafe_path == "symlink":
        target = socket_dir / "target"
        target.write_text("path-secret", encoding="utf-8")
        socket_path = socket_dir / "enrollment.sock"
        socket_path.symlink_to(target)
    else:
        socket_path = socket_dir / "enrollment.sock"
        socket_path.write_text("path-secret", encoding="utf-8")
    if unsafe_path != "symlink":
        socket_path.chmod(0o600)

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )

    _assert_safe_error(caught.value, "local_socket_path_rejected", "path-secret", str(socket_path))


@pytest.mark.security
@pytest.mark.parametrize("unsafe_object", ("root", "socket"))
async def test_local_client_rejects_group_or_world_access_before_dispatch(
    socket_dir, unsafe_object
):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_path = socket_dir / "enrollment.sock"
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    if unsafe_object == "root":
        socket_dir.chmod(0o710)
    else:
        socket_path.chmod(0o660)

    try:
        with pytest.raises(LocalEnrollmentSocketError) as caught:
            await LocalEnrollmentSocketClient(socket_dir).issue(
                socket_path=socket_path,
                manager_id=MANAGER_ID,
                enrollment_id="local-agent",
                validation_target=_local_target(),
            )
    finally:
        listener.close()

    _assert_safe_error(caught.value, "local_socket_path_rejected", str(socket_path))


@pytest.mark.security
@pytest.mark.parametrize("foreign_object", ("root", "socket"))
async def test_local_client_rejects_foreign_effective_user_ownership_before_dispatch(
    socket_dir, monkeypatch, foreign_object
):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_path = socket_dir / "enrollment.sock"
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    owner_uid = socket_dir.stat().st_uid
    if foreign_object == "root":
        monkeypatch.setattr(local_socket_module.os, "geteuid", lambda: owner_uid + 1)
    else:
        effective_uids = iter((owner_uid, owner_uid + 1))
        monkeypatch.setattr(
            local_socket_module.os, "geteuid", lambda: next(effective_uids)
        )

    try:
        with pytest.raises(LocalEnrollmentSocketError) as caught:
            await LocalEnrollmentSocketClient(socket_dir).issue(
                socket_path=socket_path,
                manager_id=MANAGER_ID,
                enrollment_id="local-agent",
                validation_target=_local_target(),
            )
    finally:
        listener.close()

    _assert_safe_error(caught.value, "local_socket_path_rejected", socket_path)


@pytest.mark.security
async def test_local_client_rejects_oversized_response(socket_dir):
    server, socket_path, _ = _one_shot_server(
        socket_dir, b"x" * (MAX_RESPONSE_BYTES + 1)
    )

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(caught.value, "local_socket_response_too_large", "x")


@pytest.mark.security
async def test_local_client_times_out_with_a_stable_safe_error(socket_dir):
    server, socket_path, _ = _one_shot_server(
        socket_dir, encode_response(_response()), response_delay=0.2
    )

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir, timeout_seconds=0.05).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(caught.value, "local_socket_timeout", str(socket_path))


@pytest.mark.security
async def test_local_client_maps_unrepresentable_socket_timeout_to_safe_code(
    socket_dir, monkeypatch
):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_path = socket_dir / "enrollment.sock"
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    real_socket = socket.socket
    private_detail = "private-timeout-overflow"

    class OverflowingTimeoutSocket:
        def __init__(self, *args, **kwargs):
            self._socket = real_socket(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self._socket.close()

        def __getattr__(self, name):
            return getattr(self._socket, name)

        def settimeout(self, _timeout):
            raise OverflowError(private_detail)

    monkeypatch.setattr(
        local_socket_module.socket, "socket", OverflowingTimeoutSocket
    )
    try:
        with pytest.raises(LocalEnrollmentSocketError) as caught:
            await LocalEnrollmentSocketClient(socket_dir).issue(
                socket_path=socket_path,
                manager_id=MANAGER_ID,
                enrollment_id="local-agent",
                validation_target=_local_target(),
            )
    finally:
        listener.close()

    _assert_safe_error(
        caught.value,
        "local_socket_timeout",
        private_detail,
        socket_path,
    )


@pytest.mark.security
async def test_local_client_uses_one_deadline_for_a_slow_drip_response(socket_dir):
    server, socket_path, _ = _one_shot_server(
        socket_dir, b"not-json\n", response_interval=0.02
    )

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir, timeout_seconds=0.05).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(caught.value, "local_socket_timeout", str(socket_path))


@pytest.mark.security
@pytest.mark.parametrize(
    "response",
    (
        b"not-json response-secret\n",
        json.dumps(
            {
                "protocol": "manager-enrollment.v2",
                "instance_id": INSTANCE_ID,
                "credential_id": CREDENTIAL_ID,
                "token": "response-secret",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ).encode("ascii")
        + b"\n",
    ),
)
async def test_local_client_maps_invalid_or_mismatched_response_to_safe_code(
    socket_dir, response
):
    server, socket_path, _ = _one_shot_server(socket_dir, response)

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(
        caught.value,
        "local_enrollment_protocol_error",
        "response-secret",
        response.decode("ascii"),
    )


@pytest.mark.security
async def test_local_client_rejects_expired_credential_without_revealing_token(socket_dir):
    server, socket_path, _ = _one_shot_server(
        socket_dir,
        encode_response(
            _response(
                token="expired-secret",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
    )

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(caught.value, "local_credential_expired", "expired-secret")


@pytest.mark.security
async def test_local_client_maps_non_ascii_token_to_safe_protocol_error(socket_dir):
    token = "managed-\N{SNOWMAN}-secret"
    server, socket_path, _ = _one_shot_server(
        socket_dir, encode_response(_response(token=token))
    )

    with pytest.raises(LocalEnrollmentSocketError) as caught:
        await LocalEnrollmentSocketClient(socket_dir).issue(
            socket_path=socket_path,
            manager_id=MANAGER_ID,
            enrollment_id="local-agent",
            validation_target=_local_target(),
        )
    server.join(timeout=2)

    _assert_safe_error(caught.value, "local_enrollment_protocol_error", token)

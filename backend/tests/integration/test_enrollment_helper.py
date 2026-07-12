import io
import json
import os
import tempfile
from pathlib import Path
from shutil import rmtree
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ic_env_guard.config.loader import load_config
from ic_env_guard.config.models import (
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
    EnrollmentConfig,
)
from ic_env_guard.enrollment.helper import run_helper
from ic_env_guard.enrollment.socket_server import EnrollmentSocketServer
from ic_env_guard.main import create_app

REQUEST = (
    b'{"protocol":"manager-enrollment.v1",'
    b'"manager_id":"2b576727-4f36-4f08-b90b-e8cbe98ebc80",'
    b'"enrollment_id":"01J2W4ABCDEFGHJKMNPQRSTVWX"}'
)


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="ieg-", dir="/tmp"))
    path.chmod(0o700)
    yield path
    rmtree(path, ignore_errors=True)


def _config(tmp_path: Path, socket_dir: Path, *, mode: str = "agent") -> AppConfig:
    token_file = tmp_path / "token"
    token_file.write_text("local-admin-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return AppConfig(
        mode=mode,
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        enrollment=EnrollmentConfig(socket_path=socket_dir / "enroll.sock", socket_mode="0600"),
    )


@pytest.mark.integration
def test_helper_uses_local_socket_and_stdout_contains_exactly_one_response(tmp_path, socket_dir):
    config = _config(tmp_path, socket_dir)
    app = create_app(config=config, state_database=tmp_path / "state.db")
    stdout = io.BytesIO()
    stderr = io.StringIO()

    with TestClient(app) as client:
        capabilities = client.get(
            "/api/v2/capabilities",
            headers={"Authorization": "Bearer local-admin-token"},
        ).json()["capabilities"]
        assert "manager-enrollment.v1" in capabilities
        assert run_helper(config.enrollment.socket_path, io.BytesIO(REQUEST), stdout, stderr) == 0

    output = stdout.getvalue()
    assert output.count(b"\n") == 1
    assert len(output) <= 8192
    assert json.loads(output)["protocol"] == "manager-enrollment.v1"
    assert stderr.getvalue() == ""
    assert not config.enrollment.socket_path.exists()
    issued_token = json.loads(output)["token"]
    with app.state.container.session_factory() as session:
        audit = "\n".join(
            str(value)
            for row in session.execute(text("SELECT * FROM audit_events"))
            for value in row
        )
    assert issued_token not in audit
    stopped = TestClient(app).get(
        "/api/v2/capabilities",
        headers={"Authorization": "Bearer local-admin-token"},
    )
    assert "manager-enrollment.v1" not in stopped.json()["capabilities"]


@pytest.mark.integration
def test_legacy_agent_config_starts_default_enrollment_capability(
    tmp_path, socket_dir, monkeypatch
):
    token_file = tmp_path / "token"
    token_file.write_text("local-admin-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config_path = tmp_path / "legacy-agent.yaml"
    config_path.write_text(
        f"mode: agent\nauth:\n  token_file: {token_file}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert "enrollment" not in config.model_fields_set
    config.enrollment.socket_path = socket_dir / "enroll.sock"
    start = Mock()
    stop = Mock()
    monkeypatch.setattr(EnrollmentSocketServer, "start", start)
    monkeypatch.setattr(EnrollmentSocketServer, "stop", stop)
    monkeypatch.setattr(EnrollmentSocketServer, "healthy", property(lambda _self: True))

    app = create_app(config=config, state_database=tmp_path / "state.db")
    with TestClient(app) as client:
        response = client.get(
            "/api/v2/capabilities",
            headers={"Authorization": "Bearer local-admin-token"},
        )

        assert response.status_code == 200
        assert "manager-enrollment.v1" in response.json()["capabilities"]
    start.assert_called_once_with()
    stop.assert_called_once_with()


@pytest.mark.integration
def test_helper_errors_are_bounded_and_never_disclose_token(tmp_path, socket_dir):
    config = _config(tmp_path, socket_dir)
    app = create_app(config=config, state_database=tmp_path / "state.db")
    stdout = io.BytesIO()
    stderr = io.StringIO()

    with TestClient(app):
        assert (
            run_helper(
                config.enrollment.socket_path,
                io.BytesIO(b"x" * 4097),
                stdout,
                stderr,
            )
            != 0
        )

    assert stdout.getvalue() == b""
    assert "stdin exceeds 4096 bytes" in stderr.getvalue()
    assert len(stderr.getvalue().encode()) <= 1024


@pytest.mark.integration
def test_manager_mode_never_creates_agent_enrollment_socket(tmp_path, socket_dir):
    config = _config(tmp_path, socket_dir, mode="control-plane")
    app = create_app(config=config, state_database=tmp_path / "state.db")
    with TestClient(app):
        assert not config.enrollment.socket_path.exists()


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="Unix peer credentials require POSIX")
def test_platform_peer_credentials_are_enforced_by_real_socket(tmp_path, socket_dir):
    config = _config(tmp_path, socket_dir)
    app = create_app(config=config, state_database=tmp_path / "state.db")
    with TestClient(app):
        stdout = io.BytesIO()
        assert (
            run_helper(config.enrollment.socket_path, io.BytesIO(REQUEST), stdout, io.StringIO())
            == 0
        )
        assert json.loads(stdout.getvalue())["token"]

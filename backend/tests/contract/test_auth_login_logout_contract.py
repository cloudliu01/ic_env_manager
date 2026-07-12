from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ic_env_guard.api.auth import get_login_audit_recorder
from ic_env_guard.auth.rate_limit import LoginRateLimiter
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.audit import AuditEvent
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditEvent
from ic_env_guard.main import create_app, main


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(token_file=token_file)
    return TestClient(app)


@pytest.mark.contract
def test_login_accepts_generated_local_bearer_token(client):
    response = client.post("/api/auth/login", json={"token": "secret-token"})

    assert response.status_code == 200
    assert response.json() == {"actor": "local-admin", "token_type": "bearer"}


@pytest.mark.contract
@pytest.mark.security
def test_login_rejects_invalid_token_without_echoing_secret(client):
    response = client.post("/api/auth/login", json={"token": "wrong-token"})

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert "wrong-token" not in str(body)


@pytest.mark.contract
def test_logout_requires_authentication(client):
    missing = client.post("/api/auth/logout")
    assert missing.status_code == 401

    ok = client.post("/api/auth/logout", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 204


@pytest.mark.contract
@pytest.mark.security
def test_repeated_invalid_logins_are_limited_by_actual_source_address(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    limiter = LoginRateLimiter(capacity=2, refill_seconds=60, clock=lambda: 0.0)
    client = TestClient(create_app(token_file=token_file, login_limiter=limiter))

    first = client.post(
        "/api/auth/login",
        json={"token": "wrong-token"},
        headers={"X-Forwarded-For": "198.51.100.1"},
    )
    second = client.post(
        "/api/auth/login",
        json={"token": "wrong-token"},
        headers={"X-Forwarded-For": "198.51.100.2"},
    )
    limited = client.post(
        "/api/auth/login",
        json={"token": "wrong-token"},
        headers={"X-Forwarded-For": "198.51.100.3"},
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert limited.status_code == 429
    assert limited.json()["error"] == "too_many_login_attempts"
    assert "198.51.100" not in limited.text


@pytest.mark.contract
@pytest.mark.security
def test_login_success_and_failure_are_durably_audited_without_token(tmp_path):
    token_file = tmp_path / "token"
    state_database = tmp_path / "state.db"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(token_file=token_file, state_database=state_database)
    client = TestClient(app)

    denied = client.post("/api/auth/login", json={"token": "submitted-wrong-token"})
    accepted = client.post("/api/auth/login", json={"token": "secret-token"})

    assert denied.status_code == 401
    assert accepted.status_code == 200
    with app.state.container.session_factory() as session:
        events = session.execute(
            select(AuditEvent).where(AuditEvent.operation == "auth.login").order_by(AuditEvent.id)
        ).scalars().all()
    assert [event.result for event in events] == ["denied", "success"]
    assert all(event.source_addr == "testclient" for event in events)
    assert "submitted-wrong-token" not in " ".join(
        str(event.to_safe_dict()) for event in events
    )


def test_production_launcher_uses_configured_coordinated_servers(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "mode: agent\n"
        f"auth:\n  token_file: {token_file}\n"
        f"state_database: {tmp_path / 'state.db'}\n",
        encoding="utf-8",
    )
    launcher = AsyncMock()
    monkeypatch.setenv("IC_ENV_GUARD_CONFIG", str(config_path))
    monkeypatch.setattr("ic_env_guard.main.serve_config", launcher)

    main()

    config = launcher.await_args.args[0]
    assert config.mode == "agent"
    assert config.server.port == 8765
    assert config.ingest.port == 8766
    assert launcher.await_args.kwargs["config_path"] == config_path


@pytest.mark.contract
@pytest.mark.security
def test_schema_invalid_logins_are_limited_and_durably_audited(tmp_path):
    token_file = tmp_path / "token"
    state_database = tmp_path / "state.db"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    limiter = LoginRateLimiter(capacity=5, refill_seconds=60, clock=lambda: 0.0)
    app = create_app(
        token_file=token_file, state_database=state_database, login_limiter=limiter
    )
    client = TestClient(app)

    missing = client.post("/api/auth/login", json={})
    empty = client.post("/api/auth/login", json={"token": ""})
    malformed = client.post(
        "/api/auth/login", content=b'{"token":', headers={"Content-Type": "application/json"}
    )
    invalid_utf8 = client.post(
        "/api/auth/login",
        content=b'{"token":"\xff"}',
        headers={"Content-Type": "application/json"},
    )
    wrong_content_type = client.post(
        "/api/auth/login",
        content=b'{"token":"secret-token"}',
        headers={"Content-Type": "text/plain"},
    )
    limited = client.post("/api/auth/login", json={})

    assert [
        missing.status_code,
        empty.status_code,
        malformed.status_code,
        invalid_utf8.status_code,
        wrong_content_type.status_code,
    ] == [422, 422, 422, 422, 422]
    assert limited.status_code == 429
    with app.state.container.session_factory() as session:
        events = session.execute(
            select(AuditEvent).where(AuditEvent.operation == "auth.login").order_by(AuditEvent.id)
        ).scalars().all()
    assert [event.result for event in events] == ["denied"] * 6
    assert [event.failure_reason for event in events] == [
        "invalid_request",
        "invalid_request",
        "invalid_request",
        "invalid_request",
        "invalid_request",
        "rate_limited",
    ]
    assert "token" not in " ".join(str(event.to_safe_dict()) for event in events).lower()


@pytest.mark.contract
@pytest.mark.security
def test_deeply_nested_json_is_safely_audited_and_charged(tmp_path):
    token_file = tmp_path / "token"
    state_database = tmp_path / "state.db"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    limiter = LoginRateLimiter(capacity=1, refill_seconds=60, clock=lambda: 0.0)
    app = create_app(
        token_file=token_file, state_database=state_database, login_limiter=limiter
    )
    client = TestClient(app, raise_server_exceptions=False)
    deeply_nested = b"[" * 20_000 + b"0" + b"]" * 20_000

    rejected = client.post(
        "/api/auth/login",
        content=deeply_nested,
        headers={"Content-Type": "application/json"},
    )
    limited = client.post("/api/auth/login", json={"token": "secret-token"})

    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["type"] == "json_invalid"
    assert limited.status_code == 429
    with app.state.container.session_factory() as session:
        events = session.execute(
            select(AuditEvent).where(AuditEvent.operation == "auth.login").order_by(AuditEvent.id)
        ).scalars().all()
    assert [event.result for event in events] == ["denied", "denied"]
    assert [event.failure_reason for event in events] == [
        "invalid_request",
        "rate_limited",
    ]
    assert "[" not in " ".join(str(event.to_safe_dict()) for event in events)


@pytest.mark.contract
@pytest.mark.security
def test_manager_login_success_and_failure_are_durably_audited(tmp_path):
    token_file = tmp_path / "token"
    audit_database = tmp_path / "control-plane.db"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(audit_database=audit_database),
    )
    app = create_app(config=config)
    client = TestClient(app)

    denied = client.post("/api/auth/login", json={"token": "submitted-wrong-token"})
    accepted = client.post("/api/auth/login", json={"token": "secret-token"})

    assert denied.status_code == 401
    assert accepted.status_code == 200
    with app.state.container.control_plane_session_factory() as session:
        events = session.execute(
            select(ControlPlaneAuditEvent)
            .where(ControlPlaneAuditEvent.operation == "auth.login")
            .order_by(ControlPlaneAuditEvent.id)
        ).scalars().all()
    assert [event.result for event in events] == ["denied", "success"]
    assert all(event.source_addr == "testclient" for event in events)
    assert "submitted-wrong-token" not in " ".join(
        str(event.to_safe_dict()) for event in events
    )


@pytest.mark.contract
@pytest.mark.security
def test_login_fails_closed_when_audit_write_fails(tmp_path):
    class FailingAuditRecorder:
        def record_success(self, actor_id, source_addr, correlation_id):
            raise RuntimeError("audit write failed")

        def record_failure(self, source_addr, correlation_id, reason):
            raise RuntimeError("audit write failed")

    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(token_file=token_file)
    app.dependency_overrides[get_login_audit_recorder] = FailingAuditRecorder

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/auth/login", json={"token": "secret-token"}
    )

    assert response.status_code == 500
    assert response.text != '{"actor":"local-admin","token_type":"bearer"}'

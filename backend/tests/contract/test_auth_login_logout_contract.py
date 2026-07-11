import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ic_env_guard.auth.rate_limit import LoginRateLimiter
from ic_env_guard.db.audit import AuditEvent
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


def test_production_launcher_does_not_trust_proxy_headers(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    main()

    assert captured["proxy_headers"] is False

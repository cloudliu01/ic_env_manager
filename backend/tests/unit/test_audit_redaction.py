import pytest

from ic_env_guard.db.audit import AuditEventCreate
from ic_env_guard.db.repositories import redact_value


@pytest.mark.unit
@pytest.mark.security
def test_redact_value_redacts_secret_like_keys():
    redacted = redact_value(
        {
            "token": "secret-token",
            "nested": {"password": "pw", "safe": "ok"},
            "items": [{"private_key": "key"}],
        }
    )

    assert redacted["token"] == "<redacted>"
    assert redacted["nested"]["password"] == "<redacted>"
    assert redacted["nested"]["safe"] == "ok"
    assert redacted["items"][0]["private_key"] == "<redacted>"


@pytest.mark.unit
@pytest.mark.security
def test_audit_event_create_redacts_failure_reason():
    event = AuditEventCreate(
        operation="login",
        target_type="auth",
        result="denied",
        failure_reason="token=secret-token password=pw",
    )

    assert "secret-token" not in event.safe_failure_reason()
    assert "password" not in event.safe_failure_reason().lower()

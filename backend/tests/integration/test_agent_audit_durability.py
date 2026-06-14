import pytest
from fastapi.testclient import TestClient

from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.session import create_session_factory, create_sqlite_engine
from ic_env_guard.main import create_app


@pytest.mark.integration
def test_agent_audit_events_survive_application_restart(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    state_database = tmp_path / "state.db"

    create_app(token_file=token_file, state_database=state_database)
    engine = create_sqlite_engine(state_database)
    session = create_session_factory(engine)()
    AuditRepository(session).add(
        AuditEventCreate(
            actor_id="local-admin",
            operation="durability.probe",
            target_type="audit",
            result="success",
        )
    )
    session.commit()
    session.close()
    engine.dispose()

    second_app = create_app(token_file=token_file, state_database=state_database)
    with TestClient(second_app) as client:
        response = client.get(
            "/api/audit",
            headers={"Authorization": "Bearer secret-token"},
            params={"target_type": "audit"},
        )

    assert response.status_code == 200
    assert any(
        event["operation"] == "durability.probe" for event in response.json()["events"]
    )

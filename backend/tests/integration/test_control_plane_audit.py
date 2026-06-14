import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_session_factory, create_sqlite_engine
from ic_env_guard.main import create_app


def _token_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


@pytest.mark.integration
def test_control_plane_audit_repository_persists_and_filters(tmp_path):
    audit_database = tmp_path / "control-plane.db"
    run_control_plane_migrations(audit_database)
    engine = create_sqlite_engine(audit_database)
    session = create_session_factory(engine)()
    try:
        repo = ControlPlaneAuditRepository(session)
        event = repo.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id="local-admin",
                source_addr="127.0.0.1",
                agent_id="lab-01",
                operation="services.start",
                target="service:demo",
                correlation_id="corr-1",
            )
        )
        repo.finalize(event.id, result="success", dispatch_state="dispatched", upstream_status=200)
        session.commit()
    finally:
        session.close()
        engine.dispose()

    engine = create_sqlite_engine(audit_database)
    session = create_session_factory(engine)()
    try:
        events = ControlPlaneAuditRepository(session).list_events(agent_id="lab-01", limit=10)
    finally:
        session.close()
        engine.dispose()

    assert len(events) == 1
    assert events[0]["operation"] == "services.start"
    assert events[0]["result"] == "success"
    assert events[0]["upstream_status"] == 200


@pytest.mark.integration
def test_control_plane_audit_route_returns_bounded_filtered_events(tmp_path):
    audit_database = tmp_path / "control-plane.db"
    run_control_plane_migrations(audit_database)
    engine = create_sqlite_engine(audit_database)
    session = create_session_factory(engine)()
    try:
        repo = ControlPlaneAuditRepository(session)
        repo.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id="local-admin",
                source_addr="127.0.0.1",
                agent_id="lab-01",
                operation="services.stop",
                target="service:demo",
                correlation_id="corr-2",
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=audit_database),
        agents=[],
    )
    with TestClient(create_app(config=config)) as client:
        response = client.get(
            "/api/control-plane/audit",
            headers={"Authorization": "Bearer secret-token"},
            params={"agent_id": "lab-01", "limit": 10},
        )

    assert response.status_code == 200
    assert response.json()["events"][0]["correlation_id"] == "corr-2"

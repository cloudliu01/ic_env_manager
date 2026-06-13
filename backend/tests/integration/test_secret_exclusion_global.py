import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.session import Base
from ic_env_guard.main import create_app

SECRET_TEXT = "super-secret-token password private_key terminal-output"


@pytest.mark.integration
@pytest.mark.security
def test_secret_exclusion_across_audit_metrics_ui_and_sqlite(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = AuditRepository(session)
    repo.add(
        AuditEventCreate(
            operation="service.start",
            target_type="service",
            result="failed",
            failure_reason=SECRET_TEXT,
        )
    )
    session.commit()

    audit_text = "\n".join(
        str(value)
        for row in session.execute(repo.query(limit=10)).scalars()
        for value in [row.operation, row.target_type, row.failure_reason]
    )
    assert "super-secret-token" not in audit_text
    assert "password" not in audit_text
    assert "private_key" not in audit_text

    token_file = tmp_path / "token"
    token_file.write_text("super-secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    client = TestClient(create_app(token_file=token_file))

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    for family in text_string_to_metric_families(metrics.text):
        assert SECRET_TEXT not in family.name
        for sample in family.samples:
            assert "super-secret-token" not in str(sample.labels)
            assert "terminal-output" not in str(sample.labels)

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert "super-secret-token" not in ready.text

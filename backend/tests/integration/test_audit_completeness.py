from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ic_env_guard.auth.audit import audit_auth_failure, audit_auth_success
from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.db.session import Base


@pytest.mark.integration
def test_audit_event_completeness_across_lifecycle_categories():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = AuditRepository(session)

    audit_auth_success(repo, "local-admin", "127.0.0.1")
    audit_auth_failure(repo, "127.0.0.1")
    for operation, target_type, target_id in [
        ("terminal.create", "terminal", "term-1"),
        ("service.start", "service", "demo"),
        ("config.load", "config", "config.yaml"),
        ("agent.start", "agent", "local"),
    ]:
        repo.add(
            AuditEventCreate(
                actor_id="local-admin",
                source_addr="127.0.0.1",
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                result="success",
            )
        )
    session.commit()

    rows = session.execute(repo.query(limit=20)).scalars().all()
    operations = {row.operation for row in rows}

    expected = {"auth.login", "terminal.create", "service.start", "config.load", "agent.start"}
    assert expected <= operations
    for row in rows:
        assert row.timestamp is not None
        assert row.operation
        assert row.target_type
        assert row.result in {"success", "denied", "rejected", "failed", "timeout"}


@pytest.mark.integration
def test_audit_query_bounds_and_secret_redaction():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = AuditRepository(session)

    repo.add(
        AuditEventCreate(
            operation="service.start",
            target_type="service",
            target_id="demo",
            result="failed",
            failure_reason="bearer token secret password private_key leaked",
        )
    )
    session.commit()

    since = datetime.now(UTC) - timedelta(minutes=1)
    rows = session.execute(
        repo.query(limit=5000, target_type="service", since=since)
    ).scalars().all()

    assert len(rows) == 1
    assert len(session.execute(repo.query(limit=5000)).scalars().all()) <= 1000
    reason = rows[0].failure_reason or ""
    assert "bearer" not in reason
    assert "token" not in reason
    assert "secret" not in reason
    assert "password" not in reason
    assert "private_key" not in reason

import concurrent.futures
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.enrollment.models import (
    CredentialState,
    DuplicateEnrollment,
    EnrollmentCapacityExceeded,
    EnrollmentForbidden,
)

NOW = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
MANAGER_ID = "2b576727-4f36-4f08-b90b-e8cbe98ebc80"


@pytest.fixture
def container(tmp_path):
    return build_agent_container(None, tmp_path / "state.db", tmp_path / "instance-id")


@pytest.mark.unit
def test_issue_is_one_time_hash_only_and_token_has_256_bits(container):
    service = container.enrollment_service
    issued = service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)

    assert len(issued.token.encode("ascii")) >= 43
    serialized = container.manager_credential_repository.dump_serialized_rows()
    assert issued.token not in serialized
    assert hashlib.sha256(issued.token.encode()).hexdigest() in serialized
    assert service.verify(issued.token, now=NOW).state is CredentialState.PENDING

    with pytest.raises(DuplicateEnrollment):
        service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)
    with pytest.raises(DuplicateEnrollment):
        service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW + timedelta(hours=1))


@pytest.mark.unit
def test_pending_expiry_capacity_activation_and_idempotency(container):
    service = container.enrollment_service
    service.max_pending = 1
    issued = service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)
    with pytest.raises(EnrollmentCapacityExceeded):
        service.issue_pending(MANAGER_ID, "enrollment-2", now=NOW)

    assert service.verify(issued.token, now=NOW + timedelta(minutes=10)) is None
    second = service.issue_pending(
        MANAGER_ID, "enrollment-2", now=NOW + timedelta(minutes=10)
    )
    active = service.activate(
        second.credential_id, "enrollment-2", second.token, now=NOW + timedelta(minutes=10)
    )
    repeated = service.activate(
        second.credential_id, "enrollment-2", second.token, now=NOW + timedelta(minutes=10)
    )
    assert active.state is CredentialState.ACTIVE
    assert repeated == active
    assert service.verify(second.token, now=NOW + timedelta(minutes=11)).actor_id == (
        f"manager:{MANAGER_ID}"
    )


@pytest.mark.unit
def test_activation_requires_matching_pending_secret_and_identifiers(container):
    service = container.enrollment_service
    first = service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)
    other = service.issue_pending(
        "f54e933c-925c-46d4-a4f4-2638ce7c0651", "enrollment-2", now=NOW
    )

    for credential_id, enrollment_id, token in [
        (first.credential_id, "wrong", first.token),
        (first.credential_id, "enrollment-1", other.token),
        (other.credential_id, "enrollment-1", first.token),
    ]:
        with pytest.raises(EnrollmentForbidden):
            service.activate(credential_id, enrollment_id, token, now=NOW)


@pytest.mark.unit
def test_revoke_ownership_local_admin_and_idempotency(container):
    service = container.enrollment_service
    first = service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)
    first_record = service.activate(first.credential_id, "enrollment-1", first.token, now=NOW)
    old = service.issue_pending(MANAGER_ID, "enrollment-old", now=NOW)
    old_record = service.activate(old.credential_id, "enrollment-old", old.token, now=NOW)
    foreign = service.issue_pending(
        "f54e933c-925c-46d4-a4f4-2638ce7c0651", "enrollment-foreign", now=NOW
    )
    foreign_record = service.activate(
        foreign.credential_id, "enrollment-foreign", foreign.token, now=NOW
    )

    assert service.revoke(
        old_record.credential_id,
        actor_id=first_record.actor_id,
        manager_id=MANAGER_ID,
        now=NOW,
    ).state is CredentialState.REVOKED
    assert service.revoke(
        old_record.credential_id,
        actor_id=first_record.actor_id,
        manager_id=MANAGER_ID,
        now=NOW,
    ).state is CredentialState.REVOKED
    with pytest.raises(EnrollmentForbidden):
        service.revoke(
            foreign_record.credential_id,
            actor_id=first_record.actor_id,
            manager_id=MANAGER_ID,
            now=NOW,
        )
    assert service.revoke(
        foreign_record.credential_id,
        actor_id="local-admin",
        manager_id=None,
        now=NOW,
    ).state is CredentialState.REVOKED


@pytest.mark.unit
def test_concurrent_issue_activate_and_revoke_are_safe(container):
    service = container.enrollment_service

    def issue():
        try:
            return service.issue_pending(MANAGER_ID, "same-enrollment", now=NOW)
        except DuplicateEnrollment:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        issued = [item for item in pool.map(lambda _: issue(), range(16)) if item]
    assert len(issued) == 1
    credential = issued[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        activated = list(
            pool.map(
                lambda _: service.activate(
                    credential.credential_id,
                    "same-enrollment",
                    credential.token,
                    now=NOW,
                ),
                range(16),
            )
        )
    assert {item.state for item in activated} == {CredentialState.ACTIVE}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        revoked = list(
            pool.map(
                lambda _: service.revoke(
                    credential.credential_id,
                    actor_id="local-admin",
                    manager_id=None,
                    now=NOW,
                ),
                range(16),
            )
        )
    assert {item.state for item in revoked} == {CredentialState.REVOKED}


@pytest.mark.unit
def test_lifecycle_audit_is_fail_closed_and_contains_no_secret(container, monkeypatch):
    service = container.enrollment_service
    monkeypatch.setattr(service.audit, "record_intent", lambda **_: (_ for _ in ()).throw(
        sqlite3.OperationalError("audit unavailable")
    ))

    with pytest.raises(sqlite3.OperationalError):
        service.issue_pending(MANAGER_ID, "blocked", now=NOW)
    assert container.manager_credential_repository.list_all() == ()

    monkeypatch.undo()
    issued = service.issue_pending(MANAGER_ID, "allowed", now=NOW)
    with container.session_factory() as session:
        values = "\n".join(
            str(value)
            for row in session.execute(sqlite3_to_sqlalchemy("SELECT * FROM audit_events"))
            for value in row
        )
    assert issued.token not in values
    assert hashlib.sha256(issued.token.encode()).hexdigest() not in values


def sqlite3_to_sqlalchemy(statement: str):
    from sqlalchemy import text

    return text(statement)

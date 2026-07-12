from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ic_env_guard.db.control_plane_migrations import run_control_plane_migrations
from ic_env_guard.db.session import create_sqlite_engine
from ic_env_guard.fleet.models import (
    AgentQuery,
    AgentRecord,
    AgentStatus,
    EnrollmentJob,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RegistryInvariantError,
    RevisionConflict,
)
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.manager_registry import (
    AgentStatusRepository,
    ManagerRegistryRepository,
)

NOW = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def repositories(tmp_path):
    database = tmp_path / "control-plane.db"
    run_control_plane_migrations(database)
    engine = create_sqlite_engine(database)
    yield (
        ManagerRegistryRepository(engine),
        AgentStatusRepository(engine),
        EnrollmentJournalRepository(engine),
    )
    engine.dispose()


def agent_record(**changes) -> AgentRecord:
    values = {
        "agent_id": "lab-01",
        "instance_id": str(uuid4()),
        "display_name": "Lab 01",
        "normalized_endpoint": "https://lab-01.example:8765",
        "credential_ref": "a" * 48,
        "remote_credential_id": str(uuid4()),
        "transport_profile_id": "system-tls",
        "enrollment_method": EnrollmentMethod.SSH_AUTO,
        "enabled": True,
        "source": "manual",
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return AgentRecord(**values)


@pytest.mark.unit
def test_registry_create_list_and_compare_and_swap(repositories):
    registry, _, _ = repositories
    original = registry.create(agent_record())

    page = registry.list(AgentQuery(limit=10))
    assert page.items == (original,)
    assert page.next_cursor is None

    changed = replace(original, display_name="Renamed", updated_at=NOW + timedelta(seconds=1))
    updated = registry.update_if_revision(changed, expected_revision=1)
    assert updated.display_name == "Renamed"
    assert updated.revision == 2

    with pytest.raises(RevisionConflict):
        registry.update_if_revision(changed, expected_revision=1)
    assert registry.get("lab-01") == updated


@pytest.mark.unit
def test_registry_rejects_duplicate_identity_endpoint_and_id_atomically(repositories):
    registry, _, _ = repositories
    original = registry.create(agent_record())

    conflicts = (
        replace(original, display_name="duplicate id"),
        agent_record(agent_id="lab-02", normalized_endpoint=original.normalized_endpoint),
        agent_record(
            agent_id="lab-03",
            normalized_endpoint="https://lab-03.example:8765",
            instance_id=original.instance_id,
        ),
    )
    for conflict in conflicts:
        with pytest.raises(RegistryConflict):
            registry.create(conflict)

    assert registry.list(AgentQuery(limit=10)).items == (original,)


@pytest.mark.unit
def test_registry_requires_identity_except_for_legacy_config_import(repositories):
    registry, _, _ = repositories

    with pytest.raises(RegistryInvariantError):
        registry.create(agent_record(instance_id=None))

    imported = registry.create(
        agent_record(
            agent_id="legacy-01",
            instance_id=None,
            normalized_endpoint="https://legacy.example:8765",
            remote_credential_id=None,
            enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            source="config_import",
        )
    )
    assert imported.instance_id is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    (
        {"credential_ref": "not-a-reference"},
        {"normalized_endpoint": "HTTPS://Lab-01.Example/"},
        {"normalized_endpoint": "https://user@lab-01.example:8765"},
        {"normalized_endpoint": "https://lab-01.example:8765/path"},
    ),
)
def test_registry_rejects_noncanonical_security_fields(repositories, changes):
    registry, _, _ = repositories
    with pytest.raises(RegistryInvariantError):
        registry.create(agent_record(**changes))
    assert registry.list(AgentQuery()).items == ()


@pytest.mark.unit
def test_status_write_is_bound_to_registry_revision_and_cascades(repositories):
    registry, statuses, _ = repositories
    registry.create(agent_record())
    observation = AgentStatus(
        agent_id="lab-01",
        target_revision=1,
        connection_status="ready",
        workload_status="healthy",
        observed_at=NOW,
        stale_after=NOW + timedelta(seconds=45),
        api_version="v2",
        agent_version="0.1.0",
        capabilities=("summary.v2",),
        summary={"services": {"running": 1}},
        last_error_code=None,
        updated_at=NOW,
    )
    assert statuses.update_if_target_revision(observation, expected_revision=1)

    changed = replace(registry.get("lab-01"), updated_at=NOW + timedelta(seconds=1))
    registry.update_if_revision(changed, expected_revision=1)
    assert not statuses.update_if_target_revision(
        replace(observation, updated_at=NOW + timedelta(seconds=2)), expected_revision=1
    )
    assert statuses.get("lab-01") == observation

    registry.delete("lab-01")
    assert statuses.get("lab-01") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    (
        {"connection_status": "online"},
        {"workload_status": "excellent"},
        {"summary": {"bad": float("nan")}},
        {"summary": {"bad": object()}},
        {"summary": {1: "non-string key"}},
    ),
)
def test_status_rejects_invalid_enums_and_unsafe_json(repositories, changes):
    registry, statuses, _ = repositories
    registry.create(agent_record())
    observation = AgentStatus(
        agent_id="lab-01",
        target_revision=1,
        connection_status="ready",
        workload_status="healthy",
        observed_at=NOW,
        stale_after=NOW + timedelta(seconds=45),
        api_version="v2",
        agent_version="0.1.0",
        capabilities=("summary.v2",),
        summary={},
        last_error_code=None,
        updated_at=NOW,
    )
    with pytest.raises(RegistryInvariantError):
        statuses.update_if_target_revision(replace(observation, **changes), expected_revision=1)
    assert statuses.get("lab-01") is None


def enrollment_job(**changes) -> EnrollmentJob:
    values = {
        "enrollment_id": "enroll-01",
        "manager_id": str(uuid4()),
        "state": EnrollmentState.PENDING,
        "normalized_endpoint": "https://lab-01.example:8765",
        "transport_profile_id": "system-tls",
        "discovery_result_id": None,
        "replace_agent_id": None,
        "requested_display_name": None,
        "ssh_user": "agent-user",
        "ssh_host": "lab-01.example",
        "ssh_port": 22,
        "enrollment_method": EnrollmentMethod.SSH_AUTO,
        "remote_instance_id": None,
        "remote_credential_id": None,
        "credential_temp_ref": None,
        "old_credential_ref": None,
        "old_remote_credential_id": None,
        "save_requested": False,
        "expires_at": NOW + timedelta(minutes=10),
        "last_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return EnrollmentJob(**values)


@pytest.mark.unit
def test_enrollment_journal_enforces_method_and_recovery_invariants(repositories):
    _, _, journal = repositories

    with pytest.raises(RegistryInvariantError):
        journal.create(enrollment_job(ssh_user=None))
    with pytest.raises(RegistryInvariantError):
        journal.create(
            enrollment_job(
                enrollment_id="legacy",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
            )
        )
    with pytest.raises(RegistryInvariantError):
        journal.create(enrollment_job(enrollment_id="save", save_requested=True))
    with pytest.raises(RegistryInvariantError):
        journal.create(
            enrollment_job(
                enrollment_id="ambiguous",
                discovery_result_id="candidate-1",
                replace_agent_id="lab-01",
            )
        )

    valid = journal.create(enrollment_job())
    assert journal.get(valid.enrollment_id) == valid
    assert journal.non_terminal_credential_references() == set()

    journal.set_state(
        valid.enrollment_id,
        EnrollmentState.CANCELLED,
        NOW + timedelta(seconds=1),
        expected_state=EnrollmentState.PENDING,
    )
    assert journal.recovery_credential_references() == set()


@pytest.mark.unit
def test_enrollment_state_transition_is_compare_and_swap_and_rejects_regression(repositories):
    _, _, journal = repositories
    valid = journal.create(enrollment_job())

    with pytest.raises(RevisionConflict):
        journal.set_state(
            valid.enrollment_id,
            EnrollmentState.CREDENTIAL_ISSUED,
            NOW + timedelta(seconds=1),
            expected_state=EnrollmentState.RUNNING,
        )
    journal.set_state(
        valid.enrollment_id,
        EnrollmentState.RUNNING,
        NOW + timedelta(seconds=1),
        expected_state=EnrollmentState.PENDING,
    )
    with pytest.raises(RegistryInvariantError):
        journal.set_state(
            valid.enrollment_id,
            EnrollmentState.CANCELLED,
            NOW + timedelta(seconds=2),
            expected_state=EnrollmentState.ACTIVATED,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("expected_state", "new_state"),
    (
        (EnrollmentState.PENDING, EnrollmentState.FAILED),
        (EnrollmentState.CREDENTIAL_ISSUED, EnrollmentState.FAILED),
        (EnrollmentState.VERIFIED, EnrollmentState.FAILED),
    ),
)
def test_enrollment_state_machine_matches_plan_boundaries(
    repositories, expected_state, new_state
):
    _, _, journal = repositories
    journal.create(enrollment_job())
    with pytest.raises(RegistryInvariantError):
        journal.set_state(
            "enroll-01",
            new_state,
            NOW + timedelta(seconds=1),
            expected_state=expected_state,
        )

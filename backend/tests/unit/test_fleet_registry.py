from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ic_env_guard.config.models import AgentConfig
from ic_env_guard.enrollment.credential_store import CredentialStore
from ic_env_guard.fleet.models import (
    AgentPage,
    AgentQuery,
    AgentRecord,
    EnrollmentMethod,
    RevisionConflict,
)
from ic_env_guard.fleet.registry import (
    FleetRegistry,
    FleetRegistryConfigurationError,
    FleetRegistryConflict,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _record(agent_id: str, reference: str, **changes) -> AgentRecord:
    values = {
        "agent_id": agent_id,
        "instance_id": None,
        "display_name": agent_id,
        "normalized_endpoint": f"https://{agent_id}.example:443",
        "credential_ref": reference,
        "remote_credential_id": None,
        "transport_profile_id": "system-tls",
        "enrollment_method": EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        "enabled": True,
        "source": "config_import",
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return AgentRecord(**values)


class _Repository:
    def __init__(self, records):
        self.records = {record.agent_id: record for record in records}
        self.update_calls = 0
        self.always_conflict = False
        self.conflict_once_to_desired = False
        self.third_conflict_update = None

    def get(self, agent_id):
        return self.records.get(agent_id)

    def list(self, query: AgentQuery):
        ids = sorted(
            agent_id
            for agent_id in self.records
            if query.cursor is None or agent_id > query.cursor
        )
        selected = ids[: query.limit]
        next_cursor = selected[-1] if len(ids) > query.limit else None
        return AgentPage(tuple(self.records[agent_id] for agent_id in selected), next_cursor)

    def update_if_revision(self, record, expected_revision):
        self.update_calls += 1
        if self.conflict_once_to_desired and self.update_calls == 1:
            self.records[record.agent_id] = replace(record, revision=expected_revision + 1)
            raise RevisionConflict("race")
        if self.always_conflict:
            if self.update_calls == 3 and self.third_conflict_update is not None:
                self.records[record.agent_id] = self.third_conflict_update(record)
            raise RevisionConflict("race")
        updated = replace(record, revision=expected_revision + 1)
        self.records[record.agent_id] = updated
        return updated


@pytest.mark.unit
def test_fleet_registry_lists_every_page_beyond_one_hundred(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    records = [_record(f"lab-{index:03d}", reference) for index in range(205)]
    registry = FleetRegistry(
        _Repository(records), store, (VerifiedTlsProfile(id="system-tls"),)
    )

    assert len(registry.list()) == 205
    assert registry.list()[0].id == "lab-000"
    assert registry.list()[-1].id == "lab-204"


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    (
        {"transport_profile_id": "missing"},
        {"normalized_endpoint": "http://lab.example:80"},
        {
            "transport_profile_id": "lan-http",
            "normalized_endpoint": "https://lab.example:443",
        },
        {"transport_profile_id": "legacy-disabled-no-credential", "enabled": True},
        {
            "transport_profile_id": "legacy-config-http",
            "normalized_endpoint": "https://lab.example:443",
        },
    ),
)
def test_invalid_stored_profile_is_runtime_disabled_and_rejected_before_update(
    tmp_path, changes
):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    repository = _Repository([_record("lab-01", reference, **changes)])
    registry = FleetRegistry(
        repository,
        store,
        (
            VerifiedTlsProfile(id="system-tls"),
            TrustedLanHttpProfile(id="lan-http", allowed_cidrs=["10.0.0.0/24"]),
        ),
    )

    projected = registry.get("lab-01")
    assert projected.enabled is repository.records["lab-01"].enabled
    assert projected.token_file is None
    assert projected.managed_credential is True
    assert "managed_credential" not in projected.model_dump()
    with pytest.raises(FleetRegistryConfigurationError):
        registry.set_enabled("lab-01", True)
    assert repository.update_calls == 0


@pytest.mark.unit
def test_legacy_http_marker_is_allowed_only_for_imported_legacy_record(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    imported = _record(
        "lab-01",
        reference,
        normalized_endpoint="http://127.0.0.1:8765",
        transport_profile_id="legacy-config-http",
    )
    registry = FleetRegistry(_Repository([imported]), store, ())
    assert registry.get("lab-01").enabled is True


@pytest.mark.unit
def test_set_enabled_retries_revision_conflict_idempotently_and_is_bounded(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    repository = _Repository([_record("lab-01", reference, enabled=False)])
    repository.conflict_once_to_desired = True
    registry = FleetRegistry(
        repository, store, (VerifiedTlsProfile(id="system-tls"),)
    )

    assert registry.set_enabled("lab-01", True).enabled is True
    assert repository.update_calls == 1

    repository.records["lab-01"] = _record("lab-01", reference, enabled=False)
    repository.conflict_once_to_desired = False
    repository.always_conflict = True
    with pytest.raises(FleetRegistryConflict):
        registry.set_enabled("lab-01", True)
    assert repository.update_calls == 4


@pytest.mark.unit
@pytest.mark.parametrize("invalid_after_conflict", ["credential", "profile"])
def test_concurrent_desired_enable_rechecks_security_gate_after_final_conflict(
    tmp_path, invalid_after_conflict
):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    repository = _Repository([_record("lab-01", reference, enabled=False)])
    repository.always_conflict = True

    def concurrent_enable(record):
        if invalid_after_conflict == "credential":
            store.delete(reference)
            return replace(record, enabled=True, revision=record.revision + 1)
        return replace(
            record,
            enabled=True,
            revision=record.revision + 1,
            transport_profile_id="missing",
        )

    repository.third_conflict_update = concurrent_enable
    registry = FleetRegistry(
        repository, store, (VerifiedTlsProfile(id="system-tls"),)
    )

    with pytest.raises(FleetRegistryConfigurationError):
        registry.set_enabled("lab-01", True)
    assert repository.records["lab-01"].enabled is True


@pytest.mark.unit
def test_concurrent_desired_enable_returns_when_final_security_gate_is_valid(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"secret")
    repository = _Repository([_record("lab-01", reference, enabled=False)])
    repository.always_conflict = True
    repository.third_conflict_update = lambda record: replace(
        record, enabled=True, revision=record.revision + 1
    )
    registry = FleetRegistry(
        repository, store, (VerifiedTlsProfile(id="system-tls"),)
    )

    assert registry.set_enabled("lab-01", True).enabled is True
    assert repository.update_calls == 3


@pytest.mark.unit
def test_registry_credential_loader_reads_store_not_projection_path(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"agent-secret")
    registry = FleetRegistry(
        _Repository([_record("lab-01", reference)]),
        store,
        (VerifiedTlsProfile(id="system-tls"),),
    )

    agent = registry.get("lab-01")
    assert agent.token_file is None
    assert agent.enabled is True
    assert agent.managed_credential is True
    assert registry.load_credential(agent) == "agent-secret"


@pytest.mark.unit
def test_missing_managed_credential_keeps_inventory_truth_and_allows_disable(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"agent-secret")
    repository = _Repository([_record("lab-01", reference)])
    registry = FleetRegistry(
        repository, store, (VerifiedTlsProfile(id="system-tls"),)
    )
    store.delete(reference)

    projected = registry.get("lab-01")
    assert projected.enabled is True
    assert projected.token_file is None
    disabled = registry.set_enabled("lab-01", False)
    assert disabled.enabled is False
    assert repository.records["lab-01"].enabled is False
    with pytest.raises(FleetRegistryConfigurationError):
        registry.set_enabled("lab-01", True)


@pytest.mark.unit
def test_static_enabled_agent_still_requires_token_and_managed_flag_is_not_public(tmp_path):
    with pytest.raises(ValidationError):
        AgentConfig(
            id="lab-01",
            name="Lab 01",
            base_url="https://lab-01.example",
            enabled=True,
        )

    managed = AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        enabled=True,
        managed_credential=True,
    )
    assert "managed_credential" not in managed.model_dump()

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ic_env_guard.config.models import DiscoveryConfig
from ic_env_guard.discovery.models import DiscoveryResult
from ic_env_guard.discovery.service import DiscoveryService
from ic_env_guard.fleet.models import EnrollmentState


class Repository:
    pass


class Registry:
    existing = False

    def find_duplicate(self, **_kwargs):
        return object() if self.existing else None


class Journal:
    state = None

    def get(self, _enrollment_id):
        return SimpleNamespace(state=self.state) if self.state else None


def _result():
    now = datetime.now(UTC)
    return DiscoveryResult(
        "result", "job", "https://10.0.0.1:8765", "10.0.0.1", 8765,
        "system-tls", "2", True, None, now, now, "enrollment",
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (EnrollmentState.PENDING, ("new", "enrolling")),
        (EnrollmentState.RUNNING, ("new", "enrolling")),
        (EnrollmentState.VERIFIED, ("new", "verified")),
        (EnrollmentState.CONSUMED, ("already_registered", "already_registered")),
        (EnrollmentState.FAILED, ("new", "enrollment_required")),
    ],
)
def test_candidate_enrollment_status_is_derived_from_journal(state, expected):
    registry = Registry()
    journal = Journal()
    journal.state = state
    service = DiscoveryService(
        config=DiscoveryConfig(),
        transport_profiles=(),
        repository=Repository(),
        fingerprinter=object(),
        registry=registry,
        enrollment_journal=journal,
    )
    assert service.classify(_result()) == expected


def test_registry_endpoint_always_derives_already_registered():
    registry = Registry()
    registry.existing = True
    service = DiscoveryService(
        config=DiscoveryConfig(),
        transport_profiles=(),
        repository=Repository(),
        fingerprinter=object(),
        registry=registry,
        enrollment_journal=Journal(),
    )
    assert service.classify(_result()) == (
        "already_registered",
        "already_registered",
    )

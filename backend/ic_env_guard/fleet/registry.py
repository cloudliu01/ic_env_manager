from dataclasses import replace
from urllib.parse import urlsplit

from ic_env_guard.config.models import AgentConfig, AgentTlsConfig
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.fleet.models import AgentQuery, AgentRecord, RevisionConflict
from ic_env_guard.fleet.ports import ManagerRegistryRepository
from ic_env_guard.fleet.transport import (
    TransportProfile,
    TrustedLanHttpProfile,
    VerifiedTlsProfile,
)


class FleetRegistryConfigurationError(Exception):
    pass


class FleetRegistryConflict(Exception):
    pass


class FleetRegistry:
    """SQLite-backed compatibility projection for the v1 Agent routes."""

    def __init__(
        self,
        repository: ManagerRegistryRepository,
        credential_store: CredentialStore,
        transport_profiles: tuple[TransportProfile, ...],
    ) -> None:
        self._repository = repository
        self._credential_store = credential_store
        self._profiles = {profile.id: profile for profile in transport_profiles}

    def get(self, agent_id: str) -> AgentConfig | None:
        record = self._repository.get(agent_id)
        return self._project(record) if record is not None else None

    def list(self) -> list[AgentConfig]:
        records: list[AgentRecord] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self._repository.list(AgentQuery(limit=100, cursor=cursor))
            records.extend(page.items)
            if page.next_cursor is None:
                break
            if page.next_cursor == cursor or page.next_cursor in seen_cursors:
                raise FleetRegistryConflict("Agent Registry pagination did not advance")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        return [self._project(record) for record in records]

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentConfig | None:
        for _attempt in range(3):
            record = self._repository.get(agent_id)
            if record is None:
                return None
            if enabled:
                self._validate_enable(record)
            if record.enabled == enabled:
                return self._project(record)
            try:
                updated = self._repository.update_if_revision(
                    replace(record, enabled=enabled), expected_revision=record.revision
                )
            except RevisionConflict:
                continue
            return self._project(updated)
        latest = self._repository.get(agent_id)
        if latest is not None and latest.enabled == enabled:
            if enabled:
                self._validate_enable(latest)
            return self._project(latest)
        raise FleetRegistryConflict("Agent Registry revision changed repeatedly")

    def record(self, agent_id: str) -> AgentRecord | None:
        return self._repository.get(agent_id)

    def load_credential(self, agent: AgentConfig) -> str:
        record = self._repository.get(agent.id)
        if record is None or not self._configuration_valid(record):
            raise FleetRegistryConfigurationError("Agent credential is unavailable")
        return self._read_credential(record)

    def _read_credential(self, record: AgentRecord) -> str:
        try:
            token = self._credential_store.read(record.credential_ref).decode("utf-8").strip()
        except (CredentialStoreError, OSError, UnicodeError, ValueError) as exc:
            raise FleetRegistryConfigurationError("Agent credential is unavailable") from exc
        if not token:
            raise FleetRegistryConfigurationError("Agent credential is unavailable")
        return token

    def _validate_enable(self, record: AgentRecord) -> None:
        if not self._configuration_valid(record):
            raise FleetRegistryConfigurationError("Agent transport profile is invalid")
        self._read_credential(record)

    def _project(self, record: AgentRecord) -> AgentConfig:
        profile = self._profiles.get(record.transport_profile_id)
        tls = AgentTlsConfig()
        if isinstance(profile, VerifiedTlsProfile):
            tls = AgentTlsConfig(verify=True, ca_bundle=profile.ca_bundle)
        elif urlsplit(record.normalized_endpoint).scheme == "http":
            tls = AgentTlsConfig(verify=False)
        return AgentConfig(
            id=record.agent_id,
            name=record.display_name,
            base_url=record.normalized_endpoint,
            token_file=None,
            tls=tls,
            enabled=record.enabled,
            managed_credential=True,
        )

    def _configuration_valid(self, record: AgentRecord) -> bool:
        scheme = urlsplit(record.normalized_endpoint).scheme
        if record.transport_profile_id == "legacy-disabled-no-credential":
            return False
        if record.transport_profile_id == "legacy-config-http":
            return (
                scheme == "http"
                and record.source == "config_import"
                and record.enrollment_method.value == "legacy_admin_token"
            )
        profile = self._profiles.get(record.transport_profile_id)
        if isinstance(profile, VerifiedTlsProfile):
            return scheme == "https"
        if isinstance(profile, TrustedLanHttpProfile):
            return scheme == "http"
        return False

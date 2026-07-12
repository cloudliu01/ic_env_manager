from dataclasses import replace
from urllib.parse import urlsplit

from ic_env_guard.config.models import AgentConfig, AgentTlsConfig
from ic_env_guard.enrollment.credential_store import CredentialStore
from ic_env_guard.fleet.models import AgentQuery, AgentRecord
from ic_env_guard.fleet.ports import ManagerRegistryRepository
from ic_env_guard.fleet.transport import TransportProfile, VerifiedTlsProfile


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
        return [self._project(record) for record in self._repository.list(AgentQuery()).items]

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentConfig | None:
        record = self._repository.get(agent_id)
        if record is None:
            return None
        updated = self._repository.update_if_revision(
            replace(record, enabled=enabled), expected_revision=record.revision
        )
        return self._project(updated)

    def record(self, agent_id: str) -> AgentRecord | None:
        return self._repository.get(agent_id)

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
            token_file=(
                None
                if record.transport_profile_id == "legacy-disabled-no-credential"
                else self._credential_store.resolve_for_test(record.credential_ref)
            ),
            tls=tls,
            enabled=record.enabled,
        )

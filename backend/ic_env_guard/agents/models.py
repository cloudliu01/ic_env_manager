from dataclasses import dataclass

API_VERSION = "1"
AGENT_VERSION = "0.2.0"
LOCAL_CAPABILITIES = ["services.v1", "terminals.v1", "audit.v1", "monitoring.snapshot.v1"]


@dataclass(frozen=True)
class CapabilityResponse:
    api_version: str = API_VERSION
    agent_version: str = AGENT_VERSION
    capabilities: tuple[str, ...] = tuple(LOCAL_CAPABILITIES)

    def to_dict(self) -> dict[str, object]:
        return {
            "api_version": self.api_version,
            "agent_version": self.agent_version,
            "capabilities": list(self.capabilities),
        }

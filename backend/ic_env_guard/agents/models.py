from dataclasses import dataclass
from uuid import UUID

API_VERSION = "1"
AGENT_VERSION = "0.2.0"
LOCAL_CAPABILITIES = ["services.v1", "terminals.v1", "audit.v1", "monitoring.snapshot.v1"]
V2_LOCAL_CAPABILITIES = [
    *LOCAL_CAPABILITIES,
    "observations.v2",
    "logs.v2",
    "summary.v2",
    "manager-enrollment.v1",
]


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


@dataclass(frozen=True)
class V2CapabilityResponse:
    instance_id: UUID
    name: str
    capabilities: tuple[str, ...]
    api_version: str = "2"
    agent_version: str = AGENT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "instance_id": str(self.instance_id),
            "name": self.name,
            "api_version": self.api_version,
            "agent_version": self.agent_version,
            "capabilities": list(self.capabilities),
        }

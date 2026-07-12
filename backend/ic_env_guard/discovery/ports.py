from typing import Protocol

from ic_env_guard.discovery.models import DiscoveryFingerprint, DiscoveryTarget


class DiscoveryFingerprinter(Protocol):
    async def probe(
        self,
        target: DiscoveryTarget,
        *,
        connect_timeout: float,
        fingerprint_timeout: float,
    ) -> DiscoveryFingerprint | None: ...

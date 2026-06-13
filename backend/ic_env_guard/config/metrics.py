from ipaddress import ip_address, ip_network

from pydantic import BaseModel, Field, field_validator


class MetricsExposureConfig(BaseModel):
    enabled: bool = True
    collect_interval_seconds: int = Field(default=10, ge=1)
    remote_network_allowlist: list[str] = Field(default_factory=list)

    @field_validator("remote_network_allowlist")
    @classmethod
    def validate_cidr_networks(cls, values: list[str]) -> list[str]:
        for value in values:
            ip_network(value, strict=False)
        return values

    def allows_source(self, source: str) -> bool:
        addr = ip_address(source)
        if addr.is_loopback:
            return True
        return any(
            addr in ip_network(network, strict=False) for network in self.remote_network_allowlist
        )

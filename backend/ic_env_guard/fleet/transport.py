import os
import ssl
import stat
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyNetwork, field_validator, model_validator


def create_ca_context(ca_bundle: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(ca_bundle))


class VerifiedTlsProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    type: Literal["verified_tls"] = "verified_tls"
    ca_bundle: Path | None = None

    @field_validator("ca_bundle")
    @classmethod
    def validate_ca_bundle(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("CA bundle path must be absolute")
        try:
            if value.is_symlink():
                raise ValueError("CA bundle must not be a symlink")
            metadata = os.stat(value)
        except OSError as exc:
            raise ValueError("CA bundle is not accessible") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("CA bundle must be a regular file")
        if metadata.st_uid not in {0, os.geteuid()}:
            raise ValueError("CA bundle must be owned by root or the Manager user")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError("CA bundle must not be group or world writable")
        resolved = value.resolve()
        try:
            create_ca_context(resolved)
        except (OSError, ssl.SSLError) as exc:
            raise ValueError("CA bundle must contain valid certificates") from exc
        return resolved


class TrustedLanHttpProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    type: Literal["trusted_lan_http"] = "trusted_lan_http"
    allowed_cidrs: list[IPvAnyNetwork]

    @model_validator(mode="after")
    def validate_private_allowlist(self) -> "TrustedLanHttpProfile":
        if not self.allowed_cidrs or any(not network.is_private for network in self.allowed_cidrs):
            raise ValueError("trusted LAN HTTP requires non-empty private CIDRs")
        return self


TransportProfile = Annotated[
    VerifiedTlsProfile | TrustedLanHttpProfile,
    Field(discriminator="type"),
]


SYSTEM_TLS_PROFILE = VerifiedTlsProfile(id="system-tls")

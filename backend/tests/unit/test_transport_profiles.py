import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from ic_env_guard.config.models import ControlPlaneConfig
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile


@pytest.mark.unit
def test_control_plane_profiles_are_discriminated_unique_and_include_system_tls(tmp_path):
    ca = _ca_file(tmp_path)
    config = ControlPlaneConfig(
        allowed_agent_cidrs=["10.20.0.0/16"],
        transport_profiles=[
            {"id": "lab-tls", "type": "verified_tls", "ca_bundle": ca},
            {
                "id": "lab-http",
                "type": "trusted_lan_http",
                "allowed_cidrs": ["10.20.30.0/24"],
            },
        ],
    )

    assert isinstance(config.transport_profiles[0], VerifiedTlsProfile)
    assert config.transport_profiles[0].id == "system-tls"
    assert isinstance(config.transport_profiles[1], VerifiedTlsProfile)
    assert isinstance(config.transport_profiles[2], TrustedLanHttpProfile)

    with pytest.raises(ValidationError, match="unique"):
        ControlPlaneConfig(
            allowed_agent_cidrs=["10.0.0.0/8"],
            transport_profiles=[
                {"id": "same", "type": "verified_tls"},
                {"id": "same", "type": "verified_tls"},
            ],
        )


@pytest.mark.unit
def test_system_tls_profile_id_is_reserved():
    with pytest.raises(ValidationError, match="reserved"):
        ControlPlaneConfig(
            transport_profiles=[{"id": "system-tls", "type": "verified_tls"}]
        )


@pytest.mark.unit
def test_trusted_lan_profile_is_private_and_subset_of_global_allowlist():
    with pytest.raises(ValidationError, match="private"):
        ControlPlaneConfig(
            allowed_agent_cidrs=["0.0.0.0/0"],
            transport_profiles=[
                {
                    "id": "public-http",
                    "type": "trusted_lan_http",
                    "allowed_cidrs": ["8.8.8.0/24"],
                }
            ],
        )

    with pytest.raises(ValidationError, match="subset"):
        ControlPlaneConfig(
            allowed_agent_cidrs=["10.20.0.0/16"],
            transport_profiles=[
                {
                    "id": "other-lan",
                    "type": "trusted_lan_http",
                    "allowed_cidrs": ["10.30.0.0/16"],
                }
            ],
        )


@pytest.mark.unit
@pytest.mark.parametrize("unsafe", ["symlink", "owner", "mode", "directory"])
def test_verified_tls_ca_bundle_must_be_safe(tmp_path, monkeypatch, unsafe):
    ca = _ca_file(tmp_path)
    candidate: Path = ca
    if unsafe == "symlink":
        candidate = tmp_path / "linked.pem"
        candidate.symlink_to(ca)
    elif unsafe == "owner":
        real_stat = os.stat

        def foreign_owner(path, *args, **kwargs):
            value = real_stat(path, *args, **kwargs)
            if Path(path) == ca:
                return os.stat_result((*value[:4], value.st_uid + 1, *value[5:]))
            return value

        monkeypatch.setattr("ic_env_guard.fleet.transport.os.stat", foreign_owner)
    elif unsafe == "mode":
        ca.chmod(0o666)
    else:
        candidate = tmp_path

    with pytest.raises(ValidationError, match="CA bundle"):
        VerifiedTlsProfile(id="lab-tls", ca_bundle=candidate)


def _ca_file(tmp_path: Path) -> Path:
    path = tmp_path / "ca.pem"
    path.write_text("certificate", encoding="utf-8")
    path.chmod(0o644)
    return path

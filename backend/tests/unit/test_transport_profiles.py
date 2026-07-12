import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from ic_env_guard.config.models import ControlPlaneConfig
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

VALID_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDCzCCAfOgAwIBAgIUaV8FbGmZ3V8q0KtmyBxGQBEJZ1cwDQYJKoZIhvcNAQEL
BQAwFTETMBEGA1UEAwwKdGFzazMtdGVzdDAeFw0yNjA3MTIwNDE4NDRaFw0zNjA3
MDkwNDE4NDRaMBUxEzARBgNVBAMMCnRhc2szLXRlc3QwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQDNWJb2+4uyhJ/TPPhezMI/1iJSJjRzKV4tKGYwlkny
GScAsnYk8RegAGzkMmGH6JU6j3pDcffzwnLvsaCNbghfD+/1LJNQSn633wXFvPDl
iuPb4wnHCnvAWQuguVpbJDjOd4cSJV/xVJOx9rjRbOwUVJmSUdr+BnK2BuvQbPzz
KDZQ3XsU8H4FciVgWWZwKc8Jny3Ry3+5h2Nl+SNMYlV6HMgXo8457Oijc6A9cp1w
3V9zqLy/wcyuOx95ynNU2FIZKFW2hPrOOxOHJ5bYCI/Y4K0nOW42XsGN4sLQ5Cfh
mo+bH6PjkcBG/iFkdMSUFQG5CBnOjjGotiE5GJyEBpPxAgMBAAGjUzBRMB0GA1Ud
DgQWBBS0ZGGTz4ImyfZ9mCr4w6rhxtPRNTAfBgNVHSMEGDAWgBS0ZGGTz4ImyfZ9
mCr4w6rhxtPRNTAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQAt
Jf82fxp1zlCBf7D7cVS9k4uVKBsL7dIjLC9ZFaG7srNTVjItuWFtjmFWL2MVLejE
hLidVJFcX3g9KBL7qdC0is5TLIj0BPRlqPe8YDP9IYMzsT7p7mGlliV5VwOx3vwj
16lRD8o0IwXr4j10XkkftEPTgmqYOrAonxWmvdZJ0Rk9sDPGKxszjmF5ACrTVb1G
MRFizTlRW6VrnAVNybpHbxyJFB5m9PZCU9r/oR0gL/bca58ctknimav08eP2vPWw
hCs9x7kqc/wLtHVevgxh8OKd5iXQUXPvnOp8FLub+rICRg7WeozXNNeW3Kt9J00t
vVl4EauxOZ8Vuh6SZ/87
-----END CERTIFICATE-----
"""


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
            transport_profiles=[
                {"id": "system-tls", "type": "verified_tls", "ca_bundle": "/tmp/custom.pem"}
            ]
        )


@pytest.mark.unit
def test_transport_profiles_round_trip_without_allowing_system_tls_override(tmp_path):
    config = ControlPlaneConfig(
        allowed_agent_cidrs=["10.0.0.0/8"],
        transport_profiles=[
            {"id": "lab-tls", "type": "verified_tls", "ca_bundle": _ca_file(tmp_path)}
        ],
    )

    restored = ControlPlaneConfig.model_validate(config.model_dump())

    assert restored == config
    assert isinstance(restored.transport_profiles, tuple)
    with pytest.raises(ValidationError, match="reserved"):
        ControlPlaneConfig(
            transport_profiles=[
                {"id": "system-tls", "type": "verified_tls", "ca_bundle": _ca_file(tmp_path)}
            ]
        )
    with pytest.raises(ValidationError, match="reserved"):
        ControlPlaneConfig(
            transport_profiles=[{"id": "system-tls", "type": "verified_tls"}]
        )
    with pytest.raises(ValidationError, match="reserved"):
        ControlPlaneConfig(
            allowed_agent_cidrs=["10.0.0.0/8"],
            transport_profiles=[
                {
                    "id": "system-tls",
                    "type": "trusted_lan_http",
                    "allowed_cidrs": ["10.0.0.0/8"],
                }
            ],
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


@pytest.mark.unit
def test_verified_tls_ca_bundle_must_contain_parseable_certificates(tmp_path):
    invalid = tmp_path / "invalid-ca.pem"
    invalid.write_text("not a certificate", encoding="utf-8")
    invalid.chmod(0o600)

    with pytest.raises(ValidationError, match="valid certificates"):
        VerifiedTlsProfile(id="lab-tls", ca_bundle=invalid)


def _ca_file(tmp_path: Path) -> Path:
    path = tmp_path / "ca.pem"
    path.write_text(VALID_CA_PEM, encoding="utf-8")
    path.chmod(0o644)
    return path

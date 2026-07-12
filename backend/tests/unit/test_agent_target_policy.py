from ipaddress import ip_address

import pytest

from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

VERIFIED_TLS = VerifiedTlsProfile(id="system-tls")
TRUSTED_LAN = TrustedLanHttpProfile(
    id="lab-http", allowed_cidrs=["10.20.30.0/24", "fd20:30::/64"]
)


def _policy(answers=("10.20.30.10",)):
    return AgentTargetPolicy(
        allowed_agent_cidrs=["10.20.30.0/24", "fd20:30::/64"],
        resolver=lambda _host, _port: answers,
        self_targets=[("10.20.30.1", 8765), ("fd20:30::1", 8765)],
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://agent.example:8765",
        "https://user@agent.example:8765",
        "https://agent.example:8765/path",
        "https://agent.example:8765?query=1",
        "https://agent.example:8765?",
        "https://agent.example:8765#fragment",
        "https://agent.example:8765#",
        "https://agent.example:8765\\@evil.example",
        "https://agent.example:0",
    ],
)
def test_target_shape_rejects_unsupported_scheme_and_url_components(endpoint):
    with pytest.raises(TargetPolicyError, match="target_url_invalid"):
        _policy().resolve(endpoint, VERIFIED_TLS)


@pytest.mark.unit
def test_target_normalizes_idna_effective_port_and_pins_address():
    target = _policy().resolve("https://B\u00dcCHER.example", VERIFIED_TLS)

    assert target.hostname == "xn--bcher-kva.example"
    assert target.port == 443
    assert target.normalized_endpoint == "https://xn--bcher-kva.example:443"
    assert target.pinned_address == ip_address("10.20.30.10")
    assert target.host_header == "xn--bcher-kva.example"
    assert target.sni_hostname == "xn--bcher-kva.example"


@pytest.mark.unit
def test_trusted_lan_profile_requires_http_and_its_private_allowlist():
    target = _policy().resolve("http://10.20.30.10:8765", TRUSTED_LAN)
    assert target.warning_code == "trusted_lan_http_unencrypted"

    with pytest.raises(TargetPolicyError, match="transport_profile_mismatch"):
        _policy().resolve("https://10.20.30.10:8765", TRUSTED_LAN)
    with pytest.raises(TargetPolicyError, match="transport_profile_mismatch"):
        _policy().resolve("http://10.20.30.10:8765", VERIFIED_TLS)


@pytest.mark.unit
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "::1",
        "fe80::1",
        "ff02::1",
        "::",
        "fd00:ec2::254",
    ],
)
def test_dynamic_targets_reject_forbidden_ranges(address):
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["0.0.0.0/0", "::/0"],
        resolver=lambda _host, _port: (address,),
        self_targets=[("10.0.0.1", 8765)],
    )
    endpoint = f"https://[{address}]:8765" if ":" in address else f"https://{address}:8765"
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve(endpoint, VERIFIED_TLS)


@pytest.mark.unit
def test_every_dns_answer_must_be_allowed_before_one_is_pinned():
    with pytest.raises(TargetPolicyError, match="target_address_not_allowed"):
        _policy(("10.20.30.10", "192.0.2.10")).resolve(
            "https://agent.example:8765", VERIFIED_TLS
        )


@pytest.mark.unit
def test_self_target_is_rejected_only_at_manager_effective_port():
    with pytest.raises(TargetPolicyError, match="target_is_manager"):
        _policy(("10.20.30.1",)).resolve("https://agent.example:8765", VERIFIED_TLS)

    target = _policy(("10.20.30.1",)).resolve("https://agent.example:9443", VERIFIED_TLS)
    assert target.port == 9443


@pytest.mark.unit
def test_policy_requires_at_least_one_exact_manager_self_target():
    with pytest.raises(TargetPolicyError, match="target_policy_invalid"):
        AgentTargetPolicy(allowed_agent_cidrs=["10.0.0.0/8"], self_targets=[])


@pytest.mark.unit
def test_ipv6_target_is_bracketed_and_host_header_preserves_effective_port():
    target = _policy(("fd20:30::10",)).resolve("https://[fd20:30::10]:9443", VERIFIED_TLS)
    assert target.normalized_endpoint == "https://[fd20:30::10]:9443"
    assert target.pinned_url == "https://[fd20:30::10]:9443"
    assert target.host_header == "[fd20:30::10]:9443"


@pytest.mark.unit
def test_dns_failure_has_stable_safe_error():
    def fail(_host, _port):
        raise OSError("secret resolver detail")

    with pytest.raises(TargetPolicyError) as error:
        AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            resolver=fail,
            self_targets=[("10.0.0.1", 8765)],
        ).resolve("https://agent.example", VERIFIED_TLS)
    assert error.value.code == "agent_network_error"
    assert "secret" not in error.value.message

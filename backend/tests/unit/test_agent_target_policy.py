from dataclasses import replace
from datetime import UTC, datetime
from ipaddress import ip_address

import pytest

from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod
from ic_env_guard.fleet.registered_target import resolve_registered_target
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

VERIFIED_TLS = VerifiedTlsProfile(id="system-tls")
TRUSTED_LAN = TrustedLanHttpProfile(
    id="lab-http", allowed_cidrs=["10.20.30.0/24", "fd20:30::/64"]
)
LOCAL_HTTP = TrustedLanHttpProfile(
    id="local-loopback-http", allowed_cidrs=["127.0.0.0/8"]
)


def _agent_record(**changes):
    values = {
        "agent_id": "local-agent",
        "instance_id": "11111111-1111-4111-8111-111111111111",
        "display_name": "Local Agent",
        "normalized_endpoint": "http://127.0.0.1:8766",
        "credential_ref": "a" * 48,
        "remote_credential_id": "local-credential",
        "transport_profile_id": "local-loopback-http",
        "enrollment_method": EnrollmentMethod.LOCAL_SOCKET,
        "enabled": True,
        "source": "local_dev_bootstrap",
        "revision": 1,
        "created_at": datetime(2026, 7, 13, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 13, tzinfo=UTC),
    }
    values.update(changes)
    return AgentRecord(**values)


def _policy(answers=("10.20.30.10",)):
    return AgentTargetPolicy(
        allowed_agent_cidrs=["10.20.30.0/24", "fd20:30::/64"],
        resolver=lambda _host, _port: answers,
        self_targets=[("10.20.30.1", 8765), ("fd20:30::1", 8765)],
    )


@pytest.mark.unit
def test_local_socket_target_accepts_literal_loopback_only():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    )

    target = policy.resolve_local_socket("http://127.0.0.1:8766", LOCAL_HTTP)

    assert str(target.pinned_address) == "127.0.0.1"
    assert target.normalized_endpoint == "http://127.0.0.1:8766"
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve("http://127.0.0.1:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_url_invalid"):
        policy.resolve_local_socket("http://localhost:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve_local_socket("http://10.0.0.9:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_is_manager"):
        policy.resolve_local_socket("http://127.0.0.1:8765", LOCAL_HTTP)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("endpoint", "stored_ip"),
    (
        ("http://127.0.0.2:8766", "127.0.0.2"),
        ("http://[::1]:8766", "::1"),
    ),
)
def test_local_socket_target_rejects_every_other_loopback(endpoint, stored_ip):
    profile = TrustedLanHttpProfile(
        id="local-loopback-http",
        allowed_cidrs=["127.0.0.0/8", "::1/128"],
    )
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8", "::1/128"],
        self_targets=[("127.0.0.1", 8765)],
    )

    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve_local_socket(endpoint, profile)
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.revalidate_local_socket_target(endpoint, profile, stored_ip)


@pytest.mark.unit
def test_local_socket_target_requires_trusted_lan_http_profile():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    )

    with pytest.raises(TargetPolicyError, match="transport_profile_mismatch"):
        policy.resolve_local_socket("https://127.0.0.1:8766", VERIFIED_TLS)


@pytest.mark.unit
def test_registered_local_target_requires_gate_method_source_and_profile():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    )
    record = _agent_record(
        normalized_endpoint="http://127.0.0.1:8766",
        enrollment_method=EnrollmentMethod.LOCAL_SOCKET,
        source="local_dev_bootstrap",
        transport_profile_id="local-loopback-http",
    )

    target = resolve_registered_target(
        policy, record, LOCAL_HTTP, local_bootstrap_enabled=True
    )
    assert target.port == 8766

    for changed in (
        replace(record, enrollment_method=EnrollmentMethod.SSH_AUTO),
        replace(record, source="manual"),
        replace(record, transport_profile_id="another-profile"),
    ):
        with pytest.raises(TargetPolicyError):
            resolve_registered_target(
                policy, changed, LOCAL_HTTP, local_bootstrap_enabled=True
            )
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        resolve_registered_target(
            policy, record, LOCAL_HTTP, local_bootstrap_enabled=False
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
def test_manager_self_target_wins_over_empty_allowlist():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=[],
        self_targets=[("10.20.30.1", 8765)],
        resolver=lambda _host, _port: ["10.20.30.1"],
    )

    with pytest.raises(TargetPolicyError) as blocked:
        policy.resolve("https://manager.internal:8765", VERIFIED_TLS)

    assert blocked.value.code == "target_is_manager"


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


@pytest.mark.unit
def test_revalidate_pinned_target_never_resolves_and_preserves_http_identity():
    def must_not_resolve(_host, _port):
        raise AssertionError("recovery must not perform DNS")

    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["10.0.0.0/8"],
        resolver=must_not_resolve,
        self_targets=[("10.0.0.1", 8765)],
    )
    target = policy.revalidate_pinned_target(
        "https://Agent.Example:9443", VERIFIED_TLS, "10.20.30.40"
    )

    assert str(target.pinned_address) == "10.20.30.40"
    assert target.host_header == "agent.example:9443"
    assert target.sni_hostname == "agent.example"


@pytest.mark.unit
def test_revalidate_pinned_target_requires_canonical_current_policy_address():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["fd20::/16"],
        resolver=lambda *_args: (_ for _ in ()).throw(AssertionError("no DNS")),
        self_targets=[("fd20::1", 8765)],
    )
    target = policy.revalidate_pinned_target(
        "https://agent.example:8765", VERIFIED_TLS, "fd20::30"
    )
    assert target.pinned_url == "https://[fd20::30]:8765"

    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.revalidate_pinned_target(
            "https://agent.example:8765", VERIFIED_TLS, "fd20:0:0::30"
        )
    with pytest.raises(TargetPolicyError, match="target_address_not_allowed"):
        policy.revalidate_pinned_target(
            "https://agent.example:8765", VERIFIED_TLS, "10.20.30.40"
        )

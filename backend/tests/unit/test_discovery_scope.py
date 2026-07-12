import pytest
from pydantic import ValidationError

from ic_env_guard.config.models import ControlPlaneConfig
from ic_env_guard.discovery.service import DiscoveryService


def _control_plane(discovery):
    return ControlPlaneConfig(
        allowed_agent_cidrs=["10.20.0.0/16"],
        transport_profiles=[
            {
                "id": "eda-http",
                "type": "trusted_lan_http",
                "allowed_cidrs": ["10.20.0.0/16"],
            }
        ],
        discovery=discovery,
    )


def test_discovery_defaults_disabled_and_bounded():
    config = ControlPlaneConfig()
    assert config.discovery.scopes == ()
    assert config.discovery.max_concurrency == 32
    assert config.discovery.connect_timeout_ms == 500
    assert config.discovery.fingerprint_timeout_seconds == 2
    assert config.discovery.job_timeout_seconds == 120
    assert config.discovery.retention_seconds == 86_400
    assert config.discovery.max_targets == 2_048


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"max_concurrency": 33}, "less than or equal to 32"),
        ({"connect_timeout_ms": 0}, "greater than or equal to"),
        ({"fingerprint_timeout_seconds": 0}, "greater than"),
        ({"job_timeout_seconds": 0}, "greater than or equal to"),
        ({"retention_seconds": 60}, "greater than or equal to"),
        ({"max_targets": 2_049}, "less than or equal to 2048"),
    ],
)
def test_discovery_runtime_limits_are_bounded(override, message):
    with pytest.raises(ValidationError, match=message):
        _control_plane(override)


def test_scope_requires_private_allowlisted_cidr_and_known_endpoints():
    valid_scope = {
        "id": "eda-lab",
        "name": "EDA lab",
        "cidr": "10.20.30.0/24",
        "endpoints": [
            {"port": 8765, "transport_profile_id": "eda-http"},
            {"port": 9443, "transport_profile_id": "system-tls"},
        ],
    }
    assert _control_plane({"scopes": [valid_scope]}).discovery.scopes[0].id == "eda-lab"

    invalid = [
        ({**valid_scope, "cidr": "10.21.0.0/24"}, "subset"),
        ({**valid_scope, "cidr": "127.0.0.0/24"}, "private non-loopback"),
        ({**valid_scope, "cidr": "10.20.0.0/23"}, "at most 256"),
        (
            {
                **valid_scope,
                "endpoints": [{"port": 8765, "transport_profile_id": "missing"}],
            },
            "transport profile",
        ),
        ({**valid_scope, "endpoints": valid_scope["endpoints"] * 5}, "at most 8"),
    ]
    for scope, message in invalid:
        with pytest.raises(ValidationError, match=message):
            _control_plane({"scopes": [scope]})


def test_scope_ids_and_endpoints_are_unique():
    scope = {
        "id": "eda-lab",
        "name": "EDA lab",
        "cidr": "10.20.30.0/24",
        "endpoints": [{"port": 8765, "transport_profile_id": "eda-http"}],
    }
    with pytest.raises(ValidationError, match="scope IDs must be unique"):
        _control_plane({"scopes": [scope, scope]})
    with pytest.raises(ValidationError, match="endpoints must be unique"):
        _control_plane({"scopes": [{**scope, "endpoints": scope["endpoints"] * 2}]})


def test_http_scope_must_be_covered_by_profile_allowlist():
    with pytest.raises(ValidationError, match="HTTP profile allowlist"):
        ControlPlaneConfig(
            allowed_agent_cidrs=["10.20.0.0/16"],
            transport_profiles=[
                {
                    "id": "narrow-http",
                    "type": "trusted_lan_http",
                    "allowed_cidrs": ["10.20.30.0/24"],
                }
            ],
            discovery={
                "scopes": [
                    {
                        "id": "outside-profile",
                        "name": "Outside profile",
                        "cidr": "10.20.31.0/24",
                        "endpoints": [
                            {
                                "port": 8765,
                                "transport_profile_id": "narrow-http",
                            }
                        ],
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("cidr", "expected"),
    [
        ("10.20.30.0/31", 2),
        ("10.20.30.1/32", 1),
        ("fd00::/120", 255),
    ],
)
def test_scope_target_count_uses_stdlib_hosts_semantics(cidr, expected):
    network = "fd00::/120" if ":" in cidr else "10.20.0.0/16"
    profile = {
        "id": "http",
        "type": "trusted_lan_http",
        "allowed_cidrs": [network],
    }
    config = ControlPlaneConfig(
        allowed_agent_cidrs=[network],
        transport_profiles=[profile],
        discovery={
            "scopes": [
                {
                    "id": "scope",
                    "name": "Scope",
                    "cidr": cidr,
                    "endpoints": [
                        {"port": 8765, "transport_profile_id": "http"}
                    ],
                }
            ]
        },
    )
    service = DiscoveryService(
        config=config.discovery,
        transport_profiles=config.transport_profiles,
        repository=object(),
        fingerprinter=object(),
    )
    assert service.target_count(config.discovery.scopes[0]) == expected


def test_slash24_with_eight_endpoints_has_2032_actual_targets():
    endpoints = [
        {"port": 8700 + index, "transport_profile_id": "eda-http"}
        for index in range(8)
    ]
    config = _control_plane(
        {
            "scopes": [
                {
                    "id": "full",
                    "name": "Full",
                    "cidr": "10.20.30.0/24",
                    "endpoints": endpoints,
                }
            ]
        }
    )
    service = DiscoveryService(
        config=config.discovery,
        transport_profiles=config.transport_profiles,
        repository=object(),
        fingerprinter=object(),
    )
    assert service.target_count(config.discovery.scopes[0]) == 2_032

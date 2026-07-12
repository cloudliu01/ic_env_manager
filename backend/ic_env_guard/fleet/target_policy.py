import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from urllib.parse import urlsplit

from ic_env_guard.fleet.transport import (
    TransportProfile,
    TrustedLanHttpProfile,
    VerifiedTlsProfile,
)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
Resolver = Callable[[str, int], Iterable[str]]
METADATA_ADDRESSES = frozenset(
    {ip_address("169.254.169.254"), ip_address("fd00:ec2::254")}
)


class TargetPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedTarget:
    normalized_endpoint: str
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[IPAddress, ...]
    pinned_address: IPAddress
    host_header: str
    sni_hostname: str | None
    warning_code: str | None
    profile: TransportProfile

    @property
    def pinned_url(self) -> str:
        return f"{self.scheme}://{_url_host(str(self.pinned_address))}:{self.port}"


@dataclass(frozen=True)
class SafetyValidatedTarget:
    normalized_endpoint: str
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[IPAddress, ...]
    pinned_address: IPAddress
    host_header: str
    sni_hostname: str | None


class AgentTargetPolicy:
    def __init__(
        self,
        *,
        allowed_agent_cidrs: Sequence[str | IPNetwork],
        self_targets: Sequence[tuple[str | IPAddress, int]],
        resolver: Resolver | None = None,
    ) -> None:
        self._allowed = tuple(_as_network(value) for value in allowed_agent_cidrs)
        self._resolver = resolver or _resolve_addresses
        if not self_targets or any(port < 1 or port > 65535 for _, port in self_targets):
            raise TargetPolicyError(
                "target_policy_invalid", "at least one valid Manager self target is required"
            )
        self._self_targets = frozenset(
            (ip_address(address), port) for address, port in self_targets
        )

    def resolve(self, endpoint: str, profile: TransportProfile) -> ValidatedTarget:
        safety = self.validate_safety(endpoint)
        return self.resolve_validated(safety, profile)

    def revalidate_pinned_target(
        self, endpoint: str, profile: TransportProfile, stored_ip: str
    ) -> ValidatedTarget:
        scheme, hostname, port = _parse_endpoint(endpoint)
        _validate_profile_scheme(scheme, profile)
        try:
            address = ip_address(stored_ip)
        except ValueError:
            raise TargetPolicyError(
                "target_address_forbidden", "the stored Agent address is invalid"
            ) from None
        if str(address) != stored_ip or _is_forbidden(address):
            raise TargetPolicyError(
                "target_address_forbidden", "the stored Agent address is forbidden"
            )
        if (address, port) in self._self_targets:
            raise TargetPolicyError("target_is_manager", "the Agent target is the Manager")
        profile_allowlist = (
            tuple(profile.allowed_cidrs)
            if isinstance(profile, TrustedLanHttpProfile)
            else self._allowed
        )
        if not _in_any(address, self._allowed) or not _in_any(address, profile_allowlist):
            raise TargetPolicyError(
                "target_address_not_allowed", "the Agent address is outside the allowlist"
            )
        explicit_host = _url_host(hostname)
        default_port = 443 if scheme == "https" else 80
        return ValidatedTarget(
            normalized_endpoint=f"{scheme}://{explicit_host}:{port}",
            scheme=scheme,
            hostname=hostname,
            port=port,
            resolved_addresses=(address,),
            pinned_address=address,
            host_header=explicit_host if port == default_port else f"{explicit_host}:{port}",
            sni_hostname=hostname if scheme == "https" else None,
            warning_code=(
                "trusted_lan_http_unencrypted"
                if isinstance(profile, TrustedLanHttpProfile)
                else None
            ),
            profile=profile,
        )

    def validate_safety(self, endpoint: str) -> SafetyValidatedTarget:
        return self._validate_safety(endpoint, allow_loopback_http=False)

    def validate_legacy_import_http_safety(
        self, endpoint: str
    ) -> SafetyValidatedTarget:
        return self._validate_safety(endpoint, allow_loopback_http=True)

    def _validate_safety(
        self, endpoint: str, *, allow_loopback_http: bool
    ) -> SafetyValidatedTarget:
        scheme, hostname, port = _parse_endpoint(endpoint)
        if allow_loopback_http and scheme != "http":
            raise TargetPolicyError(
                "transport_profile_mismatch",
                "the Agent URL does not match its transport profile",
            )
        addresses = self._resolve_once(hostname, port)
        if allow_loopback_http and any(
            not address.is_loopback for address in addresses
        ):
            raise TargetPolicyError(
                "target_address_forbidden",
                "legacy imported HTTP targets must resolve only to loopback addresses",
            )
        for address in addresses:
            if _is_forbidden(address) and not (
                allow_loopback_http and address.is_loopback
            ):
                raise TargetPolicyError(
                    "target_address_forbidden", "the Agent address is forbidden"
                )
        for address in addresses:
            if (address, port) in self._self_targets:
                raise TargetPolicyError("target_is_manager", "the Agent target is the Manager")
        pinned = addresses[0]
        explicit_host = _url_host(hostname)
        default_port = 443 if scheme == "https" else 80
        host_header = explicit_host if port == default_port else f"{explicit_host}:{port}"
        return SafetyValidatedTarget(
            normalized_endpoint=f"{scheme}://{explicit_host}:{port}",
            scheme=scheme,
            hostname=hostname,
            port=port,
            resolved_addresses=addresses,
            pinned_address=pinned,
            host_header=host_header,
            sni_hostname=hostname if scheme == "https" else None,
        )

    def resolve_validated(
        self, safety: SafetyValidatedTarget, profile: TransportProfile
    ) -> ValidatedTarget:
        _validate_profile_scheme(safety.scheme, profile)
        profile_allowlist = (
            tuple(profile.allowed_cidrs)
            if isinstance(profile, TrustedLanHttpProfile)
            else self._allowed
        )
        for address in safety.resolved_addresses:
            if not _in_any(address, self._allowed) or not _in_any(address, profile_allowlist):
                raise TargetPolicyError(
                    "target_address_not_allowed", "the Agent address is outside the allowlist"
                )
        return ValidatedTarget(
            normalized_endpoint=safety.normalized_endpoint,
            scheme=safety.scheme,
            hostname=safety.hostname,
            port=safety.port,
            resolved_addresses=safety.resolved_addresses,
            pinned_address=safety.pinned_address,
            host_header=safety.host_header,
            sni_hostname=safety.sni_hostname,
            warning_code=(
                "trusted_lan_http_unencrypted"
                if isinstance(profile, TrustedLanHttpProfile)
                else None
            ),
            profile=profile,
        )

    def _resolve_once(self, hostname: str, port: int) -> tuple[IPAddress, ...]:
        try:
            literal = ip_address(hostname)
        except ValueError:
            try:
                raw_addresses = tuple(self._resolver(hostname, port))
                addresses = tuple(dict.fromkeys(ip_address(value) for value in raw_addresses))
            except (OSError, ValueError):
                raise TargetPolicyError(
                    "agent_network_error", "the Agent hostname could not be resolved"
                ) from None
        else:
            addresses = (literal,)
        if not addresses:
            raise TargetPolicyError(
                "agent_network_error", "the Agent hostname did not resolve to an address"
            )
        return addresses


def _parse_endpoint(endpoint: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        raise TargetPolicyError("target_url_invalid", "the Agent URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
        or "\\" in endpoint
        or any(ord(character) < 0x20 for character in endpoint)
        or port == 0
    ):
        raise TargetPolicyError(
            "target_url_invalid", "the Agent URL must contain only scheme, host, and port"
        )
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise TargetPolicyError("target_url_invalid", "the Agent hostname is invalid") from None
    effective_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, hostname, effective_port


def _validate_profile_scheme(scheme: str, profile: TransportProfile) -> None:
    valid = (isinstance(profile, VerifiedTlsProfile) and scheme == "https") or (
        isinstance(profile, TrustedLanHttpProfile) and scheme == "http"
    )
    if not valid:
        raise TargetPolicyError(
            "transport_profile_mismatch", "the Agent URL does not match its transport profile"
        )


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    return tuple(
        result[4][0]
        for result in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    )


def _as_network(value: str | IPNetwork) -> IPNetwork:
    return value if isinstance(value, (IPv4Network, IPv6Network)) else ip_network(value)


def _in_any(address: IPAddress, networks: Sequence[IPNetwork]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def _is_forbidden(address: IPAddress) -> bool:
    return (
        address in METADATA_ADDRESSES
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def _url_host(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname

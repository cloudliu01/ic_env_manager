import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from uuid import UUID

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError, ValidatedTarget
from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile

_CAPABILITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_PENDING_CAPABILITIES = {"manager-enrollment.v1", "summary.v2"}


class _UnsupportedAgentVersion(ValueError):
    pass


class EnrollmentValidationError(Exception):
    def __init__(self, code: str, *, dispatch_state: str = "unknown") -> None:
        super().__init__(code)
        self.code = code
        self.dispatch_state = dispatch_state


@dataclass(frozen=True)
class EnrollmentValidation:
    normalized_endpoint: str
    api_version: str
    agent_version: str
    capabilities: tuple[str, ...]
    instance_id: str | None
    summary: dict[str, Any] | None
    readiness_warning: str | None


class EnrollmentAgentClient:
    def __init__(
        self,
        *,
        target_policy: AgentTargetPolicy,
        transport_profiles: tuple[TransportProfile, ...],
        client: AgentHttpClient,
    ) -> None:
        self._policy = target_policy
        self._profiles = {profile.id: profile for profile in transport_profiles}
        self._client = client

    @property
    def max_network_operation_seconds(self) -> float:
        # Pending validation performs capabilities and summary requests serially.
        return float(self._client.max_request_timeout_seconds) * 2

    def prepare(self, endpoint: str, profile_id: str) -> ValidatedTarget:
        try:
            profile = self._profiles[profile_id]
            return self._policy.resolve(endpoint, profile)
        except KeyError as exc:
            raise EnrollmentValidationError(
                "transport_profile_invalid", dispatch_state="not_dispatched"
            ) from exc
        except TargetPolicyError as exc:
            raise EnrollmentValidationError(
                exc.code, dispatch_state="not_dispatched"
            ) from exc

    def prepare_pinned(
        self, endpoint: str, profile_id: str, stored_ip: str
    ) -> ValidatedTarget:
        try:
            profile = self._profiles[profile_id]
            return self._policy.revalidate_pinned_target(endpoint, profile, stored_ip)
        except KeyError as exc:
            raise EnrollmentValidationError(
                "transport_profile_invalid", dispatch_state="not_dispatched"
            ) from exc
        except TargetPolicyError as exc:
            raise EnrollmentValidationError(
                exc.code, dispatch_state="not_dispatched"
            ) from exc

    def prepare_cli_target(
        self,
        endpoint: str,
        profile_id: str,
        *,
        ssh_host: str,
        ssh_port: int,
        pinned_address: str,
    ) -> ValidatedTarget:
        try:
            profile = self._profiles[profile_id]
            http_target = self._policy.resolve(endpoint, profile)
            scheme = "http" if isinstance(profile, TrustedLanHttpProfile) else "https"
            url_host = f"[{ssh_host}]" if ":" in ssh_host else ssh_host
            ssh_target = self._policy.resolve(
                f"{scheme}://{url_host}:{ssh_port}", profile
            )
            pinned = ip_address(pinned_address)
            if str(pinned) != pinned_address or pinned not in set(
                http_target.resolved_addresses
            ) & set(ssh_target.resolved_addresses):
                raise TargetPolicyError(
                    "agent_identity_mismatch", "the CLI target does not match the Agent"
                )
            return replace(http_target, pinned_address=pinned)
        except KeyError as exc:
            raise EnrollmentValidationError(
                "transport_profile_invalid", dispatch_state="not_dispatched"
            ) from exc
        except (TargetPolicyError, ValueError) as exc:
            code = exc.code if isinstance(exc, TargetPolicyError) else "target_address_forbidden"
            raise EnrollmentValidationError(code, dispatch_state="not_dispatched") from exc
    async def validate_legacy(
        self, target: ValidatedTarget, token: bytes
    ) -> EnrollmentValidation:
        try:
            response = await self._client.request(
                target, token, "GET", "/api/capabilities"
            )
            if response.status_code != 200:
                raise EnrollmentValidationError(
                    "agent_protocol_error", dispatch_state="dispatched"
                )
            payload = _parse_capabilities(response.json(), expected_api="1", instance=False)
        except EnrollmentValidationError:
            raise
        except _UnsupportedAgentVersion:
            raise EnrollmentValidationError(
                "agent_version_unsupported", dispatch_state="dispatched"
            ) from None
        except AgentClientError as exc:
            raise EnrollmentValidationError(
                exc.category, dispatch_state=exc.dispatch_state
            ) from exc
        except (TypeError, ValueError):
            raise EnrollmentValidationError(
                "agent_protocol_error", dispatch_state="dispatched"
            ) from None
        return EnrollmentValidation(
            normalized_endpoint=target.normalized_endpoint,
            api_version=payload["api_version"],
            agent_version=payload["agent_version"],
            capabilities=payload["capabilities"],
            instance_id=None,
            summary=None,
            readiness_warning="legacy_readiness_unavailable",
        )

    async def validate_pending(
        self,
        target: ValidatedTarget,
        token: bytes,
        *,
        helper_instance_id: str,
    ) -> EnrollmentValidation:
        try:
            response = await self._client.request(
                target, token, "GET", "/api/v2/capabilities"
            )
            if response.status_code != 200:
                raise EnrollmentValidationError(
                    "agent_protocol_error", dispatch_state="dispatched"
                )
            payload = _parse_capabilities(response.json(), expected_api="2", instance=True)
            if not _is_canonical_uuid(helper_instance_id):
                raise EnrollmentValidationError(
                    "agent_protocol_error", dispatch_state="not_dispatched"
                )
            missing = _PENDING_CAPABILITIES - set(payload["capabilities"])
            if missing:
                raise EnrollmentValidationError(
                    "missing_capabilities", dispatch_state="dispatched"
                )
            if payload["instance_id"] != helper_instance_id:
                raise EnrollmentValidationError(
                    "agent_identity_mismatch", dispatch_state="dispatched"
                )
            summary = None
            warning = None
            try:
                summary_response = await self._client.request(
                    target, token, "GET", "/api/v2/summary"
                )
            except AgentClientError as exc:
                if exc.category == "agent_auth_error":
                    raise EnrollmentValidationError(
                        exc.category, dispatch_state=exc.dispatch_state
                    ) from exc
                warning = "agent_readiness_unavailable"
            else:
                if summary_response.status_code != 200:
                    warning = "agent_readiness_unavailable"
                else:
                    try:
                        summary = _parse_summary(summary_response.json())
                    except (TypeError, ValueError):
                        summary = None
                        warning = "agent_readiness_unavailable"
                    else:
                        if _summary_unhealthy(summary):
                            warning = "agent_readiness_unhealthy"
        except EnrollmentValidationError:
            raise
        except _UnsupportedAgentVersion:
            raise EnrollmentValidationError(
                "agent_version_unsupported", dispatch_state="dispatched"
            ) from None
        except AgentClientError as exc:
            raise EnrollmentValidationError(
                exc.category, dispatch_state=exc.dispatch_state
            ) from exc
        except (TypeError, ValueError):
            raise EnrollmentValidationError(
                "agent_protocol_error", dispatch_state="dispatched"
            ) from None
        return EnrollmentValidation(
            normalized_endpoint=target.normalized_endpoint,
            api_version=payload["api_version"],
            agent_version=payload["agent_version"],
            capabilities=payload["capabilities"],
            instance_id=payload["instance_id"],
            summary=summary,
            readiness_warning=warning,
        )

    async def activate(
        self,
        target: ValidatedTarget,
        token: bytes,
        *,
        enrollment_id: str,
        credential_id: str,
    ) -> None:
        try:
            response = await self._client.request(
                target,
                token,
                "POST",
                f"/api/v2/manager-credentials/{credential_id}/activate",
                json={"enrollment_id": enrollment_id},
            )
            if response.status_code not in {200, 204}:
                raise EnrollmentValidationError(
                    "agent_credential_activation_failed", dispatch_state="dispatched"
                )
        except EnrollmentValidationError:
            raise
        except AgentClientError as exc:
            raise EnrollmentValidationError(
                exc.category, dispatch_state=exc.dispatch_state
            ) from exc

    async def revoke(
        self,
        target: ValidatedTarget,
        token: bytes,
        *,
        credential_id: str,
    ) -> None:
        try:
            response = await self._client.request(
                target,
                token,
                "DELETE",
                f"/api/v2/manager-credentials/{credential_id}",
            )
            if response.status_code not in {200, 204, 404}:
                raise EnrollmentValidationError(
                    "agent_credential_revoke_failed", dispatch_state="dispatched"
                )
        except EnrollmentValidationError:
            raise
        except AgentClientError as exc:
            raise EnrollmentValidationError(
                exc.category, dispatch_state=exc.dispatch_state
            ) from exc


def _parse_capabilities(
    value: object, *, expected_api: str, instance: bool
) -> dict[str, Any]:
    expected_keys = {"api_version", "agent_version", "capabilities"}
    if instance:
        expected_keys.update({"instance_id", "name"})
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("api_version") != expected_api
    ):
        raise ValueError
    agent_version = value.get("agent_version")
    capabilities = value.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 256
        or any(
            not isinstance(item, str) or not _CAPABILITY.fullmatch(item)
            for item in capabilities
        )
    ):
        raise ValueError
    if not isinstance(agent_version, str) or not _VERSION.fullmatch(agent_version):
        raise _UnsupportedAgentVersion
    result: dict[str, Any] = {
        "api_version": expected_api,
        "agent_version": agent_version,
        "capabilities": tuple(capabilities),
    }
    if instance:
        instance_id = value.get("instance_id")
        name = value.get("name")
        if (
            not isinstance(instance_id, str)
            or not _is_canonical_uuid(instance_id)
            or not isinstance(name, str)
            or not 1 <= len(name) <= 128
        ):
            raise ValueError
        result["instance_id"] = instance_id
    return result


def _is_canonical_uuid(value: str) -> bool:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError):
        return False
    return str(parsed) == value


def _parse_summary(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "observed_at",
        "observations",
        "logs",
        "services",
        "terminals",
    }:
        raise ValueError
    observed_at = payload["observed_at"]
    if not isinstance(observed_at, str):
        raise ValueError
    parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    expected = {
        "observations": ("total", "warning", "critical", "stale"),
        "logs": ("total", "stale"),
        "services": ("total", "running", "unhealthy"),
        "terminals": ("active",),
    }
    result: dict[str, Any] = {
        "observed_at": parsed.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    }
    for section, fields in expected.items():
        raw = payload[section]
        if not isinstance(raw, dict) or set(raw) != set(fields):
            raise ValueError
        safe: dict[str, int] = {}
        for field in fields:
            count = raw[field]
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or not 0 <= count <= 2**63 - 1
            ):
                raise ValueError
            safe[field] = count
        result[section] = safe
    return result


def _summary_unhealthy(summary: dict[str, Any]) -> bool:
    return bool(
        summary["observations"]["critical"]
        or summary["observations"]["stale"]
        or summary["logs"]["stale"]
        or summary["services"]["unhealthy"]
    )

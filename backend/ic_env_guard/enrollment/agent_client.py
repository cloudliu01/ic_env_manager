import re
from dataclasses import dataclass
from typing import Any

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError, ValidatedTarget
from ic_env_guard.fleet.transport import TransportProfile

_CAPABILITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


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
                if summary_response.status_code != 200:
                    raise ValueError
                raw_summary = summary_response.json()
                if not isinstance(raw_summary, dict):
                    raise ValueError
                summary = raw_summary
            except (AgentClientError, TypeError, ValueError):
                warning = "agent_readiness_unavailable"
        except EnrollmentValidationError:
            raise
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


def _parse_capabilities(
    value: object, *, expected_api: str, instance: bool
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("api_version") != expected_api:
        raise ValueError
    agent_version = value.get("agent_version")
    capabilities = value.get("capabilities")
    if (
        not isinstance(agent_version, str)
        or not _VERSION.fullmatch(agent_version)
        or not isinstance(capabilities, list)
        or len(capabilities) > 256
        or any(
            not isinstance(item, str) or not _CAPABILITY.fullmatch(item)
            for item in capabilities
        )
    ):
        raise ValueError
    result: dict[str, Any] = {
        "api_version": expected_api,
        "agent_version": agent_version,
        "capabilities": tuple(capabilities),
    }
    if instance:
        instance_id = value.get("instance_id")
        if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128:
            raise ValueError
        result["instance_id"] = instance_id
    return result

from collections.abc import Mapping
from dataclasses import dataclass

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.fleet.ports import ManagerRegistryRepository
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError, ValidatedTarget
from ic_env_guard.fleet.transport import TransportProfile


class AgentProxyError(Exception):
    def __init__(
        self,
        code: str,
        status_code: int,
        *,
        dispatch_state: str = "not_dispatched",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.dispatch_state = dispatch_state
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class AgentProxyResponse:
    status_code: int
    body: dict[str, object]
    dispatch_state: str = "dispatched"


@dataclass(frozen=True)
class AgentRouteSnapshot:
    target: ValidatedTarget
    credential: bytes


class AgentHttpProxy:
    def __init__(
        self,
        *,
        registry: ManagerRegistryRepository,
        availability: AgentAvailabilityService,
        credential_store: CredentialStore,
        target_policy: AgentTargetPolicy | None,
        transport_profiles: tuple[TransportProfile, ...],
        client: AgentHttpClient,
    ) -> None:
        self._registry = registry
        self._availability = availability
        self._credentials = credential_store
        self._target_policy = target_policy
        self._profiles = {profile.id: profile for profile in transport_profiles}
        self._client = client

    def with_runtime(
        self, client: AgentHttpClient, availability: AgentAvailabilityService
    ) -> "AgentHttpProxy":
        return AgentHttpProxy(
            registry=self._registry,
            availability=availability,
            credential_store=self._credentials,
            target_policy=self._target_policy,
            transport_profiles=tuple(self._profiles.values()),
            client=client,
        )

    def resolve_captured_route(
        self,
        *,
        agent_id: str,
        revision: int,
        credential_ref: str,
        transport_profile_id: str,
        normalized_endpoint: str,
    ) -> AgentRouteSnapshot:
        captured = self._registry.get(agent_id)
        if not _matches_capture(
            captured,
            revision,
            credential_ref,
            transport_profile_id,
            normalized_endpoint,
        ):
            raise AgentProxyError("agent_target_changed", 409)
        profile = self._profiles.get(transport_profile_id)
        if profile is None or self._target_policy is None:
            raise AgentProxyError("agent_transport_profile_invalid", 409)
        try:
            target = self._target_policy.resolve(normalized_endpoint, profile)
            with self._credentials.lifecycle_lease():
                credential = self._credentials.read(credential_ref)
        except TargetPolicyError as exc:
            raise AgentProxyError(exc.code, _target_error_status(exc.code)) from exc
        except CredentialStoreError as exc:
            raise AgentProxyError("agent_auth_error", 503) from exc
        current = self._registry.get(agent_id)
        if not _matches_capture(
            current,
            revision,
            credential_ref,
            transport_profile_id,
            normalized_endpoint,
        ):
            raise AgentProxyError("agent_target_changed", 409)
        return AgentRouteSnapshot(target, credential)

    async def get_json(
        self,
        *,
        agent_id: str,
        capability: str,
        upstream_path: str,
        query: Mapping[str, str | int],
        correlation_id: str | None,
        tail: bool = False,
    ) -> AgentProxyResponse:
        return await self.request_json(
            agent_id=agent_id,
            capability=capability,
            method="GET",
            upstream_path=upstream_path,
            query=query,
            correlation_id=correlation_id,
            tail=tail,
        )

    async def request_json(
        self,
        *,
        agent_id: str,
        capability: str,
        method: str,
        upstream_path: str,
        query: Mapping[str, str | int],
        correlation_id: str | None,
        json: object | None = None,
        tail: bool = False,
    ) -> AgentProxyResponse:
        captured = self._registry.get(agent_id)
        if captured is None:
            raise AgentProxyError("agent_not_found", 404)
        if not captured.enabled:
            raise AgentProxyError("agent_disabled", 409)
        profile = self._profiles.get(captured.transport_profile_id)
        if profile is None or self._target_policy is None:
            raise AgentProxyError("agent_transport_profile_invalid", 409)
        try:
            target = self._target_policy.resolve(captured.normalized_endpoint, profile)
            with self._credentials.lifecycle_lease():
                credential = self._credentials.read(captured.credential_ref)
        except TargetPolicyError as exc:
            raise AgentProxyError(exc.code, _target_error_status(exc.code)) from exc
        except CredentialStoreError as exc:
            raise AgentProxyError("agent_auth_error", 503) from exc

        try:
            capability_check = await self._availability.check_capability(agent_id, capability)
        except Exception as exc:
            code = getattr(exc, "code", "agent_unavailable")
            dispatch = getattr(exc, "dispatch_state", "unknown")
            raise AgentProxyError(code, 503, dispatch_state=dispatch) from exc
        if not capability_check.supported:
            raise AgentProxyError(
                "agent_capability_missing",
                409,
                dispatch_state=capability_check.dispatch_state,
            )
        if not _matches_capture(
            self._registry.get(agent_id),
            captured.revision,
            captured.credential_ref,
            captured.transport_profile_id,
            captured.normalized_endpoint,
        ):
            raise AgentProxyError(
                "agent_target_changed",
                409,
                dispatch_state=capability_check.dispatch_state,
            )
        try:
            response = (
                await self._client.request_tail(
                    target,
                    credential,
                    upstream_path,
                    correlation_id=correlation_id,
                    params=query,
                    max_response_bytes=(1024 * 1024) - 4096,
                )
                if tail
                else await self._client.request(
                    target,
                    credential,
                    method,
                    upstream_path,
                    correlation_id=correlation_id,
                    params=query,
                    json=json,
                )
            )
            body = {} if response.status_code == 204 else response.json()
        except AgentClientError as exc:
            raise AgentProxyError(
                exc.category,
                503 if exc.category == "agent_network_error" else 502,
                dispatch_state=_combined_dispatch_state(
                    capability_check.dispatch_state, exc.dispatch_state
                ),
            ) from exc
        except (TypeError, ValueError) as exc:
            raise AgentProxyError("agent_protocol_error", 502, dispatch_state="dispatched") from exc
        if not isinstance(body, dict):
            raise AgentProxyError("agent_protocol_error", 502, dispatch_state="dispatched")
        current = self._registry.get(agent_id)
        if (
            current is None
            or not current.enabled
            or current.revision != captured.revision
            or current.normalized_endpoint != captured.normalized_endpoint
            or current.transport_profile_id != captured.transport_profile_id
            or current.credential_ref != captured.credential_ref
        ):
            raise AgentProxyError("agent_target_changed", 409, dispatch_state="dispatched")
        return AgentProxyResponse(response.status_code, body)


def _matches_capture(record, revision, credential_ref, profile_id, endpoint) -> bool:
    return (
        record is not None
        and record.enabled
        and record.revision == revision
        and record.credential_ref == credential_ref
        and record.transport_profile_id == profile_id
        and record.normalized_endpoint == endpoint
    )


def _combined_dispatch_state(first: str, second: str) -> str:
    if "dispatched" in {first, second}:
        return "dispatched"
    if "unknown" in {first, second}:
        return "unknown"
    return "not_dispatched"


def _target_error_status(code: str) -> int:
    return 503 if code == "agent_network_error" else 409

import asyncio
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

from ic_env_guard.agents.availability import AgentAvailabilityService, AgentObservation
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.fleet.models import (
    AgentQuery,
    AgentRecord,
    AgentStatus,
    EnrollmentMethod,
    RegistryConflict,
    RevisionConflict,
)
from ic_env_guard.fleet.ports import AgentStatusRepository, ManagerRegistryRepository
from ic_env_guard.fleet.status import derive_workload_status
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
from ic_env_guard.fleet.transport import (
    TransportProfile,
    TrustedLanHttpProfile,
    VerifiedTlsProfile,
)

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_AGENT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
DispatchState = Literal["not_dispatched", "unknown", "dispatched"]


@dataclass(frozen=True)
class ProbeResult:
    status: AgentStatus
    dispatch_state: DispatchState


class AgentProbeError(Exception):
    def __init__(
        self,
        code: str,
        *,
        dispatch_state: DispatchState = "unknown",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.dispatch_state = dispatch_state


class AgentProbeDisabled(AgentProbeError):
    pass


class FleetProbeService:
    def __init__(
        self,
        *,
        registry_repository: ManagerRegistryRepository,
        status_repository: AgentStatusRepository,
        credential_store: CredentialStore,
        target_policy: AgentTargetPolicy,
        transport_profiles: tuple[TransportProfile, ...],
        client: AgentHttpClient,
        stale_after_seconds: int,
        max_parallel_probes: int,
        probe_jitter_seconds: float = 1.0,
        clock: Callable[[], datetime] | None = None,
        legacy_availability: AgentAvailabilityService | None = None,
        allow_import_without_dynamic_allowlist: bool = False,
    ) -> None:
        self._registry = registry_repository
        self._statuses = status_repository
        self._credentials = credential_store
        self._target_policy = target_policy
        self._profiles = {profile.id: profile for profile in transport_profiles}
        self._client = client
        self._stale_after_seconds = stale_after_seconds
        self._semaphore = asyncio.Semaphore(max_parallel_probes)
        self._jitter = probe_jitter_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._legacy_availability = legacy_availability
        self._allow_import_without_dynamic_allowlist = (
            allow_import_without_dynamic_allowlist
        )
        self._identity_lock = asyncio.Lock()
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._published: dict[str, AgentStatus] = {}

    async def probe_all(self) -> dict[str, ProbeResult | AgentProbeError]:
        agents = [record for record in self._all_agents() if record.enabled]

        async def one(record: AgentRecord) -> tuple[str, ProbeResult | AgentProbeError]:
            if self._jitter:
                await asyncio.sleep(random.uniform(0, self._jitter))
            try:
                return record.agent_id, await self.probe(record.agent_id)
            except AgentProbeError as exc:
                return record.agent_id, exc

        results = await asyncio.gather(*(one(record) for record in agents))
        return dict(results)

    async def probe(self, agent_id: str) -> ProbeResult:
        lock = self._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            async with self._semaphore:
                return await self._probe(agent_id)

    async def _probe(self, agent_id: str) -> ProbeResult:
        captured = self._registry.get(agent_id)
        if captured is None:
            raise AgentProbeError("agent_not_found", dispatch_state="not_dispatched")
        if not captured.enabled:
            raise AgentProbeDisabled("agent_disabled", dispatch_state="not_dispatched")
        current_status = self._statuses.get(agent_id)
        if (
            current_status is not None
            and current_status.target_revision == captured.revision
            and current_status.last_error_code == "agent_identity_conflict"
        ):
            raise AgentProbeError(
                "agent_identity_conflict", dispatch_state="not_dispatched"
            )
        now = self._clock()
        aggregate_dispatch: DispatchState = "not_dispatched"
        try:
            if self._legacy_http_marker_allowed(captured):
                safety = self._target_policy.validate_legacy_import_http_safety(
                    captured.normalized_endpoint
                )
            else:
                safety = self._target_policy.validate_safety(
                    captured.normalized_endpoint
                )
            profile = self._profiles[captured.transport_profile_id]
            target = self._target_policy.resolve_validated(safety, profile)
            with self._credentials.lifecycle_lease():
                credential = self._credentials.read(captured.credential_ref)
            capabilities_response = await self._client.request(
                target,
                credential,
                "GET",
                "/api/v2/capabilities",
            )
            aggregate_dispatch = _combine_dispatch(
                aggregate_dispatch, "dispatched"
            )
            if getattr(capabilities_response, "status_code", 200) in {404, 405}:
                fallback = await self._legacy_fallback(
                    captured, now, aggregate_dispatch
                )
                if fallback is not None:
                    return fallback
            capabilities = _parse_capabilities(capabilities_response.json())
            summary_response = await self._client.request(
                target,
                credential,
                "GET",
                "/api/v2/summary",
            )
            aggregate_dispatch = _combine_dispatch(
                aggregate_dispatch, "dispatched"
            )
            summary = _parse_summary(summary_response.json())
        except KeyError:
            fallback = await self._legacy_fallback(captured, now, aggregate_dispatch)
            if fallback is not None:
                return fallback
            return ProbeResult(
                self._record_failure(
                    captured,
                    now,
                    "transport_profile_invalid",
                    dispatch_state="not_dispatched",
                ),
                "not_dispatched",
            )
        except CredentialStoreError:
            return ProbeResult(
                self._record_failure(
                    captured, now, "agent_auth_error", dispatch_state="not_dispatched"
                ),
                "not_dispatched",
            )
        except TargetPolicyError as exc:
            if (
                exc.code == "target_address_not_allowed"
                and self._allow_import_without_dynamic_allowlist
            ):
                fallback = await self._legacy_fallback(
                    captured, now, aggregate_dispatch
                )
                if fallback is not None:
                    return fallback
            return ProbeResult(
                self._record_failure(
                    captured, now, exc.code, dispatch_state="not_dispatched"
                ),
                "not_dispatched",
            )
        except AgentClientError as exc:
            aggregate_dispatch = _combine_dispatch(
                aggregate_dispatch, _dispatch_state(exc.dispatch_state)
            )
            return ProbeResult(
                self._record_failure(
                    captured,
                    now,
                    exc.category,
                    dispatch_state=aggregate_dispatch,
                ),
                aggregate_dispatch,
            )
        except (TypeError, ValueError):
            return ProbeResult(
                self._record_failure(
                    captured,
                    now,
                    "agent_protocol_error",
                    dispatch_state=aggregate_dispatch,
                ),
                aggregate_dispatch,
            )

        async with self._identity_lock:
            current = self._registry.get(agent_id)
            if current is None or current.revision != captured.revision:
                raise AgentProbeError("agent_target_changed", dispatch_state="dispatched")
            matching = [
                record
                for record in self._all_agents()
                if record.agent_id != agent_id
                and record.instance_id == capabilities["instance_id"]
            ]
            if matching:
                self._mark_identity_conflicts((current, *matching), now)
                raise AgentProbeError(
                    "agent_identity_conflict", dispatch_state="dispatched"
                )
            if (
                current.instance_id is not None
                and current.instance_id != capabilities["instance_id"]
            ):
                self._record_failure(
                    current,
                    now,
                    "agent_identity_changed",
                    dispatch_state="dispatched",
                )
                raise AgentProbeError("agent_identity_changed", dispatch_state="dispatched")
            if current.instance_id is None:
                try:
                    current = self._registry.update_if_revision(
                        replace(
                            current,
                            instance_id=capabilities["instance_id"],
                            updated_at=now,
                        ),
                        expected_revision=current.revision,
                    )
                except (RevisionConflict, RegistryConflict) as exc:
                    if isinstance(exc, RegistryConflict):
                        refreshed = self._registry.get(agent_id)
                        conflicts = [
                            record
                            for record in self._all_agents()
                            if record.agent_id != agent_id
                            and record.instance_id == capabilities["instance_id"]
                        ]
                        if refreshed is not None and conflicts:
                            self._mark_identity_conflicts((refreshed, *conflicts), now)
                            raise AgentProbeError(
                                "agent_identity_conflict", dispatch_state="dispatched"
                            ) from exc
                    raise AgentProbeError(
                        "agent_target_changed", dispatch_state="dispatched"
                    ) from exc

            missing = {"runtime.v2", "summary.v2"} - set(capabilities["capabilities"])
            status = AgentStatus(
                agent_id=agent_id,
                target_revision=current.revision,
                connection_status="degraded" if missing else "ready",
                workload_status=derive_workload_status(summary),
                observed_at=now,
                stale_after=now + timedelta(seconds=self._stale_after_seconds),
                api_version=capabilities["api_version"],
                agent_version=capabilities["agent_version"],
                capabilities=capabilities["capabilities"],
                summary=summary,
                last_error_code="missing_capabilities" if missing else None,
                updated_at=now,
            )
            if not self._statuses.update_if_target_revision(
                status, expected_revision=current.revision
            ):
                raise AgentProbeError("agent_target_changed", dispatch_state="dispatched")
            self._published[agent_id] = status
            return ProbeResult(status, aggregate_dispatch)

    async def _legacy_fallback(
        self,
        captured: AgentRecord,
        now: datetime,
        prior_dispatch: DispatchState,
    ) -> ProbeResult | None:
        if (
            self._legacy_availability is None
            or not self._legacy_fallback_allowed(captured)
        ):
            return None
        observation = await self._legacy_availability.probe_legacy(captured.agent_id)
        aggregate_dispatch = _combine_dispatch(
            prior_dispatch, _dispatch_state(observation.dispatch_state)
        )
        successful = observation.status in {"ready", "degraded"}
        try:
            if successful:
                _validate_legacy_observation(observation)
        except ValueError:
            status = self._failure_status(captured, now, "agent_protocol_error")
        else:
            previous = self._statuses.get(captured.agent_id)
            summary = (
                previous.summary
                if previous is not None
                and previous.target_revision == captured.revision
                else {}
            )
            status = AgentStatus(
                agent_id=captured.agent_id,
                target_revision=captured.revision,
                connection_status="degraded" if successful else "unavailable",
                workload_status="stale" if summary else "unknown",
                observed_at=now,
                stale_after=now + timedelta(seconds=self._stale_after_seconds),
                api_version=observation.api_version,
                agent_version=observation.agent_version,
                capabilities=observation.capabilities,
                summary=summary,
                last_error_code=(
                    "legacy_identity_unavailable"
                    if successful
                    else observation.last_error or "agent_unavailable"
                ),
                updated_at=now,
            )
        if not self._statuses.update_if_target_revision(status, captured.revision):
            raise AgentProbeError(
                "agent_target_changed",
                dispatch_state=aggregate_dispatch,
            )
        self._published[captured.agent_id] = status
        return ProbeResult(status, aggregate_dispatch)

    def _legacy_fallback_allowed(self, record: AgentRecord) -> bool:
        if not (
            record.source == "config_import"
            and record.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN
        ):
            return False
        if self._legacy_http_marker_allowed(record):
            return True
        scheme = urlsplit(record.normalized_endpoint).scheme
        if record.transport_profile_id == "legacy-disabled-no-credential":
            return False
        profile = self._profiles.get(record.transport_profile_id)
        return (isinstance(profile, VerifiedTlsProfile) and scheme == "https") or (
            isinstance(profile, TrustedLanHttpProfile) and scheme == "http"
        )

    @staticmethod
    def _legacy_http_marker_allowed(record: AgentRecord) -> bool:
        return (
            record.source == "config_import"
            and record.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN
            and record.transport_profile_id == "legacy-config-http"
            and urlsplit(record.normalized_endpoint).scheme == "http"
        )

    def _record_failure(
        self,
        captured: AgentRecord,
        now: datetime,
        error_code: str,
        *,
        dispatch_state: DispatchState = "unknown",
    ) -> AgentStatus:
        status = self._failure_status(captured, now, error_code)
        if not self._statuses.update_if_target_revision(
            status, expected_revision=captured.revision
        ):
            raise AgentProbeError(
                "agent_target_changed", dispatch_state=dispatch_state
            )
        self._published[captured.agent_id] = status
        return status

    def _failure_status(
        self, captured: AgentRecord, now: datetime, error_code: str
    ) -> AgentStatus:
        previous = self._statuses.get(captured.agent_id)
        summary = (
            previous.summary
            if previous is not None and previous.target_revision == captured.revision
            else {}
        )
        workload = "stale" if summary else "unknown"
        return AgentStatus(
            agent_id=captured.agent_id,
            target_revision=captured.revision,
            connection_status="unavailable",
            workload_status=workload,
            observed_at=now,
            stale_after=now + timedelta(seconds=self._stale_after_seconds),
            api_version=(
                previous.api_version
                if previous is not None and previous.target_revision == captured.revision
                else None
            ),
            agent_version=(
                previous.agent_version
                if previous is not None and previous.target_revision == captured.revision
                else None
            ),
            capabilities=(
                previous.capabilities
                if previous is not None and previous.target_revision == captured.revision
                else ()
            ),
            summary=summary,
            last_error_code=error_code,
            updated_at=now,
        )

    def _mark_identity_conflicts(
        self, records: tuple[AgentRecord, ...], now: datetime
    ) -> None:
        statuses = tuple(
            self._failure_status(record, now, "agent_identity_conflict")
            for record in records
        )
        if not self._statuses.update_many_if_target_revisions(statuses):
            raise AgentProbeError("agent_target_changed", dispatch_state="dispatched")
        for status in statuses:
            self._published[status.agent_id] = status

    def _all_agents(self) -> list[AgentRecord]:
        result: list[AgentRecord] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = self._registry.list(AgentQuery(limit=1000, cursor=cursor))
            result.extend(page.items)
            if page.next_cursor is None:
                return result
            if page.next_cursor == cursor or page.next_cursor in seen:
                raise AgentProbeError("agent_registry_pagination_invalid")
            seen.add(page.next_cursor)
            cursor = page.next_cursor


def _parse_capabilities(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid capabilities")
    instance_id = payload.get("instance_id")
    parsed_id = UUID(instance_id) if isinstance(instance_id, str) else None
    if parsed_id is None or str(parsed_id) != instance_id:
        raise ValueError("invalid instance identity")
    if payload.get("api_version") != "2":
        raise ValueError("unsupported API version")
    agent_version = payload.get("agent_version")
    raw_capabilities = payload.get("capabilities")
    if (
        not isinstance(agent_version, str)
        or _AGENT_VERSION.fullmatch(agent_version) is None
        or not isinstance(raw_capabilities, list)
        or len(raw_capabilities) > 256
        or any(
            not isinstance(value, str)
            or len(value) > 128
            or _CAPABILITY_ID.fullmatch(value) is None
            for value in raw_capabilities
        )
    ):
        raise ValueError("invalid capabilities")
    return {
        "instance_id": instance_id,
        "api_version": "2",
        "agent_version": agent_version,
        "capabilities": tuple(dict.fromkeys(raw_capabilities)),
    }


def _parse_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid summary")
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("invalid summary time")
    parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid summary time")
    expected = {
        "observations": ("total", "warning", "critical", "stale"),
        "logs": ("total", "stale"),
        "services": ("total", "running", "unhealthy"),
        "terminals": ("active",),
    }
    safe: dict[str, Any] = {
        "observed_at": parsed.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    }
    for section, fields in expected.items():
        value = payload.get(section)
        if not isinstance(value, dict):
            raise ValueError("invalid summary")
        safe_section: dict[str, int] = {}
        for field in fields:
            count = value.get(field)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("invalid summary count")
            safe_section[field] = count
        safe[section] = safe_section
    return safe


def _validate_legacy_observation(observation: AgentObservation) -> None:
    if observation.api_version != "1":
        raise ValueError("invalid legacy API version")
    if (
        not isinstance(observation.agent_version, str)
        or _AGENT_VERSION.fullmatch(observation.agent_version) is None
        or len(observation.capabilities) > 256
        or any(
            len(value) > 128 or _CAPABILITY_ID.fullmatch(value) is None
            for value in observation.capabilities
        )
    ):
        raise ValueError("invalid legacy metadata")


def _dispatch_state(value: str) -> DispatchState:
    if value in {"not_dispatched", "unknown", "dispatched"}:
        return cast(DispatchState, value)
    return "unknown"


def _combine_dispatch(left: DispatchState, right: DispatchState) -> DispatchState:
    priority = {"not_dispatched": 0, "unknown": 1, "dispatched": 2}
    return left if priority[left] >= priority[right] else right

import asyncio
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.fleet.models import (
    AgentQuery,
    AgentRecord,
    AgentStatus,
    RegistryConflict,
    RevisionConflict,
)
from ic_env_guard.fleet.ports import AgentStatusRepository, ManagerRegistryRepository
from ic_env_guard.fleet.status import derive_workload_status
from ic_env_guard.fleet.target_policy import AgentTargetPolicy, TargetPolicyError
from ic_env_guard.fleet.transport import TransportProfile


class AgentProbeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
        self._identity_lock = asyncio.Lock()
        self._published: dict[str, AgentStatus] = {}

    async def probe_all(self) -> dict[str, AgentStatus | AgentProbeError]:
        agents = [record for record in self._all_agents() if record.enabled]

        async def one(record: AgentRecord) -> tuple[str, AgentStatus | AgentProbeError]:
            if self._jitter:
                await asyncio.sleep(random.uniform(0, self._jitter))
            try:
                return record.agent_id, await self.probe(record.agent_id)
            except AgentProbeError as exc:
                return record.agent_id, exc

        results = await asyncio.gather(*(one(record) for record in agents))
        return dict(results)

    async def probe(self, agent_id: str) -> AgentStatus:
        async with self._semaphore:
            return await self._probe(agent_id)

    async def _probe(self, agent_id: str) -> AgentStatus:
        captured = self._registry.get(agent_id)
        if captured is None:
            raise AgentProbeError("agent_not_found")
        if not captured.enabled:
            raise AgentProbeDisabled("agent_disabled")
        now = self._clock()
        try:
            profile = self._profiles[captured.transport_profile_id]
            target = self._target_policy.resolve(captured.normalized_endpoint, profile)
            with self._credentials.lifecycle_lease():
                credential = self._credentials.read(captured.credential_ref)
            capabilities_response = await self._client.request(
                target,
                credential,
                "GET",
                "/api/v2/capabilities",
            )
            capabilities = _parse_capabilities(capabilities_response.json())
            summary_response = await self._client.request(
                target,
                credential,
                "GET",
                "/api/v2/summary",
            )
            summary = _parse_summary(summary_response.json())
        except KeyError:
            return self._record_failure(captured, now, "transport_profile_invalid")
        except CredentialStoreError:
            return self._record_failure(captured, now, "agent_auth_error")
        except TargetPolicyError as exc:
            return self._record_failure(captured, now, exc.code)
        except AgentClientError as exc:
            return self._record_failure(captured, now, exc.category)
        except (TypeError, ValueError):
            return self._record_failure(captured, now, "agent_protocol_error")

        async with self._identity_lock:
            current = self._registry.get(agent_id)
            if current is None or current.revision != captured.revision:
                raise AgentProbeError("agent_target_changed")
            matching = [
                record
                for record in self._all_agents()
                if record.agent_id != agent_id
                and record.instance_id == capabilities["instance_id"]
            ]
            if matching:
                self._mark_identity_conflicts((current, *matching), now)
                raise AgentProbeError("agent_identity_conflict")
            if (
                current.instance_id is not None
                and current.instance_id != capabilities["instance_id"]
            ):
                self._record_failure(current, now, "agent_identity_changed")
                raise AgentProbeError("agent_identity_changed")
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
                            raise AgentProbeError("agent_identity_conflict") from exc
                    raise AgentProbeError("agent_target_changed") from exc

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
                raise AgentProbeError("agent_target_changed")
            self._published[agent_id] = status
            return status

    def _record_failure(
        self, captured: AgentRecord, now: datetime, error_code: str
    ) -> AgentStatus:
        previous = self._statuses.get(captured.agent_id)
        summary = previous.summary if previous is not None else {}
        workload = "stale" if summary else "unknown"
        status = AgentStatus(
            agent_id=captured.agent_id,
            target_revision=captured.revision,
            connection_status="unavailable",
            workload_status=workload,
            observed_at=now,
            stale_after=now + timedelta(seconds=self._stale_after_seconds),
            api_version=previous.api_version if previous is not None else None,
            agent_version=previous.agent_version if previous is not None else None,
            capabilities=previous.capabilities if previous is not None else (),
            summary=summary,
            last_error_code=error_code,
            updated_at=now,
        )
        if not self._statuses.update_if_target_revision(
            status, expected_revision=captured.revision
        ):
            raise AgentProbeError("agent_target_changed")
        self._published[captured.agent_id] = status
        return status

    def _mark_identity_conflicts(
        self, records: tuple[AgentRecord, ...], now: datetime
    ) -> None:
        for record in records:
            self._record_failure(record, now, "agent_identity_conflict")

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
        or not agent_version
        or not isinstance(raw_capabilities, list)
        or len(raw_capabilities) > 256
        or any(not isinstance(value, str) or not value for value in raw_capabilities)
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
    for section, fields in expected.items():
        value = payload.get(section)
        if not isinstance(value, dict):
            raise ValueError("invalid summary")
        for field in fields:
            count = value.get(field)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("invalid summary count")
    return payload

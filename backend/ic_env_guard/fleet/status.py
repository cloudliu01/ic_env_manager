from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ic_env_guard.fleet.models import AgentQuery, AgentRecord, AgentStatus
from ic_env_guard.fleet.ports import AgentStatusRepository, ManagerRegistryRepository
from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile


class InvalidFleetCursor(Exception):
    pass


@dataclass(frozen=True)
class FleetAgentPage:
    agents: tuple[dict[str, Any], ...]
    next_cursor: str | None


class FleetStatusService:
    def __init__(
        self,
        registry_repository: ManagerRegistryRepository,
        status_repository: AgentStatusRepository,
        transport_profiles: tuple[TransportProfile, ...],
    ) -> None:
        self._registry = registry_repository
        self._statuses = status_repository
        self._profiles = {profile.id: profile for profile in transport_profiles}

    def get(self, agent_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        record = self._registry.get(agent_id)
        return self._project(record, now or datetime.now(UTC)) if record is not None else None

    def list(
        self,
        *,
        query: str | None = None,
        connection_status: str | None = None,
        workload_status: str | None = None,
        capability: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> FleetAgentPage:
        if not 1 <= limit <= 1000:
            raise ValueError("invalid limit")
        after = _decode_cursor(cursor) if cursor is not None else None
        current = now or datetime.now(UTC)
        projected = [self._project(record, current) for record in self._all_agents()]
        needle = query.casefold().strip() if query else None
        filtered = [
            item
            for item in projected
            if (after is None or item["agent_id"] > after)
            and (
                needle is None
                or needle in item["agent_id"].casefold()
                or needle in item["display_name"].casefold()
                or needle in item["endpoint"].casefold()
            )
            and (
                connection_status is None
                or item["connection_status"] == connection_status
            )
            and (workload_status is None or item["workload_status"] == workload_status)
            and (capability is None or capability in item["capabilities"])
        ]
        page = tuple(filtered[:limit])
        next_cursor = (
            _encode_cursor(page[-1]["agent_id"]) if len(filtered) > limit and page else None
        )
        return FleetAgentPage(agents=page, next_cursor=next_cursor)

    def overview(self, *, now: datetime | None = None) -> tuple[dict[str, Any], ...]:
        current = now or datetime.now(UTC)
        agents = [self._project(record, current) for record in self._all_agents()]
        connection_severity = {
            "unavailable": 0,
            "unknown": 1,
            "degraded": 2,
            "disabled": 3,
            "ready": 4,
        }
        workload_severity = {
            "critical": 0,
            "warning": 1,
            "stale": 2,
            "unknown": 3,
            "healthy": 4,
        }
        agents.sort(
            key=lambda item: (
                connection_severity[item["connection_status"]],
                workload_severity[item["workload_status"]],
                item["display_name"].casefold(),
                item["agent_id"],
            )
        )
        return tuple(agents)

    def _project(self, record: AgentRecord, now: datetime) -> dict[str, Any]:
        status = self._statuses.get(record.agent_id)
        if status is not None and status.target_revision != record.revision:
            status = None
        profile = self._profiles.get(record.transport_profile_id)
        return {
            "agent_id": record.agent_id,
            "instance_id": record.instance_id,
            "display_name": record.display_name,
            "endpoint": record.normalized_endpoint,
            "enabled": record.enabled,
            "revision": record.revision,
            "transport_profile_id": record.transport_profile_id,
            "transport_warning": (
                "trusted_lan_http_unencrypted"
                if isinstance(profile, TrustedLanHttpProfile)
                else None
            ),
            "connection_status": derive_connection_status(
                agent_enabled=record.enabled, probe=status, now=now
            ),
            "workload_status": (
                "unknown"
                if status is None
                else (
                    "stale"
                    if status.stale_after is not None and now >= status.stale_after
                    else status.workload_status
                )
            ),
            "observed_at": _time(status.observed_at) if status is not None else None,
            "stale_after": _time(status.stale_after) if status is not None else None,
            "api_version": status.api_version if status is not None else None,
            "agent_version": status.agent_version if status is not None else None,
            "capabilities": list(status.capabilities) if status is not None else [],
            "summary": status.summary if status is not None else {},
            "last_error_code": status.last_error_code if status is not None else None,
        }

    def _all_agents(self) -> list[AgentRecord]:
        records: list[AgentRecord] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = self._registry.list(AgentQuery(limit=1000, cursor=cursor))
            records.extend(page.items)
            if page.next_cursor is None:
                return records
            if page.next_cursor == cursor or page.next_cursor in seen:
                raise RuntimeError("Agent Registry pagination did not advance")
            seen.add(page.next_cursor)
            cursor = page.next_cursor


def derive_connection_status(
    *, agent_enabled: bool, probe: AgentStatus | None, now: datetime
) -> str:
    if not agent_enabled:
        return "disabled"
    if probe is None:
        return "unknown"
    if probe.last_error_code == "agent_identity_conflict":
        return "unavailable"
    if probe.stale_after is not None and now >= probe.stale_after:
        return "unknown"
    return probe.connection_status


def derive_workload_status(
    summary: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after: datetime | None = None,
) -> str:
    if summary is None:
        return "unknown"
    if now is not None and stale_after is not None and now >= stale_after:
        return "stale"
    if not summary:
        return "unknown"
    observations = _mapping(summary.get("observations"))
    services = _mapping(summary.get("services"))
    logs = _mapping(summary.get("logs"))
    if _count(observations, "critical") or _count(services, "unhealthy"):
        return "critical"
    if (
        _count(observations, "warning")
        or _count(observations, "stale")
        or _count(logs, "stale")
    ):
        return "warning"
    return "healthy"


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: dict[str, Any], key: str) -> int:
    candidate = value.get(key, 0)
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else 0


def _encode_cursor(agent_id: str) -> str:
    raw = json.dumps({"v": 1, "after": agent_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidFleetCursor("invalid cursor") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "after"}
        or payload["v"] != 1
        or not isinstance(payload["after"], str)
        or not payload["after"]
        or len(payload["after"]) > 128
    ):
        raise InvalidFleetCursor("invalid cursor")
    return payload["after"]


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

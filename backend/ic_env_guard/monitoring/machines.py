from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import error, request

from ic_env_guard.monitoring.snapshot import local_host_snapshot, offline_snapshot

HOST_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]+$")


@dataclass
class MonitoredMachine:
    id: str
    name: str
    address: str
    port: int
    key: str
    created_at: str
    updated_at: str

    @property
    def endpoint(self) -> str:
        return f"http://{self.address}:{self.port}"

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "endpoint": self.endpoint,
            "is_local": False,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RemoteSnapshotClient:
    def __init__(self, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, machine: MonitoredMachine) -> dict[str, object]:
        req = request.Request(
            f"{machine.endpoint}/api/monitoring/local",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {machine.key}",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                return offline_snapshot(
                    machine.id, machine.name, machine.endpoint, "authentication failed"
                )
            return offline_snapshot(machine.id, machine.name, machine.endpoint, f"HTTP {exc.code}")
        except Exception as exc:
            return offline_snapshot(machine.id, machine.name, machine.endpoint, str(exc))

        try:
            snapshot = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return offline_snapshot(
                machine.id, machine.name, machine.endpoint, "invalid JSON response"
            )
        if not isinstance(snapshot, dict):
            return offline_snapshot(
                machine.id, machine.name, machine.endpoint, "invalid response shape"
            )
        snapshot["host_id"] = machine.id
        snapshot["name"] = machine.name
        snapshot["address"] = machine.endpoint
        snapshot.setdefault("status", "online")
        return snapshot


class MachineRegistry:
    def __init__(self, remote_client: RemoteSnapshotClient | None = None) -> None:
        self._machines: dict[str, MonitoredMachine] = {}
        self.remote_client = remote_client or RemoteSnapshotClient()

    def list_machines(self) -> list[dict[str, object]]:
        local = {
            "id": "local",
            "name": "Local host",
            "address": "127.0.0.1",
            "port": None,
            "endpoint": "local",
            "is_local": True,
            "created_at": None,
            "updated_at": None,
        }
        return [local, *[machine.to_safe_dict() for machine in self._machines.values()]]

    def add_machine(
        self,
        *,
        address: str,
        port: int,
        key: str,
        name: str | None = None,
    ) -> dict[str, object]:
        clean_address = self._validate_address(address)
        clean_key = key.strip()
        if not clean_key:
            raise ValueError("machine key must not be empty")
        if port < 1 or port > 65535:
            raise ValueError("machine port must be between 1 and 65535")
        now = datetime.now(UTC).isoformat()
        machine = MonitoredMachine(
            id=str(uuid.uuid4()),
            name=(name or f"{clean_address}:{port}").strip(),
            address=clean_address,
            port=port,
            key=clean_key,
            created_at=now,
            updated_at=now,
        )
        self._machines[machine.id] = machine
        return machine.to_safe_dict()

    def delete_machine(self, machine_id: str) -> None:
        if machine_id == "local":
            raise PermissionError("local machine cannot be deleted")
        try:
            del self._machines[machine_id]
        except KeyError:
            raise KeyError(machine_id) from None

    def snapshot(self, machine_id: str) -> dict[str, object]:
        if machine_id == "local":
            return local_host_snapshot()
        try:
            machine = self._machines[machine_id]
        except KeyError:
            raise KeyError(machine_id) from None
        return self.remote_client.fetch(machine)

    @staticmethod
    def _validate_address(address: str) -> str:
        value = address.strip()
        if value.startswith("http://") or value.startswith("https://") or "/" in value:
            raise ValueError("machine address must be a host or IP without scheme/path")
        if not value or not HOST_PATTERN.fullmatch(value):
            raise ValueError("machine address contains unsupported characters")
        return value

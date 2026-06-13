from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.services.process_runner import ConfiguredProcessRunner, ProcessHandle


@dataclass
class ServiceOperationResult:
    operation_id: str
    service_id: str
    operation: str
    result: str
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "service_id": self.service_id,
            "operation": self.operation,
            "result": self.result,
            "failure_reason": self.failure_reason,
        }


class ServiceManager:
    def __init__(self, services: list[ServiceRuntime] | None = None) -> None:
        self.services = {service.id: service for service in services or []}
        self.runner = ConfiguredProcessRunner()
        self.handles: dict[str, ProcessHandle] = {}
        self.events: dict[str, list[dict[str, object]]] = {
            service_id: [] for service_id in self.services
        }

    def add_service(self, service: ServiceRuntime) -> None:
        self.services[service.id] = service
        self.events.setdefault(service.id, [])

    def list_services(self) -> list[dict[str, object]]:
        return [service.summary() for service in self.services.values()]

    def detail(self, service_id: str) -> dict[str, object]:
        return self._service(service_id).detail()

    def start(self, service_id: str) -> ServiceOperationResult:
        service = self._service(service_id)
        self._ensure_allowed(service, "start")
        if service.status == "running":
            return self._result(service, "start", "already_in_state")
        if not service.command:
            return self._result(service, "start", "rejected", "service has no command mapping")
        handle = self.runner.start(service.command)
        self.handles[service_id] = handle
        service.pid = handle.pid
        service.status = "running"
        service.updated_at = datetime.now(UTC).isoformat()
        self._event(service_id, "state_changed", "service started")
        return self._result(service, "start", "success")

    def stop(self, service_id: str) -> ServiceOperationResult:
        service = self._service(service_id)
        self._ensure_allowed(service, "stop")
        if service.status != "running":
            return self._result(service, "stop", "already_in_state")
        handle = self.handles.pop(service_id, None)
        if handle:
            self.runner.stop(handle)
        service.pid = None
        service.status = "exited"
        service.updated_at = datetime.now(UTC).isoformat()
        self._event(service_id, "state_changed", "service stopped")
        return self._result(service, "stop", "success")

    def restart(self, service_id: str) -> ServiceOperationResult:
        self.stop(service_id)
        result = self.start(service_id)
        result.operation = "restart"
        return result

    def service_events(self, service_id: str) -> list[dict[str, object]]:
        self._service(service_id)
        return self.events.get(service_id, [])

    def logs(self, service_id: str) -> dict[str, object]:
        self._service(service_id)
        return {"service_id": service_id, "truncated": False, "lines": []}

    def _service(self, service_id: str) -> ServiceRuntime:
        try:
            return self.services[service_id]
        except KeyError:
            raise KeyError(service_id) from None

    def _ensure_allowed(self, service: ServiceRuntime, operation: str) -> None:
        if operation not in service.allowed_operations:
            raise PermissionError(f"operation {operation} not allowed for service {service.id}")

    def _result(
        self,
        service: ServiceRuntime,
        operation: str,
        result: str,
        failure_reason: str | None = None,
    ) -> ServiceOperationResult:
        return ServiceOperationResult(
            operation_id=f"{service.id}-{operation}-{len(self.events.setdefault(service.id, []))}",
            service_id=service.id,
            operation=operation,
            result=result,
            failure_reason=failure_reason,
        )

    def _event(self, service_id: str, event_type: str, message: str) -> None:
        self.events.setdefault(service_id, []).append(
            {
                "id": str(len(self.events[service_id]) + 1),
                "service_id": service_id,
                "event_type": event_type,
                "message": message,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

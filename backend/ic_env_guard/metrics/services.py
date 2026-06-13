from prometheus_client import CollectorRegistry

from ic_env_guard.services.manager import ServiceManager


def update_service_metrics(
    registry: CollectorRegistry, service_manager: ServiceManager | None = None
) -> None:
    if service_manager is None:
        return
    names = registry._names_to_collectors
    for summary in service_manager.list_services():
        service = str(summary["id"])
        up = 1 if summary["status"] == "running" else 0
        names["ic_env_guard_service_up"].labels(service=service).set(up)
        names["ic_env_guard_service_healthcheck_success"].labels(service=service).set(
            1 if summary["health_status"] == "healthy" else 0
        )

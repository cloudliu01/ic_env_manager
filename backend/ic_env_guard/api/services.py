from typing import Annotated

from fastapi import APIRouter, Depends

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.services.manager import ServiceManager

router = APIRouter(prefix="/api/services", tags=["services"])


def get_service_manager() -> ServiceManager:
    raise RuntimeError("ServiceManager dependency was not configured")


def _call_service(fn, service_id: str):
    try:
        return fn(service_id)
    except KeyError:
        raise ApiError(404, "not_found", "service is not configured") from None
    except PermissionError as exc:
        raise ApiError(400, "operation_not_allowed", str(exc)) from None


@router.get("")
def list_services(
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, list[dict[str, object]]]:
    return {"services": manager.list_services()}


@router.get("/{service_id}")
def service_detail(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, object]:
    return _call_service(manager.detail, service_id)


@router.post("/{service_id}/start", status_code=202)
def start_service(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, object]:
    return _call_service(manager.start, service_id).to_dict()


@router.post("/{service_id}/stop", status_code=202)
def stop_service(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, object]:
    return _call_service(manager.stop, service_id).to_dict()


@router.post("/{service_id}/restart", status_code=202)
def restart_service(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, object]:
    return _call_service(manager.restart, service_id).to_dict()


@router.get("/{service_id}/events")
def service_events(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, list[dict[str, object]]]:
    return {"events": _call_service(manager.service_events, service_id)}


@router.get("/{service_id}/logs")
def service_logs(
    service_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    manager: Annotated[ServiceManager, Depends(get_service_manager)],
) -> dict[str, object]:
    return _call_service(manager.logs, service_id)

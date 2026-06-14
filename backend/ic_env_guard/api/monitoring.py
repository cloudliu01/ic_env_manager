from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.monitoring.machines import MachineRegistry
from ic_env_guard.monitoring.snapshot import local_host_snapshot

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])
DEPRECATED_MACHINE_SUCCESSOR = (
    '</api/agents/{agent_id}/monitoring/snapshot>; rel="successor-version"'
)


class CreateMachineRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    key: str = Field(min_length=1)


def get_machine_registry() -> MachineRegistry:
    raise RuntimeError("MachineRegistry dependency was not configured")


@router.get("/local")
def local_monitoring_snapshot(
    _: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, object]:
    return local_host_snapshot()


@router.get("/machines")
def list_machines(
    _: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[MachineRegistry, Depends(get_machine_registry)],
) -> dict[str, list[dict[str, object]]]:
    return {"machines": registry.list_machines()}


@router.post("/machines", status_code=201)
def add_machine(
    payload: CreateMachineRequest,
    response: Response,
    _: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[MachineRegistry, Depends(get_machine_registry)],
) -> dict[str, object]:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = DEPRECATED_MACHINE_SUCCESSOR
    try:
        machine = registry.add_machine(
            name=payload.name,
            address=payload.address,
            port=payload.port,
            key=payload.key,
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_machine", str(exc)) from None
    return machine


@router.delete("/machines/{machine_id}", status_code=204)
def delete_machine(
    machine_id: str,
    response: Response,
    _: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[MachineRegistry, Depends(get_machine_registry)],
) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = DEPRECATED_MACHINE_SUCCESSOR
    try:
        registry.delete_machine(machine_id)
    except PermissionError as exc:
        raise ApiError(400, "machine_not_deletable", str(exc)) from None
    except KeyError:
        raise ApiError(404, "not_found", "machine is not configured") from None


@router.get("/machines/{machine_id}/snapshot")
def machine_snapshot(
    machine_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[MachineRegistry, Depends(get_machine_registry)],
) -> dict[str, object]:
    try:
        return registry.snapshot(machine_id)
    except KeyError:
        raise ApiError(404, "not_found", "machine is not configured") from None

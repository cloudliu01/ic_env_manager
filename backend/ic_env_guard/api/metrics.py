from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CollectorRegistry, generate_latest

from ic_env_guard.api.errors import ApiError

router = APIRouter(tags=["metrics"])


class MetricsAccessPolicy:
    def __init__(self, remote_network_allowlist: list[str] | None = None) -> None:
        self.remote_network_allowlist = remote_network_allowlist or []

    def allows(self, source: str) -> bool:
        if source in {"localhost", "testclient"}:
            return True
        try:
            addr = ip_address(source)
        except ValueError:
            return False
        if addr.is_loopback:
            return True
        return any(
            addr in ip_network(network, strict=False) for network in self.remote_network_allowlist
        )


def get_metrics_access_policy() -> MetricsAccessPolicy:
    return MetricsAccessPolicy()


def get_metrics_registry() -> CollectorRegistry:
    raise RuntimeError("metrics registry dependency was not configured")


@router.get("/metrics")
def metrics(
    request: Request,
    policy: MetricsAccessPolicy = Depends(get_metrics_access_policy),
    registry: CollectorRegistry = Depends(get_metrics_registry),
) -> Response:
    source = (
        request.headers.get(
            "x-forwarded-for", request.client.host if request.client else "127.0.0.1"
        )
        .split(",")[0]
        .strip()
    )
    if not policy.allows(source):
        raise ApiError(403, "forbidden", "metrics source is not in network allowlist")
    return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")

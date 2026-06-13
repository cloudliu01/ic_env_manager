from enum import StrEnum


class RouteRisk(StrEnum):
    PUBLIC_HEALTH = "public_health"
    STATIC_ASSET = "static_asset"
    AUTHENTICATED_UI = "authenticated_ui"
    AUTHENTICATED_SERVICE_STATUS = "authenticated_service_status"
    PRIVILEGED_SERVICE_CONTROL = "privileged_service_control"
    PRIVILEGED_TERMINAL = "privileged_terminal"
    METRICS = "metrics"


PRIVILEGED_ROUTE_RISKS = {
    RouteRisk.PRIVILEGED_SERVICE_CONTROL,
    RouteRisk.PRIVILEGED_TERMINAL,
}


ROUTE_RISK_BY_PREFIX: tuple[tuple[str, RouteRisk], ...] = (
    ("/healthz", RouteRisk.PUBLIC_HEALTH),
    ("/readyz", RouteRisk.PUBLIC_HEALTH),
    ("/metrics", RouteRisk.METRICS),
    ("/assets/", RouteRisk.STATIC_ASSET),
    ("/api/monitoring", RouteRisk.AUTHENTICATED_UI),
    ("/api/audit", RouteRisk.AUTHENTICATED_UI),
    ("/api/terminals", RouteRisk.PRIVILEGED_TERMINAL),
    ("/ws/terminals", RouteRisk.PRIVILEGED_TERMINAL),
    ("/api/services", RouteRisk.AUTHENTICATED_SERVICE_STATUS),
    ("/", RouteRisk.AUTHENTICATED_UI),
)


def classify_route(path: str, method: str = "GET") -> RouteRisk:
    if path.startswith("/api/services") and method.upper() in {"POST", "DELETE", "PUT", "PATCH"}:
        return RouteRisk.PRIVILEGED_SERVICE_CONTROL
    for prefix, risk in ROUTE_RISK_BY_PREFIX:
        if path == prefix or path.startswith(prefix):
            return risk
    return RouteRisk.AUTHENTICATED_UI

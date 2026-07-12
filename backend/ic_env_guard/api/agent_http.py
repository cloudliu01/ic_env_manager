from ic_env_guard.agents.client import AgentHttpClient

ERROR_STATUS = {
    "agent_unavailable": 503,
    "agent_timeout": 504,
    "agent_operation_indeterminate": 424,
    "agent_protocol_error": 502,
}

MUTATING_METHODS = {"DELETE", "PATCH", "POST", "PUT"}


def failure_category_for_client_error(method: str, category: str) -> str:
    if category in {"agent_timeout", "agent_network_error"} and method.upper() in MUTATING_METHODS:
        return "agent_operation_indeterminate"
    if category in {"agent_network_error", "agent_tls_error"}:
        return "agent_unavailable"
    return category


def augment_upstream_error_body(
    body: object, *, agent_id: str, correlation_id: str | None, status_code: int
) -> object:
    if status_code < 400 or not isinstance(body, dict) or "error" not in body:
        return body
    return {**body, "agent_id": agent_id, "correlation_id": correlation_id}


def get_agent_http_client() -> AgentHttpClient:
    raise RuntimeError("AgentHttpClient dependency was not configured")

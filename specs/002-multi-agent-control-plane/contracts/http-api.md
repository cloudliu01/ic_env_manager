# Multi-Agent HTTP API Contract

## General Rules

- All control-plane API routes (`/api/...`) require browser authentication and
  explicit authorization, with the sole exceptions of `/healthz`, `/readyz`, and
  `/api/auth/login` (the login endpoint must be reachable before a session token
  exists). This includes `/api/agents/...`, `/api/control-plane/audit`, and any
  future control-plane paths.
- The control plane accepts and returns JSON unless stated otherwise.
- Every response includes `X-Correlation-ID`.
- Agent credentials, upstream tickets, raw upstream URLs, and unsanitized
  exceptions are never returned.
- Redirects from agents are protocol errors.
- Response bodies above the configured limit are rejected as protocol errors.

## Agent Inventory

### `GET /api/agents`

```json
{
  "agents": [
    {
      "id": "lab-host-01",
      "name": "Lab Host 01",
      "enabled": true,
      "location": "lab-a",
      "status": "ready",
      "observed_at": "2026-06-14T10:00:00Z",
      "stale_after": "2026-06-14T10:00:30Z",
      "api_version": "1",
      "agent_version": "0.2.0",
      "capabilities": ["services.v1", "monitoring.snapshot.v1"],
      "last_error": null
    }
  ]
}
```

The response does not expose `base_url`, token paths, CA paths, or internal
network errors.

### `GET /api/agents/{agent_id}`

Returns one `AgentSummary`. Unknown IDs return `agent_not_found`.

### `POST /api/agents/{agent_id}/probe`

Runs one bounded on-demand capability/readiness probe and returns the new
summary. It does not alter configuration.

## Host Status

### `GET /api/agents/{agent_id}/healthz`

Proxies the agent liveness contract.

### `GET /api/agents/{agent_id}/readyz`

Proxies the agent readiness contract. An agent-reported `503` remains a
successful gateway exchange with an agent readiness response; it is not
rewritten as transport unavailability.

### `GET /api/agents/{agent_id}/monitoring/snapshot`

Returns the authenticated JSON host snapshot currently exposed by
`/api/monitoring/local`.

## Services

The gateway preserves the current host-agent methods and status codes:

```text
GET  /api/agents/{agent_id}/services
GET  /api/agents/{agent_id}/services/{service_id}
POST /api/agents/{agent_id}/services/{service_id}/start
POST /api/agents/{agent_id}/services/{service_id}/stop
POST /api/agents/{agent_id}/services/{service_id}/restart
GET  /api/agents/{agent_id}/services/{service_id}/events
GET  /api/agents/{agent_id}/services/{service_id}/logs
```

Mutating requests are dispatched once. The gateway does not turn a timeout into
a retry.

## Terminals

The gateway preserves the current host-agent resource semantics:

```text
GET    /api/agents/{agent_id}/terminals
POST   /api/agents/{agent_id}/terminals
GET    /api/agents/{agent_id}/terminals/{terminal_id}
DELETE /api/agents/{agent_id}/terminals/{terminal_id}
GET    /api/agents/{agent_id}/terminals/{terminal_id}/history?cursor={cursor}
POST   /api/agents/{agent_id}/terminals/{terminal_id}/connect-token
POST   /api/agents/{agent_id}/terminals/{terminal_id}/resize
```

`connect-token` returns a control-plane ticket:

```json
{
  "ticket": "gateway-one-use-ticket",
  "expires_in_seconds": 60
}
```

The upstream ticket is stored only in the control plane and consumed by the
WebSocket proxy. When the bounded ticket store is already full, `connect-token`
returns `429 gateway_capacity_exceeded` and does not request an upstream ticket
from the agent.

## Audit

### `GET /api/agents/{agent_id}/audit`

Supported query fields:

- `limit`, from 1 to 1000
- `target_type`
- `result`

Each returned event adds a stable `agent_id`. The gateway does not merge events
from multiple agents in the initial release.

### `GET /api/control-plane/audit`

Returns durable routing audit records owned by the control plane. It supports
bounded filters for `agent_id`, `operation`, `result`, and `correlation_id`.

## Capability Negotiation

Each host agent exposes:

```text
GET /api/capabilities
```

It returns:

```json
{
  "api_version": "1",
  "agent_version": "0.2.0",
  "capabilities": [
    "services.v1",
    "terminals.v1",
    "audit.v1",
    "monitoring.snapshot.v1"
  ]
}
```

The control plane does not call a resource route unless the corresponding
capability is present. An agent that does not expose `GET /api/capabilities` is
below the minimum supported version: it is reported as `agent_protocol_error`
and no resource features are enabled for it.

## Normalized Errors

```json
{
  "error": "agent_unavailable",
  "message": "Lab Host 01 is unavailable",
  "agent_id": "lab-host-01",
  "correlation_id": "01J..."
}
```

Allowed gateway codes:

- `invalid_agent_request`
- `unauthorized`
- `agent_operation_forbidden`
- `agent_not_found`
- `agent_disabled`
- `gateway_capacity_exceeded` — `429`; ticket store full at `connect-token` time
- `agent_operation_indeterminate`
- `agent_protocol_error`
- `agent_unavailable`
- `agent_timeout`

Upstream application errors such as `not_found` or `operation_not_allowed` retain
their original status and safe body, with `agent_id` and `correlation_id` added.


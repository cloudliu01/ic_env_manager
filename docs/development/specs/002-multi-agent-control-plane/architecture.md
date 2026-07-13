# Multi-Agent Control Plane Architecture

## Decision

Use a control-plane gateway between the browser and host agents:

```text
Browser
  |
  | same-origin HTTP and WebSocket
  v
Control Plane
  |
  | authenticated HTTPS
  v
Host Agent A   Host Agent B   Host Agent C
```

The browser authenticates only to the control plane. Agent credentials and
upstream terminal tickets never leave the server.

## Why This Architecture

The gateway keeps browser deployment same-origin, centralizes user
authentication and audit, avoids exposing every agent directly to the browser,
and gives terminal WebSocket routing one controlled network boundary.

Direct browser-to-agent access is rejected for the first release because it
would require per-agent CORS, TLS trust, credential storage, network reachability,
and WebSocket handling in the browser.

## Runtime Modes

### `agent`

- Runs the existing single-host agent.
- Owns PTYs, service processes, local metrics, local state, and local audit.
- Exposes the existing `001` API and WebSocket contracts.
- Does not expose `/api/agents/...`.

### `control-plane`

- Serves the frontend and `/api/agents/...`.
- Owns the agent registry, HTTP/WS clients, availability observations, gateway
  tickets, and gateway audit.
- Does not implicitly manage services or terminals on its own host.

### `combined` (out of scope — feature `003`)

`combined` would run both roles in one process for small deployments, with a
synthetic local target. To satisfy FR-006 (no HTTP self-proxy) it must call the
local managers directly rather than loop back through its own listener — which
requires a transport abstraction underneath every resource router. Forcing that
seam into this feature's routing for the lowest-value deployment is not
worthwhile, so `combined` is split into a follow-up feature `003` that
introduces a domain-typed transport (`ServiceTarget`, `TerminalTarget`,
`MonitoringTarget`).

`combined` is not in the `mode` enum for this feature. A config file with
`mode: combined` fails Pydantic validation with a clear unknown-value error;
it does not reach application startup. Feature `003` adds `combined` to the
enum when it is implemented.

Mode defaults to `agent`, so existing single-host configurations and contracts
are unchanged. An empty `agents` list in `control-plane` mode is valid but
leaves no active agent; it does not silently create a self-referential target.

## Component Boundaries

### Agent Registry

Loads validated immutable agent targets from configuration and resolves IDs.
It is the only registry used by overview, monitoring, services, terminals, and
audit.

The existing dynamic `MachineRegistry` and browser-side machine credential flow
are deprecated during the documented compatibility release and removed only after
that window has elapsed. The new frontend stops using the mutation routes in this
feature.

### Agent HTTP Client

Resource routers call one constrained HTTP client directly; this feature does
not introduce a transport-interface seam (that arrives with the `combined`
follow-up feature `003`). The client owns connection pooling, TLS verification,
deadlines, correlation headers, response-size limits, content-type checks, and
normalized transport errors.

The terminal WebSocket is a separate path: it is opened with a dedicated WS
client (`websockets`) using an `ssl.SSLContext` built from the same per-target
TLS settings, because the HTTP client (`httpx`) cannot act as a WebSocket
client. Both `httpx` and `websockets` are declared as runtime dependencies.

The client does not decide authorization or expose generic arbitrary-path
proxying.

### Resource Routers

Explicit routers cover agents, services, terminals, audit, and host snapshots.
Each route:

1. authenticates the browser;
2. authorizes the operation;
3. resolves the configured agent;
4. validates the capability;
5. records a durable gateway audit intent;
6. dispatches one allowlisted upstream request;
7. records the outcome;
8. returns a normalized response.

### Availability Service

Periodically probes enabled agents with bounded concurrency and jitter. It stores
only the latest in-memory observation; durable audit records capture meaningful
state changes without creating a time-series database.

On-demand API calls are not blocked by stale cached status.

### Gateway Audit

Uses a dedicated migration-managed SQLite database at
`control_plane.audit_database`, created and durable from MVP 1. Its schema's
single source of truth is the control-plane migration; the ORM model maps the
table and does not `create_all` it.

Because the shared `db/migrations.py` runner applies every migration file to any
database it is handed, the control plane uses its own migration directory
(`ic_env_guard/control_plane_migrations/`) and `run_control_plane_migrations()`
runner. Both directories live inside the `ic_env_guard` package so they are
included in the wheel without additional packaging configuration. The two
databases never receive each other's tables. This is separate from the `001`
agent audit database, whose own durability defect (it was in-memory) is fixed as
a prerequisite so the agent-audit view can meet the original `001` contract; see
the implementation plan's Task 0. As part of Task 0, the existing agent
migrations are also moved from `backend/migrations/` into
`ic_env_guard/migrations/` to fix a pre-existing packaging bug where `MIGRATIONS_DIR`
resolved incorrectly after wheel install.

Every privileged routing attempt is recorded even when DNS, TLS, authentication,
or connection setup fails.

If the gateway audit intent cannot be durably committed, the handler fails
closed and does not dispatch upstream. If the outcome write fails after the
upstream operation succeeds, the handler returns the successful business response
to avoid inducing duplicate mutations, logs only a sanitized warning, and marks
gateway readiness failed until a subsequent audit write succeeds. Audit write
exceptions never replace the original business exception returned by routing
logic.

Gateway events include:

- actor ID and source address;
- agent ID;
- operation and target resource;
- correlation ID;
- dispatch state: `not_dispatched`, `dispatched`, or `unknown`;
- normalized result and upstream status where available.

The agent continues recording the actual local operation. The propagated
correlation ID associates the two records.

### Terminal Gateway

The HTTP connect-token endpoint first reserves capacity in the bounded ticket
store; if the store is full it returns HTTP `429 gateway_capacity_exceeded`
without contacting the agent, so a full gateway never wastes an upstream ticket.
Only after reserving does it obtain the upstream one-use ticket and return a
separate gateway ticket to the browser. Any failure releases the reservation.

The mapping is bound to:

```text
(gateway_ticket, actor_id, agent_id, terminal_id, intended_ws_path, upstream_ticket, expires_at)
```

`intended_ws_path` is the exact control-plane WebSocket route for the selected
agent and terminal. On attach, the browser must be authenticated as the actor
bound to the ticket; actor mismatch is rejected before opening the upstream
socket. The ticket is consumed once. Restart invalidates gateway tickets but does
not alter upstream terminal ownership.

The WebSocket attach calls `try_acquire_proxy_slot()` — an atomic operation that
either reserves a slot under the global cap or fails immediately. If it fails,
the attach is rejected with `4429` before touching the ticket store, so no valid
ticket is consumed and no TOCTOU race exists between a pre-check and the actual
acquire. After acquiring a slot, the gateway consumes the ticket; any failure
releases the slot. Together with the connect-token `429` (which reserves ticket
capacity before requesting the upstream ticket), this bounds both outstanding
tickets and concurrent sockets so a flood of terminal connections cannot exhaust
gateway memory. The upstream socket is opened with the `websockets` client and
verified TLS.

Each accepted proxy enforces a maximum frame size, bounded per-direction queues,
paired task cancellation, sanitized close-code mapping, reconnect cursor
handling, and shutdown cleanup. A single accepted socket therefore cannot bypass
the global cap by buffering unbounded terminal output.

## Configuration

```yaml
mode: control-plane          # valid values: agent | control-plane (combined = feature 003)

server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false

auth:
  mode: bearer_token
  token_file: /etc/ic-env-guard/control-plane.token

control_plane:
  poll_interval_seconds: 10
  status_stale_after_seconds: 30
  max_parallel_probes: 8
  audit_database: /var/lib/ic-env-guard/control-plane.db   # gateway audit; separate from state_database
  max_active_terminal_proxies: 64
  max_outstanding_tickets: 128

agents:
  - id: lab-host-01
    name: Lab Host 01
    base_url: https://lab-host-01.example.com:8765
    token_file: /etc/ic-env-guard/agents/lab-host-01.token
    tls:
      verify: true
      ca_bundle: /etc/ic-env-guard/ca/lab.pem
    connect_timeout_seconds: 3
    request_timeout_seconds: 10
    enabled: true
```

Validation rules:

- `base_url` contains only scheme, host, and optional port.
- URL userinfo, path other than `/`, query, and fragment are rejected.
- Non-loopback targets require HTTPS and `tls.verify: true`.
- An insecure loopback exception requires `development.allow_insecure_http:
  true`; it is rejected when the control plane binds remotely.
- Exactly one credential source is configured.
- Token files must be regular files owned by the service user and not readable
  by group or other.
- Agent IDs and names are never derived from remote response content.
- `state_database` is an agent-mode setting. In `control-plane` mode, startup
  does not resolve, create, or migrate the agent state database.

## Security Boundaries

### Browser to Control Plane

Uses the existing local-administrator bearer authentication in the first
release. Remote exposure still follows the `001` fail-closed bind rules.

### Control Plane to Agent

Uses HTTPS with certificate verification and a distinct bearer credential per
agent. Tokens are attached only to allowlisted upstream requests.

Forwarded headers are limited to:

- `Accept`
- `Content-Type` where required
- generated `X-Correlation-ID`

Browser `Authorization`, cookies, forwarding headers, host headers, and arbitrary
client headers are never forwarded.

### SSRF and Proxy Restrictions

Agent targets come only from validated startup configuration. The browser cannot
submit URLs, hosts, ports, or arbitrary paths. Redirect following is disabled.

Configured hostnames are resolved before connection. The control plane rejects
metadata/link-local, multicast, unspecified, and self-target addresses, including
the control-plane listener itself. RFC1918/private agent networks are allowed
only when they come from startup configuration and still satisfy TLS and
credential validation rules.

Each gateway route owns an explicit allowlist for method, upstream path,
accepted query fields, forwarded headers, and response content type. Unknown
query fields are rejected for mutating routes and ignored only where the
documented inherited host-agent contract already permits optional read filters.

## Compatibility

The agent exposes a version/capabilities endpoint:

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

The control plane enables UI features only when the corresponding capability is
present. Missing capability endpoint or unsupported API version produces
`agent_protocol_error` and enables no features for that agent. Missing optional
capabilities may show inventory status `degraded`, but the corresponding feature
is disabled and its route is not called.

The first compatibility window supports the current `001` contract plus the new
capability endpoint. Existing local routes remain authoritative on each agent.

There is no support for agents older than the version that introduces
`GET /api/capabilities`. A control plane pointed at a pre-`002` agent that lacks
this endpoint reports `agent_protocol_error` for that agent and enables no
features for it, rather than partially working against an unknown contract. The
documented minimum agent version is therefore the first release that ships the
capability endpoint.

## Metrics and Monitoring

These are separate interfaces:

- `/api/agents/{agent_id}/monitoring/snapshot` is authenticated JSON for the UI.
- Each agent's `/metrics` remains Prometheus text protected by its network
  allowlist.

The control plane does not proxy or merge raw `/metrics` in the initial feature.
Prometheus should scrape agents directly.

## Failure Semantics

- One unavailable agent never blocks other agents.
- Gateway `/healthz` reports process liveness.
- Gateway `/readyz` checks gateway configuration, credential loading, audit
  storage, and internal dependencies; it does not require all agents to be ready.
- Read-only calls may perform one bounded retry only for a failure known to occur
  before dispatch.
- Mutations are never automatically retried.
- Timeout after a mutation may have been dispatched returns
  `agent_operation_indeterminate`.
- Raw upstream exceptions are logged only after sanitization and are not returned
  to the browser.

## Migration

0. Make the existing agent audit store durable and migration-managed (it is
   in-memory today), as a prerequisite that brings `agent` mode into line with
   `001` FR-021/FR-026.
1. Add runtime modes and the static agent registry without otherwise changing
   `agent` mode.
2. Add control-plane services/readiness routes and frontend global selector.
3. Replace the monitoring-specific machine registry with the global registry.
4. Add terminal HTTP and WebSocket gateway support.
5. Add agent-scoped audit queries.
6. Remove deprecated `/api/monitoring/machines` mutation endpoints after one
   documented compatibility release.

Rollback is configuration-only: run the backend in `agent` mode and use the
existing single-host frontend/API paths.

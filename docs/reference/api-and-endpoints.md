# API and Endpoint Reference

This is an operator map of runtime surfaces, callers, authentication, and
exposure. It groups related routes instead of duplicating generated OpenAPI
schemas. Use the runtime's `/docs` or `/openapi.json` during trusted development
for exact request/response schemas.

## Agent Public Listener

Default: `http://127.0.0.1:8765`. Intended callers are the standalone browser,
Manager, Prometheus, and authenticated operator/API clients. Public never
accepts Observation or Log Source writes.

| Route family | Authentication | Purpose |
| --- | --- | --- |
| `POST /api/auth/login`, `POST /api/auth/logout` | Login token/session flow | Establish or end browser authentication. |
| `GET /healthz`, `GET /readyz` | None; bounded output | Process liveness and Agent readiness. |
| `GET /metrics` | Source CIDR policy, not browser bearer | Prometheus text exposition. |
| `GET /api/v2/runtime`, `/capabilities`, `/summary` | Bearer/session | Stable identity, API/capability contract, current summary. |
| `GET /api/v2/observations[/{identity_key}]` | Bearer/session | Current/paged Observation reads. |
| `GET /api/v2/logs[/{log_id}]` | Bearer/session | Log Source metadata reads. |
| `GET /api/v2/logs/{log_id}/tail` | Bearer/session plus audit | Bounded on-demand file tail. |
| `/api/v2/manager-credentials...` | Authenticated Manager credential flow | List/activate/revoke Agent-side managed Manager credentials. |
| `/api/services...` | Bearer/session | Configured service list, state, events, captured logs, and allowlisted actions. |
| `/api/terminals...` | Bearer/session | Create/list/read/resize/close PTYs and issue a one-use WS ticket. |
| `GET /api/audit` | Bearer/session | Bounded Agent audit reads. |
| `GET /api/monitoring/local` | Bearer/session | Current local host snapshot. |
| `/api/capabilities` | Bearer/session | v1 compatibility capability surface. |

The `/api/monitoring/machines...` routes and several unversioned Agent routes
are compatibility surfaces. New Fleet workflows use the Manager Registry and
v2 Agent-scoped routes. Do not build new credential storage or arbitrary host
selection around compatibility monitoring routes.

For producer requests, see [Local Data Ingest](../guides/local-data-ingest.md).
For service and Agent configuration, see
[Configuration](../guides/configuration.md).

## Agent Local Ingest Listener

Default: `http://127.0.0.1:8766` (or `::1`). Intended callers are programs on
the same Agent host. It has no token because actual loopback origin is the trust
boundary.

| Method and route | Authentication | Purpose |
| --- | --- | --- |
| `PUT /api/v2/observations` | None; actual loopback peer required | Create, idempotently repeat, or update one latest Observation. |
| `PUT /api/v2/logs/{log_id}` | None; actual loopback peer required | Register/update one latest Log Source metadata record. |

No other route is mounted: no read, UI, login, Terminal, service, health,
metrics, docs, or Manager API. Forwarded headers cannot make a remote peer
local. Never reverse proxy, NAT, port-forward, container-publish, or expose this
listener. Agent Public and Local Ingest must use different ports and share one
coordinated process lifecycle.

## Manager Public Listener

Default: `http://127.0.0.1:8765`. Intended callers are Fleet browser users and
authenticated Manager API clients. Manager starts no Local Ingest listener and
does not manage its own host as an Agent.

| Route family | Authentication | Purpose |
| --- | --- | --- |
| `POST /api/auth/login`, `POST /api/auth/logout` | Manager login token/session | Fleet browser authentication. |
| `GET /healthz`, `GET /readyz` | None; bounded output | Manager process/readiness state. |
| `GET /api/v2/runtime`, `/capabilities` | Manager bearer/session | Runtime mode and Manager features. |
| `/api/v2/agents` and `/{agent_id}` | Manager bearer/session plus durable audit for mutations | Registry create/list/read/update/remove. |
| `/api/v2/agents/{agent_id}/enabled`, `/probe`, `/credential-rotation` | Manager bearer/session plus audit | Agent state, probe, and credential lifecycle. |
| `/api/v2/agent-enrollments...`, `/api/v2/agents/validate` | Manager bearer/session plus audit | SSH/CLI enrollment and legacy-token recovery. |
| `/api/v2/discovery/scopes`, `/jobs...`, `/results...` | Manager bearer/session plus audit for jobs | Named bounded discovery. |
| `GET /api/v2/transport-profiles` | Manager bearer/session | Public profile choices for Registry/enrollment. |
| `GET /api/v2/fleet/overview` | Manager bearer/session | Cached, independently probed Fleet summary. |
| `/api/v2/agents/{agent_id}/observations...` | Manager bearer/session | Allowlisted Agent Observation proxy. |
| `/api/v2/agents/{agent_id}/logs...` | Manager bearer/session plus tail audit | Allowlisted Agent Log metadata/tail proxy. |
| `/api/v2/agents/{agent_id}/services...` | Manager bearer/session plus audit for actions | Allowlisted Agent service proxy. |
| `GET /api/v2/agents/{agent_id}/audit` | Manager bearer/session | Bounded Agent audit proxy. |
| `GET /api/control-plane/audit` | Manager bearer/session | Manager audit records. |

Unversioned `/api/agents...` and `/api/fleet/overview` families remain for
compatibility UI/API contracts. They are still Agent-ID scoped and allowlisted;
they are not generic proxies. Manager does not expose `/metrics`; Prometheus
scrapes each Agent Public listener directly.

Registry/enrollment examples are in [Manager Fleet](../guides/manager-fleet.md).

## Prometheus and Health

| Runtime | Route | Policy |
| --- | --- | --- |
| Agent | `GET /healthz` | Unauthenticated bounded process liveness. |
| Agent | `GET /readyz` | Unauthenticated bounded Agent readiness. |
| Agent | `GET /metrics` | Loopback by default; remote source must match `metrics.remote_network_allowlist`. |
| Manager | `GET /healthz` | Unauthenticated bounded process liveness. |
| Manager | `GET /readyz` | Unauthenticated bounded Manager readiness, not proof every Agent is online. |

Health/readiness do not reveal credentials, Terminal content, log content, or
arbitrary internal errors. Metrics exclude secret/high-cardinality labels. See
[Monitoring and Logs](../guides/monitoring-and-logs.md).

## Terminal WebSockets

### Direct Agent

`/ws/terminals/{terminal_id}?ticket=<one-use-ticket>&cursor=<offset>` shares the
Agent Public listener. An authenticated HTTP request first creates the terminal
and connect ticket. The WebSocket consumes that short-lived ticket once, binds
it to the terminal/session owner, optionally replays from a bounded cursor, and
then transports text frames to/from the Agent PTY.

### Manager Fleet

`/ws/agents/{agent_id}/terminals/{terminal_id}` shares Manager Public. Manager
authenticates the browser and consumes a one-use gateway ticket bound to actor,
Agent, terminal, Registry revision, and route. It resolves the registered
target, creates/uses an upstream Agent ticket server-side, and reserves one
bounded proxy slot.

The proxy enforces Agent capability, target/credential policy, frame limits,
backpressure, paired cancellation, close-code sanitization, revision checks,
and slot cleanup. It cannot select an arbitrary WebSocket destination. Terminal
output stays in the Agent's bounded memory replay buffer and is not persisted
to Agent/Manager SQLite, audit, metrics, or logs.

## Enrollment Unix Sockets

These are local filesystem sockets, not HTTP endpoints and not systemd socket
units. They are created at runtime in an owner-safe directory and removed only
when the runtime still owns the same socket inode. Do not expose, forward, or
back them up.

### Agent Enrollment Socket

Default path: `/run/ic-env-guard/agent-enrollment.sock`, normally mode `0600`.
The fixed SSH helper connects locally. Peer credentials are checked before a
bounded request is processed.

Protocol `manager-enrollment.v1` accepts one newline-delimited request with
canonical Manager UUID and bounded enrollment ID. A successful response
contains Agent `instance_id`, opaque credential ID, one pending token, and UTC
expiry; request/response sizes are bounded. The pending credential is one-use,
short-lived, and requires later authenticated activation. Invalid input returns
only a bounded generic error.

### Manager Enrollment Socket

Configured by `enrollment.manager_socket_path`, normally mode `0600` (or `0660`
with deliberate primary-GID authorization). `ic-env-guardctl` connects locally;
Manager checks peer UID/GID before reading the bounded header.

The CLI exchange uses versioned newline-delimited JSON phases:

- `manager-cli-enrollment.header.v1` binds enrollment ID, fixed SSH destination,
  and pinned address;
- `manager-cli-enrollment.ready.v1` binds Manager/enrollment identity, input
  fingerprint, nonce, expiry, and host-key policy;
- the CLI runs fixed SSH/helper arguments and returns
  `manager-cli-enrollment.result.v1` with the bounded helper object;
- Manager verifies and journals the result, then returns bounded verified,
  already-accepted, invalid, or rejected status.

Resume nonce and input fingerprint prevent a reconnect from changing or
replaying the job. The token is never printed by the CLI or returned through
Manager HTTP/UI.

## Authentication and Exposure Matrix

| Surface | Default bind/path | Authentication | Runtime mode | Remote exposure |
| --- | --- | --- | --- | --- |
| Agent Public HTTP | `127.0.0.1:8765` | Bearer/session; bounded health exceptions; metrics source policy | Agent | Explicit remote bind plus HTTPS/trusted-LAN and firewall. |
| Agent Local Ingest HTTP | `127.0.0.1:8766` | No token; actual loopback peer | Agent | **Forbidden**: never proxy/forward/publish. |
| Manager Public HTTP | `127.0.0.1:8765` | Manager bearer/session; bounded health exceptions | Manager | Explicit remote bind plus HTTPS/trusted-LAN and firewall. |
| Agent Terminal WS | Agent Public `/ws/terminals/...` | Short-lived one-use ticket issued after Public auth | Agent | Same boundary as Agent Public. |
| Manager Terminal WS | Manager Public `/ws/agents/...` | Manager auth plus one-use Agent-scoped gateway ticket | Manager | Same boundary as Manager Public. |
| Agent enrollment socket | `/run/ic-env-guard/agent-enrollment.sock` | Filesystem mode plus peer credentials and bounded protocol | Agent | Local filesystem only; no forwarding. |
| Manager enrollment socket | Configured absolute Unix path | Filesystem mode plus UID/primary-GID peer credentials | Manager | Local filesystem only; no forwarding. |
| Agent Prometheus | Agent Public `/metrics` | Local or source CIDR allowlist | Agent | Direct scrape only from approved network. |

For complete listener and credential policy, read
[Security](../guides/security.md).

# Feature Specification: Multi-Agent Control Plane

**Feature**: `002-multi-agent-control-plane`

**Created**: 2026-06-14

**Status**: Draft

**Depends on**: [001 Linux Host Agent](../001-linux-host-agent/spec.md)

## Scope Amendment

The `001-linux-host-agent` MVP intentionally excludes multi-host orchestration. This
feature is a post-MVP extension and does not alter the single-host guarantees or
contracts established by `001`.

The existing host agent remains independently installable and operable. A new
control-plane mode provides one browser entry point that can monitor and control
multiple configured host agents.

## User Scenarios and Testing

### User Story 1 - Select and Inspect an Agent (Priority: P1)

An authenticated administrator opens one web application, sees the configured
agents and their availability, selects one agent, and views that agent's host
overview and configured services.

**Independent Test**: Configure two test agents, stop one of them, sign in to the
control plane, switch between both agents, and verify that each page shows data
and errors only for the selected agent.

**Acceptance Scenarios**:

1. Given multiple enabled agents, when the administrator changes the active
   agent, then all agent-scoped pages reload for that agent and clearly display
   its identity.
2. Given one unavailable agent, when the administrator selects another ready
   agent, then the ready agent remains fully usable.
3. Given a stored active-agent ID that no longer exists, when the application
   loads, then it selects the first ready agent, otherwise the first enabled
   agent, otherwise no agent.
4. Given a slow response from a previously selected agent, when the administrator
   switches agents, then the stale response cannot overwrite the new agent's UI.

### User Story 2 - Control Configured Remote Services (Priority: P1)

An authenticated administrator lists, starts, stops, and restarts only the
services configured on the selected remote agent.

**Independent Test**: Start and stop a harmless service through the control
plane, verify the result on the target agent, repeat an already-satisfied
operation, and inspect both control-plane and agent audit records.

**Acceptance Scenarios**:

1. Given a configured enabled agent, when a valid service operation is requested,
   then the control plane forwards it to that agent without exposing agent
   credentials to the browser.
2. Given an unknown or disabled agent, when a service operation is requested,
   then the control plane rejects it without contacting another target.
3. Given an upstream timeout after a mutating request may have reached the agent,
   when the control plane responds, then it reports an indeterminate outcome and
   does not automatically retry the operation.
4. Given a completed or failed operation, when audit records are inspected, then
   the control plane records the actor, source, agent, action, result, correlation
   ID, and upstream outcome.

### User Story 3 - Use a Remote Terminal (Priority: P2)

An authenticated administrator creates, attaches to, resizes, reconnects to, and
closes terminal sessions on the selected agent through the control plane.

**Independent Test**: Create terminals with identical local IDs on two agents,
attach to each through the gateway, switch agents, reconnect from retained
output, and close both sessions without cross-routing input or leaving orphan
processes.

**Acceptance Scenarios**:

1. Terminal identity is the pair `(agent_id, terminal_id)` throughout the
   frontend, control plane, audit records, and WebSocket routing.
2. The browser receives only a short-lived, one-use control-plane ticket and
   never receives an agent credential or upstream terminal ticket.
3. Switching active agents cannot send terminal input, resize requests, or close
   requests to a terminal on the previous agent.
4. Browser disconnect preserves the upstream PTY according to the host-agent
   lifecycle contract; explicit close and idle timeout still terminate or reap
   the PTY.
5. Gateway restart may disconnect the WebSocket but does not create an orphan
   PTY; the browser can request a new gateway ticket and reconnect while the
   upstream terminal remains attachable.

### User Story 4 - Review Agent Audit and Monitoring Data (Priority: P3)

An authenticated administrator views audit events and host snapshots for the
selected agent while Prometheus-compatible scraping remains a separate
machine-to-machine interface.

**Independent Test**: Generate actions on two agents, verify agent-scoped audit
queries never mix records, verify host snapshots follow the active agent, and
validate that each agent's `/metrics` endpoint remains Prometheus compatible.

**Acceptance Scenarios**:

1. Agent-scoped audit results identify their source agent and preserve the
   upstream event fields without secrets.
2. Host monitoring uses the same configured agent registry as service and
   terminal control; no second browser-managed machine registry remains.
3. The browser monitoring view consumes authenticated JSON snapshots, not raw
   Prometheus text.
4. Prometheus scrapers continue to access each host agent's `/metrics` endpoint
   under the network-allowlist contract from feature `001`.

## Functional Requirements

- **FR-001**: The system MUST provide one same-origin browser application for
  selecting and operating multiple configured host agents.
- **FR-002**: The host agent from feature `001` MUST remain independently
  deployable and its existing local API contracts MUST remain compatible.
- **FR-003**: The system MUST define explicit `agent`, `control-plane`, and
  `combined` runtime modes, with `agent` as the default so existing single-host
  configurations and contracts are unaffected.
- **FR-004**: `agent` mode MUST expose local host-agent resources and MUST NOT
  expose control-plane routing APIs.
- **FR-005**: `control-plane` mode MUST expose gateway APIs and MUST NOT imply
  management of the control-plane host.
- **FR-006**: `combined` mode (deferred to feature `003`) MAY manage the local
  host, but local requests MUST resolve through an in-process transport, never
  through HTTP or WebSocket self-proxying. This feature rejects `combined` at
  startup with a pointer to `003`.
- **FR-007**: All service, terminal, audit, and monitoring requests MUST resolve
  their target through one authoritative agent registry.
- **FR-008**: Agent IDs MUST be unique, stable, URL-safe identifiers matching
  `^[a-z0-9][a-z0-9_-]{0,63}$`.
- **FR-009**: Enabled agents MUST have an HTTPS base URL and a readable,
  permission-checked credential source unless explicit development-only insecure
  transport is enabled for a loopback target.
- **FR-010**: Production configuration MUST reject non-loopback HTTP agent URLs,
  disabled TLS verification, unreadable credential files, duplicate IDs,
  unsupported URL paths, fragments, and embedded credentials.
- **FR-011**: Agent bearer credentials and upstream terminal tickets MUST remain
  server-side and MUST NOT appear in browser responses, logs, audit events,
  metrics, or persisted diagnostics.
- **FR-012**: The control plane MUST authenticate the browser before exposing
  agent inventory or agent-scoped resources.
- **FR-013**: The first release MAY retain the single `local-admin` role, but
  authorization checks MUST be explicit at every agent-scoped route.
- **FR-014**: Read-only upstream requests MAY be retried only under a documented,
  bounded policy; mutating service and terminal requests MUST NOT be
  automatically retried.
- **FR-015**: Upstream connection failures, protocol failures, timeouts, and
  indeterminate mutation outcomes MUST have distinct normalized error codes.
- **FR-016**: The control plane MUST NOT follow upstream redirects and MUST
  forward only an explicit allowlist of methods, paths, headers, query fields,
  and response content types.
- **FR-017**: Gateway readiness MUST describe the gateway's own ability to serve
  requests and MUST NOT become unready solely because an individual agent is
  unavailable.
- **FR-018**: Agent availability states MUST have documented transition rules,
  observation timestamps, and staleness behavior.
- **FR-019**: Every routed privileged request MUST create a durable control-plane
  audit record, including requests that fail before reaching an agent.
- **FR-020**: The control plane MUST propagate a correlation ID to the target
  agent so gateway and agent audit records can be associated.
- **FR-021**: Both agent audit records (inherited from `001` FR-026) and
  control-plane gateway audit records MUST be stored in migration-managed durable
  state and MUST survive restart. Agent audit durability is a prerequisite fix
  addressed before any `002` routes are added.
- **FR-022**: Agent API compatibility MUST be verified through an explicit API
  version and capability response before unsupported resources are enabled in
  the UI. An agent that does not expose the capability endpoint is below the
  documented minimum supported version and MUST be treated as a protocol error
  with no features enabled, not partially operated.
- **FR-023**: Existing `/api/monitoring/machines` browser-managed registry
  behavior MUST be migrated to the authoritative agent registry or removed.
- **FR-024**: Terminal HTTP routes MUST preserve the `001` method and resource
  semantics, including detail, history, connect-token, resize, and `DELETE`
  close behavior.
- **FR-025**: Terminal WebSocket routing MUST use a one-use control-plane ticket
  bound to actor, agent ID, terminal ID, expiry, and intended connection.
- **FR-026**: WebSocket proxy behavior MUST define backpressure, frame limits,
  close-code mapping, bidirectional cancellation, reconnect, and shutdown
  behavior.
- **FR-027**: The control plane MUST enforce a configurable global cap on
  outstanding gateway tickets and concurrently proxied terminal sockets. The
  `connect-token` endpoint MUST reserve capacity before requesting an upstream
  ticket and MUST return `429 gateway_capacity_exceeded` when full. The WebSocket
  attach MUST acquire a proxy slot before consuming the gateway ticket and MUST
  reject with `4429` when the cap is reached, so no valid ticket is wasted.
- **FR-028**: Frontend request state and terminal state MUST be scoped by agent
  and MUST discard responses from an inactive selection generation.
- **FR-029**: Raw `/metrics` federation, aggregation, long-term storage, alerting,
  and dashboarding MUST NOT be introduced by this feature.
- **FR-030**: Logs and error responses MUST not disclose credentials, terminal
  contents, private network details beyond the configured agent display
  identity, or raw upstream exception text.
- **FR-031**: The system MUST document configuration migration, rollback to
  single-agent operation, mixed-version behavior, and recovery when the control
  plane is unavailable.

## Agent Availability Model

| State | Meaning |
|---|---|
| `unknown` | No completed observation exists. |
| `ready` | The last readiness probe succeeded and is not stale. |
| `degraded` | The agent responded but reported not-ready or missing optional capabilities. |
| `unavailable` | Transport, TLS, authentication, or protocol negotiation failed. |
| `disabled` | Configuration excludes the agent from routing and probes. |

Each status response includes `observed_at` and `stale_after`. A cached status
becomes `unknown` when it exceeds `stale_after`; a request may still perform an
on-demand attempt.

## Error Model

| HTTP | Code | Meaning |
|---|---|---|
| `400` | `invalid_agent_request` | The gateway rejected request shape or operation. |
| `401` | `unauthorized` | Browser authentication is missing or invalid. |
| `403` | `agent_operation_forbidden` | The actor cannot perform the operation. |
| `404` | `agent_not_found` | The configured agent does not exist. |
| `409` | `agent_disabled` | The agent exists but routing is disabled. |
| `424` | `agent_operation_indeterminate` | A mutation may have reached the agent, but no authoritative result was received. |
| `502` | `agent_protocol_error` | The agent returned an invalid or incompatible response. |
| `503` | `agent_unavailable` | A connection, TLS, or upstream authentication failure occurred. |
| `504` | `agent_timeout` | The configured upstream deadline expired before dispatch was confirmed. |

Errors include a control-plane `correlation_id`. They do not include raw tokens,
upstream tickets, or unsanitized exception strings.

## Non-Goals

- Agent discovery, enrollment, or remote installation
- Multi-user RBAC and delegated administration
- High-availability or clustered control planes
- Cross-agent transactions or coordinated service operations
- Broadcast commands or bulk terminal execution
- Browser-managed agent credentials
- Generic reverse proxying of arbitrary agent paths
- Raw Prometheus aggregation or a custom metrics store
- Preserving live xterm buffers across agent switches in the first release
- `combined` mode (deferred to follow-up feature `003`; the mode is
  recognized but rejected at startup in this feature)

## Success Criteria

- **SC-001**: With three configured agents and one unavailable, the administrator
  can inspect and control each ready agent without the unavailable agent blocking
  the UI or gateway readiness.
- **SC-002**: 100% of browser-visible payloads and logs inspected during security
  validation contain no agent credential or upstream terminal ticket.
- **SC-003**: Every service mutation produces a durable gateway audit record with
  actor, source, agent, operation, result, correlation ID, and upstream outcome.
- **SC-004**: A timed-out mutation is never automatically retried and is reported
  as indeterminate when dispatch cannot be ruled out.
- **SC-005**: Fast agent switching during delayed responses never displays data
  from the previously selected agent.
- **SC-006**: Two agents may expose identical terminal IDs without input, resize,
  history, reconnect, or close requests crossing agent boundaries.
- **SC-007**: Gateway restart disconnects proxied WebSockets but leaves upstream
  terminal lifecycle ownership with the agent and allows a new attach within
  the original terminal retention window.
- **SC-008**: Non-loopback HTTP agent configuration and disabled TLS verification
  fail validation unless an explicit development-only exception applies.
- **SC-009**: Agent and gateway versions can differ within the documented
  compatibility window; unsupported capabilities are disabled rather than
  invoked.
- **SC-010**: The existing host-agent contract tests from feature `001` continue
  to pass unchanged in `agent` mode, which is the default mode.
- **SC-011**: When the ticket store is full, `connect-token` returns `429`
  without contacting the agent; when the proxy cap is reached, WebSocket attach
  returns `4429` without consuming the gateway ticket; in both cases gateway
  memory remains bounded.
- **SC-012**: Agent audit events survive process restart (prerequisite fix to
  `001`); control-plane gateway audit events survive restart independently in the
  dedicated control-plane database; the two databases do not share tables.
- **SC-013**: Attempting to start in `combined` mode produces a clear startup
  error pointing to follow-up feature `003` rather than partially initializing.

## Related Documents

- [Architecture](architecture.md)
- [HTTP API Contract](contracts/http-api.md)
- [Terminal WebSocket Contract](contracts/terminal-websocket.md)
- [Implementation Plan](plan.md)


# Multi-Agent Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure control-plane gateway and one active-agent frontend context while preserving the existing single-host agent contracts.

**Architecture:** The browser connects only to the control plane. Explicit resource routers resolve targets through one static agent registry and use a constrained HTTPS client; the host agent remains the owner of local services, PTYs, metrics, and local audit. Gateway audit is durable from the first slice, and terminal proxying is a separate second slice.

**Tech Stack:** Python 3.11+, FastAPI, httpx (upstream HTTP; promoted to a runtime dependency — it is test-only today), `websockets` (upstream WebSocket client, a new runtime dependency; httpx cannot act as a WS client), Pydantic, SQLAlchemy ORM, raw-`sqlite3` `.py` migrations (the `001` convention — not Alembic, despite the unused `alembic` pin in `pyproject.toml`), pytest, React, TypeScript, Vitest, xterm.js.

**Migration isolation:** The existing `db/migrations.py` runner globs every `migrations/[0-9][0-9][0-9][0-9]_*.py` and applies all of them to whatever `db_path` it is handed ([migrations.py:45](../../backend/ic_env_guard/db/migrations.py#L45)). A `0003` file in that shared directory would therefore create control-plane tables in the agent DB and agent tables in the control-plane DB. The control plane uses its own migration directory and runner — `backend/control_plane_migrations/` with `run_control_plane_migrations()` — so the two databases never receive each other's schema.

**Audit persistence:** Two distinct problems, two distinct fixes.
- The `001` audit store is built in `create_app()` against an in-memory `sqlite://` engine (`StaticPool`); both audit writes and the query API use it, so audit is lost on restart. This already violates `001` FR-021/FR-026 ("migration-managed local durable state"). Task 0 fixes it as a prerequisite, because the MVP 3 agent-audit view cannot meet the original contract on top of an ephemeral store.
- Gateway audit (FR-021) lives in a separate, durable `control-plane.db`. Its schema's single source of truth is the control-plane migration; the ORM model maps the table and does not `create_all` it.

---

## Delivery Slices

### MVP 1 - Registry, Availability, Services, and Monitoring

Delivers the `agent` and `control-plane` runtime modes, validated agent
configuration, durable gateway audit, capability negotiation, explicit
readiness/service/monitoring routes, the global agent selector, and migration
away from the monitoring-only machine registry. `combined` mode is intentionally
deferred (see below).

### MVP 2 - Terminal HTTP and WebSocket Gateway

Delivers agent-scoped terminal HTTP routes, gateway ticket exchange, bounded
WebSocket proxying (upstream WS via the `websockets` client with a verified TLS
context), agent-scoped frontend terminal state, and reconnect behavior.

### MVP 3 - Agent Audit Views and Operational Hardening

Delivers agent-scoped audit views, gateway audit UI, mixed-version tests,
packaging docs, compatibility cleanup, and deprecation of old machine-registry
mutation routes.

### `combined` mode is out of scope for this feature

`combined` mode must NOT HTTP self-proxy (FR-006), which forces every resource
router to run against a transport abstraction with two implementations: an HTTP
client and an in-process adapter calling the local managers directly, including
the terminal WebSocket path. Baking that seam into MVP 1 routing would make the
deferred mode shape every route now, for the lowest-value, highest-risk
deployment. First deployments only need a separate control plane talking to
separate agents.

Therefore this feature ships `agent` and `control-plane` modes only, and the
resource routers call the constrained HTTP client directly — no transport
interface. `combined` becomes a separate follow-up feature (`003`) that
introduces a domain-typed transport seam (e.g. `ServiceTarget`,
`TerminalTarget`, `MonitoringTarget`) so the in-process implementation calls
local managers naturally instead of simulating HTTP responses. The `mode` enum
still accepts `combined` (Task 1) but startup rejects it with a clear pointer to
feature `003`, keeping the configuration schema stable.

## Planned Source Structure

```text
backend/ic_env_guard/
├── agents/
│   ├── models.py          # validated runtime target and capability/status models
│   ├── registry.py        # immutable configured target lookup
│   ├── client.py          # constrained HTTP client and normalized failures
│   ├── availability.py    # periodic probes and latest observation cache
│   └── terminal_proxy.py  # gateway tickets and bounded WS proxy (websockets client)
├── api/
│   ├── agents.py
│   ├── agent_services.py
│   ├── agent_monitoring.py
│   ├── agent_terminals.py
│   ├── agent_terminal_ws.py
│   └── control_plane_audit.py
└── db/
    ├── control_plane_audit.py
    └── control_plane_migrations.py   # dedicated runner, separate from db/migrations.py

backend/control_plane_migrations/     # control-plane-only migrations (isolated from migrations/)
└── 0001_control_plane_audit.py

frontend/src/
├── agents/
│   ├── AgentContext.tsx
│   └── AgentSelector.tsx
└── api/
    └── agents.ts
```

Existing resource modules remain the local host-agent implementation. Gateway
modules call those contracts rather than duplicating host process logic.

## Task 0: Fix Agent Durable Audit (Prerequisite)

`001` requires audit to persist in migration-managed durable state (FR-021,
FR-026), but `create_app()` currently wires both audit writes and the query API
to an in-memory `sqlite://` engine, so audit is lost on restart. This must be
fixed before the MVP 3 agent-audit view can meet the original contract; it is a
correctness fix to `001`, not a `002` feature.

**Files:**

- Modify: `backend/ic_env_guard/main.py`
- Create: `backend/migrations/0003_audit_events.py`
- Test: `backend/tests/integration/test_agent_audit_durability.py`

- [ ] Write a failing test that records audit events, restarts the app against
  the same database path, and asserts the events are still queryable.
- [ ] Add a `0003` agent migration creating `audit_events` (the table is ORM-only
  today) so it is migration-managed; the ORM model maps it without `create_all`.
- [ ] Point the `create_app()` audit engine at the configured durable database
  path instead of `sqlite://`/`StaticPool`. Update any `001` test fixtures that
  assumed a fresh in-memory store to use a temp-file database.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/integration/test_agent_audit_durability.py
  conda run -n venv312 pytest -q tests/contract
  ```

  Expected: audit survives restart and all `001` contracts still pass.

## Task 1: Runtime Modes and Configuration

**Files:**

- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/config/loader.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_control_plane_config.py`
- Test: `backend/tests/unit/test_security_config.py`

- [ ] Add failing tests for unique agent IDs, URL shape, credential exclusivity,
  token-file permissions, verified TLS, loopback-only development HTTP, and the
  three runtime modes.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_control_plane_config.py tests/unit/test_security_config.py
  ```

  Expected: failures because `AgentConfig`, `ControlPlaneConfig`, and runtime
  modes do not exist.

- [ ] Add `AgentTlsConfig`, `AgentConfig`, `ControlPlaneConfig`, and
  `mode: Literal["agent", "control-plane", "combined"]` to `AppConfig`. `mode`
  MUST default to `agent` so existing configs and every test fixture that calls
  `create_app()` keep producing the current single-host app unchanged.
- [ ] Validate `base_url` as scheme, host, and optional port only; reject URL
  credentials, query, fragments, and non-root paths.
- [ ] Permit insecure HTTP only for loopback when
  `development.allow_insecure_http` is true and the server itself is local-only.
- [ ] Refactor `create_app()` to mount routers by mode. **Risk:** `create_app()`
  currently builds all dependencies and mounts all routers unconditionally;
  factor the mode-independent agent wiring into a helper so the `agent`-mode app
  is identical to `001`. The `mode` enum accepts `combined`, but startup rejects
  it with a clear error pointing to follow-up feature `003`; only `agent` and
  `control-plane` are wired in this feature.
- [ ] Add a regression test asserting the `agent`-mode router set and dependency
  overrides are unchanged from `001` before refactoring anything else.
- [ ] Re-run the focused tests and then:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_health_readiness_contract.py tests/contract/test_services_api_contract.py tests/contract/test_terminal_http_contract.py
  ```

  Expected: all existing `001` contracts pass in `agent` mode.

## Task 2: Durable Gateway Audit

**Files:**

- Create: `backend/ic_env_guard/db/control_plane_audit.py`
- Create: `backend/ic_env_guard/db/control_plane_migrations.py`
- Create: `backend/ic_env_guard/api/control_plane_audit.py`
- Create: `backend/control_plane_migrations/0001_control_plane_audit.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/integration/test_control_plane_audit.py`
- Test: `backend/tests/contract/test_migration_contract.py`

- [ ] Write failing integration tests that restart the application and verify
  routing intent/outcome records survive, including failures that occur before
  upstream dispatch. Until Task 4/5 exist, the test exercises the repository
  through a small dispatch stub rather than a real upstream call.
- [ ] Add a dedicated migration directory `backend/control_plane_migrations/`
  and a `run_control_plane_migrations(db_path)` runner. Do NOT add the
  control-plane migration to `backend/migrations/`: the shared `db/migrations.py`
  runner applies every file to whatever database it is given, so a shared `0003`
  would cross-contaminate the agent and control-plane schemas.
- [ ] Add `0001_control_plane_audit.py` (raw `sqlite3`, the `001` convention)
  with actor, source address, agent ID, operation, target, result, dispatch
  state, upstream status, correlation ID, failure category, and timestamp. The
  migration is the **single source of truth**; the ORM model maps the table and
  MUST NOT `create_all` it.
- [ ] Add a test asserting database isolation: the agent DB contains no
  control-plane table and the control-plane DB contains no agent table after
  both runners have applied their migrations.
- [ ] Open a dedicated durable engine against `control_plane.audit_database`
  (default `/var/lib/ic-env-guard/control-plane.db`), created only in
  `control-plane` mode. This is separate from the agent audit database fixed in
  Task 0.
- [ ] Add a repository method that creates intent before dispatch and finalizes
  the same record after success or failure.
- [ ] Add bounded authenticated query routes under `/api/control-plane/audit`.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/integration/test_control_plane_audit.py tests/contract/test_migration_contract.py
  ```

  Expected: persistence, migration, filtering, and secret-exclusion tests pass.

## Task 3: Agent Registry and Capability Negotiation

**Files:**

- Create: `backend/ic_env_guard/agents/__init__.py`
- Create: `backend/ic_env_guard/agents/models.py`
- Create: `backend/ic_env_guard/agents/registry.py`
- Create: `backend/ic_env_guard/api/agents.py`
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/ic_env_guard/api/risk.py`
- Test: `backend/tests/contract/test_agents_api.py`
- Test: `backend/tests/unit/test_agent_registry.py`

- [ ] Write failing tests for immutable lookup, disabled targets, safe inventory
  responses, and unknown targets.
- [ ] Implement one registry instance from validated startup configuration.
- [ ] Ensure safe summaries omit URLs, token paths, CA paths, and raw transport
  errors.
- [ ] Add `GET /api/capabilities` to local agents and inventory/detail/probe
  routes to the control plane. Document the **minimum agent version** that
  exposes `/api/capabilities`: a control plane pointed at a pre-`002` agent that
  lacks this endpoint treats it as `agent_protocol_error` and surfaces no
  features, rather than silently half-working.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/unit/test_agent_registry.py tests/contract/test_agents_api.py
  ```

  Expected: all registry and inventory contracts pass.

## Task 4: Constrained Agent HTTP Client and Availability

**Files:**

- Create: `backend/ic_env_guard/agents/client.py`
- Create: `backend/ic_env_guard/agents/availability.py`
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/test_agent_client.py`
- Test: `backend/tests/integration/test_agent_availability.py`

- [ ] Promote `httpx` from the `test` extra to a runtime dependency in
  `pyproject.toml`; do not rely on a transitive dependency. Update the packaging
  runtime test to assert it imports without the test extras installed.
- [ ] Write tests proving redirects are not followed, browser authorization and
  forwarding headers are not propagated, TLS settings are applied, response
  size/content type are bounded, and error categories map to the HTTP contract.
- [ ] Implement the constrained client as one application-lifetime
  `httpx.AsyncClient` with per-target credentials and verified TLS. Routers call
  this client directly (no transport-interface seam; that arrives only with the
  `combined` follow-up feature). The upstream terminal WebSocket (Task 9) is
  opened separately with the `websockets` client using an `ssl.SSLContext` built
  from the same per-target TLS settings; httpx is HTTP-only.
- [ ] Generate or preserve one correlation ID per gateway request and send it as
  `X-Correlation-ID`.
- [ ] Permit a single read-only retry only when failure is known to occur before
  dispatch. Never retry POST or DELETE.
- [ ] Add bounded-concurrency periodic probes with jitter, `observed_at`, and
  `stale_after`; cancel the probe task during shutdown.
- [ ] Verify gateway `/readyz` remains ready when one test agent is unavailable.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/unit/test_agent_client.py tests/integration/test_agent_availability.py
  ```

## Task 5: Explicit Service and Monitoring Routes

**Files:**

- Create: `backend/ic_env_guard/api/agent_services.py`
- Create: `backend/ic_env_guard/api/agent_monitoring.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_agent_services_api.py`
- Test: `backend/tests/integration/test_multi_agent_monitoring.py`

- [ ] Write contract tests for every service route and the JSON monitoring
  snapshot route using two fake agents with overlapping service IDs.
- [ ] Implement explicit allowlisted route handlers that dispatch through the
  constrained HTTP client (Task 4); do not add a catch-all `{path:path}` reverse
  proxy.
- [ ] Preserve safe upstream application errors and status codes.
- [ ] Map connection/TLS/auth failures to `503`, protocol failures to `502`,
  pre-dispatch timeouts to `504`, and uncertain mutation outcomes to `424`.
- [ ] Create/finalize one durable gateway audit record around each privileged
  request.
- [ ] Keep raw `/metrics` outside the gateway API.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_agent_services_api.py tests/integration/test_multi_agent_monitoring.py
  ```

## Task 6: Global Frontend Agent Context

**Files:**

- Create: `frontend/src/api/agents.ts`
- Create: `frontend/src/agents/AgentContext.tsx`
- Create: `frontend/src/agents/AgentSelector.tsx`
- Modify: `frontend/src/pages/AppRoutes.tsx`
- Modify: `frontend/src/pages/HostOverviewPage.tsx`
- Modify: `frontend/src/pages/ServiceListPage.tsx`
- Modify: `frontend/src/api/services.ts`
- Test: `frontend/tests/agent-context.test.tsx`
- Test: `frontend/tests/agent-routing.test.tsx`

- [ ] Write failing tests for startup selection, session storage fallback,
  unavailable-agent rendering, and delayed old-agent responses.
- [ ] Load safe agent summaries only after browser authentication.
- [ ] Persist only `activeAgentId`; never persist agent URLs or credentials.
- [ ] Add a monotonically increasing selection generation or abort controller so
  stale requests cannot update active pages.
- [ ] Pass `agentId` explicitly to service and overview API functions.
- [ ] Display agent identity and status next to primary navigation and on every
  destructive confirmation.
- [ ] Run:

  ```bash
  cd frontend
  npm test -- --run tests/agent-context.test.tsx tests/agent-routing.test.tsx
  ```

## Task 7: Replace the Monitoring-Only Registry

**Files:**

- Modify: `frontend/src/pages/MetricsPage.tsx`
- Modify: `frontend/src/api/monitoring.ts`
- Modify: `backend/ic_env_guard/api/monitoring.py`
- Modify: `backend/ic_env_guard/monitoring/machines.py`
- Test: `frontend/tests/metrics-page.test.tsx`
- Test: `backend/tests/contract/test_monitoring_api_contract.py`

- [ ] Change the monitoring page to consume the global active agent and
  `/api/agents/{agent_id}/monitoring/snapshot`.
- [ ] Remove browser forms that submit agent addresses and bearer keys.
- [ ] Mark `/api/monitoring/machines` mutation routes deprecated for one
  compatibility release; prevent new frontend use.
- [ ] Remove `MachineRegistry` after the compatibility window and keep
  `local_host_snapshot()` as the host-agent implementation.
- [ ] Verify the UI has exactly one agent selector.
- [ ] Run existing monitoring and secret-exclusion tests.

## Task 8: Agent-Scoped Terminal HTTP and Gateway Tickets

**Files:**

- Create: `backend/ic_env_guard/api/agent_terminals.py`
- Create: `backend/ic_env_guard/agents/terminal_proxy.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_agent_terminal_http_contract.py`
- Test: `backend/tests/unit/test_gateway_terminal_tickets.py`

- [ ] Write tests for list, create, detail, history, resize, connect-token, and
  `DELETE` close with duplicate terminal IDs on different agents, plus a
  capacity test: connect-token returns HTTP `429 gateway_capacity_exceeded` when
  the ticket store is full, and no upstream ticket is requested in that case.
- [ ] Implement explicit terminal routes preserving current methods and status
  codes.
- [ ] On connect-token, reserve ticket-store capacity **before** requesting the
  upstream ticket; if full, return `429` without contacting the agent. Only
  after reserving, obtain the upstream ticket server-side and issue a distinct
  gateway ticket bound to actor, agent, terminal, and expiry. Release the
  reservation on any failure (upstream error, timeout, validation).
- [ ] Enforce bounded storage, one-use consumption, expiry, and complete
  redaction of both ticket values.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_agent_terminal_http_contract.py tests/unit/test_gateway_terminal_tickets.py
  ```

## Task 9: Terminal WebSocket Proxy and Frontend State

**Files:**

- Create: `backend/ic_env_guard/api/agent_terminal_ws.py`
- Modify: `backend/ic_env_guard/agents/terminal_proxy.py`
- Modify: `backend/pyproject.toml`
- Modify: `frontend/src/api/terminals.ts`
- Modify: `frontend/src/pages/TerminalPage.tsx`
- Modify: `frontend/src/terminal/TerminalPane.tsx`
- Test: `backend/tests/integration/test_agent_terminal_websocket.py`
- Test: `frontend/tests/terminal-agent-routing.test.tsx`

- [ ] Add `websockets` as a runtime dependency in `pyproject.toml` (it is not
  declared today and must not be assumed transitively from `uvicorn[standard]`);
  cover it in the packaging runtime test.
- [ ] Write backend tests for ticket mismatch, frame limits, backpressure,
  upstream failure, paired-task cancellation, reconnect cursor, gateway
  shutdown, and rejection past the global active-proxy cap (close code `4429`).
- [ ] Open the upstream WebSocket with the `websockets` client and an
  `ssl.SSLContext` derived from the target's TLS config.
- [ ] On attach, atomically acquire a proxy slot and then consume the gateway
  ticket; if the global active-proxy cap is reached, reject with `4429` before
  consuming the ticket so a valid ticket is not wasted. Release the slot on any
  failure path.
- [ ] Implement the WebSocket contract with bounded per-direction queues, the
  configurable global cap on concurrent proxied sockets and outstanding tickets,
  and sanitized close codes.
- [ ] Add `agentId` to every frontend terminal API and WebSocket URL.
- [ ] Key frontend terminal state by `(agentId, terminalId)` and remount panes
  when the active agent changes.
- [ ] Cancel old sockets and resize timers before activating a new agent.
- [ ] Verify switching agents cannot send input to the previous terminal.
- [ ] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/integration/test_agent_terminal_websocket.py
  cd ../frontend
  npm test -- --run tests/terminal-agent-routing.test.tsx
  ```

## Task 10: Agent Audit, Documentation, and Compatibility

**Files:**

- Create: `backend/ic_env_guard/api/agent_audit.py`
- Modify: `frontend/src/api/audit.ts`
- Modify: `frontend/src/pages/AuditStatusPage.tsx`
- Modify: `README.md`
- Modify: `start.sh`
- Modify: `docs/operations/security-review.md`
- Create: `docs/operations/control-plane.md`
- Test: `backend/tests/integration/test_agent_audit_routing.py`
- Test: `backend/tests/integration/test_mixed_agent_versions.py`

- [ ] Add agent-scoped audit queries and a separate gateway audit view; do not
  merge or sort multiple remote histories in the first release.
- [ ] Add tests for correlation IDs across gateway and agent events and for
  secret exclusion in all failures.
- [ ] Add mixed-version tests that disable missing capabilities and reject
  unsupported API versions.
- [ ] Extend `start.sh` with explicit `agent` and `control-plane` development
  commands that honor configured bind and port values. (`combined` is deferred
  to feature `003`.)
- [ ] Document TLS provisioning, per-agent token files, migration from the old
  machine registry, rollback to `agent` mode, and gateway outage recovery.
- [ ] Run full verification:

  ```bash
  cd backend
  conda run -n venv312 pytest -q
  conda run -n venv312 python -m ruff check .
  cd ../frontend
  npm test -- --run
  npm run build
  ```

  Expected: all backend, frontend, original `001`, and new `002` checks pass.
  This is the final verification step; `combined` mode is delivered separately
  in feature `003`.

## Completion Gate

- [ ] The [requirements checklist](checklists/requirements.md) remains satisfied.
- [ ] All feature `001` contract tests pass unchanged in `agent` mode.
- [ ] Security review finds no browser-visible agent token or upstream ticket.
- [ ] Gateway audit survives restart and covers pre-dispatch failures.
- [ ] One unavailable agent does not affect another agent or gateway readiness.
- [ ] Service mutations are never automatically retried.
- [ ] Duplicate terminal IDs across agents remain isolated end to end.
- [ ] Rollback to single-agent operation is documented and tested.
- [ ] Agent audit survives restart (Task 0), satisfying `001` FR-021/FR-026.
- [ ] Agent and control-plane databases never contain each other's tables.
- [ ] Connect-token returns `429` when the ticket store is full without
  contacting the agent; the WS attach rejects past the cap with `4429` without
  wasting a valid ticket or growing memory unboundedly.
- [ ] `combined` mode is rejected at startup with a pointer to feature `003`.


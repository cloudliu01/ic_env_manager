# Multi-Agent Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure control-plane gateway and one active-agent frontend context while preserving the existing single-host agent contracts.

**Architecture:** The browser connects only to the control plane. Explicit resource routers resolve targets through one static agent registry and use a constrained HTTPS client; the host agent remains the owner of local services, PTYs, metrics, and local audit. Gateway audit is durable from the first slice, and terminal proxying is a separate second slice.

**Tech Stack:** Python 3.11+, FastAPI, httpx (upstream HTTP; promoted to a runtime dependency — it is test-only today), `websockets` (upstream WebSocket client, a new runtime dependency; httpx cannot act as a WS client), Pydantic, SQLAlchemy ORM, raw-`sqlite3` `.py` migrations (the `001` convention — not Alembic, despite the unused `alembic` pin in `pyproject.toml`), pytest, React, TypeScript, Vitest, xterm.js.

**Migration isolation and packaging:** The existing `db/migrations.py` runner globs every `migrations/[0-9][0-9][0-9][0-9]_*.py` and applies all of them to whatever `db_path` it is handed ([migrations.py:45](../../../../backend/ic_env_guard/db/migrations.py#L45)). A shared directory would cross-contaminate schemas. The control plane therefore uses its own migration directory and runner.

Both migration directories live **inside** the `ic_env_guard` package — `ic_env_guard/migrations/` (existing, moved from top-level `backend/migrations/`) and `ic_env_guard/control_plane_migrations/` (new) — so they are included in the wheel by the existing `include = ["ic_env_guard*"]` rule. The top-level `backend/migrations/` currently referenced by `MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"` is a pre-existing packaging bug: that path resolves correctly from source but points to `site-packages/migrations/` after wheel install. Moving migration directories inside the package and updating the `MIGRATIONS_DIR` constant fixes both the new and pre-existing issue at once.

**Audit persistence:** Two distinct problems, two distinct fixes.
- The `001` audit store is built in `create_app()` against an in-memory `sqlite://` engine (`StaticPool`); both audit writes and the query API use it, so audit is lost on restart. This already violates `001` FR-021/FR-026 ("migration-managed local durable state"). Task 0 fixes it as a prerequisite, because the MVP 3 agent-audit view cannot meet the original contract on top of an ephemeral store.
- Gateway audit (FR-021) lives in a separate, durable `control-plane.db`. Its schema's single source of truth is the control-plane migration; the ORM model maps the table and does not `create_all` it.

## Constitution Check

- **Security Gate**: Satisfied. The feature exposes service control, terminals,
  host data, logs, metrics snapshots, and configuration-derived inventory only
  behind browser authentication and explicit route authorization. Secrets,
  upstream tickets, terminal content, raw upstream URLs, and unsanitized
  exceptions are excluded from UI, logs, audit, metrics, and diagnostics.
- **Linux Operations Gate**: Satisfied. Runtime remains Python 3.11+ FastAPI on
  the existing Linux/systemd packaging model; `agent` mode is the default and
  preserves feature `001` lifecycle behavior.
- **Service Management Gate**: Satisfied. Service actions remain limited to
  configured services exposed by the selected agent, are routed through explicit
  allowlisted handlers, preserve existing idempotent host-agent semantics, and
  are gateway-audited.
- **Observability Gate**: Satisfied. Prometheus `/metrics` remains agent-local
  and allowlisted; the control plane provides authenticated JSON snapshots only
  and does not add raw metrics aggregation, alerting, or long-term time-series
  storage.
- **Terminal Safety Gate**: Satisfied. PTY ownership stays with the agent;
  gateway tickets are one-use and bounded; WebSocket proxying defines capacity,
  frame, backpressure, cancellation, reconnect, and shutdown behavior.
- **Persistence and Audit Gate**: Satisfied. Agent audit durability is fixed as a
  prerequisite; gateway audit uses a separate migration-managed SQLite database;
  both audit paths exclude secrets and terminal content.
- **Simplicity Gate**: Satisfied. `combined` mode, discovery, HA, bulk commands,
  custom TSDB, and generic reverse proxying remain out of scope. The feature adds
  no transport abstraction until feature `003` needs it.
- **Testing Gate**: Satisfied by planned contract, integration, security,
  packaging, migration, frontend, and full-suite verification tasks.

No constitutional exception is requested.

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
local managers naturally instead of simulating HTTP responses. `combined` is
NOT in the `mode` enum for this feature; a config with `mode: combined` fails
Pydantic validation at load time and never reaches application startup.

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
├── db/
│   ├── control_plane_audit.py
│   └── control_plane_migrations.py   # dedicated runner, separate from db/migrations.py
├── migrations/                        # moved from backend/migrations/ — now inside the package
│   ├── 0001_initial.py
│   └── 0002_state_audit_indexes.py
└── control_plane_migrations/          # new, inside the package — picked up by "ic_env_guard*"
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
FR-026), but `create_app()` wires both audit writes and the query API to an
in-memory `sqlite://`/`StaticPool` engine, so audit is lost on restart. This
must be fixed before the MVP 3 agent-audit view can meet the original contract.

**The fix does NOT add a new migration.** `audit_events` already exists in
`migrations/0001_initial.py` (line 46); the table is migration-managed. The
defect is purely that `main.py` constructs an in-memory engine and calls
`Base.metadata.create_all()` instead of pointing to the configured durable
database path and running the existing migration runner.

**`state_database` resolution rules (must be explicit before any code is written):**

Model field: `state_database: Path | None = None` (explicitly optional so a
missing config value is distinguishable from the production default).

Resolution order, evaluated in `_resolve_state_db(arg, config)`:
1. `create_app(state_database=...)` argument, if not `None`
2. `AppConfig.state_database`, if not `None`
3. env var `IC_ENV_GUARD_STATE_DB`, if set
4. hardcoded fallback `/var/lib/ic-env-guard/state.db`

The field must be `Path | None = None` — **not** `Path = Path("...")` — so that
steps 3 and 4 are reachable when no explicit value is given.

**Existing 17 `create_app()` call sites need no code changes.** They call
`create_app(token_file=...)` with no `state_database`, so after this change they
fall through to the env var `IC_ENV_GUARD_STATE_DB` (step 3 of
`_resolve_state_db()`). Intercept that resolution in a **function-scoped**
autouse fixture in `tests/conftest.py` that calls
`monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "state.db"))`.
Because `tmp_path` is function-scoped, every test gets an isolated database with
no cross-test pollution. Do NOT use a session-scoped fixture here — `tmp_path`
is function-scoped and pytest will raise a scope mismatch error.

The installer-generated config file and `README` example must show
`state_database:` so operators know the path is configurable.

**Files:**

- Move: `backend/migrations/` → `backend/ic_env_guard/migrations/`
- Create: `backend/ic_env_guard/migrations/__init__.py`
- Modify: `backend/ic_env_guard/db/migrations.py` (update `MIGRATIONS_DIR`)
- Modify: `backend/ic_env_guard/db/session.py` (add `check_same_thread=False`)
- Modify: `backend/ic_env_guard/config/audit.py`
  (replace `Base.metadata.create_all()` with `run_migrations(db_path)` in
  `audit_config_load_to_db()`; it has no `MIGRATIONS_DIR` reference — that was
  a description error in the previous plan version)
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/ic_env_guard/config/models.py` (add `state_database` field)
- Create: `backend/tests/conftest.py` (add autouse env-var fixture)
- Modify: `backend/tests/integration/test_migrations.py`
  (fix hardcoded `parents[2] / "migrations"` path)
- Modify: `backend/tests/integration/test_terminal_secret_exclusion.py`
  (fix hardcoded `parents[2] / "migrations"` path)
- Modify: `backend/tests/integration/test_packaging_runtime.py`
- Modify: `packaging/install/install.sh` (add `state_database:` to generated config)
- Modify: `README.md` (add `state_database:` to example config)
- Test: `backend/tests/integration/test_agent_audit_durability.py`
- Test: `backend/tests/unit/test_state_db_resolution.py`

- [x] Move `backend/migrations/` to `backend/ic_env_guard/migrations/` and add
  `__init__.py` so the directory is both a Python package (importable) and
  included in the wheel by `include = ["ic_env_guard*"]`. Update `MIGRATIONS_DIR`
  in `db/migrations.py` from `parents[2] / "migrations"` to
  `Path(__file__).parent.parent / "migrations"`. Verify `0001`/`0002` apply cleanly.
- [x] Fix `tests/integration/test_migrations.py` and
  `tests/integration/test_terminal_secret_exclusion.py`: both hardcode
  `Path(__file__).resolve().parents[2] / "migrations"` to load `0001_initial.py`
  directly. Replace with `from ic_env_guard.db.migrations import MIGRATIONS_DIR`
  and load the migration file via `MIGRATIONS_DIR / "0001_initial.py"`.
- [x] Add `state_database: Path | None = None` to `AppConfig` and a
  `state_database: Path | None = None` argument to `create_app()`. Add
  `_resolve_state_db(arg, config)` that evaluates the four-step resolution order
  defined above.
- [x] Add a **function-scoped** autouse fixture to `tests/conftest.py` that
  calls `monkeypatch.setenv("IC_ENV_GUARD_STATE_DB", str(tmp_path / "state.db"))`.
  This isolates each test's database through the env-var resolution step without
  modifying any of the 17 existing `create_app()` call sites. Run the full test
  suite as a non-root user to confirm no test touches `/var/lib/...`.
- [x] Add unit tests for all four `_resolve_state_db()` resolution branches in
  `tests/unit/test_state_db_resolution.py`: (1) explicit `create_app` argument
  wins over everything; (2) `AppConfig.state_database` wins when arg is `None`;
  (3) `IC_ENV_GUARD_STATE_DB` env var wins when config field is also `None`;
  (4) hardcoded fallback `/var/lib/ic-env-guard/state.db` is returned when none
  of the above are set. The fallback test MUST call
  `monkeypatch.delenv("IC_ENV_GUARD_STATE_DB", raising=False)` to override the
  autouse fixture; otherwise the env-var branch shadows the fallback and the
  test always passes vacuously.
- [x] Write a failing test: call `create_app(token_file=..., state_database=db)`
  with a tmp_path db, record audit events, call `create_app()` again with the
  same db, and assert events are still queryable.
- [x] In `create_app()`, replace the in-memory engine + `create_all()` with:
  1. resolve `db_path` via `_resolve_state_db()`;
  2. call `run_migrations(db_path)`;
  3. open a SQLAlchemy engine using `create_sqlite_engine(db_path)` from
     `db/session.py` (already sets WAL and foreign-key PRAGMAs via event
     listeners); first extend that function to pass
     `connect_args={"check_same_thread": False}` so it is safe in async
     contexts. Create the session factory with the existing
     `create_session_factory(engine)`;
  4. use request-scoped sessions via a FastAPI dependency that `yield`s a session
     from the factory, commits on normal exit, rolls back on any exception, and
     closes in `finally`; this replaces the current single shared session that
     is never closed. The audit repository uses its own independent session with
     explicit `commit()` calls and MUST NOT share a session with request business
     logic, so audit records survive business-logic rollbacks. (Detailed
     gateway-audit failure-mode rules — intent-commit fail-closed, outcome-commit
     semantics when upstream succeeds, exception suppression — are specified in
     Task 2 where gateway audit is introduced.);
  5. close the engine in the lifespan shutdown hook;
  6. do NOT call `Base.metadata.create_all()`.
- [x] In `config/audit.py`, replace `Base.metadata.create_all(engine)` in
  `audit_config_load_to_db()` with `run_migrations(db_path)` before opening
  the engine, for the same reason — the migration is the schema source of truth.
- [x] Update installer-generated config template and `README` to show the
  `state_database:` field with the production default path.
- [x] Extend `tests/integration/test_packaging_runtime.py`:
  - assert `ic_env_guard.migrations` is importable as a package;
  - assert `MIGRATIONS_DIR` resolves to an existing path containing `0001_initial.py`.
  (`httpx` and `websockets` import checks belong in Task 4 and Task 9 respectively,
  when those dependencies are added.)
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q \
    tests/integration/test_agent_audit_durability.py \
    tests/integration/test_migrations.py \
    tests/integration/test_terminal_secret_exclusion.py \
    tests/integration/test_packaging_runtime.py
  conda run -n venv312 pytest -q tests/contract
  conda run -n venv312 pytest -q
  ```

  Expected: audit survives restart; modified migration, secret-exclusion, and
  packaging tests pass; all `001` contracts still pass; full suite passes as a
  non-root user with no test touching `/var/lib/...`.

## Task 1: Runtime Modes and Configuration

**Files:**

- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/config/loader.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_control_plane_config.py`
- Test: `backend/tests/unit/test_security_config.py`

- [x] Add failing tests for unique agent IDs, URL shape, credential exclusivity,
  token-file permissions, verified TLS, loopback-only development HTTP, and the
  two runtime modes (`agent` and `control-plane`). Include a test asserting that
  `mode: combined` produces a Pydantic validation error at config load time.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_control_plane_config.py tests/unit/test_security_config.py
  ```

  Expected: failures because `AgentConfig`, `ControlPlaneConfig`, and runtime
  modes do not exist.

- [x] Add `AgentTlsConfig`, `AgentConfig`, `ControlPlaneConfig`, and
  `mode: Literal["agent", "control-plane"]` to `AppConfig`. `combined` is NOT
  in the enum — an unknown value fails Pydantic validation with a clear message
  rather than passing validation only to be rejected later. `mode` MUST default
  to `agent` so existing configs and every test fixture that calls `create_app()`
  keep producing the current single-host app unchanged. When feature `003` adds
  `combined`, it adds it to the enum then.
- [x] Validate `base_url` as scheme, host, and optional port only; reject URL
  credentials, query, fragments, and non-root paths.
- [x] Permit insecure HTTP only for loopback when
  `development.allow_insecure_http` is true and the server itself is local-only.
- [x] Refactor `create_app()` to mount routers by mode. **Risk:** `create_app()`
  currently builds all dependencies and mounts all routers unconditionally;
  factor the mode-independent agent wiring into a helper so the `agent`-mode app
  is identical to `001`.
- [x] Make agent-database initialization conditional on mode: open `state_database`
  and run agent migrations only when `mode == "agent"`; in `control-plane` mode,
  skip `state_database` resolution and engine creation entirely (the gateway audit
  database is opened separately in Task 2). Add a test: call
  `create_app(config=AppConfig(mode="control-plane", ...),
  state_database=tmp_path / "must-not-exist.db")` and assert that the file was
  NOT created; do NOT add `mode` as a separate keyword argument to `create_app()`
  — mode comes from `AppConfig` to avoid a second configuration override path.
  This guards FR-005 ("control-plane mode MUST NOT imply management of the
  control-plane host"). The complementary check — that the control-plane database
  contains no agent tables — belongs in Task 2's isolation test, where the
  control-plane DB is first created.
- [x] Add a regression test asserting the `agent`-mode router set and dependency
  overrides are unchanged from `001` before refactoring anything else.
- [x] Re-run the focused tests and then:

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
- Create: `backend/ic_env_guard/control_plane_migrations/__init__.py`
- Create: `backend/ic_env_guard/control_plane_migrations/0001_control_plane_audit.py`
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/pyproject.toml` (add `build>=1.2` to `test` extra)
- Modify: `backend/tests/integration/test_packaging_runtime.py`
- Test: `backend/tests/integration/test_control_plane_audit.py`
- Test: `backend/tests/contract/test_migration_contract.py`

- [x] Write failing integration tests that restart the application and verify
  routing intent/outcome records survive, including failures that occur before
  upstream dispatch. Until Task 4/5 exist, the test exercises the repository
  through a small dispatch stub rather than a real upstream call.
- [x] Create `backend/ic_env_guard/control_plane_migrations/` with an
  `__init__.py` (makes it an importable Python package, and ensures
  `include = ["ic_env_guard*"]` picks up the directory and its contents in the
  wheel). Create `run_control_plane_migrations(db_path)` in
  `db/control_plane_migrations.py`. Its `CONTROL_PLANE_MIGRATIONS_DIR` must
  resolve relative to `__file__` inside the package so it works after wheel
  install. The runner MUST follow the same contract as the existing agent runner:
  maintain `schema_versions`, be idempotent on repeated calls, and raise
  `MigrationError` if any prior migration has a `failed` result — add
  runner idempotency and failure-state tests alongside the audit tests.
- [x] Add `0001_control_plane_audit.py` (raw `sqlite3`, the `001` convention)
  with actor, source address, agent ID, operation, target, result, dispatch
  state, upstream status, correlation ID, failure category, and timestamp. The
  migration is the **single source of truth**; the ORM model maps the table and
  MUST NOT `create_all` it.
- [x] Add a test asserting database isolation: the agent DB contains no
  control-plane table and the control-plane DB contains no agent table after
  both runners have applied their migrations.
- [x] Open a dedicated durable engine against `control_plane.audit_database`
  (default `/var/lib/ic-env-guard/control-plane.db`), created only in
  `control-plane` mode. This is separate from the agent audit database fixed in
  Task 0.
- [x] Extend `tests/integration/test_packaging_runtime.py` with a wheel-content
  test. Add `build>=1.2` to the `test` extra in `pyproject.toml` first (it is
  not currently declared). The test should:
  - run `python -m build --wheel --no-isolation --outdir <tmp_dir>` against the
    backend package (`--no-isolation` avoids network access to download build
    deps during tests; the test environment already has `build>=1.2` installed);
  - open the resulting `.whl` (a zip archive) and assert it contains entries
    matching `ic_env_guard/migrations/0001_initial.py` and
    `ic_env_guard/control_plane_migrations/0001_control_plane_audit.py` — these
    are files that must be physically present in the wheel;
  - read the wheel `METADATA` file inside the archive and assert its
    `Requires-Dist` lines include `httpx` and `websockets` — runtime deps are
    declared in metadata, not as packaged files, so the ZIP content check is the
    wrong assertion for them.
  (`httpx` and `websockets` will not yet appear in `Requires-Dist` at this task;
  the METADATA assertions are added in Task 4 and Task 9 once those deps are
  declared. The migration file assertions apply now.)
- [x] Add a repository method that creates intent before dispatch and finalizes
  the same record after success or failure. Specify and test the following
  failure-mode rules explicitly:
  (a) **Intent commit fails**: the handler MUST fail closed and MUST NOT dispatch
  the upstream request — no audit trail means no dispatch; return an error to
  the caller;
  (b) **Outcome commit fails, upstream succeeded**: MUST return the successful
  business response to the caller — returning an error could induce a duplicate
  mutation on retry. Log a sanitized warning and mark gateway readiness as failed
  (`/readyz` returns 503 until the next successful audit write);
  (c) **Business exception present**: suppress the audit exception (if any),
  preserve and re-raise the original business exception; audit exceptions MUST
  NOT overwrite the original error or leak raw SQLite text to the caller.
  Add a test for each of these three cases.
- [x] Add bounded authenticated query routes under `/api/control-plane/audit`.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q \
    tests/integration/test_control_plane_audit.py \
    tests/contract/test_migration_contract.py \
    tests/integration/test_packaging_runtime.py
  ```

  Expected: persistence, migration, filtering, secret-exclusion, and wheel-content
  tests pass.

## Task 3: Agent Registry and Capability Negotiation

**Files:**

- Create: `backend/ic_env_guard/agents/__init__.py`
- Create: `backend/ic_env_guard/agents/models.py`
- Create: `backend/ic_env_guard/agents/registry.py`
- Create: `backend/ic_env_guard/api/agents.py`
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/ic_env_guard/api/risk.py`
- Modify: `specs/002-multi-agent-control-plane/contracts/control-plane-config.md`
- Test: `backend/tests/contract/test_agents_api.py`
- Test: `backend/tests/unit/test_agent_registry.py`

- [x] Write failing tests for immutable lookup, disabled targets, safe inventory
  responses, and unknown targets.
- [x] Add config-contract coverage for the documented mode-specific fields,
  credential rules, TLS exceptions, database path semantics, and exact agent ID
  regex in `specs/002-multi-agent-control-plane/contracts/control-plane-config.md`.
- [x] Implement one registry instance from validated startup configuration.
- [x] Ensure safe summaries omit URLs, token paths, CA paths, and raw transport
  errors.
- [x] Add `GET /api/capabilities` to local agents and inventory/detail/probe
  routes to the control plane. Document the **minimum agent version** that
  exposes `/api/capabilities`: a control plane pointed at a pre-`002` agent that
  lacks this endpoint treats it as `agent_protocol_error` and surfaces no
  features, rather than silently half-working.
- [x] Run:

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
- Modify: `backend/tests/integration/test_packaging_runtime.py`
- Test: `backend/tests/unit/test_agent_client.py`
- Test: `backend/tests/integration/test_agent_availability.py`

- [x] Promote `httpx` from the `test` extra to a runtime dependency in
  `pyproject.toml`; do not rely on a transitive dependency. Extend the wheel
  METADATA assertion in `tests/integration/test_packaging_runtime.py` to verify
  that `Requires-Dist` in the built wheel includes `httpx` (runtime deps appear
  in wheel METADATA, not as packaged files).
- [x] Write tests proving redirects are not followed, browser authorization and
  forwarding headers are not propagated, TLS settings are applied, response
  size/content type are bounded, and error categories map to the HTTP contract.
- [x] Add availability transition tests for `unknown`, `ready`, `degraded`,
  `unavailable`, `disabled`, timestamp updates, stale-to-`unknown`, missing
  capability endpoint, missing optional capability, and recovery.
- [x] Implement the constrained client as one application-lifetime
  `httpx.AsyncClient` with per-target credentials and verified TLS. Routers call
  this client directly (no transport-interface seam; that arrives only with the
  `combined` follow-up feature). The upstream terminal WebSocket (Task 9) is
  opened separately with the `websockets` client using an `ssl.SSLContext` built
  from the same per-target TLS settings; httpx is HTTP-only.
- [x] Generate or preserve one correlation ID per gateway request and send it as
  `X-Correlation-ID`.
- [x] Add contract tests proving successful inventory/service/monitoring/audit
  responses and normalized error responses include `X-Correlation-ID`.
- [x] Permit a single read-only retry only when failure is known to occur before
  dispatch. Never retry POST or DELETE.
- [x] Add bounded-concurrency periodic probes with jitter, `observed_at`, and
  `stale_after`; cancel the probe task during shutdown.
- [x] Verify gateway `/readyz` remains ready when one test agent is unavailable.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q \
    tests/unit/test_agent_client.py \
    tests/integration/test_agent_availability.py \
    tests/integration/test_packaging_runtime.py
  ```

## Task 5: Explicit Service and Monitoring Routes

**Files:**

- Create: `backend/ic_env_guard/api/agent_services.py`
- Create: `backend/ic_env_guard/api/agent_monitoring.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_agent_services_api.py`
- Test: `backend/tests/integration/test_multi_agent_monitoring.py`

- [x] Write contract tests for every service route and the JSON monitoring
  snapshot route using two fake agents with overlapping service IDs.
- [x] Implement explicit allowlisted route handlers that dispatch through the
  constrained HTTP client (Task 4); do not add a catch-all `{path:path}` reverse
  proxy.
- [x] Preserve safe upstream application errors and status codes.
- [x] Map connection/TLS/auth failures to `503`, protocol failures to `502`,
  pre-dispatch timeouts to `504`, and uncertain mutation outcomes to `424`.
- [x] Create/finalize one durable gateway audit record around each privileged
  request.
- [x] Keep raw `/metrics` outside the gateway API.
- [x] Run:

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

- [x] Write failing tests for startup selection, session storage fallback,
  unavailable-agent rendering, and delayed old-agent responses.
- [x] Load safe agent summaries only after browser authentication.
- [x] Persist only `activeAgentId`; never persist agent URLs or credentials.
- [x] Add a monotonically increasing selection generation or abort controller so
  stale requests cannot update active pages.
- [x] Pass `agentId` explicitly to service and overview API functions.
- [x] Display agent identity and status next to primary navigation and on every
  destructive confirmation.
- [x] Run:

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

- [x] Change the monitoring page to consume the global active agent and
  `/api/agents/{agent_id}/monitoring/snapshot`.
- [x] Remove browser forms that submit agent addresses and bearer keys.
- [x] Mark `/api/monitoring/machines` mutation routes deprecated for one
  compatibility release; prevent new frontend use. Do NOT remove the routes or
  `MachineRegistry` in this task — deletion is a separate step that requires the
  compatibility window to have elapsed (documented in Task 10 and the operations
  guide). Removing in the same task as deprecating contradicts the one-release
  window.
- [x] Verify the UI has exactly one agent selector.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_monitoring_api_contract.py tests/integration/test_terminal_secret_exclusion.py
  cd ../frontend
  npm test -- --run tests/metrics-page.test.tsx
  ```

## Task 8: Agent-Scoped Terminal HTTP and Gateway Tickets

**Files:**

- Create: `backend/ic_env_guard/api/agent_terminals.py`
- Create: `backend/ic_env_guard/agents/terminal_proxy.py`
- Modify: `backend/ic_env_guard/main.py`
- Test: `backend/tests/contract/test_agent_terminal_http_contract.py`
- Test: `backend/tests/unit/test_gateway_terminal_tickets.py`

- [x] Write tests for list, create, detail, history, resize, connect-token, and
  `DELETE` close with duplicate terminal IDs on different agents, plus a
  capacity test: connect-token returns HTTP `429 gateway_capacity_exceeded` when
  the ticket store is full, and no upstream ticket is requested in that case.
- [x] Add gateway audit tests for terminal create, resize, connect-token, close,
  authorization denial, and pre-dispatch upstream failures.
- [x] Implement explicit terminal routes preserving current methods and status
  codes.
- [x] On connect-token, reserve ticket-store capacity **before** requesting the
  upstream ticket; if full, return `429` without contacting the agent. Only
  after reserving, obtain the upstream ticket server-side and issue a distinct
  gateway ticket bound to actor, agent, terminal, intended WebSocket path, and
  expiry. Release the reservation on any failure (upstream error, timeout,
  validation).
- [x] Enforce bounded storage, one-use consumption, expiry, and complete
  redaction of both ticket values.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q tests/contract/test_agent_terminal_http_contract.py tests/unit/test_gateway_terminal_tickets.py
  ```

## Task 9: Terminal WebSocket Proxy and Frontend State

**Files:**

- Create: `backend/ic_env_guard/api/agent_terminal_ws.py`
- Modify: `backend/ic_env_guard/agents/terminal_proxy.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/tests/integration/test_packaging_runtime.py`
- Modify: `frontend/src/api/terminals.ts`
- Modify: `frontend/src/pages/TerminalPage.tsx`
- Modify: `frontend/src/terminal/TerminalPane.tsx`
- Test: `backend/tests/integration/test_agent_terminal_websocket.py`
- Test: `frontend/tests/terminal-agent-routing.test.tsx`

- [x] Add `websockets` as a runtime dependency in `pyproject.toml` (it is not
  declared today and must not be assumed transitively from `uvicorn[standard]`).
  Extend the wheel METADATA assertion in `tests/integration/test_packaging_runtime.py`
  to verify that `Requires-Dist` in the built wheel includes `websockets` (runtime
  deps appear in wheel METADATA, not as packaged files).
- [x] Write backend tests for ticket mismatch, frame limits, backpressure,
  upstream failure, paired-task cancellation, reconnect cursor, gateway
  shutdown, and rejection past the global active-proxy cap (close code `4429`).
- [x] Add WebSocket attach audit tests for successful attach, actor mismatch,
  invalid ticket, proxy-cap rejection, upstream establishment failure, and
  sanitized close categories.
- [x] Open the upstream WebSocket with the `websockets` client and an
  `ssl.SSLContext` derived from the target's TLS config.
- [x] On attach, atomically acquire a proxy slot and then consume the gateway
  ticket; if the global active-proxy cap is reached, reject with `4429` before
  consuming the ticket so a valid ticket is not wasted. Release the slot on any
  failure path.
- [x] Implement the WebSocket contract with bounded per-direction queues, the
  configurable global cap on concurrent proxied sockets and outstanding tickets,
  and sanitized close codes.
- [x] Add `agentId` to every frontend terminal API and WebSocket URL.
- [x] Key frontend terminal state by `(agentId, terminalId)` and remount panes
  when the active agent changes.
- [x] Cancel old sockets and resize timers before activating a new agent.
- [x] Verify switching agents cannot send input to the previous terminal.
- [x] Run:

  ```bash
  cd backend
  conda run -n venv312 pytest -q \
    tests/integration/test_agent_terminal_websocket.py \
    tests/integration/test_packaging_runtime.py
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

- [x] Add agent-scoped audit queries and a separate gateway audit view; do not
  merge or sort multiple remote histories in the first release.
- [x] Add tests for correlation IDs across gateway and agent events and for
  secret exclusion in browser payloads, gateway logs, agent logs, audit rows,
  metrics, WebSocket close reasons, and frontend state snapshots.
- [x] Add mixed-version tests that disable missing capabilities and reject
  unsupported API versions.
- [x] Extend `start.sh` with explicit `agent` and `control-plane` development
  commands that honor configured bind and port values. (`combined` is deferred
  to feature `003`.)
- [x] Document TLS provisioning, per-agent token files, migration from the old
  machine registry, rollback to `agent` mode, gateway outage recovery, and the
  post-compatibility-release follow-up that removes deprecated
  `/api/monitoring/machines` mutation routes.
- [x] Run full verification:

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

- [x] The [requirements checklist](checklists/requirements.md) remains satisfied.
- [x] All feature `001` contract tests pass in `agent` mode; original assertions
  and external behavior are preserved (test infrastructure such as fixtures may
  be adjusted as needed by Task 0).
- [x] Security review finds no browser-visible agent token or upstream ticket.
- [x] Gateway audit survives restart and covers pre-dispatch failures.
- [x] One unavailable agent does not affect another agent or gateway readiness.
- [x] Service mutations are never automatically retried.
- [x] Duplicate terminal IDs across agents remain isolated end to end.
- [x] Rollback to single-agent operation is documented and tested.
- [x] Agent audit survives restart (Task 0), satisfying `001` FR-021/FR-026.
- [x] Agent and control-plane databases never contain each other's tables.
- [x] Connect-token returns `429` when the ticket store is full without
  contacting the agent; the WS attach rejects past the cap with `4429` without
  wasting a valid ticket or growing memory unboundedly.
- [x] A config with `mode: combined` fails Pydantic validation; `combined` is not
  in the enum and never reaches application startup.

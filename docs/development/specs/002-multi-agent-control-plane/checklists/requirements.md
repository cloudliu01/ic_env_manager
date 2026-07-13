# Specification Quality Checklist: Multi-Agent Control Plane

**Created**: 2026-06-14

**Feature**: [spec.md](../spec.md)

## Scope

- [x] The feature is explicitly post-MVP and does not rewrite feature `001`.
- [x] This feature ships `agent` and `control-plane` modes only. `combined` is
      not in the enum; a config file with `mode: combined` fails Pydantic
      validation at load time and never reaches application startup.
- [x] Non-goals exclude discovery, bulk orchestration, HA, generic proxying, and
      `combined` mode.

## Security

- [x] Browser and agent authentication boundaries are separate.
- [x] All `/api/` routes require browser authentication and explicit authorization,
      except `/healthz`, `/readyz`, and `/api/auth/login`; this explicitly covers
      `/api/control-plane/audit`.
- [x] Non-loopback agent transport requires verified TLS.
- [x] Agent credentials and upstream terminal tickets remain server-side.
- [x] Agent targets cannot be supplied by browser requests.
- [x] Redirects and generic arbitrary-path proxying are prohibited.
- [x] Errors, audit, logs, metrics, and UI exclude secrets and terminal content.

## Reliability

- [x] Mutating requests are not automatically retried.
- [x] Indeterminate mutation outcomes have an explicit error.
- [x] One unavailable agent does not affect gateway readiness or other agents.
- [x] Agent status freshness and transition meanings are defined.
- [x] WebSocket backpressure, cancellation, reconnect, and shutdown are defined.
- [x] A global cap bounds concurrent terminal proxies and outstanding tickets.

## Audit and Compatibility

- [x] Agent audit durability (a `001` FR-026 defect) is fixed as a prerequisite:
      `create_app()` uses the configured `state_database` path (default
      `/var/lib/ic-env-guard/state.db`; overridable via `create_app(state_database=...)`
      for tests) and calls `run_migrations()`. No new migration needed —
      `audit_events` already exists in `0001_initial.py`. `config/audit.py`'s
      `audit_config_load_to_db()` also replaces its `create_all()` with
      `run_migrations()`. The `state_database` field and default are shown in the
      architecture config example and the installer-generated template.
- [x] Gateway audit is durable in a dedicated control-plane database, separate
      from the agent database, and required from MVP 1.
- [x] Both migration directories live inside the `ic_env_guard` package
      (`ic_env_guard/migrations/` and `ic_env_guard/control_plane_migrations/`),
      each with an `__init__.py`, so the wheel includes them via
      `include = ["ic_env_guard*"]`. The pre-existing top-level `backend/migrations/`
      and its hardcoded references in `test_migrations.py` and
      `test_terminal_secret_exclusion.py` are fixed in Task 0.
- [x] Each database uses its own runner; neither database ever receives the
      other's tables; isolation is verified by a test.
- [x] The control-plane migration runner follows the same contract as the agent
      runner: `schema_versions` table, idempotent on repeated calls, raises
      `MigrationError` on any prior `failed` result; covered by dedicated tests.
- [x] Packaging tests verify wheel archive contents (not just source-tree imports)
      for both migration directories and `httpx`/`websockets` runtime deps.
- [x] Correlation IDs associate gateway and agent events.
- [x] API versions and capabilities support mixed-version deployments, with a
      documented minimum agent version that exposes the capability endpoint; an
      agent lacking the endpoint is `agent_protocol_error`, not partially usable.
- [x] Existing feature `001` routes and tests remain valid in the default
      `agent` mode.
- [x] Monitoring migrates to one authoritative registry.

## Contract Completeness

- [x] Service methods preserve current host-agent semantics.
- [x] Terminal detail, history, connect-token, resize, and DELETE close are covered.
- [x] `connect-token` reserves capacity before requesting an upstream ticket;
      returns `429 gateway_capacity_exceeded` when full.
- [x] WebSocket attach calls atomic `try_acquire_proxy_slot()`; only on success
      does it consume the ticket. Rejects with `4429` on slot failure before
      touching the ticket, with no check-then-acquire race.
- [x] `httpx` is a runtime dependency (promoted from test-only) and `websockets`
      is a new runtime dependency; both declared in `pyproject.toml` and verified
      by `tests/integration/test_packaging_runtime.py`.
- [x] Monitoring JSON and Prometheus text interfaces remain distinct.
- [x] Normalized HTTP and WebSocket error mappings are documented, including
      `gateway_capacity_exceeded` (`429`) and `4429`.
- [x] FR numbers are sequential (FR-001 through FR-031).
- [x] Success criteria are measurable and testable.


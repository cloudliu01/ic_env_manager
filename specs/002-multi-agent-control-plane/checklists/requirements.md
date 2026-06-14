# Specification Quality Checklist: Multi-Agent Control Plane

**Created**: 2026-06-14

**Feature**: [spec.md](../spec.md)

## Scope

- [x] The feature is explicitly post-MVP and does not rewrite feature `001`.
- [x] This feature ships `agent` and `control-plane` modes only; `combined` is
      recognized in the config schema but rejected at startup with a pointer to
      feature `003`.
- [x] Non-goals exclude discovery, bulk orchestration, HA, generic proxying, and
      `combined` mode.

## Security

- [x] Browser and agent authentication boundaries are separate.
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

- [x] Agent audit durability (a `001` FR-026 defect) is fixed as a prerequisite
      before any `002` routes are added; the in-memory `sqlite://` engine is
      replaced with a durable path.
- [x] Gateway audit is durable in a dedicated control-plane database, separate
      from the agent database, and required from MVP 1.
- [x] The two databases use isolated migration directories and runners; neither
      database ever receives the other's tables.
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
- [x] WebSocket attach acquires a proxy slot before consuming the gateway
      ticket; rejects with `4429` when the cap is reached, never wasting a valid
      ticket.
- [x] `httpx` is a runtime dependency (promoted from test-only) and `websockets`
      is a new runtime dependency; both are declared in `pyproject.toml`.
- [x] Monitoring JSON and Prometheus text interfaces remain distinct.
- [x] Normalized HTTP and WebSocket error mappings are documented, including
      `gateway_capacity_exceeded` (`429`) and `4429`.
- [x] FR numbers are sequential (FR-001 through FR-031).
- [x] Success criteria are measurable and testable.


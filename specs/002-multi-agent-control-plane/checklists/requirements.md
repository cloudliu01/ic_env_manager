# Specification Quality Checklist: Multi-Agent Control Plane

**Created**: 2026-06-14

**Feature**: [spec.md](../spec.md)

## Scope

- [x] The feature is explicitly post-MVP and does not rewrite feature `001`.
- [x] Runtime modes and local-host ownership are unambiguous.
- [x] Non-goals exclude discovery, bulk orchestration, HA, and generic proxying.

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

- [x] Gateway audit is durable, in a dedicated database separate from the `001`
      in-memory store, and required from MVP 1.
- [x] Correlation IDs associate gateway and agent events.
- [x] API versions and capabilities support mixed-version deployments, with a
      documented minimum agent version that exposes the capability endpoint.
- [x] Existing feature `001` routes and tests remain valid in the default
      `agent` mode.
- [x] Monitoring migrates to one authoritative registry.
- [x] `combined` mode uses an in-process transport (no HTTP/WS self-proxy) and
      is delivered after the transport interface lands.
- [x] The upstream WebSocket client library is identified (`websockets`) with
      verified TLS, since the HTTP client cannot open WebSockets.

## Contract Completeness

- [x] Service methods preserve current host-agent semantics.
- [x] Terminal detail, history, connect-token, resize, and DELETE close are covered.
- [x] Monitoring JSON and Prometheus text interfaces remain distinct.
- [x] Normalized HTTP and WebSocket error mappings are documented.
- [x] Success criteria are measurable and testable.


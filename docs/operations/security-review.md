# Security Review Checklist

Use this checklist before release and after security-sensitive changes.

## Authentication and authorization

- [ ] Terminal, service-control, log/status, and audit/status routes require authentication where specified.
- [ ] Control-plane inventory and every `/api/agents/{agent_id}/...` route require browser authentication before resolving or contacting an agent.
- [ ] `/healthz` and `/readyz` expose only bounded liveness/readiness diagnostics.
- [ ] Generated bearer token values are never echoed, logged, stored in audit, exposed in metrics, or shown in UI diagnostics.
- [ ] Token file permissions restrict access to the runtime user and host administrators.
- [ ] Invalid authentication or remote-bind security configuration fails closed.

## Control-plane agent routing

- [ ] Agent IDs come only from validated startup configuration and match the documented URL-safe pattern.
- [ ] Browser requests cannot submit arbitrary upstream URLs, hosts, ports, paths, headers, or credentials.
- [ ] Non-loopback agents require HTTPS and verified TLS; `development.allow_insecure_http` is limited to local-only loopback development.
- [ ] Each enabled agent has exactly one server-side token file with restrictive permissions.
- [ ] Agent bearer credentials and upstream terminal tickets never appear in browser responses, frontend state, logs, metrics, audit, or diagnostics.
- [ ] Gateway routes use explicit method/path/query/content-type allowlists and do not follow upstream redirects.
- [ ] SSRF protections reject metadata, link-local, multicast, unspecified, and self-target addresses.
- [ ] Unsupported API versions or missing capability endpoints disable all features for that agent instead of partially routing against an unknown contract.

## Remote bind and metrics exposure

- [ ] Default bind is local-only.
- [ ] Remote bind requires `remote_bind_enabled: true` plus valid authentication settings.
- [ ] `/metrics` accepts local scrapes by default.
- [ ] Remote metrics scraping is restricted by CIDR allowlist.
- [ ] Control-plane UI monitoring uses authenticated JSON snapshots; Prometheus scrapes each agent's `/metrics` endpoint directly.
- [ ] Metrics labels do not include source IPs, request IDs, terminal IDs, commands, credentials, raw paths, or unbounded user input.

## Service management constraints

- [ ] Only configured services are listed or controlled.
- [ ] API payloads never provide commands or arbitrary command arguments.
- [ ] Unknown service IDs are rejected without command execution.
- [ ] Unsupported operations are rejected without command execution.
- [ ] Repeated start/stop/restart operations are idempotent or produce predictable audited outcomes.
- [ ] Service environment values are redacted from logs, audit, metrics, UI, and diagnostics.

## Terminal safety

- [ ] Terminal sessions have owner, process ID, creation time, last activity time, idle timeout, and close path.
- [ ] Browser disconnect does not orphan shell processes.
- [ ] Idle timeout cleanup reaps abandoned sessions.
- [ ] WebSocket transport remains separated from PTY manager logic.
- [ ] Control-plane terminal tickets are one-use, short-lived, capacity bounded, and bound to actor, agent, terminal, and intended WebSocket path.
- [ ] Gateway WebSocket proxying enforces frame limits, backpressure, paired cancellation, sanitized close codes, and reconnect cursor handling.
- [ ] Terminal content is not persisted by default.

## Local state, audit, and migration

- [ ] SQLite schema changes are migration-managed.
- [ ] Failed or incompatible migrations produce actionable startup diagnostics.
- [ ] Startup reconciliation compares persisted terminal/service state to actual host processes.
- [ ] Audit events include timestamp, actor where available, source address where available, operation, target, result, and failure reason where applicable.
- [ ] Control-plane gateway audit is durable, migration-managed, separate from agent `state_database`, and records both pre-dispatch failures and routed outcomes.
- [ ] Correlation IDs allow gateway audit records and agent audit records to be associated without exposing secrets.
- [ ] Audit records exclude terminal content, passwords, bearer tokens, private keys, and service environment secrets.

## Compatibility and deprecation

- [ ] Existing `agent` mode remains the default and does not expose control-plane routing APIs.
- [ ] `control-plane` mode does not create or migrate an agent `state_database` and does not imply local host management.
- [ ] Deprecated `/api/monitoring/machines` mutation routes are retained only for the documented compatibility window and return deprecation metadata.
- [ ] New frontend and operator workflows use the configured agent registry instead of browser-managed machine credentials.

## MVP scope guard

Confirm the MVP does not include:

- [ ] desktop wrapper
- [ ] SSH server
- [ ] custom time-series database
- [ ] PromQL engine
- [ ] alerting engine
- [ ] unrestricted command API
- [ ] cloud control plane dependency
- [ ] Windows PTY support
- [ ] discovery-based or unrestricted multi-host orchestration

If any item above is present, stop and require an explicit post-MVP architectural amendment before release.

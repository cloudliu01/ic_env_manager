# Security Review Checklist

Use this checklist before release and after security-sensitive changes.

## Authentication and authorization

- [ ] Terminal, service-control, log/status, and audit/status routes require authentication where specified.
- [ ] `/healthz` and `/readyz` expose only bounded liveness/readiness diagnostics.
- [ ] Generated bearer token values are never echoed, logged, stored in audit, exposed in metrics, or shown in UI diagnostics.
- [ ] Token file permissions restrict access to the runtime user and host administrators.
- [ ] Invalid authentication or remote-bind security configuration fails closed.

## Remote bind and metrics exposure

- [ ] Default bind is local-only.
- [ ] Remote bind requires `remote_bind_enabled: true` plus valid authentication settings.
- [ ] `/metrics` accepts local scrapes by default.
- [ ] Remote metrics scraping is restricted by CIDR allowlist.
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
- [ ] Terminal content is not persisted by default.

## Local state, audit, and migration

- [ ] SQLite schema changes are migration-managed.
- [ ] Failed or incompatible migrations produce actionable startup diagnostics.
- [ ] Startup reconciliation compares persisted terminal/service state to actual host processes.
- [ ] Audit events include timestamp, actor where available, source address where available, operation, target, result, and failure reason where applicable.
- [ ] Audit records exclude terminal content, passwords, bearer tokens, private keys, and service environment secrets.

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
- [ ] multi-host orchestration

If any item above is present, stop and require an explicit post-MVP architectural amendment before release.

# Service Configuration Reference

Services are controlled only when they are explicitly declared in the local configuration file. API requests never provide commands.

## Minimal command service

```yaml
services:
  - id: demo-http
    name: Demo HTTP service
    description: Harmless local test service
    command: python3 -m http.server 18080
    cwd: /tmp
    allowed_operations: [start, stop, restart, status, healthcheck]
    restart: never
    start_timeout_seconds: 10
    stop_timeout_seconds: 10
    healthcheck:
      type: tcp
      target: 127.0.0.1:18080
      interval_seconds: 10
      timeout_seconds: 2
      failure_threshold: 3
    logs:
      capture: true
      max_tail_lines: 200
      rotate:
        max_bytes: 1048576
        backup_count: 3
```

## Systemd-mapped service

```yaml
services:
  - id: local-worker
    name: Local Worker
    systemd_unit: local-worker.service
    allowed_operations: [start, stop, restart, status]
    restart: on-failure
    start_timeout_seconds: 30
    stop_timeout_seconds: 30
    healthcheck:
      type: none
      interval_seconds: 60
      timeout_seconds: 5
    logs:
      capture: false
      max_tail_lines: 100
```

## Required fields

- `id`: unique service identifier matching `^[a-zA-Z0-9_.-]+$`
- `name`: display name
- exactly one execution mapping: `command` or `systemd_unit`
- `allowed_operations`: one or more of `start`, `stop`, `restart`, `status`, `healthcheck`
- `restart`: `never`, `on-failure`, or `always`
- `start_timeout_seconds`
- `stop_timeout_seconds`
- `logs`

## Safety rules

- Commands come only from local configuration; they are never accepted from API payloads.
- Unknown service IDs are rejected without command execution.
- Unsupported operations are rejected without command execution.
- Environment variable values are not exposed in audit records, metrics, or UI diagnostics.
- Failure messages are bounded and redacted before persistence.
- Ambiguous definitions, such as setting both `command` and `systemd_unit`, fail validation.

## Health checks

Supported health check types:

- `none`: health reporting disabled
- `http`: check an HTTP endpoint
- `tcp`: connect to a host/port
- `process`: verify the managed process is alive

`timeout_seconds` must be less than or equal to `interval_seconds`.

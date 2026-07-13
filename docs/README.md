# Documentation

This documentation describes the current supported behavior of IC Design
Environment Guard. Choose a task below; do not use archived implementation
plans as operating instructions.

## Start Here

- [Getting Started](guides/getting-started.md) — choose Standalone Agent,
  Manager Fleet, or local development and verify the result.

## Deployment and Operation

- [Configuration Guide](guides/configuration.md) — practical Agent and Manager
  configurations and validation.
- [Agent Deployment](guides/agent-deployment.md) — install and run an Agent as
  an existing Linux user.
- [Manager Fleet](guides/manager-fleet.md) — register, discover, enroll, probe,
  rotate, disable, and remove Agents.
- [Local Data Ingest](guides/local-data-ingest.md) — publish latest local
  observations and log-source metadata.
- [Monitoring and Logs](guides/monitoring-and-logs.md) — Prometheus, status,
  expiry, Fleet summaries, and bounded log tails.
- [Security](guides/security.md) — listener, token, TLS, SSH enrollment,
  credential, Terminal, and audit boundaries.
- [Backup, Upgrade, and Recovery](guides/backup-upgrade-recovery.md) — preserve
  and restore the Agent and Manager atomic state units.
- [Development](guides/development.md) — Conda/npm setup, local wrapper,
  generated files, tests, builds, and platform boundaries.

## Reference

- [Configuration Reference](reference/configuration.md) — mode-aware fields,
  defaults, constraints, and purposes.
- [API and Endpoint Reference](reference/api-and-endpoints.md) — listener,
  route-family, authentication, WebSocket, and Unix-socket map.

## Development History

[Development History](development/README.md) preserves specifications, design
notes, implementation plans, and validation records. These files explain why
the system evolved, but they are non-normative: current code, tests, guides,
and references define supported operation.

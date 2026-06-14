# Control Plane Operations

The control plane is a gateway for multiple configured host agents. Browsers authenticate to the control plane only; per-agent bearer tokens and upstream terminal tickets stay on the server.

## Runtime modes

`agent` is the default mode and preserves the single-host behavior: local services, terminals, metrics, audit, and state are owned by that host.

`control-plane` serves the browser application and `/api/agents/...` gateway routes. It owns the static agent registry, availability observations, gateway terminal tickets, and gateway audit database. It does not manage services or terminals on the control-plane host unless that host is also installed separately as an agent.

`combined` is not supported in this feature. A config value of `combined` is invalid.

## Development wrapper

Use the repository wrapper for local mode-specific startup:

```bash
./start.sh agent
./start.sh control-plane
./start.sh frontend
```

`./start.sh agent` creates `/tmp/ic-env-guard-dev/agent.yaml` and runs an agent-mode backend. `./start.sh control-plane` creates `/tmp/ic-env-guard-dev/control-plane.yaml` and configures a loopback development target named `local-agent` on `IC_ENV_GUARD_AGENT_PORT`.

The control-plane development config opts into loopback HTTP with `development.allow_insecure_http: true`. Do not use that setting for non-loopback or production agents.

## Production configuration

Each agent runs with its own `agent` mode configuration and durable `state_database`. The control plane runs with `mode: control-plane`, a gateway audit database, and one configured entry per target agent:

```yaml
mode: control-plane

server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false

auth:
  mode: bearer_token
  token_file: /etc/ic-env-guard/control-plane.token

control_plane:
  audit_database: /var/lib/ic-env-guard/control-plane.db
  poll_interval_seconds: 10
  status_stale_after_seconds: 30
  max_parallel_probes: 8
  max_active_terminal_proxies: 64
  max_outstanding_tickets: 128

agents:
  - id: lab-host-01
    name: Lab Host 01
    base_url: https://lab-host-01.example.com:8765
    token_file: /etc/ic-env-guard/agents/lab-host-01.token
    tls:
      verify: true
      ca_bundle: /etc/ic-env-guard/ca/lab.pem
    enabled: true
```

In `control-plane` mode, `state_database` is not resolved, created, or migrated. Gateway audit uses `control_plane.audit_database`; agent audit remains on each agent's own `state_database`.

## TLS and tokens

Use HTTPS with certificate verification for every non-loopback agent. If an internal CA signs agent certificates, set `tls.ca_bundle` to the CA bundle readable by the service user.

Use a distinct token file per agent. Token files must be regular files, readable only by the runtime user and host administrators, and must not be copied into browser-visible configuration, logs, audit records, or support tickets.

The control plane forwards only allowlisted upstream headers and generated correlation IDs. Browser `Authorization` headers and cookies are not forwarded to agents.

## Migration from single-agent operation

1. Upgrade each host agent first and keep it running in `agent` mode.
2. Confirm each agent exposes `GET /api/capabilities` and includes the capabilities needed by the UI.
3. Create one server-side token file per target agent on the control-plane host.
4. Add each agent to the control-plane `agents:` list with HTTPS, verified TLS, and a stable URL-safe ID.
5. Start the control plane and validate `/readyz`, `/api/agents`, and one read-only agent-scoped page before enabling service or terminal operations.

Agents without the capability endpoint or with an unsupported API version are treated as protocol errors and no features are enabled for them. Missing optional capabilities can degrade only the affected feature.

## Rollback

Rollback is configuration-only. Stop the control plane, run the backend in `agent` mode, and use the existing single-host frontend/API paths. Agent state and audit remain on each host agent; the gateway audit database can be retained for investigation or moved aside after backup.

Do not point a control plane at itself to simulate rollback. A control-plane process does not own local service or terminal managers.

## Outage recovery

If the control plane is unavailable, existing host agents continue running their local services, terminals, metrics endpoint, and local audit database. Browser gateway access is unavailable until the control plane restarts.

For startup failures:

```bash
systemctl status ic-env-guard
journalctl -u ic-env-guard -n 200
ic-env-guard-config validate /etc/ic-env-guard/config.yaml
```

Check token-file permissions, TLS CA paths, agent URLs, and write access to `control_plane.audit_database`. Gateway readiness may fail if durable gateway audit writes fail; fix storage before resuming privileged routing.

If one agent is unavailable, other configured agents remain usable. Inspect the agent's own service status and local logs on that host rather than restarting the control plane first.

## Monitoring and metrics

The browser UI uses authenticated JSON snapshots through:

```text
GET /api/agents/{agent_id}/monitoring/snapshot
```

Prometheus should scrape each agent's `/metrics` endpoint directly under the agent metrics allowlist. The control plane does not proxy, merge, federate, or store raw Prometheus metrics.

## Deprecated monitoring registry routes

The browser-managed `/api/monitoring/machines` mutation routes are retained for one compatibility release with deprecation headers. New frontend code must use the configured agent registry instead.

Remove those mutation routes after the compatibility window by confirming no supported frontend or operator workflow still creates browser-managed monitoring machines, updating release notes, and deleting the deprecated route handlers and tests in the same release.

# IC Design Environment Guard

IC Design Environment Guard provides browser terminals, host observations,
service status, bounded log access, Prometheus metrics, and local audit records
for Linux engineering hosts. It can run as one standalone Agent or as a Manager
controlling a Fleet of Agents.

## What It Runs

- **Standalone Agent** — one Linux host runs an Agent Public listener and Web UI.
  A browser signs in directly to that Agent.
- **Manager Fleet** — browsers sign in to one Manager. The Manager keeps the
  Agent Registry, probes each Agent independently, and proxies only the defined
  Agent API and Terminal surfaces.
- **Local Ingest** — programs on an Agent host publish latest observations and
  log-source metadata to a separate loopback listener. This listener has no
  token and must never be exposed, forwarded, or reverse proxied.

The Agent and Manager run as existing Linux users. Installation does not create
accounts or modify sudoers. A browser Terminal has exactly the authority of the
selected Agent user, including any sudo rights that user already has.

## Choose Your Path

| Goal | Start here |
| --- | --- |
| Operate one host | [Deploy a standalone Agent](docs/guides/agent-deployment.md) |
| Operate multiple hosts | [Deploy and use a Manager Fleet](docs/guides/manager-fleet.md) |
| Publish local status or log metadata | [Use Local Ingest](docs/guides/local-data-ingest.md) |
| Change or test the project | [Set up development](docs/guides/development.md) |

For a short mode comparison and prerequisites, see
[Getting Started](docs/guides/getting-started.md).

## Five-Minute Local Demo

Prerequisites are Conda environment `venv312`, Python 3.11 or newer, Node.js,
and npm. From the repository root:

```bash
./start.sh all
```

The wrapper creates owner-only development files under
`/tmp/ic-env-guard-dev`, starts both runtime modes, and then starts Vite:

| Surface | Address |
| --- | --- |
| Manager Public API and Fleet UI | `http://127.0.0.1:8765` |
| Agent Public API | `http://127.0.0.1:8766` |
| Agent Local Ingest | `http://127.0.0.1:8767` |
| Vite development UI | `http://127.0.0.1:5173` |

Each `all` run rebuilds the generated Agent and Manager databases and managed
Agent credentials. It preserves valid non-empty `agent.token` and
`control-plane.token` login-token files, so existing local browser sign-ins can
survive a restart. The wrapper registers `local-agent` through development-only,
owner-only local v2 enrollment; the same-host flow has no SSH or static Agent
configuration dependency. Remote Agent enrollment continues to use SSH.

Startup prints `Local Agent enrolled.` after the v2 Registry commit. It prints
`Local Terminal proxy ready.` only after terminal discovery, creation, resize,
a command/output sentinel through the Manager WebSocket proxy, and cleanup have
succeeded. Vite starts after that readiness check.

Open the Vite address and sign in with the token printed from:

```bash
cat /tmp/ic-env-guard-dev/control-plane.token
```

Use `Ctrl-C` to stop the demo. Run `./start.sh help` for individual modes and
environment overrides. An Agent started alone uses Public port `8765` and Local
Ingest port `8766`.

## Production Installation Summary

Choose an existing non-root Linux account whose normal shell and sudo policy
should be available through the Agent Terminal. From the repository root:

```bash
sudo packaging/install/install.sh edaops
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard@edaops.service
```

The installer creates `/etc/ic-env-guard/edaops.yaml`, owner-only state under
`/var/lib/ic-env-guard/edaops`, and enables the template unit. Review the config
before exposing Public beyond loopback.

```bash
sudo ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
systemctl status ic-env-guard@edaops.service --no-pager
journalctl -u ic-env-guard@edaops.service -n 100 --no-pager
```

See the [Agent deployment guide](docs/guides/agent-deployment.md) for the full
lifecycle and [Manager Fleet guide](docs/guides/manager-fleet.md) for Manager
installation, discovery, enrollment, credential rotation, and removal.

## Minimal Configuration

A minimal standalone Agent configuration is:

```yaml
mode: agent
server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false
ingest:
  bind: 127.0.0.1
  port: 8766
auth:
  mode: bearer_token
  token_file: /var/lib/ic-env-guard/edaops/token
state_database: /var/lib/ic-env-guard/edaops/state.db
services: []
```

A minimal loopback Manager configuration is:

```yaml
mode: control-plane
server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false
auth:
  mode: bearer_token
  token_file: /var/lib/ic-env-guard/manager/token
control_plane:
  audit_database: /var/lib/ic-env-guard/manager/control-plane.db
  credential_directory: /var/lib/ic-env-guard/manager/agent-credentials
  allowed_agent_cidrs:
    - 10.0.0.0/8
```

Agents are added to the Manager's SQLite Registry through enrollment or the Web
UI; a static `agents:` list is legacy recovery input, not the normal Fleet
workflow. Complete examples and every supported field are in the
[configuration guide](docs/guides/configuration.md) and
[configuration reference](docs/reference/configuration.md).

## Validate the Installation

Public health and readiness checks do not require a bearer token:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
```

Authenticated Agent APIs and metrics use the Public listener:

```bash
TOKEN="$(cat /var/lib/ic-env-guard/edaops/token)"
curl --fail -H "Authorization: Bearer ${TOKEN}" \
  http://127.0.0.1:8765/api/services
curl --fail http://127.0.0.1:8765/metrics
```

Then sign in through the Web UI, open the service or Fleet view, and create a
Terminal session. Detailed acceptance checks are in
[Getting Started](docs/guides/getting-started.md).

## Security Boundaries

- Keep bearer token files mode `0600`; keep Manager Agent credentials as
  plaintext owner-only files inside a `0700` directory with `0600` files.
- Keep Local Ingest on `127.0.0.1` or `::1`. It intentionally has no token.
- Require verified TLS for non-loopback Agent connections. Plain HTTP is only
  for explicit trusted-LAN profiles or loopback development.
- Discovery is limited to configured private CIDR scopes and bounded jobs.
- Terminal WebSocket tickets are short-lived and one-use. Terminal output is
  not written to SQLite or audit records.
- Existing Linux account permissions remain authoritative; this application
  does not reduce that user's shell or sudo privileges.

Read [Security](docs/guides/security.md) before enabling remote access.

## Documentation

The [documentation index](docs/README.md) links all operator guides, reference
material, and development history. Common topics include:

- [Monitoring and logs](docs/guides/monitoring-and-logs.md)
- [Backup, upgrade, and recovery](docs/guides/backup-upgrade-recovery.md)
- [API and endpoint reference](docs/reference/api-and-endpoints.md)

Historical plans and specifications are preserved under `docs/development/`
but are not current operating instructions.

## Development Checks

```bash
cd backend
conda run -n venv312 pytest -q
conda run -n venv312 ruff check .

cd ../frontend
npm test
npm run build
npm run lint
```

macOS supports local development and contract tests. systemd, packaging,
ownership, service lifecycle, and Linux PTY behavior require a Linux host or VM.

## Repository Layout

```text
backend/             FastAPI runtimes, domain modules, and pytest suites
frontend/            React, TypeScript, Vite, and Vitest UI
packaging/           systemd units and install/upgrade/uninstall scripts
docs/guides/         Current task-oriented operating and development guides
docs/reference/      Configuration and endpoint reference
docs/development/    Historical plans, specifications, and validation records
```

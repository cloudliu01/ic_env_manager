# IC Design Environment Guard

IC Design Environment Guard is a Linux host agent for secure browser terminal access, configured local service management, Prometheus-compatible metrics, and durable local audit/state records.

## Current Specifications

The single-host agent baseline is:

- [specs/001-linux-host-agent/plan.md](specs/001-linux-host-agent/plan.md)

The post-MVP multi-agent control-plane design is:

- [specs/002-multi-agent-control-plane/spec.md](specs/002-multi-agent-control-plane/spec.md)
- [specs/002-multi-agent-control-plane/architecture.md](specs/002-multi-agent-control-plane/architecture.md)
- [specs/002-multi-agent-control-plane/plan.md](specs/002-multi-agent-control-plane/plan.md)

Operational docs live in [docs/operations/](docs/operations/), including lifecycle, recovery, service configuration, Prometheus, terminal safety, control-plane operations, and validation notes.

## Repository Layout

```text
backend/                 FastAPI host-agent backend and pytest suites
frontend/                Vite + React + TypeScript browser UI
packaging/               systemd unit, installer, upgrade, uninstall, runtime notes
docs/operations/         Operator and validation documentation
specs/001-linux-host-agent/  Spec Kit plan, contracts, tasks, and quickstart
specs/002-multi-agent-control-plane/  Multi-agent spec, architecture, contracts, and plan
```

## Prerequisites

- Conda environment `venv312` for backend Python tooling.
- Python 3.11+ inside that environment.
- Node.js and npm for frontend tooling.
- A Linux systemd host or VM for full packaging/systemd validation. macOS is fine for local unit/contract/frontend development, but cannot validate the supported Linux service lifecycle.

## Quick Start Wrapper

Use [start.sh](start.sh) from the repository root for common local development workflows. The wrapper initializes the needed environment before starting the selected process.

```bash
./start.sh agent     # Agent Public 127.0.0.1:8765 + Local Ingest 127.0.0.1:8766
./start.sh control-plane  # create/validate control-plane dev config, start the gateway on 127.0.0.1:8765
./start.sh backend   # activate Conda, create/validate dev config, start FastAPI on 127.0.0.1:8765
./start.sh frontend  # install npm dependencies if needed, start Vite on 127.0.0.1:5173
./start.sh all       # Manager 8765 + Agent Public 8766 + Agent Ingest 8767 + frontend
./start.sh config    # create/validate the local agent-mode dev token and config only
./start.sh config control-plane  # create/validate the local control-plane dev token and config only
./start.sh help      # show wrapper options
```

The wrapper creates local development files by default under:

```text
/tmp/ic-env-guard-dev/token
/tmp/ic-env-guard-dev/config.yaml
/tmp/ic-env-guard-dev/agent.yaml
/tmp/ic-env-guard-dev/control-plane.yaml
/tmp/ic-env-guard-dev/agent-enrollment.sock  # ephemeral while an Agent is running
```

Generated Agent configs set the enrollment socket inside the owner-only development directory;
they do not depend on systemd creating `/run/ic-env-guard`.

Useful overrides:

```bash
CONDA_ENV_NAME=venv312 ./start.sh backend
IC_ENV_GUARD_PORT=9000 ./start.sh backend
IC_ENV_GUARD_AGENT_PORT=8766 ./start.sh control-plane
IC_ENV_GUARD_AGENT_INGEST_PORT=9001 ./start.sh agent
IC_ENV_GUARD_FRONTEND_PORT=3000 ./start.sh frontend
SKIP_INSTALL=1 ./start.sh all
```

`SKIP_INSTALL=1` skips automatic backend/frontend dependency installation checks.

Manager Fleet operations, enrollment security, and grouped backup/rollback
procedures are documented in [Manager Fleet Operations](docs/manager-fleet-operations.md),
[Manager Enrollment Security](docs/manager-enrollment-security.md), and
[Manager Backup and Rollback](docs/manager-backup-and-rollback.md).

## Backend Setup

Use the Conda environment `venv312` for project Python tooling:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate venv312
```

If your shell already initializes Conda, this is enough:

```bash
conda activate venv312
```

Install backend development dependencies from the backend project manifest:

```bash
cd backend
python -m pip install -e '.[test]'
```

## Backend Configuration

For normal local development, prefer:

```bash
./start.sh config
```

This creates and validates the default development token/config under `/tmp/ic-env-guard-dev`.

The recommended existing-user systemd configuration path is:

```text
/etc/ic-env-guard/<linux-user>.yaml
```

For local development, create a temporary config and bearer token outside the repo or under a scratch directory:

```bash
mkdir -p /tmp/ic-env-guard-dev
umask 077
python - <<'PY' > /tmp/ic-env-guard-dev/token
import secrets
print(secrets.token_urlsafe(32))
PY
chmod 0600 /tmp/ic-env-guard-dev/token
```

Example development config:

```yaml
server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false
ingest:
  bind: 127.0.0.1
  port: 8766
auth:
  mode: bearer_token
  token_file: /tmp/ic-env-guard-dev/token
state_database: /tmp/ic-env-guard-dev/state.db
metrics:
  enabled: true
  collect_interval_seconds: 10
terminal:
  idle_timeout_minutes: 60
  replay_buffer_bytes: 2097152
  exited_retention_minutes: 30
services:
  - id: demo-http
    name: Demo HTTP service
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
```

Validate a config file with the packaged CLI entrypoint:

```bash
cd backend
ic-env-guard-config validate /tmp/ic-env-guard-dev/config.yaml
```

See [docs/operations/service-config.md](docs/operations/service-config.md) for the service configuration reference.

For multi-agent deployments, run each managed host in `agent` mode and run a separate `control-plane` mode process with server-side per-agent token files. The control plane owns only the gateway registry, gateway audit database, and routed browser entry point; host services, terminals, Prometheus `/metrics`, and local audit remain on each agent. See [docs/operations/control-plane.md](docs/operations/control-plane.md) and [docs/operations/security-review.md](docs/operations/security-review.md) before exposing a control plane beyond localhost.

## Running the Backend Locally

Preferred local command:

```bash
./start.sh agent
```

`./start.sh backend` remains available as the generic backend command and uses `IC_ENV_GUARD_MODE` or the existing config path when supplied.

If you need to start it manually instead of using the wrapper, use the packaged
launcher so Agent mode starts both listeners:

```bash
cd backend
ic-env-guard --config /tmp/ic-env-guard-dev/agent.yaml
```

Useful backend endpoints:

```bash
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/readyz
curl http://127.0.0.1:8765/metrics
```

Authenticate by reading the generated token and using it as a bearer token:

```bash
TOKEN="$(cat /tmp/ic-env-guard-dev/token)"
curl -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8765/api/terminals
curl -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8765/api/services
curl -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8765/api/audit
```

## Backend Tests and Lint

Run the full backend test suite:

```bash
cd backend
conda run -n venv312 pytest -q
```

Run backend lint:

```bash
cd backend
conda run -n venv312 python -m ruff check .
```

## Frontend Setup

For normal local development, [start.sh](start.sh) runs `npm install` automatically when [frontend/node_modules/](frontend/node_modules/) is missing.

Manual install from [frontend/](frontend/):

```bash
cd frontend
npm install
```

## Running the Frontend Locally

Preferred local command:

```bash
./start.sh frontend
```

To start both backend and frontend together:

```bash
./start.sh all
```

Manual Vite command:

```bash
cd frontend
npm run dev
```

By default Vite serves the UI at:

```text
http://127.0.0.1:5173
```

The frontend API client uses same-origin relative API paths. In a same-origin deployment, the backend serves the API and UI from the same host. During local Vite development, [frontend/vite.config.ts](frontend/vite.config.ts) proxies `/api` and `/ws` to the backend on `127.0.0.1:8765` by default. If you override `IC_ENV_GUARD_HOST` or `IC_ENV_GUARD_PORT`, start the frontend through [start.sh](start.sh) so the same values are passed to Vite.

## Frontend Tests, Build, and Lint

```bash
cd frontend
npm test -- --run
npm run build
npm run lint
```

The production frontend build is written to:

```text
frontend/dist/
```

## Packaging and systemd Operations

Packaging artifacts are under [packaging/](packaging/):

- [packaging/systemd/ic-env-guard@.service](packaging/systemd/ic-env-guard@.service) — recommended existing-user template unit
- [packaging/systemd/ic-env-guard.service](packaging/systemd/ic-env-guard.service) — deprecated compatibility unit with an explicit non-root user
- [packaging/install/install.sh](packaging/install/install.sh) — install directories, token, config, and unit
- [packaging/install/upgrade.sh](packaging/install/upgrade.sh) — preserve config, token, and state during upgrade
- [packaging/install/uninstall.sh](packaging/install/uninstall.sh) — stop/disable service and optionally retain state
- [packaging/runtime/README.md](packaging/runtime/README.md) — controlled runtime layout notes

On a supported Linux host, install and start with:

Choose an existing non-root Linux account; the installer never creates a user.

```bash
sudo packaging/install/install.sh edaops
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard@edaops
systemctl status ic-env-guard@edaops --no-pager
journalctl -u ic-env-guard@edaops -n 100 --no-pager
```

Uninstall with:

```bash
sudo packaging/install/uninstall.sh edaops
```

See [docs/operations/lifecycle.md](docs/operations/lifecycle.md), [docs/operations/recovery.md](docs/operations/recovery.md), and [docs/operations/platform-validation.md](docs/operations/platform-validation.md) for full operator workflows and Linux validation commands.

Agent v2 listener isolation, local producer examples, selected-user PTY/sudo
authority, enrollment forced-command policy, and exact backup/rollback guidance
are documented in [docs/agent-v2-operations.md](docs/agent-v2-operations.md).

## Metrics

The backend exposes Prometheus-compatible metrics at:

```text
GET /metrics
```

Local scrapes are allowed by default. Remote metrics exposure must be explicitly allowlisted by CIDR in configuration. See [docs/operations/prometheus.md](docs/operations/prometheus.md).

## Security and Scope Boundaries

The MVP remains a local web application served by Linux host agents and an optional same-origin control plane. It does not include a desktop wrapper, custom SSH server, custom time-series database, PromQL, alerting engine, unrestricted command API, cloud control plane, Windows PTY support, or discovery-based/unrestricted multi-host orchestration.

Security review guidance is in [docs/operations/security-review.md](docs/operations/security-review.md). Terminal privacy guidance is in [docs/operations/terminal-safety.md](docs/operations/terminal-safety.md).

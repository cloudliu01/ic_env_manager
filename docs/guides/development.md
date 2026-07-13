# Development Guide

Use the repository wrapper for normal local work. It creates owner-only
development credentials and validated configurations without changing system
services.

## Backend Environment

Activate the expected Conda environment and install the backend with test
dependencies:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate venv312
cd backend
python -m pip install -e '.[test]'
```

Python 3.11 or newer is required. The documented environment name is
`venv312`, but `CONDA_ENV_NAME` can select another prepared environment.

## Frontend Environment

```bash
cd frontend
npm install
```

Vite serves the UI at `http://127.0.0.1:5173`. Its development proxy forwards
`/api`, `/healthz`, `/readyz`, `/metrics`, and `/ws` to the Public backend at
`http://127.0.0.1:8765` by default. The frontend uses same-origin relative API
paths, so run it through `start.sh` when overriding backend host or port.

## Wrapper Commands

Run these commands from the repository root:

| Command | Behavior |
| --- | --- |
| `./start.sh agent` | Generate/validate Agent config; start Public `8765` and Local Ingest `8766`. |
| `./start.sh control-plane` | Generate/validate Manager config; start Manager Public `8765`. |
| `./start.sh backend` | Start the mode selected by `IC_ENV_GUARD_MODE` or an existing config. |
| `./start.sh frontend` | Install missing npm dependencies and start Vite `5173`. |
| `./start.sh all` | Start Manager `8765`, Agent Public `8766`, Agent Ingest `8767`, then Vite. |
| `./start.sh config [agent\|control-plane]` | Create and validate config without starting a server. |
| `./start.sh help` | Print current commands and overrides. |

`Ctrl-C` stops foreground processes; `all` also cleans up the two background
backends.

## Environment Overrides

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONDA_ENV_NAME` | `venv312` | Backend Conda environment. |
| `IC_ENV_GUARD_DEV_DIR` | `/tmp/ic-env-guard-dev` | Generated config/token/state root. |
| `IC_ENV_GUARD_TOKEN_FILE` | `$IC_ENV_GUARD_DEV_DIR/token` | Token path for generic config/backend. |
| `IC_ENV_GUARD_CONFIG` | `$IC_ENV_GUARD_DEV_DIR/config.yaml` | Generic config path. |
| `IC_ENV_GUARD_AGENT_TOKEN_FILE` | `$IC_ENV_GUARD_DEV_DIR/agent.token` | Compatibility Agent token input for local Manager config. |
| `IC_ENV_GUARD_MODE` | `agent` | Mode for generic config/backend commands. |
| `IC_ENV_GUARD_HOST` | `127.0.0.1` | Public backend host. |
| `IC_ENV_GUARD_PORT` | `8765` | Public backend port. |
| `IC_ENV_GUARD_AGENT_PORT` | `8766` | Local Agent target used by `all`. |
| `IC_ENV_GUARD_AGENT_INGEST_PORT` | `8766`; `8767` with `all` | Agent Local Ingest port. |
| `IC_ENV_GUARD_FRONTEND_HOST` | `127.0.0.1` | Vite bind host. |
| `IC_ENV_GUARD_FRONTEND_PORT` | `5173` | Vite port. |
| `SKIP_INSTALL` | `0` | Set to `1` to skip dependency checks/install. |

Examples:

```bash
IC_ENV_GUARD_PORT=9000 ./start.sh agent
IC_ENV_GUARD_FRONTEND_PORT=3000 ./start.sh frontend
SKIP_INSTALL=1 ./start.sh all
```

## Generated Development Files

The wrapper creates `/tmp/ic-env-guard-dev` with mode `0700` and generated
configs/tokens with mode `0600`. Depending on the command, it uses:

```text
/tmp/ic-env-guard-dev/agent.yaml
/tmp/ic-env-guard-dev/agent.token
/tmp/ic-env-guard-dev/control-plane.yaml
/tmp/ic-env-guard-dev/control-plane.token
/tmp/ic-env-guard-dev/state.db
/tmp/ic-env-guard-dev/control-plane.db
/tmp/ic-env-guard-dev/manager-credentials/
/tmp/ic-env-guard-dev/agent-enrollment.sock
/tmp/ic-env-guard-dev/manager-enrollment.sock
```

The socket files exist only while their runtime is active. The local Manager
config may contain a static `agents:` entry solely to bootstrap the demo; the
Web-managed SQLite Registry is the production Fleet authority.

## Backend Checks

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q
conda run -n venv312 ruff check .
```

Run one file or test with its normal pytest path. Tests that bind Unix sockets
or local TCP ports must be allowed to create those local resources.

## Frontend Checks

```bash
cd frontend
npm test
npm run build
npm run lint
```

`npm run build` runs TypeScript checking before creating `frontend/dist/`.
Vitest runs in the configured jsdom environment.

## Platform Boundary

macOS is suitable for unit, contract, API, UI, and most integration work. The
supported deployment target is Linux. Validate these behaviors on a Linux host
or VM before release:

- systemd template-unit installation, reload, restart, and journal behavior;
- existing-user ownership and file modes under `/etc`, `/var/lib`, and `/run`;
- packaging install, upgrade, rollback, and uninstall;
- Linux PTY, process, peer-credential, and socket lifecycle semantics.

Current operating docs are indexed in [Documentation](../README.md).
Historical designs and implementation plans are under
[Development History](../development/README.md) and are non-normative.

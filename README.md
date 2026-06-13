# IC Design Environment Guard

IC Design Environment Guard is a Linux host agent for secure browser terminal access, configured local service management, Prometheus-compatible metrics, and durable local audit/state records.

## Current Spec Kit Plan

Read the active implementation plan before development:

- [specs/001-linux-host-agent/plan.md](specs/001-linux-host-agent/plan.md)

## Python Environment

Use the Conda environment `venv312` for project Python tooling:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate venv312
```

If the activation helper above is not needed in your shell, use:

```bash
conda activate venv312
```

Install backend development dependencies from the backend project manifest:

```bash
cd backend
python -m pip install -e '.[test]'
```

Run backend tests:

```bash
cd backend
pytest
```

## Frontend Commands

Install frontend dependencies and run checks from [frontend/](frontend/):

```bash
cd frontend
npm install
npm run build
npm test
```

## Scope Boundaries

The MVP remains a local web application served by the host agent. It does not include a desktop wrapper, custom SSH server, custom time-series database, PromQL, alerting engine, unrestricted command API, cloud control plane, Windows PTY support, or multi-host orchestration.

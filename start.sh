#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"

CONDA_ENV_NAME="${CONDA_ENV_NAME:-venv312}"
DEV_DIR="${IC_ENV_GUARD_DEV_DIR:-/tmp/ic-env-guard-dev}"
TOKEN_FILE="${IC_ENV_GUARD_TOKEN_FILE:-${DEV_DIR}/token}"
CONFIG_FILE="${IC_ENV_GUARD_CONFIG:-${DEV_DIR}/config.yaml}"
AGENT_TOKEN_FILE="${IC_ENV_GUARD_AGENT_TOKEN_FILE:-${DEV_DIR}/agent.token}"
DEV_CONFIG_MODE="${IC_ENV_GUARD_MODE:-agent}"
BACKEND_HOST="${IC_ENV_GUARD_HOST:-127.0.0.1}"
BACKEND_PORT="${IC_ENV_GUARD_PORT:-8765}"
AGENT_PORT="${IC_ENV_GUARD_AGENT_PORT:-8766}"
AGENT_INGEST_PORT="${IC_ENV_GUARD_AGENT_INGEST_PORT:-8766}"
FRONTEND_HOST="${IC_ENV_GUARD_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${IC_ENV_GUARD_FRONTEND_PORT:-5173}"

usage() {
  cat <<'EOF'
Usage: ./start.sh <agent|control-plane|backend|frontend|all|config|help> [mode]

Commands:
  agent          Start an agent-mode backend with a mode-specific dev config.
  control-plane  Start a control-plane backend with a mode-specific dev config.
  backend   Activate Conda, ensure dev token/config, install missing backend deps, start FastAPI.
  frontend  Ensure npm dependencies, start Vite dev server.
  all       Start local agent and control-plane in the background, then start frontend.
  config    Create/validate the local development config and print paths. Optional mode: agent|control-plane.
  help      Show this help.

Environment overrides:
  CONDA_ENV_NAME                 Backend Conda env name. Default: venv312
  IC_ENV_GUARD_DEV_DIR           Dev config/token directory. Default: /tmp/ic-env-guard-dev
  IC_ENV_GUARD_TOKEN_FILE        Token file path. Default: $IC_ENV_GUARD_DEV_DIR/token
  IC_ENV_GUARD_CONFIG            Config path. Default: $IC_ENV_GUARD_DEV_DIR/config.yaml
  IC_ENV_GUARD_AGENT_TOKEN_FILE  Target agent token for control-plane dev. Default: $IC_ENV_GUARD_DEV_DIR/agent.token
  IC_ENV_GUARD_MODE              Config mode for config/backend commands. Default: agent
  IC_ENV_GUARD_HOST              Backend host. Default: 127.0.0.1
  IC_ENV_GUARD_PORT              Backend port. Default: 8765
  IC_ENV_GUARD_AGENT_PORT        Loopback target agent port for control-plane dev. Default: 8766
  IC_ENV_GUARD_AGENT_INGEST_PORT Agent Local Ingest port. Default: 8766 standalone, 8767 with all
  IC_ENV_GUARD_FRONTEND_HOST     Frontend host. Default: 127.0.0.1
  IC_ENV_GUARD_FRONTEND_PORT     Frontend port. Default: 5173
  SKIP_INSTALL=1                 Skip automatic pip/npm dependency installation checks.
EOF
}

use_mode_defaults() {
  DEV_CONFIG_MODE="$1"
  if [[ -z "${IC_ENV_GUARD_CONFIG:-}" ]]; then
    CONFIG_FILE="${DEV_DIR}/${DEV_CONFIG_MODE}.yaml"
  fi
  if [[ -z "${IC_ENV_GUARD_TOKEN_FILE:-}" ]]; then
    TOKEN_FILE="${DEV_DIR}/${DEV_CONFIG_MODE}.token"
  fi
}

activate_backend_env() {
  if [[ "${CONDA_DEFAULT_ENV:-}" == "${CONDA_ENV_NAME}" ]]; then
    return
  fi

  if ! command -v conda >/dev/null 2>&1; then
    echo "conda is required to activate ${CONDA_ENV_NAME}." >&2
    exit 1
  fi

  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "${CONDA_ENV_NAME}"
}

ensure_dev_config() {
  mkdir -p "${DEV_DIR}"
  chmod 0700 "${DEV_DIR}"

  if [[ ! -f "${TOKEN_FILE}" ]]; then
    umask 077
    python - <<'PY' > "${TOKEN_FILE}"
import secrets
print(secrets.token_urlsafe(32))
PY
    chmod 0600 "${TOKEN_FILE}"
  fi

  if [[ "${DEV_CONFIG_MODE}" == "control-plane" && ! -f "${AGENT_TOKEN_FILE}" ]]; then
    umask 077
    python - <<'PY' > "${AGENT_TOKEN_FILE}"
import secrets
print(secrets.token_urlsafe(32))
PY
    chmod 0600 "${AGENT_TOKEN_FILE}"
  fi

  if [[ ! -f "${CONFIG_FILE}" ]]; then
    if [[ "${DEV_CONFIG_MODE}" == "control-plane" ]]; then
      cat > "${CONFIG_FILE}" <<YAML
mode: control-plane
server:
  bind: ${BACKEND_HOST}
  port: ${BACKEND_PORT}
  remote_bind_enabled: false
auth:
  mode: bearer_token
  token_file: ${TOKEN_FILE}
development:
  allow_insecure_http: true
control_plane:
  audit_database: ${DEV_DIR}/control-plane.db
agents:
  - id: local-agent
    name: Local development agent
    base_url: http://${BACKEND_HOST}:${AGENT_PORT}
    token_file: ${AGENT_TOKEN_FILE}
    enabled: true
YAML
    else
      cat > "${CONFIG_FILE}" <<YAML
mode: agent
server:
  bind: ${BACKEND_HOST}
  port: ${BACKEND_PORT}
  remote_bind_enabled: false
auth:
  mode: bearer_token
  token_file: ${TOKEN_FILE}
metrics:
  enabled: true
  collect_interval_seconds: 10
state_database: ${DEV_DIR}/state.db
ingest:
  bind: 127.0.0.1
  port: ${AGENT_INGEST_PORT}
terminal:
  idle_timeout_minutes: 60
  replay_buffer_bytes: 2097152
  exited_retention_minutes: 30
services: []
YAML
    fi
    chmod 0600 "${CONFIG_FILE}"
  fi

  if [[ "${DEV_CONFIG_MODE}" == "agent" ]] && ! grep -q '^state_database:' "${CONFIG_FILE}"; then
    cat >> "${CONFIG_FILE}" <<YAML
state_database: ${DEV_DIR}/state.db
YAML
  fi

  if [[ "${DEV_CONFIG_MODE}" == "agent" ]] && ! grep -q '^ingest:' "${CONFIG_FILE}"; then
    cat >> "${CONFIG_FILE}" <<YAML
ingest:
  bind: 127.0.0.1
  port: ${AGENT_INGEST_PORT}
YAML
  fi

  python - "${CONFIG_FILE}" "${DEV_CONFIG_MODE}" "${BACKEND_HOST}" \
    "${BACKEND_PORT}" "${AGENT_INGEST_PORT}" "${AGENT_PORT}" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
mode, host = sys.argv[2:4]
public_port, ingest_port, agent_port = map(int, sys.argv[4:7])
config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
config["mode"] = mode
server = config.setdefault("server", {})
server["bind"] = host
server["port"] = public_port
if mode == "agent":
    ingest = config.setdefault("ingest", {})
    ingest["bind"] = "127.0.0.1"
    ingest["port"] = ingest_port
else:
    for agent in config.get("agents", []):
        if agent.get("id") == "local-agent":
            agent["base_url"] = f"http://{host}:{agent_port}"
path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
  chmod 0600 "${CONFIG_FILE}"

  echo "Dev mode:   ${DEV_CONFIG_MODE}"
  echo "Dev token:  ${TOKEN_FILE}"
  if [[ "${DEV_CONFIG_MODE}" == "control-plane" ]]; then
    echo "Agent token: ${AGENT_TOKEN_FILE}"
  fi
  echo "Dev config: ${CONFIG_FILE}"
  python - "${CONFIG_FILE}" <<'PY'
import sys
from pathlib import Path

import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
server = config["server"]
print(f"Public listener: {server['bind']}:{server['port']}")
if config["mode"] == "agent":
    ingest = config["ingest"]
    print(f"Ingest listener: {ingest['bind']}:{ingest['port']}")
PY
}

ensure_backend_deps() {
  if [[ "${SKIP_INSTALL:-0}" == "1" ]]; then
    return
  fi

  cd "${BACKEND_DIR}"
  if ! python - <<'PY' >/dev/null 2>&1
import fastapi
import uvicorn
import ptyprocess
import psutil
import prometheus_client
import pydantic
import sqlalchemy
import httpx
import websockets
import yaml
PY
  then
    echo "Installing backend dependencies into Conda env ${CONDA_ENV_NAME}..."
    python -m pip install -e '.[test]'
  fi
}

validate_config() {
  cd "${BACKEND_DIR}"
  python - "${CONFIG_FILE}" <<'PY'
import sys

from ic_env_guard.systemd.cli import main

raise SystemExit(main(["validate", sys.argv[1]]))
PY
}

start_backend() {
  activate_backend_env
  ensure_dev_config
  ensure_backend_deps
  validate_config

  export IC_ENV_GUARD_TOKEN_FILE="${TOKEN_FILE}"
  export IC_ENV_GUARD_CONFIG="${CONFIG_FILE}"
  export IC_ENV_GUARD_HOST="${BACKEND_HOST}"
  export IC_ENV_GUARD_PORT="${BACKEND_PORT}"

  cd "${BACKEND_DIR}"
  echo "Starting Public listener on http://${BACKEND_HOST}:${BACKEND_PORT}"
  if [[ "${DEV_CONFIG_MODE}" == "agent" ]]; then
    echo "Starting Local Ingest listener on http://127.0.0.1:${AGENT_INGEST_PORT}"
  fi
  python - <<'PY'
import os
from pathlib import Path

from ic_env_guard.systemd.cli import runtime_main

raise SystemExit(runtime_main(["--config", str(Path(os.environ["IC_ENV_GUARD_CONFIG"]))]))
PY
}

ensure_frontend_deps() {
  if [[ "${SKIP_INSTALL:-0}" == "1" ]]; then
    return
  fi

  cd "${FRONTEND_DIR}"
  if [[ ! -d node_modules ]]; then
    echo "Installing frontend dependencies..."
    npm install
  fi
}

start_frontend() {
  ensure_frontend_deps
  export IC_ENV_GUARD_HOST="${BACKEND_HOST}"
  export IC_ENV_GUARD_PORT="${BACKEND_PORT}"

  cd "${FRONTEND_DIR}"
  echo "Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT}"
  npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
}

wait_for_backend() {
  echo "Waiting for backend readiness..."
  for _ in $(seq 1 60); do
    if python - <<PY >/dev/null 2>&1
from urllib.request import urlopen
urlopen("http://${BACKEND_HOST}:${BACKEND_PORT}/healthz", timeout=1).read()
PY
    then
      return
    fi
    sleep 0.5
  done
  echo "Backend did not become ready at http://${BACKEND_HOST}:${BACKEND_PORT}/healthz" >&2
  return 1
}

start_all() {
  local control_plane_port="${BACKEND_PORT}"
  local agent_pid=""
  local control_plane_pid=""

  activate_backend_env

  cleanup() {
    if [[ -n "${agent_pid}" ]]; then
      kill "${agent_pid}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${control_plane_pid}" ]]; then
      kill "${control_plane_pid}" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup EXIT INT TERM

  use_mode_defaults agent
  BACKEND_PORT="${AGENT_PORT}"
  if [[ -z "${IC_ENV_GUARD_AGENT_INGEST_PORT:-}" ]]; then
    AGENT_INGEST_PORT=8767
  fi
  start_backend &
  agent_pid=$!
  wait_for_backend

  use_mode_defaults control-plane
  BACKEND_PORT="${control_plane_port}"
  start_backend &
  control_plane_pid=$!
  wait_for_backend

  start_frontend
}

command="${1:-help}"
case "${command}" in
  agent)
    use_mode_defaults agent
    start_backend
    ;;
  control-plane)
    use_mode_defaults control-plane
    start_backend
    ;;
  backend)
    start_backend
    ;;
  frontend)
    start_frontend
    ;;
  all)
    start_all
    ;;
  config)
    if [[ "${2:-}" == "agent" || "${2:-}" == "control-plane" ]]; then
      use_mode_defaults "$2"
    fi
    activate_backend_env
    ensure_dev_config
    ensure_backend_deps
    validate_config
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${command}" >&2
    usage >&2
    exit 2
    ;;
esac

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"

CONDA_ENV_NAME="${CONDA_ENV_NAME:-venv312}"
DEV_DIR="${IC_ENV_GUARD_DEV_DIR:-/tmp/ic-env-guard-dev}"
TOKEN_FILE="${IC_ENV_GUARD_TOKEN_FILE:-${DEV_DIR}/token}"
CONFIG_FILE="${IC_ENV_GUARD_CONFIG:-${DEV_DIR}/config.yaml}"
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

use_generated_mode_defaults() {
  DEV_CONFIG_MODE="$1"
  CONFIG_FILE="${DEV_DIR}/${DEV_CONFIG_MODE}.yaml"
  TOKEN_FILE="${DEV_DIR}/${DEV_CONFIG_MODE}.token"
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

ensure_dev_token() {
  local token_path="$1"
  local temporary_path

  if [[ -f "${token_path}" ]] && grep -q '[^[:space:]]' "${token_path}"; then
    return
  fi

  umask 077
  temporary_path="$(mktemp "${token_path}.tmp.XXXXXX")"
  if ! python - <<'PY' > "${temporary_path}"
import secrets
print(secrets.token_urlsafe(32))
PY
  then
    rm -f "${temporary_path}"
    return 1
  fi
  if ! chmod 0600 "${temporary_path}"; then
    rm -f "${temporary_path}"
    return 1
  fi
  if ! mv -f "${temporary_path}" "${token_path}"; then
    rm -f "${temporary_path}"
    return 1
  fi
}

ensure_dev_config() {
  mkdir -p "${DEV_DIR}"
  chmod 0700 "${DEV_DIR}"

  ensure_dev_token "${TOKEN_FILE}"

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
  local_agent_bootstrap: true
control_plane:
  audit_database: ${DEV_DIR}/control-plane.db
  credential_directory: ${DEV_DIR}/manager-credentials
  allowed_agent_cidrs:
    - 127.0.0.0/8
  transport_profiles:
    - id: local-loopback-http
      type: trusted_lan_http
      allowed_cidrs:
        - 127.0.0.0/8
  discovery:
    scopes: []
enrollment:
  manager_socket_path: ${DEV_DIR}/manager-enrollment.sock
  manager_socket_mode: "0600"
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
    "${BACKEND_PORT}" "${AGENT_INGEST_PORT}" "${AGENT_PORT}" "${DEV_DIR}" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
mode, host = sys.argv[2:4]
public_port, ingest_port, agent_port = map(int, sys.argv[4:7])
dev_dir = Path(sys.argv[7])
config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
config["mode"] = mode
server = config.setdefault("server", {})
server["bind"] = host
server["port"] = public_port
if mode == "agent":
    ingest = config.setdefault("ingest", {})
    ingest["bind"] = "127.0.0.1"
    ingest["port"] = ingest_port
    enrollment = config.setdefault("enrollment", {})
    enrollment["socket_path"] = str(dev_dir / "agent-enrollment.sock")
    enrollment.setdefault("socket_mode", "0600")
else:
    for agent in config.get("agents", []):
        if agent.get("id") == "local-agent":
            agent["base_url"] = f"http://{host}:{agent_port}"
path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
  chmod 0600 "${CONFIG_FILE}"

  echo "Dev mode:   ${DEV_CONFIG_MODE}"
  echo "Dev token:  ${TOKEN_FILE}"
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
  exec python - "${CONFIG_FILE}" <<'PY'
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

wait_for_socket() {
  local socket_path="$1"
  echo "Waiting for enrollment socket ${socket_path}..."
  for _ in $(seq 1 60); do
    if [[ -S "${socket_path}" ]]; then
      return
    fi
    sleep 0.5
  done
  echo "Enrollment socket did not become ready: ${socket_path}" >&2
  return 1
}

prepare_dev_dir_for_reset() {
  DEV_DIR="$(python - "${DEV_DIR}" <<'PY'
import os
import stat
import sys
from pathlib import Path

requested = Path(sys.argv[1]).expanduser()
resolved = requested.resolve()
home = Path.home().resolve()
if not resolved.is_absolute() or resolved == Path("/") or resolved == home:
    raise SystemExit("unsafe development directory")
resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
metadata = resolved.stat()
if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("development directory must be owner-only")
print(resolved)
PY
)"
}

recorded_process_matches() {
  local pid="$1"
  local expected_config="$2"
  python - "${pid}" "$(id -u)" "${expected_config}" <<'PY'
import shlex
import subprocess
import sys

pid, expected_uid, expected_config = sys.argv[1:]
result = subprocess.run(
    ["ps", "-o", "uid=", "-o", "command=", "-p", pid],
    capture_output=True,
    text=True,
)
if result.returncode != 0 or not result.stdout.strip():
    raise SystemExit(2)
uid, command = result.stdout.strip().split(maxsplit=1)
if uid != expected_uid:
    raise SystemExit(1)
try:
    arguments = shlex.split(command)
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if expected_config in arguments else 1)
PY
}

wait_for_process_exit() {
  local pid="$1"
  local process_state
  for _ in $(seq 1 50); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      return
    fi
    process_state="$(ps -o stat= -p "${pid}" 2>/dev/null || true)"
    if [[ "${process_state}" == Z* ]]; then
      return
    fi
    sleep 0.1
  done
  return 1
}

stop_recorded_process() {
  local pid_file="$1"
  local expected_config="$2"
  local pid
  local identity_status

  if [[ ! -e "${pid_file}" ]]; then
    return
  fi
  pid="$(cat "${pid_file}")"
  if [[ ! "${pid}" =~ ^[1-9][0-9]*$ ]]; then
    echo "development process identity mismatch" >&2
    return 1
  fi
  if recorded_process_matches "${pid}" "${expected_config}"; then
    kill -TERM "${pid}"
    if ! wait_for_process_exit "${pid}"; then
      kill -KILL "${pid}" >/dev/null 2>&1 || true
      wait_for_process_exit "${pid}" || true
    fi
    rm -f "${pid_file}"
    return
  else
    identity_status=$?
  fi
  if [[ "${identity_status}" == "2" ]] && ! kill -0 "${pid}" >/dev/null 2>&1; then
    rm -f "${pid_file}"
    return
  fi
  echo "development process identity mismatch" >&2
  return 1
}

reset_generated_state() {
  stop_recorded_process "${DEV_DIR}/agent.pid" "${DEV_DIR}/agent.yaml"
  stop_recorded_process "${DEV_DIR}/control-plane.pid" "${DEV_DIR}/control-plane.yaml"
  rm -f \
    "${DEV_DIR}/state.db" \
    "${DEV_DIR}/state.db-wal" \
    "${DEV_DIR}/state.db-shm" \
    "${DEV_DIR}/state.db-journal" \
    "${DEV_DIR}/control-plane.db" \
    "${DEV_DIR}/control-plane.db-wal" \
    "${DEV_DIR}/control-plane.db-shm" \
    "${DEV_DIR}/control-plane.db-journal" \
    "${DEV_DIR}/agent-enrollment.sock" \
    "${DEV_DIR}/manager-enrollment.sock" \
    "${DEV_DIR}/agent.pid" \
    "${DEV_DIR}/control-plane.pid" \
    "${DEV_DIR}/agent.yaml" \
    "${DEV_DIR}/control-plane.yaml"
  rm -rf "${DEV_DIR}/manager-credentials"
}

write_pid_file() {
  local pid_file="$1"
  local pid="$2"
  umask 077
  printf '%s\n' "${pid}" > "${pid_file}"
  chmod 0600 "${pid_file}"
}

remove_owned_pid_file() {
  local pid_file="$1"
  local pid="$2"
  if [[ -f "${pid_file}" ]] && [[ "$(cat "${pid_file}")" == "${pid}" ]]; then
    rm -f "${pid_file}"
  fi
}

terminate_child() {
  local pid="$1"
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    wait "${pid}" >/dev/null 2>&1 || true
    return
  fi
  kill -TERM "${pid}" >/dev/null 2>&1 || true
  if ! wait_for_process_exit "${pid}"; then
    kill -KILL "${pid}" >/dev/null 2>&1 || true
    wait_for_process_exit "${pid}" || true
  fi
  wait "${pid}" >/dev/null 2>&1 || true
}

bootstrap_local_agent() {
  (
  cd "${BACKEND_DIR}"
  python - "${DEV_DIR}" "${BACKEND_HOST}" "${AGENT_PORT}" <<'PY'
import sys
from pathlib import Path
from ic_env_guard.systemd.cli import ctl_main

dev_dir = Path(sys.argv[1])
raise SystemExit(ctl_main([
    "agent", "bootstrap-local",
    "--manager-socket", str(dev_dir / "manager-enrollment.sock"),
    "--agent-socket", str(dev_dir / "agent-enrollment.sock"),
    "--base-url", f"http://{sys.argv[2]}:{sys.argv[3]}",
    "--transport-profile", "local-loopback-http",
    "--agent-id", "local-agent",
    "--display-name", "Local development agent",
]))
PY
  )
}

start_all() {
  local control_plane_port="${BACKEND_PORT}"
  local agent_pid=""
  local control_plane_pid=""
  local cleaned_up=0

  activate_backend_env
  prepare_dev_dir_for_reset
  reset_generated_state

  cleanup() {
    local status=$?
    if [[ "${cleaned_up}" == "1" ]]; then
      return "${status}"
    fi
    cleaned_up=1
    trap - EXIT INT TERM
    if [[ -n "${agent_pid}" ]]; then
      terminate_child "${agent_pid}"
      remove_owned_pid_file "${DEV_DIR}/agent.pid" "${agent_pid}"
    fi
    if [[ -n "${control_plane_pid}" ]]; then
      terminate_child "${control_plane_pid}"
      remove_owned_pid_file "${DEV_DIR}/control-plane.pid" "${control_plane_pid}"
    fi
    rm -f "${DEV_DIR}/agent-enrollment.sock" "${DEV_DIR}/manager-enrollment.sock"
    return "${status}"
  }
  trap cleanup EXIT INT TERM

  use_generated_mode_defaults agent
  BACKEND_PORT="${AGENT_PORT}"
  if [[ -z "${IC_ENV_GUARD_AGENT_INGEST_PORT:-}" ]]; then
    AGENT_INGEST_PORT=8767
  fi
  start_backend &
  agent_pid=$!
  write_pid_file "${DEV_DIR}/agent.pid" "${agent_pid}"
  wait_for_backend
  wait_for_socket "${DEV_DIR}/agent-enrollment.sock"

  use_generated_mode_defaults control-plane
  BACKEND_PORT="${control_plane_port}"
  start_backend &
  control_plane_pid=$!
  write_pid_file "${DEV_DIR}/control-plane.pid" "${control_plane_pid}"
  wait_for_backend
  wait_for_socket "${DEV_DIR}/manager-enrollment.sock"
  bootstrap_local_agent

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

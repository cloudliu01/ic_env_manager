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
  exited_retention_minutes: 0
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

start_tracked_frontend() {
  export IC_ENV_GUARD_HOST="${BACKEND_HOST}"
  export IC_ENV_GUARD_PORT="${BACKEND_PORT}"

  echo "Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT}"
  exec python - \
    "${DEV_DIR}/frontend-runtime" \
    "${FRONTEND_DIR}" \
    "${FRONTEND_HOST}" \
    "${FRONTEND_PORT}" \
    "${SKIP_INSTALL:-0}" <<'PY'
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

_, frontend_dir, host, port, skip_install = sys.argv[1:]
requested_signal = 0
phase_timeout = 1.0
poll_interval = 0.05


def handle_signal(signal_number, _frame):
    global requested_signal
    requested_signal = signal_number


for signal_number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(signal_number, handle_signal)
os.setsid()
if requested_signal:
    raise SystemExit(128 + requested_signal)


def group_members():
    try:
        probe = subprocess.Popen(
            ["ps", "-ww", "-axo", "pid=,pgid=,stat="],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None
    try:
        stdout, _ = probe.communicate(timeout=phase_timeout)
    except subprocess.TimeoutExpired:
        try:
            probe.kill()
            probe.communicate(timeout=phase_timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None
    if probe.returncode != 0:
        return None
    members = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            return None
        try:
            pid, pgid = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        state = parts[2]
        if (
            pgid == os.getpgrp()
            and pid not in {os.getpid(), probe.pid}
            and not state.startswith("Z")
        ):
            members.append(pid)
    return members


def cleanup_residual_group():
    members = group_members()
    if members == []:
        return True
    previous_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        os.killpg(os.getpgrp(), signal.SIGTERM)
    except OSError:
        signal.signal(signal.SIGTERM, previous_term)
        return False
    deadline = time.monotonic() + phase_timeout
    while time.monotonic() < deadline:
        members = group_members()
        if members == []:
            signal.signal(signal.SIGTERM, previous_term)
            return True
        time.sleep(poll_interval)
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except OSError:
        signal.signal(signal.SIGTERM, previous_term)
        return False
    return False


def run_npm(arguments):
    if requested_signal:
        return 128 + requested_signal, True
    process = subprocess.Popen(
        ["npm", *arguments],
        cwd=frontend_dir,
    )
    returncode = process.wait()
    cleaned = cleanup_residual_group()
    return returncode, cleaned


if skip_install != "1" and not (Path(frontend_dir) / "node_modules").is_dir():
    print("Installing frontend dependencies...", flush=True)
    install_returncode, install_cleaned = run_npm(["install"])
    if install_returncode != 0 or not install_cleaned or requested_signal:
        raise SystemExit(
            128 + requested_signal
            if requested_signal
            else install_returncode if install_returncode != 0 else 125
        )

if requested_signal:
    raise SystemExit(128 + requested_signal)
returncode, cleaned = run_npm(
    ["run", "dev", "--", "--host", host, "--port", port]
)
raise SystemExit(
    128 + requested_signal
    if requested_signal
    else returncode if cleaned else 125
)
PY
}

wait_for_backend() {
  local expected_pid="$1"
  local process_status
  echo "Waiting for backend readiness..."
  for _ in $(seq 1 60); do
    if process_exists "${expected_pid}"; then
      :
    else
      process_status=$?
      if [[ "${process_status}" == "1" ]]; then
        echo "backend child exited before readiness" >&2
      else
        echo "backend child state is unknown" >&2
      fi
      return 1
    fi
    if python - <<PY >/dev/null 2>&1
from urllib.request import urlopen
urlopen("http://${BACKEND_HOST}:${BACKEND_PORT}/healthz", timeout=1).read()
PY
    then
      if ! process_exists "${expected_pid}"; then
        echo "backend child exited before readiness" >&2
        return 1
      fi
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
import pwd
import stat
import sys
import unicodedata
from pathlib import Path

requested = Path(sys.argv[1]).expanduser()
resolved = requested.resolve()
passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
environment_home = Path(os.environ["HOME"]).resolve() if os.environ.get("HOME") else None
unsupported_path = any(
    character.isspace() or unicodedata.category(character) == "Cc"
    for character in str(resolved)
)
if unsupported_path:
    raise SystemExit("development directory path is unsupported")
if (
    not resolved.is_absolute()
    or resolved == Path("/")
    or resolved == passwd_home
    or resolved == environment_home
):
    raise SystemExit("unsafe development directory")
resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
metadata = resolved.stat()
if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("development directory must be owner-only")
print(resolved)
PY
)"
}

lifecycle_lock_keeper() {
  exec python - \
    "${DEV_DIR}/.start-all.lock" \
    "$(id -u)" \
    "${lifecycle_lock_acquire_timeout:-121}" \
    3<"${lifecycle_lock_io_dir}/control" \
    4>"${lifecycle_lock_io_dir}/status" <<'PY'
import fcntl
import os
import signal
import stat
import sys
import time

def fail(message):
    os.write(4, b"error\n")
    raise SystemExit(message)


path, expected_uid, outer_timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
created = False
try:
    descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    created = True
except FileExistsError:
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail("development lifecycle lock is unsafe")
except OSError:
    fail("development lifecycle lock is unsafe")

try:
    if created:
        os.fchmod(descriptor, 0o600)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        fail("development lifecycle lock is unsafe")

    deadline = time.monotonic() + max(0.0, outer_timeout - 0.1)
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fail("development lifecycle lock timed out")
            time.sleep(0.05)

    try:
        current = os.lstat(path)
    except OSError:
        fail("development lifecycle lock is unsafe")
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid != expected_uid
        or stat.S_IMODE(current.st_mode) != 0o600
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        fail("development lifecycle lock is unsafe")

    for signal_number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signal_number, signal.SIG_IGN)
    os.write(4, b"locked\n")
    while os.read(3, 1):
        pass
    os.write(4, b"released\n")
finally:
    os.close(descriptor)
PY
}

close_lifecycle_lock_io() {
  if [[ "${lifecycle_lock_fds_open}" == "1" ]]; then
    exec 8>&-
    exec 9>&-
    lifecycle_lock_fds_open=0
  fi
  if [[ -n "${lifecycle_lock_io_dir}" ]]; then
    rm -f \
      "${lifecycle_lock_io_dir}/control" \
      "${lifecycle_lock_io_dir}/status"
    rmdir "${lifecycle_lock_io_dir}" 2>/dev/null || true
    lifecycle_lock_io_dir=""
  fi
}

wait_for_lifecycle_lock_keeper_exit() {
  local pid="$1"
  local process_state
  for _ in $(seq 1 "${lifecycle_lock_cleanup_attempts:-20}"); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      return
    fi
    process_state="$(ps -ww -o stat= -p "${pid}" 2>/dev/null || true)"
    if [[ "${process_state}" == Z* ]]; then
      return
    fi
    sleep "${lifecycle_lock_cleanup_interval:-0.05}"
  done
  return 1
}

cleanup_lifecycle_lock_keeper() {
  local cleanup_status=0
  local pid="${lifecycle_lock_pid}"

  close_lifecycle_lock_io
  if [[ -n "${pid}" ]]; then
    if ! wait_for_lifecycle_lock_keeper_exit "${pid}"; then
      kill -TERM "${pid}" >/dev/null 2>&1 || true
      if ! wait_for_lifecycle_lock_keeper_exit "${pid}"; then
        kill -KILL "${pid}" >/dev/null 2>&1 || true
        if ! wait_for_lifecycle_lock_keeper_exit "${pid}"; then
          cleanup_status=1
        fi
      fi
    fi
    if [[ "${cleanup_status}" == "0" ]]; then
      wait "${pid}" >/dev/null 2>&1 || true
    fi
  fi
  lifecycle_lock_pid=""
  lifecycle_lock_held=0
  lifecycle_lock_fds_open=0
  lifecycle_lock_io_dir=""
  return "${cleanup_status}"
}

acquire_lifecycle_lock() {
  local ready
  if [[ "${lifecycle_lock_held}" == "1" ]]; then
    return
  fi

  if ! lifecycle_lock_io_dir="$(
    mktemp -d "${DEV_DIR}/.start-all-lock.XXXXXX"
  )"; then
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  if ! chmod 0700 "${lifecycle_lock_io_dir}" \
    || ! mkfifo \
      "${lifecycle_lock_io_dir}/control" \
      "${lifecycle_lock_io_dir}/status" \
    || ! chmod 0600 \
      "${lifecycle_lock_io_dir}/control" \
      "${lifecycle_lock_io_dir}/status"; then
    cleanup_lifecycle_lock_keeper || true
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  if ! exec 8<>"${lifecycle_lock_io_dir}/control"; then
    cleanup_lifecycle_lock_keeper || true
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  lifecycle_lock_fds_open=1
  if ! exec 9<>"${lifecycle_lock_io_dir}/status"; then
    cleanup_lifecycle_lock_keeper || true
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  lifecycle_lock_keeper 8>&- 9>&- &
  lifecycle_lock_pid="$!"
  if ! IFS= read -r -t "${lifecycle_lock_acquire_timeout:-121}" ready <&9; then
    cleanup_lifecycle_lock_keeper || true
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  if [[ "${ready}" != "locked" ]]; then
    cleanup_lifecycle_lock_keeper || true
    echo "could not acquire development lifecycle lock" >&2
    return 1
  fi
  lifecycle_lock_held=1
}

release_lifecycle_lock() {
  local released
  local release_status=0
  if [[ "${lifecycle_lock_held}" != "1" ]]; then
    return
  fi
  exec 8>&-
  if ! IFS= read -r -t "${lifecycle_lock_release_timeout:-2}" released <&9 \
    || [[ "${released}" != "released" ]]; then
    echo "could not release development lifecycle lock" >&2
    release_status=1
  fi
  if ! cleanup_lifecycle_lock_keeper; then
    release_status=1
  fi
  return "${release_status}"
}

recorded_process_matches() {
  local pid="$1"
  local expected_config="$2"
  python - "${pid}" "$(id -u)" "${expected_config}" <<'PY'
import hashlib
import subprocess
import sys

pid, expected_uid, expected_config = sys.argv[1:]
result = subprocess.run(
    ["ps", "-ww", "-o", "uid=", "-o", "lstart=", "-o", "stat=", "-o", "command=", "-p", pid],
    capture_output=True,
    text=True,
)
if result.returncode != 0 or not result.stdout.strip():
    raise SystemExit(2)
parts = result.stdout.strip().split(maxsplit=7)
if len(parts) != 8:
    raise SystemExit(4)
uid, *start_fields, state, command = parts
if state.startswith("Z"):
    raise SystemExit(3)
if uid != expected_uid or expected_config not in command.split():
    raise SystemExit(1)
identity = "\0".join((uid, " ".join(start_fields), command))
print(hashlib.sha256(identity.encode("utf-8")).hexdigest())
PY
}

recorded_process_identity_with_retry() {
  local pid="$1"
  local expected_config="$2"
  local current_identity
  local identity_status
  for _ in $(seq 1 10); do
    if current_identity="$(recorded_process_matches "${pid}" "${expected_config}")"; then
      printf '%s\n' "${current_identity}"
      return
    else
      identity_status=$?
    fi
    if [[ "${identity_status}" != "2" ]]; then
      return "${identity_status}"
    fi
    if process_exists "${pid}"; then
      sleep 0.05
      continue
    fi
    return 2
  done
  return 2
}

frontend_process_matches() {
  local pid="$1"
  python - "${pid}" "$(id -u)" "${DEV_DIR}/frontend-runtime" <<'PY'
import hashlib
import subprocess
import sys

pid, expected_uid, marker = sys.argv[1:]
result = subprocess.run(
    [
        "ps", "-ww", "-o", "uid=", "-o", "lstart=", "-o", "stat=",
        "-o", "pgid=", "-o", "command=", "-p", pid,
    ],
    capture_output=True,
    text=True,
)
if result.returncode != 0 or not result.stdout.strip():
    raise SystemExit(2)
parts = result.stdout.strip().split(maxsplit=8)
if len(parts) != 9:
    raise SystemExit(1)
uid, *start_fields, state, pgid, command = parts
if state.startswith("Z"):
    raise SystemExit(3)
if uid != expected_uid or pgid != pid or marker not in command.split():
    raise SystemExit(1)
identity = "\0".join((uid, " ".join(start_fields), pgid, command))
print(hashlib.sha256(identity.encode("utf-8")).hexdigest())
PY
}

wait_for_frontend_identity() {
  local pid="$1"
  local identity_status
  for _ in $(seq 1 100); do
    if frontend_process_matches "${pid}"; then
      return
    else
      identity_status=$?
    fi
    if [[ "${identity_status}" == "2" || "${identity_status}" == "3" ]]; then
      return 1
    fi
    sleep 0.02
  done
  return 1
}

write_frontend_metadata() {
  local pid="$1"
  local identity="$2"
  local temporary_path
  umask 077
  temporary_path="$(mktemp "${DEV_DIR}/frontend.pid.tmp.XXXXXX")"
  if ! printf '%s %s\n' "${pid}" "${identity}" > "${temporary_path}" \
    || ! chmod 0600 "${temporary_path}" \
    || ! mv -f "${temporary_path}" "${DEV_DIR}/frontend.pid"; then
    rm -f "${temporary_path}"
    return 1
  fi
}

read_frontend_metadata() {
  python - "${DEV_DIR}/frontend.pid" "$(id -u)" <<'PY'
import os
import re
import stat
import sys

path, expected_uid = sys.argv[1], int(sys.argv[2])
try:
    metadata = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(2)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != expected_uid
    or stat.S_IMODE(metadata.st_mode) & 0o077
    or not 67 <= metadata.st_size <= 96
):
    raise SystemExit(1)
flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
descriptor = None
try:
    descriptor = os.open(path, flags)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != expected_uid
        or stat.S_IMODE(opened.st_mode) & 0o077
        or not 67 <= opened.st_size <= 96
        or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise SystemExit(1)
    payload = os.read(descriptor, 97)
except OSError:
    raise SystemExit(1)
finally:
    if descriptor is not None:
        os.close(descriptor)
try:
    value = payload.decode("ascii")
except UnicodeDecodeError:
    raise SystemExit(1)
match = re.fullmatch(r"([1-9][0-9]*) ([0-9a-f]{64})\n?", value)
if match is None:
    raise SystemExit(1)
print(*match.groups())
PY
}

remove_owned_frontend_metadata() {
  local expected="$1"
  python - "${DEV_DIR}/frontend.pid" "${expected}" <<'PY'
import os
import stat
import sys

path, expected = sys.argv[1:]
try:
    metadata = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(0)
if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit(0)
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(0)
try:
    opened = os.fstat(descriptor)
    payload = os.read(descriptor, 97)
finally:
    os.close(descriptor)
if (
    (opened.st_dev, opened.st_ino) == (metadata.st_dev, metadata.st_ino)
    and payload.decode("ascii", errors="ignore").rstrip("\n") == expected
):
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(0)
    if (current.st_dev, current.st_ino) == (metadata.st_dev, metadata.st_ino):
        os.unlink(path)
PY
}

process_exists() {
  local pid="$1"
  python - "${pid}" <<'PY'
import errno
import os
import sys

pid = int(sys.argv[1])
try:
    os.kill(pid, 0)
except OSError as error:
    if error.errno == errno.ESRCH:
        raise SystemExit(1)
    if error.errno == errno.EPERM:
        raise SystemExit(0)
    raise SystemExit(2)
PY
}

wait_for_recorded_process_exit() {
  local pid="$1"
  local expected_config="$2"
  local expected_identity="$3"
  local current_identity
  local identity_status
  local process_status
  for _ in $(seq 1 50); do
    if current_identity="$(recorded_process_matches "${pid}" "${expected_config}")"; then
      if [[ "${current_identity}" != "${expected_identity}" ]]; then
        return 2
      fi
    else
      identity_status=$?
      if [[ "${identity_status}" == "1" ]]; then
        return 2
      fi
      if [[ "${identity_status}" == "3" ]]; then
        return
      fi
      if [[ "${identity_status}" == "2" ]]; then
        if process_exists "${pid}"; then
          sleep 0.1
          continue
        else
          process_status=$?
        fi
        if [[ "${process_status}" == "1" ]]; then
          return
        fi
      fi
      return 3
    fi
    sleep 0.1
  done
  return 1
}

wait_for_process_exit() {
  local pid="$1"
  local process_state
  local process_status
  for _ in $(seq 1 50); do
    if process_exists "${pid}"; then
      :
    else
      process_status=$?
      if [[ "${process_status}" == "1" ]]; then
        return
      fi
      return 1
    fi
    process_state="$(ps -ww -o stat= -p "${pid}" 2>/dev/null || true)"
    if [[ "${process_state}" == Z* ]]; then
      return
    fi
    sleep 0.1
  done
  return 1
}

read_pid_metadata() {
  local pid_file="$1"
  python - "${pid_file}" "$(id -u)" <<'PY'
import os
import re
import stat
import sys

path, expected_uid = sys.argv[1], int(sys.argv[2])
try:
    metadata = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(2)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != expected_uid
    or stat.S_IMODE(metadata.st_mode) & 0o077
    or not 1 <= metadata.st_size <= 32
):
    raise SystemExit(1)
flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
descriptor = None
try:
    descriptor = os.open(path, flags)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != expected_uid
        or stat.S_IMODE(opened.st_mode) & 0o077
        or not 1 <= opened.st_size <= 32
        or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise SystemExit(1)
    payload = os.read(descriptor, 33)
except OSError:
    raise SystemExit(1)
finally:
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError:
            raise SystemExit(1)
try:
    value = payload.decode("ascii")
except UnicodeDecodeError:
    raise SystemExit(1)
if not re.fullmatch(r"[1-9][0-9]*\n?", value):
    raise SystemExit(1)
print(value.rstrip("\n"))
PY
}

stop_recorded_process() {
  local pid_file="$1"
  local expected_config="$2"
  local expected_identity="${3:-}"
  local pid
  local identity_status
  local recorded_identity
  local current_identity
  local wait_status
  local process_status

  if pid="$(read_pid_metadata "${pid_file}")"; then
    :
  else
    identity_status=$?
    if [[ "${identity_status}" == "2" ]]; then
      return
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if recorded_identity="$(
    recorded_process_identity_with_retry "${pid}" "${expected_config}"
  )"; then
    if [[ -n "${expected_identity}" \
      && "${recorded_identity}" != "${expected_identity}" ]]; then
      return 2
    fi
  else
    identity_status=$?
    if [[ "${identity_status}" == "3" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
    if [[ "${identity_status}" == "2" ]]; then
      if process_exists "${pid}"; then
        :
      else
        process_status=$?
        if [[ "${process_status}" == "1" ]]; then
          remove_owned_pid_file "${pid_file}" "${pid}"
          return
        fi
      fi
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if current_identity="$(
    recorded_process_identity_with_retry "${pid}" "${expected_config}"
  )"; then
    if [[ "${current_identity}" != "${recorded_identity}" ]]; then
      echo "development process identity mismatch" >&2
      return 1
    fi
  else
    identity_status=$?
    if [[ "${identity_status}" == "3" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
    if [[ "${identity_status}" == "2" ]]; then
      if process_exists "${pid}"; then
        :
      else
        process_status=$?
        if [[ "${process_status}" == "1" ]]; then
          remove_owned_pid_file "${pid_file}" "${pid}"
          return
        fi
      fi
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if ! kill -TERM "${pid}" >/dev/null 2>&1; then
    if process_exists "${pid}"; then
      :
    else
      process_status=$?
      if [[ "${process_status}" == "1" ]]; then
        remove_owned_pid_file "${pid_file}" "${pid}"
        return
      fi
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if wait_for_recorded_process_exit \
    "${pid}" "${expected_config}" "${recorded_identity}"; then
    remove_owned_pid_file "${pid_file}" "${pid}"
    return
  else
    wait_status=$?
  fi
  if [[ "${wait_status}" == "2" ]]; then
    remove_owned_pid_file "${pid_file}" "${pid}"
    return
  fi
  if [[ "${wait_status}" == "3" ]]; then
    echo "development process identity mismatch" >&2
    return 1
  fi
  if current_identity="$(recorded_process_matches "${pid}" "${expected_config}")"; then
    if [[ "${current_identity}" != "${recorded_identity}" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
  else
    wait_status=$?
    if [[ "${wait_status}" == "1" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
    if [[ "${wait_status}" == "3" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
    if [[ "${wait_status}" == "2" ]]; then
      if process_exists "${pid}"; then
        :
      else
        process_status=$?
        if [[ "${process_status}" == "1" ]]; then
          remove_owned_pid_file "${pid_file}" "${pid}"
          return
        fi
      fi
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if ! kill -KILL "${pid}" >/dev/null 2>&1; then
    if process_exists "${pid}"; then
      echo "development process identity mismatch" >&2
      return 1
    else
      process_status=$?
    fi
    if [[ "${process_status}" == "1" ]]; then
      remove_owned_pid_file "${pid_file}" "${pid}"
      return
    fi
    echo "development process identity mismatch" >&2
    return 1
  fi
  if wait_for_recorded_process_exit \
    "${pid}" "${expected_config}" "${recorded_identity}"; then
    remove_owned_pid_file "${pid_file}" "${pid}"
    return
  else
    wait_status=$?
  fi
  if [[ "${wait_status}" == "2" ]]; then
    remove_owned_pid_file "${pid_file}" "${pid}"
    return
  elif [[ "${wait_status}" == "3" ]]; then
    echo "development process identity mismatch" >&2
  else
    echo "development process did not exit" >&2
  fi
  return 1
}

frontend_group_exists() {
  local pid="$1"
  python - "${pid}" <<'PY'
import errno
import os
import sys

try:
    os.killpg(int(sys.argv[1]), 0)
except OSError as error:
    if error.errno == errno.ESRCH:
        raise SystemExit(1)
    if error.errno == errno.EPERM:
        raise SystemExit(0)
    raise SystemExit(2)
PY
}

signal_frontend_group() {
  local pid="$1"
  local signal_name="$2"
  python - "${pid}" "${signal_name}" <<'PY'
import errno
import os
import signal
import sys

pid = int(sys.argv[1])
signal_number = getattr(signal, sys.argv[2])
try:
    os.killpg(pid, signal_number)
except OSError as error:
    if error.errno != errno.ESRCH:
        raise
PY
}

signal_owned_frontend() {
  local pid="$1"
  local signal_name="$2"
  python - "${pid}" "${signal_name}" <<'PY'
import errno
import os
import signal
import sys

pid = int(sys.argv[1])
signal_number = getattr(signal, sys.argv[2])
for target in (lambda: os.kill(pid, signal_number), lambda: os.killpg(pid, signal_number)):
    try:
        target()
    except OSError as error:
        if error.errno != errno.ESRCH:
            raise
PY
}

terminate_owned_frontend() {
  local pid="$1"
  local leader_reaped="${2:-0}"
  local group_status
  if [[ "${leader_reaped}" != "1" ]]; then
    signal_owned_frontend "${pid}" SIGTERM
    if ! wait_for_process_exit "${pid}"; then
      signal_owned_frontend "${pid}" SIGKILL
      if ! wait_for_process_exit "${pid}"; then
        echo "development frontend did not exit" >&2
        return 1
      fi
    fi
    wait "${pid}" >/dev/null 2>&1 || true
    leader_reaped=1
  fi
  if frontend_group_exists "${pid}"; then
    :
  else
    group_status=$?
    if [[ "${group_status}" == "1" ]]; then
      return
    fi
    echo "development frontend group state is unknown" >&2
    return 1
  fi
  signal_frontend_group "${pid}" SIGTERM
  if wait_for_owned_frontend_group_exit "${pid}"; then
    return
  fi
  signal_frontend_group "${pid}" SIGKILL
  if ! wait_for_owned_frontend_group_exit "${pid}"; then
    echo "development frontend did not exit" >&2
    return 1
  fi
}

wait_for_owned_frontend_group_exit() {
  local pid="$1"
  local group_status
  for _ in $(seq 1 50); do
    if frontend_group_exists "${pid}"; then
      :
    else
      group_status=$?
      if [[ "${group_status}" == "1" ]]; then
        return
      fi
      return 1
    fi
    sleep 0.1
  done
  return 1
}

wait_for_frontend_group_exit() {
  local pid="$1"
  local expected_identity="$2"
  local current_identity
  local identity_status
  local group_status
  for _ in $(seq 1 50); do
    if current_identity="$(frontend_process_matches "${pid}")"; then
      if [[ "${current_identity}" != "${expected_identity}" ]]; then
        return 2
      fi
    else
      identity_status=$?
      if [[ "${identity_status}" == "1" ]]; then
        return 2
      fi
    fi
    if frontend_group_exists "${pid}"; then
      :
    else
      group_status=$?
      if [[ "${group_status}" == "1" ]]; then
        return
      fi
      return 2
    fi
    sleep 0.1
  done
  return 1
}

terminate_frontend_group() {
  local pid="$1"
  local expected_identity="$2"
  local current_identity
  local identity_status
  local wait_status
  local group_status

  if current_identity="$(frontend_process_matches "${pid}")"; then
    if [[ "${current_identity}" != "${expected_identity}" ]]; then
      echo "development frontend identity mismatch" >&2
      return 1
    fi
  else
    identity_status=$?
    if [[ "${identity_status}" == "1" ]]; then
      echo "development frontend identity mismatch" >&2
      return 1
    fi
    if frontend_group_exists "${pid}"; then
      echo "development frontend identity mismatch" >&2
      return 1
    else
      group_status=$?
      if [[ "${group_status}" == "1" ]]; then
        return
      fi
    fi
    echo "development frontend identity mismatch" >&2
    return 1
  fi
  if current_identity="$(frontend_process_matches "${pid}")"; then
    if [[ "${current_identity}" != "${expected_identity}" ]]; then
      echo "development frontend identity mismatch" >&2
      return 1
    fi
  else
    echo "development frontend identity mismatch" >&2
    return 1
  fi
  signal_frontend_group "${pid}" SIGTERM
  if wait_for_frontend_group_exit "${pid}" "${expected_identity}"; then
    return
  else
    wait_status=$?
  fi
  if [[ "${wait_status}" == "2" ]]; then
    echo "development frontend identity mismatch" >&2
    return 1
  fi
  if current_identity="$(frontend_process_matches "${pid}")"; then
    if [[ "${current_identity}" != "${expected_identity}" ]]; then
      echo "development frontend identity mismatch" >&2
      return 1
    fi
  else
    identity_status=$?
    if [[ "${identity_status}" == "1" ]]; then
      echo "development frontend identity mismatch" >&2
      return 1
    fi
  fi
  signal_frontend_group "${pid}" SIGKILL
  if wait_for_frontend_group_exit "${pid}" "${expected_identity}"; then
    return
  else
    wait_status=$?
  fi
  if [[ "${wait_status}" == "2" ]]; then
    echo "development frontend identity mismatch" >&2
  else
    echo "development frontend did not exit" >&2
  fi
  return 1
}

stop_recorded_frontend() {
  local metadata
  local metadata_status
  local pid
  local expected_identity
  if metadata="$(read_frontend_metadata)"; then
    read -r pid expected_identity <<< "${metadata}"
  else
    metadata_status=$?
    if [[ "${metadata_status}" == "2" ]]; then
      return
    fi
    echo "development frontend identity mismatch" >&2
    return 1
  fi
  if ! terminate_frontend_group "${pid}" "${expected_identity}"; then
    return 1
  fi
  remove_owned_frontend_metadata "${pid} ${expected_identity}"
}

stop_recorded_backends() {
  if ! stop_recorded_process "${DEV_DIR}/agent.pid" "${DEV_DIR}/agent.yaml"; then
    return 1
  fi
  if ! stop_recorded_process \
    "${DEV_DIR}/control-plane.pid" "${DEV_DIR}/control-plane.yaml"; then
    return 1
  fi
}

validate_development_ports_available() {
  python - \
    "${BACKEND_HOST}" "${AGENT_PORT}" \
    "127.0.0.1" "${AGENT_INGEST_PORT}" \
    "${BACKEND_HOST}" "$1" \
    "${FRONTEND_HOST}" "${FRONTEND_PORT}" <<'PY'
import socket
import sys

reservations = []
try:
    arguments = sys.argv[1:]
    for host, port_text in zip(arguments[::2], arguments[1::2], strict=True):
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        reservation = socket.socket(family, socket.SOCK_STREAM)
        reservations.append(reservation)
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind((host, port))
        reservation.listen()
except (OSError, ValueError):
    print("development port already in use", file=sys.stderr)
    raise SystemExit(1)
finally:
    for reservation in reservations:
        reservation.close()
PY
}

reset_generated_state() {
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
    "${DEV_DIR}/frontend.pid" \
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
  python - "${pid_file}" "${pid}" <<'PY'
import os
import stat
import sys

path, expected = sys.argv[1:]
try:
    metadata = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(0)
if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit(0)
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(0)
try:
    opened = os.fstat(descriptor)
    payload = os.read(descriptor, 33)
finally:
    os.close(descriptor)
if (
    (opened.st_dev, opened.st_ino) == (metadata.st_dev, metadata.st_ino)
    and payload.decode("ascii", errors="ignore").rstrip("\n") == expected
):
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(0)
    if (current.st_dev, current.st_ino) == (metadata.st_dev, metadata.st_ino):
        os.unlink(path)
PY
}

capture_socket_identity() {
  local socket_path="$1"
  python - "${socket_path}" "$(id -u)" <<'PY'
import os
import stat
import sys

metadata = os.lstat(sys.argv[1])
if metadata.st_uid != int(sys.argv[2]) or not stat.S_ISSOCK(metadata.st_mode):
    raise SystemExit("enrollment socket identity mismatch")
print(f"{metadata.st_dev}:{metadata.st_ino}:{metadata.st_uid}")
PY
}

remove_owned_socket() {
  local socket_path="$1"
  local expected_identity="$2"
  python - "${socket_path}" "${expected_identity}" <<'PY'
import os
import stat
import sys

path, expected = sys.argv[1:]
try:
    metadata = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(0)
identity = f"{metadata.st_dev}:{metadata.st_ino}:{metadata.st_uid}"
if identity == expected and stat.S_ISSOCK(metadata.st_mode):
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(0)
    if (current.st_dev, current.st_ino, current.st_uid) == (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
    ):
        os.unlink(path)
PY
}

terminate_child() {
  local pid="$1"
  local process_status
  if process_exists "${pid}"; then
    :
  else
    process_status=$?
    if [[ "${process_status}" == "1" ]]; then
      wait "${pid}" >/dev/null 2>&1 || true
      return
    fi
    echo "development child process state is unknown" >&2
    return 1
  fi
  kill -TERM "${pid}" >/dev/null 2>&1 || true
  if wait_for_process_exit "${pid}"; then
    wait "${pid}" >/dev/null 2>&1 || true
    return
  fi
  kill -KILL "${pid}" >/dev/null 2>&1 || true
  if wait_for_process_exit "${pid}"; then
    wait "${pid}" >/dev/null 2>&1 || true
    return
  fi
  echo "development child did not exit" >&2
  return 1
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

run_terminal_readiness() {
  (
  cd "${BACKEND_DIR}"
  python -m ic_env_guard.development.readiness \
    --manager-url "http://${BACKEND_HOST}:${BACKEND_PORT}" \
    --token-file "${DEV_DIR}/control-plane.token" \
    --agent-id local-agent
  )
}

start_all() {
  local control_plane_port="${BACKEND_PORT}"
  local agent_pid=""
  local control_plane_pid=""
  local frontend_pid=""
  local frontend_identity=""
  local frontend_reaped=0
  local agent_reaped=0
  local control_plane_reaped=0
  local agent_process_identity=""
  local control_plane_process_identity=""
  local agent_socket_identity=""
  local control_plane_socket_identity=""
  local cleaned_up=0
  local lifecycle_lock_pid=""
  local lifecycle_lock_io_dir=""
  local lifecycle_lock_fds_open=0
  local lifecycle_lock_held=0
  local lifecycle_lock_acquire_timeout=121
  local lifecycle_lock_release_timeout=2
  local lifecycle_lock_cleanup_attempts=20
  local lifecycle_lock_cleanup_interval=0.05
  local lifecycle_lock_cleanup_acquire_timeout=2

  activate_backend_env
  prepare_dev_dir_for_reset

  cleanup() {
    local status="$1"
    local cleanup_status="${status}"
    local cleanup_started_with_lock="${lifecycle_lock_held}"
    local recorded_child_pid
    local child_cleanup_status
    local agent_cleanup_owned=0
    local control_plane_cleanup_owned=0
    if [[ "${cleaned_up}" == "1" ]]; then
      return "${status}"
    fi
    trap - EXIT ERR
    trap '' INT TERM
    cleaned_up=1
    if [[ -n "${frontend_pid}" && "${frontend_reaped}" != "1" ]]; then
      if terminate_owned_frontend "${frontend_pid}"; then
        frontend_reaped=1
      else
        cleanup_status=1
      fi
    fi
    if [[ "${lifecycle_lock_held}" != "1" \
      && ( -n "${lifecycle_lock_pid}" \
        || -n "${lifecycle_lock_io_dir}" \
        || "${lifecycle_lock_fds_open}" == "1" ) ]]; then
      if ! cleanup_lifecycle_lock_keeper; then
        cleanup_status=1
      fi
    fi
    local cleanup_lock_available=0
    local shared_cleanup_required=0
    if [[ -n "${frontend_identity}" \
      || -n "${agent_pid}" \
      || -n "${control_plane_pid}" \
      || -n "${agent_socket_identity}" \
      || -n "${control_plane_socket_identity}" ]]; then
      shared_cleanup_required=1
    fi
    if [[ "${lifecycle_lock_held}" == "1" ]]; then
      cleanup_lock_available=1
    elif [[ "${shared_cleanup_required}" == "1" ]]; then
      lifecycle_lock_acquire_timeout="${lifecycle_lock_cleanup_acquire_timeout}"
      if acquire_lifecycle_lock; then
        cleanup_lock_available=1
      else
        cleanup_status=1
      fi
    fi
    if [[ "${cleanup_lock_available}" == "1" ]]; then
      if [[ -n "${agent_pid}" && "${agent_reaped}" != "1" ]]; then
        if [[ "${cleanup_started_with_lock}" == "1" ]]; then
          recorded_child_pid="${agent_pid}"
        elif recorded_child_pid="$(read_pid_metadata "${DEV_DIR}/agent.pid")"; then
          :
        else
          recorded_child_pid=""
        fi
        if [[ "${recorded_child_pid}" == "${agent_pid}" ]]; then
          if [[ "${cleanup_started_with_lock}" == "1" \
            && -z "${agent_process_identity}" ]]; then
            if terminate_child "${agent_pid}"; then
              agent_reaped=1
              agent_cleanup_owned=1
            else
              cleanup_status=1
            fi
          elif stop_recorded_process \
            "${DEV_DIR}/agent.pid" \
            "${DEV_DIR}/agent.yaml" \
            "${agent_process_identity}"; then
            wait "${agent_pid}" >/dev/null 2>&1 || true
            agent_reaped=1
            agent_cleanup_owned=1
          else
            child_cleanup_status=$?
            if [[ "${child_cleanup_status}" != "2" ]]; then
              cleanup_status=1
            fi
          fi
        fi
      fi
      if [[ -n "${control_plane_pid}" \
        && "${control_plane_reaped}" != "1" ]]; then
        if [[ "${cleanup_started_with_lock}" == "1" ]]; then
          recorded_child_pid="${control_plane_pid}"
        elif recorded_child_pid="$(
          read_pid_metadata "${DEV_DIR}/control-plane.pid"
        )"; then
          :
        else
          recorded_child_pid=""
        fi
        if [[ "${recorded_child_pid}" == "${control_plane_pid}" ]]; then
          if [[ "${cleanup_started_with_lock}" == "1" \
            && -z "${control_plane_process_identity}" ]]; then
            if terminate_child "${control_plane_pid}"; then
              control_plane_reaped=1
              control_plane_cleanup_owned=1
            else
              cleanup_status=1
            fi
          elif stop_recorded_process \
            "${DEV_DIR}/control-plane.pid" \
            "${DEV_DIR}/control-plane.yaml" \
            "${control_plane_process_identity}"; then
            wait "${control_plane_pid}" >/dev/null 2>&1 || true
            control_plane_reaped=1
            control_plane_cleanup_owned=1
          else
            child_cleanup_status=$?
            if [[ "${child_cleanup_status}" != "2" ]]; then
              cleanup_status=1
            fi
          fi
        fi
      fi
      if [[ -n "${frontend_identity}" ]]; then
        remove_owned_frontend_metadata \
          "${frontend_pid} ${frontend_identity}"
      fi
      if [[ -n "${agent_pid}" && "${agent_cleanup_owned}" == "1" ]]; then
        remove_owned_pid_file "${DEV_DIR}/agent.pid" "${agent_pid}"
      fi
      if [[ -n "${control_plane_pid}" \
        && "${control_plane_cleanup_owned}" == "1" ]]; then
        remove_owned_pid_file "${DEV_DIR}/control-plane.pid" "${control_plane_pid}"
      fi
      if [[ -n "${agent_socket_identity}" ]]; then
        remove_owned_socket \
          "${DEV_DIR}/agent-enrollment.sock" "${agent_socket_identity}"
      fi
      if [[ -n "${control_plane_socket_identity}" ]]; then
        remove_owned_socket \
          "${DEV_DIR}/manager-enrollment.sock" "${control_plane_socket_identity}"
      fi
    fi
    if [[ "${lifecycle_lock_held}" == "1" ]]; then
      if ! release_lifecycle_lock; then
        cleanup_status=1
      fi
    elif [[ -n "${lifecycle_lock_pid}" \
      || -n "${lifecycle_lock_io_dir}" \
      || "${lifecycle_lock_fds_open}" == "1" ]]; then
      if ! cleanup_lifecycle_lock_keeper; then
        cleanup_status=1
      fi
    fi
    return "${cleanup_status}"
  }

  handle_exit() {
    local status=$?
    if [[ -n "${cleaned_up+x}" ]]; then
      cleanup "${status}" || true
    fi
    trap - EXIT
    exit "${status}"
  }

  handle_error() {
    local status=$?
    cleanup "${status}" || true
    exit "${status}"
  }

  handle_signal() {
    local status="$1"
    cleanup "${status}" || true
    exit "${status}"
  }

  trap handle_exit EXIT
  trap handle_error ERR
  trap 'handle_signal 130' INT
  trap 'handle_signal 143' TERM

  acquire_lifecycle_lock
  if [[ -z "${IC_ENV_GUARD_AGENT_INGEST_PORT:-}" ]]; then
    AGENT_INGEST_PORT=8767
  fi
  stop_recorded_frontend
  stop_recorded_backends
  validate_development_ports_available "${control_plane_port}"
  reset_generated_state

  use_generated_mode_defaults agent
  BACKEND_PORT="${AGENT_PORT}"
  start_backend 8>&- 9>&- &
  agent_pid=$!
  write_pid_file "${DEV_DIR}/agent.pid" "${agent_pid}"
  wait_for_backend "${agent_pid}"
  agent_process_identity="$(
    recorded_process_matches "${agent_pid}" "${DEV_DIR}/agent.yaml"
  )"
  wait_for_socket "${DEV_DIR}/agent-enrollment.sock"
  agent_socket_identity="$(capture_socket_identity "${DEV_DIR}/agent-enrollment.sock")"

  use_generated_mode_defaults control-plane
  BACKEND_PORT="${control_plane_port}"
  start_backend 8>&- 9>&- &
  control_plane_pid=$!
  write_pid_file "${DEV_DIR}/control-plane.pid" "${control_plane_pid}"
  wait_for_backend "${control_plane_pid}"
  control_plane_process_identity="$(
    recorded_process_matches \
      "${control_plane_pid}" "${DEV_DIR}/control-plane.yaml"
  )"
  wait_for_socket "${DEV_DIR}/manager-enrollment.sock"
  control_plane_socket_identity="$(
    capture_socket_identity "${DEV_DIR}/manager-enrollment.sock"
  )"
  bootstrap_local_agent

  run_terminal_readiness
  start_tracked_frontend 8>&- 9>&- &
  frontend_pid=$!
  if ! frontend_identity="$(wait_for_frontend_identity "${frontend_pid}")"; then
    echo "frontend child exited before identity capture" >&2
    cleanup 1 || true
    return 1
  fi
  write_frontend_metadata "${frontend_pid}" "${frontend_identity}"
  release_lifecycle_lock
  local frontend_wait_status=0
  wait "${frontend_pid}" || frontend_wait_status=$?
  frontend_reaped=1
  if ! terminate_owned_frontend "${frontend_pid}" 1; then
    cleanup 1 || true
    return 1
  fi
  if [[ "${frontend_wait_status}" != "0" ]]; then
    cleanup "${frontend_wait_status}" || true
    return "${frontend_wait_status}"
  fi
  cleanup 0
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

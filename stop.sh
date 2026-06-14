#!/usr/bin/env bash
set -euo pipefail

BACKEND_PORT="${IC_ENV_GUARD_PORT:-8765}"
AGENT_PORT="${IC_ENV_GUARD_AGENT_PORT:-8766}"
FRONTEND_PORT="${IC_ENV_GUARD_FRONTEND_PORT:-5173}"
EXTRA_PORTS="${IC_ENV_GUARD_STOP_PORTS:-}"

usage() {
  cat <<'EOF'
Usage: ./stop.sh

Stops local development servers by killing processes listening on dev ports.

Default ports:
  8765  backend/control-plane
  8766  local target agent
  5173  frontend Vite server

Environment overrides:
  IC_ENV_GUARD_PORT           Backend/control-plane port. Default: 8765
  IC_ENV_GUARD_AGENT_PORT     Local target agent port. Default: 8766
  IC_ENV_GUARD_FRONTEND_PORT  Frontend port. Default: 5173
  IC_ENV_GUARD_STOP_PORTS     Extra space-separated ports to stop.
EOF
}

port_pids() {
  local port="$1"
  lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
}

stop_port() {
  local port="$1"
  local pids
  pids="$(port_pids "${port}")"
  if [[ -z "${pids}" ]]; then
    echo "No process listening on port ${port}"
    return
  fi

  echo "Stopping port ${port}: ${pids//$'\n'/ }"
  kill ${pids} 2>/dev/null || true

  for _ in $(seq 1 20); do
    if [[ -z "$(port_pids "${port}")" ]]; then
      echo "Stopped port ${port}"
      return
    fi
    sleep 0.2
  done

  pids="$(port_pids "${port}")"
  if [[ -n "${pids}" ]]; then
    echo "Force stopping port ${port}: ${pids//$'\n'/ }"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

if [[ "${1:-}" == "help" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

ports=("${BACKEND_PORT}" "${AGENT_PORT}" "${FRONTEND_PORT}")
if [[ -n "${EXTRA_PORTS}" ]]; then
  for extra_port in ${EXTRA_PORTS}; do
    ports+=("${extra_port}")
  done
fi

seen=" "
for port in "${ports[@]}"; do
  if [[ ! "${port}" =~ ^[0-9]+$ ]]; then
    echo "Skipping invalid port: ${port}" >&2
    continue
  fi
  if [[ "${seen}" == *" ${port} "* ]]; then
    continue
  fi
  seen+="${port} "
  stop_port "${port}"
done

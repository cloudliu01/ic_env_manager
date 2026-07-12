#!/usr/bin/env bash
set -euo pipefail

account="${1:-}"
if [[ -z "${account}" ]]; then
  echo "usage: $0 <existing-linux-user>" >&2
  exit 2
fi
if ! id "${account}" >/dev/null 2>&1; then
  echo "existing Linux user not found: ${account}" >&2
  exit 2
fi
if [[ "$(id -u "${account}")" -eq 0 ]]; then
  echo "the Agent must not run as root; select an existing non-root user" >&2
  exit 2
fi
group="$(id -gn "${account}")"
state_dir="/var/lib/ic-env-guard/${account}"
config_file="/etc/ic-env-guard/${account}.yaml"
token_file="${state_dir}/token"

install -d -o root -g root -m 0755 /etc/ic-env-guard
install -d -o root -g root -m 0755 /var/lib/ic-env-guard
install -d -o "${account}" -g "${group}" -m 0700 "${state_dir}"
install -d -o "${account}" -g "${group}" -m 0750 "${state_dir}/runtime"
install -d -o "${account}" -g "${group}" -m 0750 "/var/log/ic-env-guard-${account}"

if [[ ! -f "${token_file}" ]]; then
  umask 077
  python3 - <<'PY' > "${token_file}"
import secrets
print(secrets.token_urlsafe(32))
PY
  chown "${account}:${group}" "${token_file}"
  chmod 0600 "${token_file}"
fi

if [[ ! -f "${config_file}" ]]; then
  cat > "${config_file}" <<YAML
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
  token_file: ${token_file}
state_database: ${state_dir}/state.db
enrollment:
  socket_path: /run/ic-env-guard/agent-enrollment.sock
  socket_mode: "0600"
metrics:
  enabled: true
  collect_interval_seconds: 10
services: []
YAML
  chown "root:${group}" "${config_file}"
  chmod 0640 "${config_file}"
fi

install -m 0644 packaging/systemd/ic-env-guard@.service \
  /etc/systemd/system/ic-env-guard@.service
systemctl daemon-reload
systemctl enable "ic-env-guard@${account}.service"

echo "Installed Agent for existing user ${account}; review ${config_file}, then start ic-env-guard@${account}.service."

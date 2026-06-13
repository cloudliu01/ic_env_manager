#!/usr/bin/env bash
set -euo pipefail

install -d -m 0750 /etc/ic-env-guard
install -d -m 0750 /var/lib/ic-env-guard
install -d -m 0750 /var/lib/ic-env-guard/runtime
install -d -m 0750 /var/log/ic-env-guard

if ! id ic-env-guard >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/ic-env-guard --shell /sbin/nologin ic-env-guard
fi

if [ ! -f /var/lib/ic-env-guard/token ]; then
  umask 077
  python3 - <<'PY' > /var/lib/ic-env-guard/token
import secrets
print(secrets.token_urlsafe(32))
PY
  chown ic-env-guard:ic-env-guard /var/lib/ic-env-guard/token
  chmod 0600 /var/lib/ic-env-guard/token
fi

if [ ! -f /etc/ic-env-guard/config.yaml ]; then
  cat > /etc/ic-env-guard/config.yaml <<'YAML'
server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false
auth:
  mode: bearer_token
  token_file: /var/lib/ic-env-guard/token
metrics:
  enabled: true
  collect_interval_seconds: 10
services: []
YAML
  chmod 0640 /etc/ic-env-guard/config.yaml
fi

install -m 0644 packaging/systemd/ic-env-guard.service /etc/systemd/system/ic-env-guard.service
systemctl daemon-reload
systemctl enable ic-env-guard.service

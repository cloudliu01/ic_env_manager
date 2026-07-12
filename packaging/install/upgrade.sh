#!/usr/bin/env bash
set -euo pipefail

account="${1:-}"
if [[ -z "${account}" ]] || ! id "${account}" >/dev/null 2>&1; then
  echo "usage: $0 <existing-linux-user>" >&2
  exit 2
fi

systemctl stop "ic-env-guard@${account}.service" 2>/dev/null || true
install -m 0644 packaging/systemd/ic-env-guard@.service \
  /etc/systemd/system/ic-env-guard@.service
systemctl daemon-reload
systemctl start "ic-env-guard@${account}.service"

echo "Agent upgraded while preserving the user config, identity, token, and state database."

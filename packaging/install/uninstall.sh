#!/usr/bin/env bash
set -euo pipefail

account="${1:-}"
if [[ -z "${account}" ]]; then
  echo "usage: $0 <existing-linux-user>" >&2
  exit 2
fi

systemctl disable --now "ic-env-guard@${account}.service" 2>/dev/null || true
systemctl daemon-reload

echo "Agent instance removed. Configuration and state are retained for ${account}."

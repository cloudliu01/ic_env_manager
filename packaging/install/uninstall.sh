#!/usr/bin/env bash
set -euo pipefail

systemctl disable --now ic-env-guard.service 2>/dev/null || true
rm -f /etc/systemd/system/ic-env-guard.service
systemctl daemon-reload

echo "ic-env-guard service removed. Configuration and state are retained under /etc/ic-env-guard and /var/lib/ic-env-guard."

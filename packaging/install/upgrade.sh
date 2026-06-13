#!/usr/bin/env bash
set -euo pipefail

systemctl stop ic-env-guard.service 2>/dev/null || true
install -m 0644 packaging/systemd/ic-env-guard.service /etc/systemd/system/ic-env-guard.service
systemctl daemon-reload
systemctl start ic-env-guard.service

echo "ic-env-guard upgraded while preserving /etc/ic-env-guard, /var/lib/ic-env-guard/token, and state database."

# Platform Validation

## Scope

The MVP targets:

- CentOS 7
- Red Hat Enterprise Linux 8
- Ubuntu 24.04

Packaging is designed around systemd plus a controlled runtime under `/var/lib/ic-env-guard/runtime/` so startup does not depend on a modern system Python from the host OS.

## Ubuntu 24.04 smoke validation

Status: pending on an Ubuntu 24.04 host or container/VM with systemd available.

Recommended commands:

```bash
sudo packaging/install/install.sh
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard
systemctl status ic-env-guard --no-pager
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
journalctl -u ic-env-guard -n 100 --no-pager
sudo packaging/install/uninstall.sh
```

Expected result:

- Service installs as `ic-env-guard.service`.
- Service starts under the configured runtime user.
- `/healthz` returns `{"status":"ok"}`.
- `/readyz` returns ready when security and configuration are valid.
- Journald logs contain actionable diagnostics and no token values.

## CentOS 7 packaging/runtime smoke validation

Status: pending on a CentOS 7 host or VM.

Known limitation for local developer validation: macOS and non-systemd containers cannot verify the systemd runtime contract. CentOS 7 validation must confirm that the controlled runtime path is present and that the service does not use unsupported system Python features.

Recommended checks:

```bash
sudo packaging/install/install.sh
sudo test -d /var/lib/ic-env-guard/runtime
sudo test -f /var/lib/ic-env-guard/token
stat -c '%a %U %G' /var/lib/ic-env-guard/token
systemd-analyze verify /etc/systemd/system/ic-env-guard.service || true
sudo systemctl enable --now ic-env-guard
curl -fsS http://127.0.0.1:8765/healthz
sudo packaging/install/uninstall.sh
```

Expected result:

- Token file permissions are owner-read/write only.
- Unit file loads under systemd.
- The service starts with the controlled runtime layout or documents the exact runtime artifact that must be installed.

## RHEL 8 packaging/runtime smoke validation

Status: pending on a RHEL 8 host or VM.

Recommended checks mirror CentOS 7 and additionally validate SELinux/journald behavior if enforced in the target environment:

```bash
sudo packaging/install/install.sh
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard
systemctl show ic-env-guard -p User -p WorkingDirectory -p Restart -p After -p Wants
journalctl -u ic-env-guard -n 100 --no-pager
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
sudo packaging/install/uninstall.sh
```

Expected result:

- Unit metadata matches the packaging contract.
- Restart policy, working directory, runtime user, environment file handling, and dependency ordering match `packaging/systemd/ic-env-guard.service`.
- State, token, and config remain preserved across upgrade.

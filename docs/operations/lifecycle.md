# Agent Lifecycle Operations

## Install

Run `packaging/install/install.sh` as root. The installer creates:

- `/etc/ic-env-guard/config.yaml`
- `/var/lib/ic-env-guard/token`
- `/var/lib/ic-env-guard/state.db`
- `/var/lib/ic-env-guard/runtime/`
- `/var/log/ic-env-guard/`
- `/etc/systemd/system/ic-env-guard.service`

## Configure

Edit `/etc/ic-env-guard/config.yaml`. The default bind address is `127.0.0.1`. Remote binding requires explicit `remote_bind_enabled: true` and valid authentication settings.

## Validate Configuration

```bash
ic-env-guard-config validate /etc/ic-env-guard/config.yaml
```

## Start / Stop / Restart / Status

```bash
systemctl start ic-env-guard
systemctl stop ic-env-guard
systemctl restart ic-env-guard
systemctl status ic-env-guard
```

## Inspect Logs

```bash
journalctl -u ic-env-guard -f
```

## Upgrade

Run `packaging/install/upgrade.sh` as root. Configuration, token, and state are preserved.

## Uninstall

Run `packaging/install/uninstall.sh` as root. Configuration and state are retained by default.

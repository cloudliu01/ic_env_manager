# Agent Deployment Guide

Deploy one Agent per Linux host under an existing non-root account. That account
defines every filesystem, process, PTY, and sudo permission available through
the browser Terminal.

## Choose the Agent Account

Select the existing Linux user whose normal environment operators need. The
project does not create a user, change its groups, install sudo rules, or narrow
its existing shell authority. Before remote exposure, review:

```bash
id edaops
sudo -l -U edaops
getent passwd edaops
```

Do not run the Agent as root. Enable only one Agent unit instance per host unless
you deliberately configure distinct Public, Ingest, state, and socket paths.

## Install the Agent

From an installed source tree, run the installer as root with the existing user:

```bash
sudo packaging/install/install.sh edaops
```

The installer creates or preserves:

| Path | Ownership/mode | Purpose |
| --- | --- | --- |
| `/etc/ic-env-guard/edaops.yaml` | `root:<user-group>` `0640` | Agent configuration. |
| `/var/lib/ic-env-guard/edaops/` | Agent user `0700` | State and credentials. |
| `/var/lib/ic-env-guard/edaops/token` | Agent user `0600` | Public bearer token. |
| `/var/lib/ic-env-guard/edaops/state.db` | Agent user | SQLite state/audit database after start. |
| `/var/lib/ic-env-guard/edaops/instance-id` | Agent user, private | Stable Agent identity after first start. |
| `/etc/systemd/system/ic-env-guard@.service` | root `0644` | Existing-user template unit. |

systemd starts `ic-env-guard@edaops.service` with `User=edaops`, reads the YAML
through `IC_ENV_GUARD_CONFIG`, and creates `/run/ic-env-guard` mode `0700` for
the enrollment socket. The non-template `ic-env-guard.service` is deprecated
compatibility packaging; never remove its explicit non-root `User=` boundary.

## Configure the Listeners and State

Edit `/etc/ic-env-guard/edaops.yaml` using the complete
[Agent example](configuration.md#agent-configuration).

An Agent process owns two coordinated listeners:

- **Public** defaults to `127.0.0.1:8765`. It serves Web UI, authenticated read
  and control APIs, Terminal WebSockets, health, and Prometheus metrics. It does
  not accept Observation or Log Source writes.
- **Local Ingest** defaults to `127.0.0.1:8766`. It accepts only loopback
  Observation and Log Source `PUT` requests. It has no token, UI, read API,
  Terminal, health, or metrics surface.

Public owns startup and shutdown for both listeners. If Local Ingest cannot
bind, Agent startup fails instead of silently running with partial isolation.
Never expose, NAT, SSH-forward, container-publish, or reverse proxy Local Ingest.

Use owner-controlled absolute paths for `state_database` and token. Keep the
stable `instance-id` beside the state DB after its first creation; losing it
after initialization causes fail-closed startup rather than a new identity.

## Configure Services

Service control is allowlisted in local YAML. API requests select only a known
service ID and permitted operation; they never submit an arbitrary command.
Each service defines exactly one execution mapping:

```yaml
services:
  - id: license-server
    name: License Server
    systemd_unit: lmgrd.service
    allowed_operations: [start, stop, restart, status]
    restart: never
```

Or map a command managed as the Agent user:

```yaml
services:
  - id: demo-http
    name: Demo HTTP
    command: python3 -m http.server 18080
    cwd: /tmp
    allowed_operations: [start, stop, restart, status, healthcheck]
    restart: never
    healthcheck:
      type: tcp
      target: 127.0.0.1:18080
      interval_seconds: 10
      timeout_seconds: 2
      failure_threshold: 3
```

Do not set both `command` and `systemd_unit`. A mapped systemd operation also
depends on the Agent user's existing authorization; this project does not grant
it. See the [configuration reference](../reference/configuration.md#services)
for all fields.

## Validate and Start

```bash
sudo ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard@edaops.service
```

After every configuration change, validate before restart:

```bash
sudo ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
sudo systemctl restart ic-env-guard@edaops.service
```

Inspect the lifecycle and bounded logs:

```bash
systemctl status ic-env-guard@edaops.service --no-pager
journalctl -u ic-env-guard@edaops.service -n 100 --no-pager
journalctl -u ic-env-guard@edaops.service -f
```

## Acceptance Checks

Check Public process health and readiness:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
```

Read authenticated runtime state and services:

```bash
TOKEN="$(sudo -u edaops cat /var/lib/ic-env-guard/edaops/token)"
curl --fail -H "Authorization: Bearer ${TOKEN}" \
  http://127.0.0.1:8765/api/v2/runtime
curl --fail -H "Authorization: Bearer ${TOKEN}" \
  http://127.0.0.1:8765/api/services
```

Check metrics from an allowed source:

```bash
curl --fail http://127.0.0.1:8765/metrics
```

Open the Web UI, sign in with the Public token, create a Terminal, and run
`id`. Confirm its UID, groups, home, shell, and `sudo -l` match `edaops`; close
the Terminal when finished.

Check the enrollment socket after startup:

```bash
sudo -u edaops test -S /run/ic-env-guard/agent-enrollment.sock
stat -f '%Su %Sg %Lp %N' /run/ic-env-guard/agent-enrollment.sock 2>/dev/null || \
  stat -c '%U %G %a %n' /run/ic-env-guard/agent-enrollment.sock
```

The socket is ephemeral and must not be backed up. Its parent must be owned by
the Agent account and no wider than `0700`.

## Local Producers

Schedule collectors with cron, a systemd timer, or another program running on
the Agent host. They write to loopback without a token. Follow
[Local Data Ingest](local-data-ingest.md) for exact payloads and ordering rules.

Local producer scheduling is intentionally outside the Agent. Remote collectors
must execute their check on the Agent host and submit locally; they must not
reach the tokenless listener over a network tunnel.

## Remote Access Checklist

Before binding Public beyond loopback:

1. Set `server.remote_bind_enabled: true` deliberately.
2. Use HTTPS, or an explicit private trusted-LAN HTTP policy.
3. Restrict firewall/client CIDRs and remote `/metrics` scrapers.
4. Protect the bearer token and review the Agent user's sudo authority.
5. Keep Local Ingest loopback-only and verify forwarded headers cannot affect
   its peer check.

See [Security](security.md) for the complete boundary model and
[Monitoring and Logs](monitoring-and-logs.md) for scrape and tail policy.

## Stop, Upgrade, Recover, or Remove

```bash
sudo systemctl stop ic-env-guard@edaops.service
sudo packaging/install/upgrade.sh edaops
sudo packaging/install/uninstall.sh edaops
```

Never mix config, token, `instance-id`, and SQLite files from different backup
generations. Follow [Backup, Upgrade, and Recovery](backup-upgrade-recovery.md)
before upgrading, restoring identity, rolling back, or uninstalling.

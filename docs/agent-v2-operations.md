# Agent v2 Operations

## Process and listener model

One Agent process runs both HTTP listeners with one shared dependency container and SQLite
engine. Public owns startup and cleanup; Local Ingest has no independent lifecycle tasks.

- Public defaults to `127.0.0.1:8765`. It serves the Web UI, authenticated read/control APIs,
  Terminal WebSockets, health, and Prometheus metrics. It never accepts Observation or Log
  writes.
- Local Ingest defaults to `127.0.0.1:8766`. It accepts only local Observation and Log Source
  `PUT` requests. It has no token and exposes no read, Terminal, health, metrics, or management
  API.
- Manager mode starts Public only. It never opens Local Ingest.

Local Ingest trusts every process and Linux user on the host. Do not reverse proxy, NAT,
SSH-forward, container-publish, or firewall-open this listener. Both Uvicorn listeners ignore
forwarded proxy headers. A remote producer must run its collection command on the Agent host
and submit to loopback.

## Agent account and systemd

Use the template unit with an existing non-root Linux account:

```bash
sudo install -m 0644 packaging/systemd/ic-env-guard@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ic-env-guard@edaops.service
```

The template reads `/etc/ic-env-guard/edaops.yaml` and optional
`/etc/ic-env-guard/edaops.env`. Before starting it, create an owner-only state directory and
token that `edaops` owns, and point `state_database` and `auth.token_file` there. The
`instance-id` is created beside `state_database`.

`User=%i` means `ic-env-guard@edaops.service` runs as `edaops`. That account determines the
home directory, files, PTY programs, and `sudo` authority available in browser terminals. The
project neither creates that user nor changes sudoers. Review this authority before enabling
remote Public access. The non-template `ic-env-guard.service` is a deprecated compatibility
unit and still has an explicit non-root `User=`; never remove `User=` from either unit. Enable
only one Agent unit instance per host.

systemd creates `/run/ic-env-guard` with mode `0700` and ownership matching `User=%i`. The
default enrollment configuration is:

```yaml
enrollment:
  socket_path: /run/ic-env-guard/agent-enrollment.sock
  socket_mode: "0600"
```

The socket is ephemeral and must not be backed up. Its parent must be owned by the Agent
account and no wider than `0700`. The fixed helper is `ic-env-guard agent enroll-manager`.
It accepts no config or shell arguments. It resolves configuration from `IC_ENV_GUARD_CONFIG`
when set, otherwise `/etc/ic-env-guard/<current-euid-name>.yaml`, then the deprecated
`/etc/ic-env-guard/config.yaml` fallback. The template sets the environment for service
execution; SSH forced commands do not inherit the service environment, so keep the normal
per-user path or set a fixed `IC_ENV_GUARD_CONFIG` in the forced command for a custom path.

For an optional unattended Manager SSH key, install the public key manually for the Agent
account with a forced command. Keep host-key verification enabled and use an entry equivalent
to:

```text
restrict,command="ic-env-guard agent enroll-manager",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... manager-enrollment
```

The key must not open a shell or permit forwarding. The Agent never returns a user's public
key, reads a Manager private key, edits `authorized_keys`, creates users, or modifies sudoers.
The `manager_credentials.last_used_at` column is reserved and is not currently authentication
telemetry; rely on bounded audit events rather than that field when investigating access.

## Local producers

Producer scheduling remains outside the Agent. Use cron, a systemd timer, or any local
program. `details` is an extensible JSON object; labels must remain small and stable because
numeric labels become Prometheus labels.

```bash
curl --fail --request PUT http://127.0.0.1:8766/api/v2/observations \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"eda","name":"license_alive","kind":"gauge","value":1,"status":"ok","labels":{"server":"license01"},"details":{"pid":1234},"observed_at":"2026-07-11T10:00:00Z","ttl_seconds":120}'
```

Each update replaces the latest value only when ordering rules allow it. `ttl_seconds` is
measured from `observed_at`; producers must submit a current RFC 3339 timestamp. SQLite
preserves `details`, but `details` never becomes Prometheus labels.

Register only Log Source metadata. The file must already be a regular file beneath one of the
locally configured `logs.allowed_roots`:

```bash
curl --fail --request PUT http://127.0.0.1:8766/api/v2/logs/license-server \
  --header 'Content-Type: application/json' \
  --data '{"path":"/var/log/eda/license.log","last_updated":"2026-07-11T10:00:00Z","observed_at":"2026-07-11T10:00:00Z","ttl_seconds":120}'
```

Log content is never stored in SQLite. An authenticated Public client can request a bounded
tail by stable Log ID, or an operator can inspect the file from the remote Terminal. Remote
clients cannot submit arbitrary paths or enlarge `logs.allowed_roots`.

## Prometheus

Prometheus scrapes `GET /metrics` on Public, never Local Ingest. Loopback scrapes work by
default. For a remote Prometheus server, bind Public deliberately and restrict
`metrics.remote_network_allowlist` to the scraper CIDR. Prometheus stores time series history;
the Agent stores only the latest Observation and its TTL.

## Upgrade, backup, and rollback

Stop the service and make a consistent backup before an upgrade:

1. Back up the Agent config, legacy local-admin token file, `instance-id`, and SQLite state DB
   together while the process is stopped. Preserve owner and `0600` modes for identity/token.
   The DB contains the initialization marker but never a copy of the identity UUID; both the
   DB and `instance-id` are required for recovery.
2. Back up `logs.allowed_roots` configuration, but not log contents unless normal operations
   require them.
3. On a Manager, back up its DB, durable enrollment journal, and `0600` plaintext Agent
   credential directory as one unit. Agent SQLite contains only Manager token hashes; those
   hashes are not a substitute for Manager credential files.
4. Do not back up the enrollment socket or anything under `/run`; systemd recreates the runtime
   directory and the Agent recreates its owned socket.

Run `sudo packaging/install/upgrade.sh <existing-non-root-user>`. For the documented legacy
layout, the upgrader detects `/etc/ic-env-guard/config.yaml` plus
`/var/lib/ic-env-guard/{token,state.db,instance-id}`, validates a staged per-user config, and
only then stops `ic-env-guard.service` for a consistent state copy. It starts
and enables `ic-env-guard@<user>.service` before disabling the legacy unit. If the new instance
cannot start, it disables and stops that instance, then re-enables and restarts the legacy unit
while leaving the original config and recovery files unchanged. The project never creates the
selected account and rejects root.

The upgrader serializes runs with an owner-only lock and records each cutover phase in the
owner-only `.ic-env-guard-<user>.upgrade` staging directory. State and config publish by atomic
same-filesystem rename; the final config is fsynced in an owner-only temporary file beside its
destination, never moved from `/var` to `/etc`. Lock, marker, staging, config-temp, and completed
metadata must have the effective owner and exact private modes, and their parents must not be
symlinks or group/world writable. If the process is killed after the marker is durable, rerun
the same command: it recognizes its marker, restores the legacy unit, removes only its known
staged or published copies, and retries from the unchanged legacy originals. Unknown or
symlinked control paths fail closed and require operator inspection.

For an already migrated per-user install, the upgrader publishes and fsyncs the unit template
from a temporary file in the systemd unit directory, reloads systemd, and enables it before any
running instance is stopped. It records the original active/enabled state and attempts to
restore that exact state after install, reload, enable, or start failure; an originally inactive
instance is not started merely because its package was upgraded.

All upgrade control files are created under an initial `umask 077`; a kill before an explicit
`chmod` therefore cannot leave a trusted file readable by another user. Before replacing an
existing per-user unit, the upgrader atomically publishes an owner-only backup containing the
old bytes, mode, and active/enabled state. Failure or rerun stops the new instance, restores and
reloads that exact unit (or removes the new unit if none existed), and then restores service
state. Secure interrupted unit temporaries are recoverable; symlinked, misowned, or incorrectly
mode-controlled temporaries and backups fail closed.

Automatic migration accepts only the paths emitted by the old installer. If the legacy config
uses customized token or state paths, the upgrader exits before stopping the legacy service;
copy the grouped backup to the per-user layout, update and validate the config manually, then
rerun the upgrade. Do not run both units together: they otherwise use the same default Public
and Ingest ports.

During the first new/pre-v2 initialization, the Agent may briefly create an owner-only
`.instance-identity-bootstrap.<database-hash>` beside the state DB. Its versioned content binds
the canonical DB and identity file paths; it contains no UUID or secret and cannot authorize a
different DB or identity location. It is removed only after migrations, the identity file,
the SQLite initialized marker, and persistent identity-path binding are durable. If an upgrade
is interrupted and this file remains, do not recreate, edit, loosen, move, copy, or delete it;
retry the same v2 startup with the same paths. Unsafe or mismatched files fail closed. A stale
intent cannot replace a missing identity after the SQLite initialized marker exists.

On the first v2 Agent startup, `instance-id` is created once next to the state DB. Subsequent
restarts and upgrades must retain that exact file. If it disappears after enrollment, stop the
Agent and restore it from the matching backup. Once the SQLite initialization marker or an
existing v2 schema proves initialization occurred, startup without the file fails closed and
does not generate a replacement UUID. A new install or an explicitly pre-v2 database may
create the file once. Never copy one Agent's identity to another host.

An older valid Agent config does not need an explicit `enrollment:` block. The documented
defaults still create `/run/ic-env-guard/agent-enrollment.sock` and advertise
`manager-enrollment.v1`; the template unit's `RuntimeDirectory=ic-env-guard` and mode `0700`
provide its parent directory. Add an explicit block only to override the socket path or mode.

Migrations add Observation, Log Source, and `manager_credentials` tables in place and retain
existing tables. A failed migration prevents startup. An older binary may leave unknown new
tables untouched, but it cannot authenticate Manager-specific tokens. Before rollback, verify
the legacy local-admin token still works and retain it as the recovery path. Then stop the
Agent, back up the new state, restore the old binary/config and—only if necessary—the matching
pre-upgrade DB plus `instance-id`. Do not combine a DB from one backup generation with
credentials or identity from another. After returning to v2, restore the grouped backup,
verify the legacy token, and re-enroll or rotate any Manager credential whose state is unclear.

After upgrade or rollback, verify Public `/healthz`, authenticated `/api/v2/runtime`, local
Ingest write isolation, `/metrics`, Terminal login, and that the enrollment socket is owned by
the selected Agent account.

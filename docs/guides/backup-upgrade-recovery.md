# Backup, Upgrade, and Recovery Guide

Stop the affected runtime before copying or restoring durable state. Keep each
backup generation together and preserve original ownership and modes. Agent and
Manager are separate atomic units; never mix their files or generations.

## Atomic Backup Units

### Agent

Back up these files together while `ic-env-guard@<agent-user>.service` is
stopped:

```text
/etc/ic-env-guard/<agent-user>.yaml
/var/lib/ic-env-guard/<agent-user>/token
/var/lib/ic-env-guard/<agent-user>/instance-id
/var/lib/ic-env-guard/<agent-user>/state.db
```

Include SQLite `-wal` or `-shm` sidecars if they remain after a clean stop.
Preserve the user ownership and private modes, especially token and
`instance-id` (`0600`). The state DB contains its initialization marker and
identity-path binding but not a replacement copy of the UUID; the matching DB
and `instance-id` are both required.

Back up configuration for `logs.allowed_roots`, not log contents unless your
normal application backup policy requires them. Never copy one Agent's
`instance-id` or DB to another host.

### Manager

Back up these together while `ic-env-guard@<manager-user>.service` is stopped:

```text
/etc/ic-env-guard/<manager-user>.yaml
<control_plane.audit_database>
<control_plane.audit_database>-wal       # if present
<control_plane.audit_database>-shm       # if present
<control_plane.credential_directory>/
```

The SQLite DB contains Registry, audit, enrollment, discovery, rotation, and
removal journals. The credential directory is mode `0700` and contains mode
`0600` plaintext Agent credentials. A DB without its matching credential files
cannot safely probe, proxy, rotate, or revoke. Credential files without the
matching Registry/journal are also not a safe restore.

Back up the Manager Public bearer token with its deployment secrets if it is
stored outside the paths above.

### Never Back Up Runtime Sockets

Do not back up anything under `/run/ic-env-guard` or any Agent/Manager
enrollment `.sock` file. systemd recreates the runtime directory and each
runtime securely recreates its owned socket.

## Backup Procedure

For an Agent named `edaops`:

```bash
sudo systemctl stop ic-env-guard@edaops.service
sudo ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
# Copy the complete Agent atomic unit with ownership and modes preserved.
sudo systemctl start ic-env-guard@edaops.service
```

For Manager, stop its own unit and copy the complete Manager unit separately.
Record binary/package version and configuration checksum with each generation,
but never print token or credential contents.

After restart, check:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
systemctl status ic-env-guard@edaops.service --no-pager
```

## Agent Upgrade

Take a stopped backup first, then run the supported upgrader with the existing
non-root Agent user:

```bash
sudo packaging/install/upgrade.sh edaops
```

For an existing per-user installation, the upgrader stages and fsyncs the
template unit, records the original active/enabled state, publishes atomically,
reloads systemd, and restores that service state. An inactive instance is not
started merely because its package was upgraded.

For the documented legacy layout, it recognizes only:

```text
/etc/ic-env-guard/config.yaml
/var/lib/ic-env-guard/token
/var/lib/ic-env-guard/state.db
/var/lib/ic-env-guard/instance-id   # when already created
```

It validates staged per-user config before stopping the legacy unit, copies the
matching state into `/var/lib/ic-env-guard/edaops/`, starts/enables the template
unit, then disables legacy. Original recovery files remain unchanged. If the
legacy config uses custom token/state paths, automatic migration exits before
stopping anything; migrate the grouped unit manually and validate it.

Do not run legacy and per-user units together because their default listener
ports conflict.

## Interrupted Upgrade

The upgrader serializes runs with an owner-only lock and records durable phases
under an owner-only `.ic-env-guard-<user>.upgrade` staging directory. It uses
same-filesystem atomic renames, fsync, private temporary files, and an exact
backup of a pre-existing unit plus its active/enabled state.

If the process is interrupted, rerun the exact same command:

```bash
sudo packaging/install/upgrade.sh edaops
```

Recognized markers cause rollback to the unchanged legacy/original state,
cleanup of only known owned staging paths, and a fresh retry. Do not edit,
delete, move, loosen, or replace marker/lock/staging/unit-backup files. Unknown,
symlinked, misowned, or wrong-mode control paths fail closed and require manual
inspection before proceeding.

## Identity Bootstrap and Fail-Closed Recovery

On first v2 initialization, Agent creates `instance-id` once beside the state
DB. A short-lived owner-only
`.instance-identity-bootstrap.<database-hash>` intent may appear while DB
migrations, identity, initialized marker, and path binding become durable. It
contains no UUID or credential.

If startup is interrupted and the intent remains, retry the same binary with
the same config, database, and identity paths. Do not recreate, edit, copy,
move, loosen, or delete it. Once SQLite proves initialization, a missing
`instance-id` causes startup to fail closed; Agent will not silently generate a
new identity.

To recover a missing identity:

1. Stop the Agent.
2. Select one matching backup generation.
3. Restore config, legacy token, `instance-id`, and SQLite DB together with
   original user ownership/modes.
4. Validate config and restart.
5. Confirm runtime reports the expected `instance_id` before Manager access.

Never copy an identity from a different Agent.

## Restore an Agent

```bash
sudo systemctl stop ic-env-guard@edaops.service
# Restore one complete matching Agent generation and original modes.
sudo ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
sudo systemctl start ic-env-guard@edaops.service
```

Then validate Public health/readiness, authenticated `/api/v2/runtime`, Local
Ingest isolation/write, `/metrics`, Web Terminal login, services, and enrollment
socket ownership. Re-probe from Manager and confirm the same stable identity.

If a forward migration fails, keep the failed DB for inspection, fix the
reported filesystem/config cause, restore the last complete generation, and
rerun the upgraded Agent. Do not reset state by deleting only the DB or
`instance-id`.

## Restore a Manager

1. Stop Manager.
2. Restore its configuration, SQLite DB/journals, Public token, and complete
   credential directory from one generation.
3. Restore Manager ownership; credential directory `0700`, files `0600`.
4. Validate Manager YAML.
5. Start Manager and inspect readiness/audit storage.
6. Probe every enabled Agent independently.
7. Test one Agent-scoped read and Terminal without exposing credentials.

If a credential file and Registry row do not match, stop and recover from a
matching generation. Do not invent a file, copy another Agent's credential, or
delete the journal to force progress. Use the Agent's retained legacy admin
token only through the documented recovery flow, then re-enroll or rotate.

## YAML-to-SQLite Registry Import

During migration from static Manager `agents:` configuration:

1. Preserve the original YAML and all referenced token files.
2. Start the upgraded Manager and allow the one-time compatibility import.
3. Confirm the Web-managed SQLite Registry contains each intended Agent.
4. Restart Manager and probe every Agent.
5. Back up the new Manager atomic unit.
6. Remove obsolete static inputs only after recovery from the new backup is
   proven.

SQLite is authoritative after import. Editing YAML does not recreate a renamed
or deleted Registry Agent and is not the normal Fleet workflow.

## Rollback

Before rollback, retain a backup of the current generation and verify the Agent
legacy local-admin token remains available as the recovery path. Stop the
runtime, restore the matching earlier binary/config, and restore its matching
atomic state unit only when schema compatibility requires it.

Do not combine a DB from one generation with identity, token, Manager Registry,
or credential files from another. An older Agent binary may ignore new tables
but cannot use managed Manager credentials introduced by newer behavior.

After returning to the current version, restore one complete current backup,
validate the legacy token, and re-enroll or rotate any Manager credential whose
activation/revocation state is uncertain.

## Credential Recovery and Residuals

Credential rotation is the preferred recovery when Manager and Agent are both
reachable. Complete and consume the new enrollment, probe successfully, and
confirm the old managed credential was retired.

If a normal removal failed while Agent was offline, Manager may perform
local-only removal only with explicit confirmation. This deletes local
Registry/credential state but can leave a valid remote credential on Agent.
Track the residual; when Agent returns, use retained local-admin access to
revoke it or re-enroll/rotate before treating the host as clean.

Never assume deleting a Manager credential file, Registry row, or local-only
registration revoked the remote credential.

## Post-Recovery Checklist

- Config validates and the expected unit/user is active.
- Public `healthz`/`readyz` succeed; Local Ingest remains loopback-only.
- Agent runtime identity matches the backed-up host.
- Agent SQLite data, Observations, services, and audit are readable.
- Manager DB and credential directory have original owner-only modes.
- Every Agent probe settles independently; one offline Agent does not block
  healthy results.
- One Agent-scoped API page and Terminal work with no credential exposure.
- `/metrics` is available only to intended scraper sources.
- Enrollment sockets were recreated rather than restored.
- Legacy tokens, uncertain rotations, and local-only removal residuals are
  tracked until resolved.

See [Security](security.md) for secret-exclusion rules and
[Monitoring and Logs](monitoring-and-logs.md) for operational checks.

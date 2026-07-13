# Manager Fleet Guide

The Manager is the browser entry point for several independent Agents. It
stores Fleet registration and cached status, orchestrates bounded discovery and
enrollment, and forwards only defined Agent-scoped API and Terminal traffic.

## Manager Responsibilities

Browser users authenticate only to Manager. Manager then selects the registered
Agent, resolves and pins its allowed target, loads that Agent's server-side
credential, and calls an allowlisted upstream route. Browser credentials,
cookies, and Agent tokens are never forwarded between those trust domains.

Manager owns:

- the Web-managed SQLite Agent Registry and cached Fleet status;
- enrollment/discovery jobs and control-plane audit records;
- owner-only plaintext Agent credential files;
- bounded probes, Agent API proxy calls, and Terminal WebSocket proxy slots.

Each Agent continues to own its PTYs, services, observations, log metadata,
metrics, local audit, SQLite state, and stable instance identity. Manager is not
a generic HTTP, SSH, or network proxy.

## Install and Configure the Manager

Run Manager as an existing non-root Linux account. The project does not create
that account or modify sudoers. A Manager can use the same template unit, with
its own per-user config:

```bash
sudo install -d -m 0755 /etc/ic-env-guard
sudo install -d -o manager -g manager -m 0700 /var/lib/ic-env-guard/manager
sudo install -o manager -g manager -m 0600 manager.yaml \
  /etc/ic-env-guard/manager.yaml
sudo -u manager ic-env-guard-config validate /etc/ic-env-guard/manager.yaml
sudo systemctl enable --now ic-env-guard@manager.service
```

Use the complete [Manager configuration](configuration.md#manager-configuration).
At minimum, define:

- Manager Public listener and bearer-token file;
- absolute control-plane SQLite database and credential directory;
- `allowed_agent_cidrs` covering every possible Agent target;
- verified-TLS or explicit trusted-LAN transport profiles;
- named discovery scopes, if discovery is enabled;
- the Manager enrollment Unix socket, if CLI fallback is enabled.

The Manager credential directory must be mode `0700`; every Agent credential
file is plaintext and mode `0600`. Tokens do not belong in YAML, SQLite,
browser storage, logs, screenshots, or support bundles.

After startup:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
systemctl status ic-env-guard@manager.service --no-pager
journalctl -u ic-env-guard@manager.service -n 100 --no-pager
```

Sign in using the Manager token. The Manager Fleet UI, not a static `agents:`
list, is the normal source of truth. Static entries are accepted only for
legacy recovery import.

## Add an Agent by Address

Before opening **Add agent**, confirm the Agent Public origin is covered by
`allowed_agent_cidrs` and an appropriate transport profile.

1. Enter the complete Agent Public URL, such as
   `https://agent01.example.net:8765`.
2. Select a transport profile loaded from Manager configuration.
3. Enter the existing Agent Linux user's SSH user, host, and port.
4. Optionally set the display name.
5. Start enrollment and review its connection, identity, capability, and
   summary preview.
6. Save only after the preview identifies the intended Agent.

The target URL contains only scheme, host, and optional port. User info, path,
query, and fragment are rejected. Manager rejects self-targets, loopback and
forbidden address classes, targets outside its CIDR allowlist, DNS changes that
escape policy, and unknown transport profiles.

Saving consumes the enrollment once, activates the Agent-side Manager
credential, writes the Agent token to the owner-only credential directory, and
commits the registration to SQLite. The browser sees only opaque IDs and public
status, never the token.

## Discover Agents in a Bounded Scope

Discovery is optional and Manager-only. Operators may choose only a named scope
defined in Manager YAML:

1. Open **Discover agents**.
2. Select one configured scope and review its CIDR, endpoint/profile pairs, and
   computed target count.
3. Start the bounded job; cancel it if needed.
4. Review fingerprinted candidates.
5. Choose **Enroll candidate** to carry the opaque discovery result into Add
   Agent, then confirm the SSH user.

The Web UI cannot submit an arbitrary network, host, or port. Each scope is a
private, non-loopback CIDR of at most 256 addresses; concurrency, per-connect,
fingerprint, whole-job, retention, and total-target limits come from config.
Discovery findings are candidates, not trusted registrations.

## SSH Enrollment and CLI Fallback

Automatic enrollment runs only the fixed Agent helper:

```text
ic-env-guard agent enroll-manager
```

It uses an existing Agent user, disables shell/PTY/forwarding/proxy/jump/local
command/multiplexing features, and preserves SSH host-key verification. The
Agent helper talks to its local owner-only Unix socket and returns one bounded,
short-lived credential response. It does not return the user's SSH public key,
read a Manager private key, edit `authorized_keys`, create a user, or modify
sudoers.

If automatic SSH is unavailable, copy the displayed non-secret CLI command and
run it from an authorized account on the Manager host. Example:

```bash
ic-env-guardctl agent enroll \
  --manager-socket /run/ic-env-guard/manager-enrollment.sock \
  --enrollment-id 6f142a38-85dc-4bcf-aaf2-a9c58c0a6a32 \
  --ssh edaops@10.20.30.41:22
```

Do not append tokens, passwords, key paths, shell redirection, `ProxyCommand`,
or `ProxyJump`. The CLI uses fixed OpenSSH arguments, parses a bounded response
without printing the credential, and submits it once to Manager's Unix socket.
Expired, replayed, mismatched, or unauthorized submissions are rejected.

For unattended enrollment, configure the dedicated service identity and
`known_hosts` together, then authorize its public key with the restricted
forced-command template described in [Security](security.md#ssh-enrollment).
Never disable host-key verification to automate enrollment.

## Local Development Enrollment

The repository's `./start.sh all` workflow uses a narrower enrollment path for
its same-owner, same-host development Agent. The development-gated Manager and
Agent exchange one bounded request through owner-only Unix sockets, then commit
`local-agent` to the v2 Registry with enrollment method `local_socket`, source
`local_dev_bootstrap`, and transport profile `local-loopback-http`.

This path requires neither local SSH nor a static `agents:` entry, is not
available through public HTTP or WebSocket APIs, and does not make loopback a
general Agent target. Remote Agent enrollment and rotation continue to use the
SSH workflows above.

## Legacy Token Recovery

**Use legacy token instead** is a compatibility and recovery path, not the
normal enrollment model. Supply the Agent's existing local-admin token once;
Manager validates it against the chosen endpoint/profile and stores the needed
credential server-side. The browser clears the submitted secret.

Static `agents:` configuration is likewise a legacy recovery import, not a
bootstrap mechanism for the current local stack or a normal Registry workflow.

A legacy bearer token proves knowledge of a secret but carries no stable Agent
instance identity by itself. It can be copied, reused, or presented by a
different installation. Confirm the returned runtime `instance_id`, endpoint,
API version, and capabilities before saving. Prefer SSH enrollment and then
rotate credentials when the Agent supports managed Manager credentials.

Never paste a legacy token into Agent URL user info, Manager YAML, audit notes,
shell history, or a support ticket.

## Probe and Interpret Fleet Status

Manager probes enabled Agents on schedule and on operator request. **Refresh
all** starts one independent probe per enabled Agent and reports the number that
succeeded or failed. One timeout, TLS error, or offline host does not abort or
hide results for other Agents.

The Fleet page separates:

- **connection status** — `ready`, `degraded`, `unavailable`, `unknown`, or
  `disabled`;
- **workload status** — `healthy`, `warning`, `critical`, `stale`, or `unknown`;
- cached capabilities and latest summary;
- last error code and staleness based on Manager configuration.

An unavailable Agent remains visible with its last known bounded summary. Treat
cached data as current only within `status_stale_after_seconds`. Probe again
after repairing network, TLS, Agent process, or credentials.

## Use Agent-Scoped Pages and Terminal

Choose an Agent from Fleet before opening Overview, Services, Observations,
Logs, Metrics, Audit, Settings, or Terminal. Each page checks that Agent's
advertised capability and uses routes scoped by its Registry ID.

The Terminal flow creates a one-use Manager ticket, reserves a bounded proxy
slot, and opens a WebSocket to that specific Agent/session. Manager does not
offer arbitrary upstream paths. The PTY still runs on the Agent as its selected
Linux user and has that user's existing sudo authority. Terminal output is not
persisted in Manager SQLite or audit.

Close active terminals and wait for enrollment/rotation/removal work before
changing target-critical settings. Revision and slot checks prevent an Agent
record from being changed underneath an active proxy operation.

## Edit, Rotate, Disable, and Remove

From Agent Settings you can change display name, enabled state, endpoint, or
transport profile. A target/profile change is revalidated before commit and may
require the legacy token for a compatibility Agent. Do not use an endpoint edit
to bypass target policy.

Disable an Agent to stop scheduled/interactive probes and new routed work while
retaining its Registry entry and cached state. Re-enable only after its
configuration is valid.

Credential rotation uses a new bounded SSH enrollment job:

1. Start rotation with the existing Agent SSH destination.
2. Complete automatic or CLI enrollment.
3. Consume the new enrollment only for the same Agent.
4. Confirm probe success with the new credential.

Rotation activates the new credential and retires the old managed credential
through an audited saga. Retry recoverable failures; do not manually swap
credential files while Manager is running.

Normal removal asks the reachable Agent to revoke the managed credential before
deleting the local Registry row and credential file. If the Agent is offline,
**Remove locally only** requires explicit confirmation that a remote credential
may remain. Record that residual. When the Agent returns, revoke or rotate the
orphaned credential locally before considering it decommissioned.

Legacy-token registrations have no managed remote credential to revoke; protect
and rotate the Agent's legacy admin token separately.

## Control-Plane Audit

Manager records bounded intent/outcome events for discovery, enrollment,
create, update, enable/disable, probe, rotation, proxy, Terminal, and removal
operations. Use the correlation ID from an error with the Manager Audit page and
service journal.

Audit stores actor, Agent/target identity, operation, result, dispatch state,
failure category, source address, and correlation ID. It must not store bearer
tokens, SSH private data, Terminal input/output, log content, or arbitrary
upstream response bodies. Privileged mutations fail closed while durable audit
storage is unavailable.

## Failure Isolation and Troubleshooting

Common stable failures:

| Code/category | Action |
| --- | --- |
| `agent_network_error` / `agent_timeout` | Check Agent process, route, firewall, address, and port; leave healthy Agents alone. |
| `agent_tls_error` | Check hostname, chain, clock, profile, and CA bundle. |
| `agent_target_forbidden` / `target_address_forbidden` | Correct CIDR/profile/DNS policy; do not tunnel around it. |
| `agent_disabled` | Validate configuration, then enable before probing. |
| `agent_busy` / `agent_mutation_in_progress` | Finish or cancel the current Agent mutation and retry. |
| `ssh_host_key_unknown` / `ssh_host_key_changed` | Verify out of band and update known_hosts; never disable checking. |
| `service_key_unavailable` | Repair key type, ownership, modes, and known_hosts or use CLI fallback. |
| `agent_enrollment_expired` / `consumed` | Start a new job; never reuse an enrollment ID. |
| `audit_unavailable` | Repair Manager DB/storage before privileged work. |

If Manager is down, Agents keep running services, metrics, local producers,
state, and local audit; only the Fleet browser gateway is unavailable. If one
Agent is down, do not restart Manager first—inspect that Agent's systemd status,
Public readiness, network/TLS path, and local journal.

Back up the Manager database and credential directory as one stopped atomic
unit. See [Backup, Upgrade, and Recovery](backup-upgrade-recovery.md) and the
[Security guide](security.md) before production enrollment or remote exposure.

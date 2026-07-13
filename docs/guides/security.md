# Security Guide

IC Design Environment Guard exposes real Linux shell and service authority.
Security depends on deliberate listener exposure, existing-user permissions,
server-side credentials, bounded routing, and fail-closed audit/storage checks.

## Authentication Domains

A standalone browser authenticates to one Agent Public listener. In Fleet mode,
the browser authenticates only to Manager; Manager authenticates separately to
the selected Agent with an owner-only server credential.

Use a distinct high-entropy bearer token file per Public runtime. Token files
must be safe regular files, normally mode `0600`, readable only by the runtime
user and host administrators. Never place bearer values in YAML, URL user info,
browser storage beyond the in-memory/session mechanism, logs, metrics, audit,
diagnostics, screenshots, or support tickets.

`/healthz` and `/readyz` expose only bounded status. `/metrics` follows its own
local/remote CIDR scrape policy. Terminal, services, observations/log reads,
audit, Agent Registry, Fleet, discovery, enrollment, and proxy routes require
the appropriate Public authentication.

## Public and Local Ingest Exposure

Public defaults to loopback. A non-loopback bind is invalid unless
`server.remote_bind_enabled: true` and authentication is configured. Protect
remote Public with HTTPS or an explicit trusted-LAN HTTP policy plus firewall
controls.

Agent Local Ingest has no token because it is for same-host producers. It
accepts only actual loopback peers on `127.0.0.1` or `::1` and ignores forwarded
proxy headers. Every local process/user able to connect is trusted to publish
current data.

Never expose Local Ingest through:

- a reverse proxy, NAT, firewall opening, or container port publication;
- SSH local/remote/dynamic forwarding;
- a load balancer, service mesh ingress, or TCP relay;
- Manager proxy routes.

Remote collection must run its check on the Agent host and submit to loopback.

## TLS and Trusted-LAN HTTP

Non-loopback Manager-to-Agent connections require verified TLS by default.
`system-tls` uses the OS trust store. A custom `verified_tls` CA bundle must be
an absolute regular file, not a symlink, owned by root or Manager, not group or
world writable, and contain valid certificates.

`trusted_lan_http` is an explicit exception for controlled private networks. Its
non-empty private CIDRs must be subsets of Manager's global Agent allowlist.
Traffic is unencrypted; use it only when the physical/network boundary and risk
are understood.

`development.allow_insecure_http` permits only loopback Agent HTTP while
Manager itself is loopback-only. It cannot disable verification for a remote
Agent.

For browser-to-Public HTTP on a trusted LAN,
`server.trusted_lan_http.client_cidrs` is a separate inbound policy. Neither
transport profile makes tokenless Ingest remotely safe.

## Manager Target and Proxy Boundaries

Manager enforces several layers before contacting an Agent:

- target origins contain only scheme, host, and optional port;
- resolved addresses must stay inside `allowed_agent_cidrs`;
- loopback/self, link-local, metadata-like, multicast, unspecified, and
  reserved targets are rejected;
- discovery uses configured named private scopes and bounded endpoint pairs;
- DNS resolution is rechecked/pinned for requests;
- upstream methods, paths, query fields, content types, headers, redirects, and
  response sizes are allowlisted;
- Agent capabilities and API version gate each feature.

Browser input cannot select an arbitrary upstream URL, header, credential, or
path. Terminal proxying is bound to one actor, Agent, PTY session, ticket, and
WebSocket path; it is not a generic TCP/WebSocket proxy.

Do not bypass target validation with `ProxyJump`, SSH tunnels, DNS tricks, or a
generic reverse proxy.

## SSH Enrollment

Enrollment uses an existing Agent Linux user and the fixed helper
`ic-env-guard agent enroll-manager`. Host-key verification remains enabled.
Unknown or changed keys require out-of-band review, not `StrictHostKeyChecking=no`.

The Agent helper accepts no arbitrary shell/config argument. It talks to an
owner-controlled Unix socket, returns one bounded short-lived credential, and
never handles a Manager private key. The Manager CLI socket authenticates the
local peer before accepting one bounded helper result.

For unattended enrollment, use a dedicated Ed25519 service key and a dedicated
non-empty `known_hosts` file. Configure both paths or neither. Parent
directories and files must be owner-safe; the private key is mode `0600`.
Install its public key manually on the existing Agent account using this exact
template from `packaging/ssh/ic-env-guard-enrollment-authorized-key.example`:

```text
command="ic-env-guard agent enroll-manager",restrict,no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding,no-user-rc ssh-ed25519 REPLACE_WITH_SERVICE_PUBLIC_KEY ic-env-guard-enrollment
```

The key must not open a shell or allow PTY, user rc, agent/X11/port forwarding.
The project does not edit `authorized_keys`, create users, change groups, or
modify sudoers.

Enrollment IDs and pending credentials are one-use and expire. CLI output and
API/UI state contain only opaque IDs and bounded public preview data. Never add
a token, password, private-key path, redirection, `ProxyCommand`, or
`ProxyJump` to the displayed `ic-env-guardctl` command.

## Existing-User Terminal Authority

The template unit uses `User=%i`. Agent Terminal PTYs therefore inherit the
selected user's UID, groups, home, shell, files, executable access, resource
limits, and existing sudo policy. The project does not create a restricted
shell or reduce that authority.

Treat browser Terminal access as equivalent to an interactive login for that
user. Before enabling remote Public access, review `id`, `sudo -l`, SSH/file
permissions, secrets readable by the account, and commands it can invoke.

Terminal HTTP operations require Public authentication. WebSockets use
short-lived one-use tickets. Agent and Manager bound active sessions and
outstanding tickets; Manager tickets also bind actor, Agent, terminal, revision,
and intended path. Frame limits, backpressure, paired cancellation, sanitized
close codes, cursor replay, and slot cleanup prevent unbounded proxy behavior.

Terminal output exists only in a bounded in-memory replay buffer for reconnect.
It is not persisted in SQLite, audit, logs, or metrics. Durable records contain
lifecycle metadata only. Close sessions when finished; idle timeout and process
exit cleanup reap abandoned PTYs.

## Service and Log Boundaries

Service commands come only from validated local configuration. Requests select
a configured ID and allowlisted operation; they cannot submit commands or
arguments. Environment values and bounded/redacted failures stay out of UI,
metrics, and audit secrets.

Local producers may register a Log Source only under configured absolute roots.
Authenticated tail reads revalidate path/file identity, freshness, requested
lines, and byte limits. They fail closed if bounded audit cannot be written.
Log content is returned only to the caller and is never copied into SQLite or
audit.

## Manager Credential Storage

Managed Agent credentials are plaintext files because Manager must present
them upstream. Store them only in `control_plane.credential_directory`:

- directory owned by Manager and mode `0700`;
- individual regular files owned by Manager and mode `0600`;
- no symlinked or group/world-writable path components;
- no credential values in Registry rows, API responses, logs, metrics, audit,
  frontend state, backups sent to support, or crash diagnostics.

Back up Manager SQLite and this directory together while stopped. A DB without
matching credentials cannot safely proxy/revoke; credentials without their
Registry/journal are also unsafe.

## Audit Boundaries

Agent audit and Manager control-plane audit are separate durable SQLite data.
Manager records pre-dispatch intent and bounded outcomes, including dispatch
state and failure category. Correlation IDs can associate Manager and Agent
events without copying secrets.

Audit may contain timestamp, actor, source address, operation, target identity,
result, bounded failure reason, and correlation ID. It must not contain:

- bearer tokens, passwords, private keys, or credential payloads;
- Terminal input/output or shell history;
- Log Source content;
- service environment secrets;
- unrestricted upstream request/response bodies.

Privileged operations fail closed if required audit persistence fails.

## Removal and Residual Credentials

Normal managed removal contacts the Agent to revoke the remote credential
before Manager deletes local state. Local-only removal is an offline recovery
action and explicitly may leave that credential active on the Agent. It
requires confirmation and records a residual warning/audit category.

Track every residual. When the Agent returns, revoke or rotate its Manager
credential locally before treating the registration as fully removed. Legacy
admin tokens are separate shared secrets and are not automatically revoked by
Manager removal.

## Release Review Checklist

- Public remote exposure is deliberate; Local Ingest is still loopback-only.
- Bearer, Manager credential, SSH key, CA, state, and config modes are correct.
- Non-loopback Agent TLS or trusted-LAN policy matches the intended network.
- Manager CIDR, discovery, and route boundaries cannot target arbitrary hosts.
- The selected Agent user's shell and sudo authority is approved.
- Terminal tickets, replay, slot limits, cleanup, and content privacy pass.
- Service mappings and Log Source roots are minimal and local-configured.
- Audit contains lifecycle metadata and no secret/content fields.
- Backup generations include matching DB/identity/credentials and exclude
  runtime sockets.
- Local-only removals and uncertain rotations have tracked residuals.

See [Backup, Upgrade, and Recovery](backup-upgrade-recovery.md) before changes
to identity or credentials and [API and Endpoint Reference](../reference/api-and-endpoints.md)
for the authentication/exposure matrix.

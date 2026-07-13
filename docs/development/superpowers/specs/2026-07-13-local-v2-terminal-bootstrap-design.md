# Local v2 Agent Bootstrap and Terminal Proxy Repair Design

**Date:** 2026-07-13
**Status:** Approved design, pending implementation review

## Context

The development launcher currently starts a local Agent on
`127.0.0.1:8766` and imports it into the Manager from a static `agents:` YAML
entry. Because the generated Manager configuration has no trusted transport
profile, the importer records the Agent with the compatibility marker
`legacy-config-http`.

The legacy Fleet adapter recognizes that marker for a limited set of old
requests. The current Agent-scoped HTTP and Terminal WebSocket proxies do not:
they require the Registry record to reference a configured transport profile.
As a result, the Agent itself answers `/api/terminals`, but the same request
through the Manager fails before dispatch with
`agent_transport_profile_invalid`. The React Terminal page consequently shows
`agent request failed`.

This is a development bootstrap defect, not a PTY defect. Extending the proxy
to accept the legacy marker would preserve two different trust models and
spread compatibility logic into current code. The local development launcher
should instead create the same kind of managed Registry and credential state
used by the current Fleet architecture.

## Goals

- Make `./start.sh all` boot a self-contained current-version Agent, Manager,
  and React UI without static legacy Agent import.
- Register `local-agent` in the v2 Registry with a real transport profile and
  a Manager-specific managed credential.
- Use the existing owner-only Agent and Manager Unix-socket trust boundary for
  same-host enrollment, without requiring SSH on the development machine.
- Keep the normal loopback and SSRF protections closed for production and for
  all non-local enrollment methods.
- Verify both Terminal HTTP discovery and an actual Terminal WebSocket/PTY
  exchange before declaring the development stack ready.
- Make failed `all` startup bounded and clean up processes started by that
  invocation.

## Non-goals

- Do not make legacy YAML import work with the current proxy.
- Do not add a public HTTP endpoint for local bootstrap.
- Do not require a local SSH daemon, SSH key provisioning, TLS certificates,
  or a dedicated operating-system user for the development stack.
- Do not weaken target validation for SSH enrollment, discovery, or ordinary
  Registry records.
- Do not preserve generated development Registry, managed credentials,
  observations, or terminal sessions across `./start.sh all` runs.
- Do not change the React Terminal design unless end-to-end testing exposes an
  independent frontend defect after the backend bootstrap is corrected.
- Do not remove compatibility import from production code as part of this
  focused repair; it simply stops being used by the generated development
  configuration.

## Considered Approaches

### 1. Owner-only local v2 bootstrap — selected

Start an empty current-version Manager and use a development-only local
enrollment command over the Agent and Manager Unix sockets. The command reuses
the managed enrollment transaction, commits a normal Registry record, and
uses a configured loopback HTTP profile.

This preserves the current credential and proxy model while keeping
`start.sh all` dependency-free on machines that do not run SSH locally.

### 2. Standard SSH enrollment to localhost

The launcher could use the existing SSH enrollment path. This would require a
local SSH daemon, user key setup, and an exception to the deliberate loopback
target rejection. Those requirements make the one-command development flow
platform-dependent without adding a meaningful security boundary on the same
host.

### 3. Accept the legacy marker in current proxies

The HTTP and WebSocket proxies could special-case `legacy-config-http` in the
same way as the old Fleet adapter. This is the smallest code change, but it
would keep the generated stack on compatibility credentials and duplicate the
exception across every new Agent-scoped proxy. It conflicts with the stated
requirement that the new version run on its own model.

## Architecture

The generated stack uses the following startup sequence:

```text
./start.sh all
  |
  +-- stop development processes owned by the previous run
  +-- rebuild generated Registry, credential, database, and socket state
  +-- generate current Agent and Manager configuration
  +-- start Agent and wait for health + enrollment socket
  +-- start Manager with an empty Registry and wait for health + CLI socket
  +-- invoke the owner-only local v2 enrollment command
  |     |
  |     +-- Agent creates a pending Manager credential
  |     +-- Manager stores the credential and commits the Registry record
  |     +-- Agent activates the credential
  |     +-- enrollment transaction completes or compensates
  +-- verify the Manager-to-Agent Terminal HTTP proxy
  +-- verify Terminal WebSocket creation, command output, and clean close
  +-- start the React development server
```

The Manager starts with no generated `agents:` entries. Once bootstrap
completes, the UI discovers `local-agent` from the same v2 Registry used for
manually enrolled remote Agents.

## Configuration and Data Model

### Generated transport profile

The Manager development configuration contains a real trusted-LAN HTTP
profile named `local-loopback-http`. It is limited to the IPv4 loopback range,
and the generated Agent URL remains the literal
`http://127.0.0.1:8766`. The profile is present in the same profile collection
consumed by probes, Agent-scoped HTTP proxies, and the Terminal WebSocket
proxy.

The generated configuration no longer contains:

- a static `agents:` entry for `local-agent`;
- the Agent administrator bearer token as an imported Manager credential; or
- any reference to `legacy-config-http`.

### Enrollment identity

Add `EnrollmentMethod.LOCAL_SOCKET` for a same-owner, same-host enrollment and
record the source as `local_dev_bootstrap`. These values make the trust origin
explicit in the Registry and audit log; they are not aliases for
`LEGACY_ADMIN_TOKEN` or any SSH method.

### Development gate

Manager configuration gains an explicit local-bootstrap development gate,
disabled by default. The local command is accepted only when all of the
following hold:

- the gate is enabled;
- both generated public URLs use literal loopback addresses;
- the selected profile is the configured loopback HTTP profile;
- the requested Agent enrollment method is `LOCAL_SOCKET`;
- the Agent enrollment socket is under the generated development runtime
  directory; and
- the Manager CLI and Agent enrollment sockets pass their existing owner and
  permission checks.

This is a separate validation path from ordinary target resolution. It does
not make loopback a valid target for SSH enrollment, discovery, arbitrary
Agent creation, or a Registry record with another enrollment method.

### Runtime target resolution

Enrollment is not the only point at which the target is checked. Status
probes, Agent-scoped HTTP requests, and Terminal WebSocket capture and
revalidation all resolve the stored endpoint again before dispatch. They must
use one shared record-aware resolver so they cannot disagree about the local
record.

The resolver uses the ordinary target policy for every record except a record
whose enrollment method is `LOCAL_SOCKET` and whose source is
`local_dev_bootstrap`. For that exact pair it uses a dedicated, non-legacy
loopback validator that requires:

- an `http` URL with a literal loopback address rather than a hostname;
- the configured `local-loopback-http` profile;
- the address to be covered by both the global and profile allowlists; and
- the target port not to be a Manager listener.

The same rule is applied during enrollment verification, periodic probes,
Agent-scoped HTTP proxying, and Terminal WebSocket route capture and dispatch.
The captured Terminal route includes the enrollment method and source in its
revision-safe comparison so a concurrent Registry change cannot retain local
privileges accidentally. No caller reuses the legacy-import validator.

## Local Enrollment Transaction

The Manager's owner-only CLI socket receives a bounded local-bootstrap
command. The Manager uses a local Agent-socket adapter instead of the SSH
helper adapter, while retaining the existing enrollment transaction stages:

1. Create an enrollment journal entry with a unique enrollment identifier.
2. Ask the Agent enrollment socket to mint a pending Manager-specific
   credential.
3. Store the returned credential in the Manager credential store without
   printing or returning it to the shell.
4. Validate the Agent identity and advertised capabilities through the Agent
   API using that pending credential.
5. Activate the credential on the Agent.
6. Commit the normal Registry record referencing `local-loopback-http` and the
   managed credential identifier.
7. Mark the enrollment journal complete and append a success audit event.

If a stage fails, the existing compensation rules revoke the pending or active
credential where possible, remove uncommitted secret material, and leave no
usable partial Registry entry. A failure audit event identifies the stage
without including credential bytes.

The socket request and response remain bounded JSON messages. Paths and IDs
are validated before any connection is opened, and the command has a finite
deadline so `start.sh` cannot wait indefinitely.

## Development State Lifecycle

`./start.sh all` treats its generated state as disposable. Before starting a
new stack it removes only generated runtime state owned by the development
launcher: Agent and Manager databases, managed credential stores, enrollment
journals, Unix sockets, PID metadata, and frontend runtime metadata.

Existing non-empty Agent and Manager login-token files may be retained. They
authenticate the developer to the public listeners and are distinct from the
new Manager-to-Agent managed credential. Preserving them avoids invalidating
an existing browser login on every restart. Missing or blank login tokens
continue to use the atomic recovery behavior already designed for
`start.sh`.

The cleanup routine must not recursively delete an arbitrary caller-provided
directory. It operates on a fixed list of generated paths, rejects unsafe
roots, and never kills a process solely because it occupies a configured
port. A process recorded by the development launcher may be stopped after its
identity is checked; an unrelated port owner produces a clear startup error.

## Security Boundaries

- The local-bootstrap operation is not exposed by public HTTP or WebSocket
  routing.
- Unix-socket directory and socket permissions remain owner-only.
- Managed credential values exist only in Agent/Manager process memory and the
  existing protected credential stores; `start.sh`, logs, API responses, and
  browser state never receive them.
- Plain HTTP is accepted only for the literal same-host loopback target under
  the guarded `LOCAL_SOCKET` enrollment. Remote trusted-LAN HTTP behavior is
  unchanged.
- The generic target policy continues to reject loopback, link-local,
  wildcard, multicast, metadata, and rebinding targets.
- A stored Registry record cannot gain the local exception merely by naming
  the transport profile; its source and enrollment method must also match the
  committed local-bootstrap transaction.
- Production configuration defaults remain fail-closed because the
  development gate defaults to false.

## Startup and Error Handling

The launcher waits independently for Agent health, the Agent enrollment
socket, Manager health, and the Manager CLI socket. Each wait has a finite
deadline and reports the component and path that failed.

If startup fails, a trap terminates only child processes started by that
invocation, waits for them to exit, removes their stale PID/socket metadata,
and returns a non-zero status. This prevents the previous failure mode in
which the Agent survived a partially failed `all` run.

Bootstrap errors are surfaced with a stable stage-specific message. The
generic browser message is not the primary diagnostic path; the Manager audit
entry and launcher output retain the safe error code. No failure path falls
back to legacy import.

## Terminal Readiness Verification

Manager health alone does not prove that the Fleet transport is usable. Before
starting the React server, the launcher performs two bounded checks using the
Manager public API:

1. Request the Agent-scoped terminal collection and require HTTP 200.
2. Open the Manager Terminal WebSocket proxy, create one PTY, run a harmless
   deterministic command, observe its sentinel output, and close the session.

The readiness command does not invoke `sudo`, mutate user files, or leave a
terminal slot allocated. A failed proxy, authentication, credential,
revision, slot, or PTY check aborts startup and triggers process cleanup.

## Test Strategy

### Unit and security tests

- Accept the exact guarded `LOCAL_SOCKET` + loopback-profile combination.
- Reject local bootstrap when its development gate is disabled.
- Reject a non-loopback URL, non-loopback socket path, mismatched profile, or
  other enrollment method.
- Confirm the ordinary target policy still rejects loopback.
- Verify the Agent-socket adapter bounds messages, deadlines, and errors.
- Verify compensation removes credentials and Registry state for failures at
  every new transaction boundary.

### Integration tests

- Invoke the real `./start.sh all` flow with an isolated development runtime.
- Assert the generated YAML has no static Agent and no legacy profile marker.
- Assert `local-agent` is a v2 Registry record with
  `LOCAL_SOCKET`, `local_dev_bootstrap`, and `local-loopback-http`.
- Assert the Manager credential differs from the Agent administrator token and
  is not exposed in output.
- Assert the Agent-scoped terminal collection returns HTTP 200.
- Create a PTY through the Manager WebSocket, send a deterministic command,
  receive its output, resize the PTY, and close it cleanly.
- Restart `all` and confirm generated Fleet state is rebuilt while valid public
  login tokens remain unchanged.
- Inject a bootstrap failure and confirm all processes created by that run are
  stopped.

### Regression tests

Run the focused enrollment, target-policy, proxy, Terminal revision/slot,
configuration, and launcher tests, followed by the complete backend and
frontend suites. Perform a browser-level check of
`/agents/local-agent/terminal` after the automated cross-end verification.

## Acceptance Criteria

- `./start.sh all` creates a self-contained current-version local Fleet with no
  legacy Agent import.
- `local-agent` has a real configured transport profile and a Manager-specific
  managed credential in the v2 Registry.
- No public API can invoke local bootstrap, and production loopback rejection
  remains intact.
- Manager Terminal HTTP and WebSocket proxy paths both work against the local
  Agent.
- Opening `/agents/local-agent/terminal` creates a usable terminal instead of
  showing `agent request failed`.
- A failed startup leaves no child process from that invocation running.
- Targeted tests, complete backend/frontend regressions, and the browser smoke
  check pass.

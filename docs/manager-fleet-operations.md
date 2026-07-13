# Manager Fleet Operations

## Local development

`./start.sh all` starts a Manager on `127.0.0.1:8765`, an Agent Public
listener on `127.0.0.1:8766`, Agent Local Ingest on `127.0.0.1:8767`, and
Vite on `127.0.0.1:5173`.  It uses separate `agent.yaml` and
`control-plane.yaml`, Agent and Manager tokens, `state.db`,
`control-plane.db`, `manager-credentials/`, and enrollment sockets beneath
`IC_ENV_GUARD_DEV_DIR` (default `/tmp/ic-env-guard-dev`, mode `0700`).

`./start.sh config control-plane` validates and prints the Manager config. It
has no Ingest listener. Development uses the built-in `system-tls` profile and
an explicit loopback allowlist only; replace that allowlist before registering
a remote Agent.

## Remote Fleet configuration

The Manager permits only Agent addresses in `control_plane.allowed_agent_cidrs`.
Every trusted-LAN HTTP profile must be a subset of that allowlist; use it only
on a controlled private network because it is unencrypted. `system-tls` is the
built-in verified HTTPS profile. The Manager resolves and pins targets, rejects
loopback, link-local, metadata, multicast, and its own listener; do not bypass
that policy with an SSH tunnel or generic proxy.

Discovery is Manager-only. Define named private CIDR scopes and endpoint/profile
pairs in `control_plane.discovery`; a scope is capped at 256 addresses and its
CIDR/profile must be covered by the Manager allowlists. Operators select a
named scope; the UI cannot submit a network range, address, or port.

A complete Manager example using built-in verified TLS plus an explicitly
bounded trusted-LAN profile is:

```yaml
mode: control-plane
server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false
auth:
  mode: bearer_token
  token_file: /etc/ic-env-guard/manager.token
control_plane:
  audit_database: /var/lib/ic-env-guard/control-plane.db
  credential_directory: /var/lib/ic-env-guard/agent-credentials
  poll_interval_seconds: 15
  status_stale_after_seconds: 45
  max_parallel_probes: 8
  allowed_agent_cidrs:
    - 10.20.30.0/24
  transport_profiles:
    - id: eda-lan-http
      type: trusted_lan_http
      allowed_cidrs:
        - 10.20.30.0/24
  discovery:
    max_concurrency: 32
    job_timeout_seconds: 120
    scopes:
      - id: eda-lab
        name: EDA lab network
        cidr: 10.20.30.0/24
        endpoints:
          - port: 8765
            transport_profile_id: eda-lan-http
          - port: 9443
            transport_profile_id: system-tls
enrollment:
  manager_socket_path: /run/ic-env-guard/manager-enrollment.sock
  manager_socket_mode: "0600"
```

The `system-tls` profile is always present and must not be redefined. A custom
verified CA profile may instead use `type: verified_tls` and an absolute,
root-or-Manager-owned, non-writable `ca_bundle` containing valid certificates.
The trusted-LAN scope must be a subset of both `allowed_agent_cidrs` and that
profile's `allowed_cidrs`.

Probe an Agent after registration and use the Fleet details page for cached
connection and workload status. A failed or offline Agent remains isolated:
other Fleet probes, pages, and Agents continue operating.

## Linux service operation

Run the template as an existing account, for example
`ic-env-guard@edaops.service`; that user owns its PTY programs and determines
terminal/sudo authority. The template uses `/etc/ic-env-guard/edaops.yaml` and
systemd's owner-only `/run/ic-env-guard` directory. Inspect with:

```bash
sudo systemctl status ic-env-guard@edaops.service --no-pager
sudo journalctl -u ic-env-guard@edaops.service -n 100 --no-pager
```

Manager credential files are plaintext, owner-only files: the credential
directory must be `0700` and individual files `0600`. Configure the Manager
socket with `enrollment.manager_socket_path`; use `0600`, or `0660` only with a
dedicated primary socket group configured by `manager_socket_gid`.

Install and validate a per-user deployment with:

```bash
sudo install -d -m 0755 /etc/ic-env-guard
sudo install -o edaops -g edaops -m 0600 manager.yaml /etc/ic-env-guard/edaops.yaml
sudo -u edaops ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
sudo systemctl enable --now ic-env-guard@edaops.service
```

The service creates `/run/ic-env-guard` through `RuntimeDirectory` and owns
the Manager Unix socket; there is no separately exposed TCP or systemd socket
unit. Do not run a second Manager for the same socket/database.

## Fleet operation and troubleshooting

“Refresh all” probes enabled Agents independently and reports successes and
failures without aborting on the first offline host. Probe/enable/disable,
settings, removal, and control-plane audit are also available per Agent. Fleet
status is cached, so an unavailable Agent remains visible with its last known
summary and stable error code.

- `agent_network_error` / `agent_timeout`: verify routing, Agent listener,
  firewall, and endpoint; other Agents remain usable.
- `agent_tls_error`: verify hostname, certificate chain, clock, and selected
  verified-TLS profile or CA bundle.
- `agent_target_forbidden`: the resolved target is outside
  `allowed_agent_cidrs`, is self/loopback/link-local/metadata, or changed DNS;
  correct configuration rather than tunnelling around the policy.
- `agent_disabled`: enable the Agent before probing it.
- `agent_busy`: wait for enrollment, rotation, removal, or another mutation to
  finish, then retry on the same Agent page.
- `audit_unavailable`: treat mutations as unavailable until the Manager audit
  database and filesystem permissions are repaired.

Use the correlation ID from the UI/API with the Manager control-plane Audit
page and journal. Responses and logs must never contain credential values.

## Removal and recovery

Normal removal revokes the remote credential before deleting the local
registry entry and credential. If the Agent is offline, choose local-only
removal and explicitly confirm that its remote credential can remain. Record
the residual and, once the Agent returns, revoke or rotate the credential from
the Agent before treating the host as decommissioned.

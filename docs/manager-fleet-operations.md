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

## Removal and recovery

Normal removal revokes the remote credential before deleting the local
registry entry and credential. If the Agent is offline, choose local-only
removal and explicitly confirm that its remote credential can remain. Record
the residual and, once the Agent returns, revoke or rotate the credential from
the Agent before treating the host as decommissioned.

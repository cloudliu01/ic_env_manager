# Manager Enrollment Security

Enrollment creates a short-lived Manager credential on the Agent and stores
its plaintext only in the Manager credential directory. API responses and UI
state use opaque enrollment and credential references; never copy a token into
shell history, YAML, screenshots, browser storage, or a support ticket.

Automatic enrollment uses SSH to run only the Agent's fixed
`ic-env-guard agent enroll-manager` helper. It disables shell, PTY, forwarding,
proxy commands, jumps, local commands, and SSH multiplexing. Host-key checks
remain enabled: verified TLS uses an interactive host-key policy; trusted-LAN
HTTP may use `accept-new` for the SSH host key only. A changed host key stops
enrollment and requires operator review.

For optional unattended enrollment, install a restricted service public key on
the existing Agent user, with a forced command equivalent to:

```text
restrict,command="ic-env-guard agent enroll-manager",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... manager-enrollment
```

Use a dedicated Manager private key and `known_hosts` file; configure
`enrollment.service_key_identity_file` and
`enrollment.service_key_known_hosts_file` together, as absolute paths. The
Manager never edits `authorized_keys`, creates users, changes sudoers, or
receives a user private key.

If automatic SSH cannot complete, the UI provides a CLI enrollment command.
Run it from an authorized local account and retain SSH host-key verification;
the Manager's owner-only Unix socket authenticates the local peer before it
accepts the helper result. Do not replace this with an SSH proxy, `ProxyJump`,
or `ProxyCommand`.

The displayed command is directly executable and contains only bounded,
non-secret arguments. For example (IDs and hosts are illustrative):

```bash
ic-env-guardctl agent enroll \
  --manager-socket /run/ic-env-guard/manager-enrollment.sock \
  --enrollment-id 6f142a38-85dc-4bcf-aaf2-a9c58c0a6a32 \
  --ssh edaops@10.20.30.41:22
```

Do not add a token, private-key path, password, `ProxyCommand`, or shell
redirection. The CLI invokes fixed OpenSSH arguments, parses bounded helper
JSON without printing the credential, and submits it once to the Manager
socket. Reusing an enrollment ID is rejected.

The Agent enrollment socket is ephemeral under `/run/ic-env-guard`, mode
`0600` by default. Do not expose, forward, back up, or widen it. Audit logs
record bounded operation metadata, not credential values.

## Manager enrollment configuration

`enrollment` is a top-level section (not nested below `control_plane`):

```yaml
enrollment:
  manager_socket_path: /run/ic-env-guard/manager-enrollment.sock
  manager_socket_mode: "0600"
  pending_ttl_seconds: 600
  max_pending: 32
  ssh_binary: /usr/bin/ssh
  ssh_connect_timeout_seconds: 10
  ssh_total_timeout_seconds: 15
  # Optional unattended enrollment; configure both or neither.
  service_key_identity_file: /var/lib/ic-env-guard/ssh/id_ed25519
  service_key_known_hosts_file: /var/lib/ic-env-guard/ssh/known_hosts
```

The Manager user must own the service-key directory and files. The private key
must be an unencrypted Ed25519 OpenSSH key with mode `0600`; `known_hosts` must
be non-empty and neither file nor any parent may be symlinked or group/world
writable. Authorize its public key on an existing Agent user with these exact
forced-command options:

```text
command="ic-env-guard agent enroll-manager",restrict,no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding,no-user-rc ssh-ed25519 AAAA... manager-enrollment
```

## Stable failures

- `service_key_unavailable`: fix key type, ownership, mode, parent directory,
  or `known_hosts`; the flow safely falls back to the displayed CLI.
- `ssh_host_key_unknown` / `ssh_host_key_changed`: verify the fingerprint out
  of band, then update the Manager user's `known_hosts`; never disable checks.
- `agent_enrollment_expired`: start a new enrollment; do not reuse the old
  command.
- `agent_enrollment_consumed` or `agent_enrollment_input_changed`: the job was
  already consumed or its immutable connection input no longer matches; inspect
  Manager audit before starting again.
- `enrollment_rejected`: the local socket deliberately returns one generic
  rejection for an invalid, expired, replayed, or unauthorized submission.
  Verify the absolute socket path, running Manager, `/run/ic-env-guard`
  ownership, socket mode, and enrollment ID before starting a new job.

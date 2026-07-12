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

The Agent enrollment socket is ephemeral under `/run/ic-env-guard`, mode
`0600` by default. Do not expose, forward, back up, or widen it. Audit logs
record bounded operation metadata, not credential values.

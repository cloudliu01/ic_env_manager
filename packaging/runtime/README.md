# Controlled Runtime Packaging

The MVP must not depend on a modern system Python being available on supported hosts.

Supported packaging approaches:

1. Installer-managed virtual environment under `/var/lib/ic-env-guard/runtime/`.
2. Bundled Python runtime copied by the installer.
3. Self-contained executable produced by a packaging tool.

The install workflow must keep the generated token, configuration, and SQLite state outside the runtime directory so upgrades can replace runtime files without losing operator data.

CentOS 7 validation must confirm the service starts with the controlled Python runtime rather than `/usr/bin/python`.

## Agent v2 layout

Run the template unit as an existing Linux account, for example
`ic-env-guard@edaops.service`. systemd creates the owner-only
`/run/ic-env-guard` runtime directory for the enrollment socket. Keep the
stable `instance-id`, legacy admin token, SQLite state, and configuration outside
the replaceable runtime. The runtime directory and socket are ephemeral.

Public and Local Ingest are two listeners in one process. Local Ingest is
loopback-only and must never be reverse proxied, forwarded, published, or opened
in a firewall. See [Agent v2 Operations](../../docs/agent-v2-operations.md) for
account selection, producer examples, backup, migration, and rollback steps.

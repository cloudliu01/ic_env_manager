# Controlled Runtime Packaging

The MVP must not depend on a modern system Python being available on supported hosts.

Supported packaging approaches:

1. Installer-managed virtual environment under `/var/lib/ic-env-guard/runtime/`.
2. Bundled Python runtime copied by the installer.
3. Self-contained executable produced by a packaging tool.

The install workflow must keep the generated token, configuration, and SQLite state outside the runtime directory so upgrades can replace runtime files without losing operator data.

CentOS 7 validation must confirm the service starts with the controlled Python runtime rather than `/usr/bin/python`.

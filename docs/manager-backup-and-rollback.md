# Manager Backup and Rollback

Stop the Manager before a consistent backup. Treat these as one atomic backup
unit: the Manager SQLite audit/registry database (which includes the durable
enrollment, removal, and discovery journals), the `0700` Manager credential
directory containing `0600` Agent credentials, and the Manager configuration.
Keep matching generations together. Do not back up runtime sockets under
`/run`.

To restore, stop the Manager, restore the grouped DB, credential directory, and
configuration with their original ownership and modes, then validate the
configuration before starting it. A database without its credential directory
cannot proxy, probe, rotate, or revoke registered Agents safely. Credentials
without the matching registry/journal are also not a safe restore.

The Agent backup unit is separate: config, legacy local-admin token,
`instance-id`, and state SQLite DB. Never replace one Agent's identity with
another Agent's backup. After a restore, verify Agent Public health and Manager
credentials, then re-probe the Fleet.

For the supported YAML-to-SQLite migration, retain the original YAML and token
files until the Manager import and post-restart probe are verified. SQLite is
authoritative after the one-time import; editing YAML does not re-import a
deleted or renamed Agent. Roll back by stopping the new service, restoring the
matching old binary/config and grouped backup, and verifying the legacy local
admin token before opening access.

If an upgrade or rollback leaves credential state uncertain, use the retained
legacy token to reach the Agent, then revoke the residual Manager credential or
perform a credential rotation. Do not assume local-only Manager removal revoked
the remote credential; it explicitly leaves a recoverable remote residual.

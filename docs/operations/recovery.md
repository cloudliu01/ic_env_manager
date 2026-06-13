# Recovery Operations

## Failed Startup

1. Inspect service status:

   ```bash
   systemctl status ic-env-guard
   ```

2. Inspect logs:

   ```bash
   journalctl -u ic-env-guard -n 200
   ```

3. Validate configuration:

   ```bash
   ic-env-guard-config validate /etc/ic-env-guard/config.yaml
   ```

4. Fix configuration or token file permissions and restart:

   ```bash
   systemctl restart ic-env-guard
   ```

## Reset Local State

Stop the service before modifying state:

```bash
systemctl stop ic-env-guard
```

Move the existing database aside instead of deleting it immediately:

```bash
mv /var/lib/ic-env-guard/state.db /var/lib/ic-env-guard/state.db.bak.$(date +%s)
systemctl start ic-env-guard
```

## Migration Recovery

If a forward-only migration fails, keep the failed database for inspection, restore the latest backup, and rerun the upgraded agent after fixing the reported cause.

Secrets, token values, and terminal contents should not be copied into support tickets or logs.

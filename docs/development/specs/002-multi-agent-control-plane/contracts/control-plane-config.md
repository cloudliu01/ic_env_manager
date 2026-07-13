# Control-Plane Configuration Contract

## Modes

```yaml
mode: agent | control-plane
```

`agent` is the default. `combined` is not a valid value in this feature and must
fail configuration validation before application startup.

## Agent Mode

```yaml
state_database: /var/lib/ic-env-guard/state.db
```

`state_database` configures migration-managed local durable state for the host
agent, including inherited audit durability from feature `001`.

## Control-Plane Mode

```yaml
control_plane:
  poll_interval_seconds: 10
  status_stale_after_seconds: 30
  max_parallel_probes: 8
  audit_database: /var/lib/ic-env-guard/control-plane.db
  max_active_terminal_proxies: 64
  max_outstanding_tickets: 128

agents:
  - id: lab-host-01
    name: Lab Host 01
    base_url: https://lab-host-01.example.com:8765
    token_file: /etc/ic-env-guard/agents/lab-host-01.token
    tls:
      verify: true
      ca_bundle: /etc/ic-env-guard/ca/lab.pem
    connect_timeout_seconds: 3
    request_timeout_seconds: 10
    enabled: true
```

In `control-plane` mode, startup does not resolve, create, or migrate
`state_database`. Gateway audit uses only `control_plane.audit_database`.

## Validation Rules

- Agent IDs match `^[a-z0-9][a-z0-9_-]{0,63}$` and are unique.
- `base_url` contains only scheme, host, and optional port.
- URL userinfo, query, fragment, and non-root paths are rejected.
- Enabled agents always require exactly one readable credential source.
- Non-loopback agents require HTTPS and verified TLS.
- Loopback HTTP requires explicit development-only insecure transport and a
  local-only control-plane bind.
- Token files must be regular files owned by the service user and not readable by
  group or other.
- Agent display names and IDs are configuration-owned and never derived from
  remote response content.

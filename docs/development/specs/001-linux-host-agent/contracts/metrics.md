# Metrics Contract

## Endpoint

```text
GET /metrics
```

The endpoint returns Prometheus-compatible text exposition format.

## Access Model

- Local-only bind is the default.
- When metrics are exposed beyond localhost, access relies on a configured network allowlist.
- Metrics access does not use the local administrator browser login token.
- Requests from outside the configured allowlist are rejected.

## Collection Model

- Host, agent, service, and health-check metrics are collected into in-memory metric families.
- Scrape requests render current in-memory values and must not perform expensive synchronous host inspection.
- Long-term high-frequency metrics storage is owned by Prometheus-compatible external tools, not SQLite.

## Required Metric Families

| Name | Type | Labels | Unit | Description |
|------|------|--------|------|-------------|
| `ic_env_guard_build_info` | gauge | `version` | none | Agent build/version info with value `1`. |
| `ic_env_guard_websocket_connections` | gauge | none | connections | Current active WebSocket connections. |
| `ic_env_guard_terminal_sessions` | gauge | `status` | sessions | Terminal sessions by bounded status. |
| `ic_env_guard_api_requests_total` | counter | `method`, `route_group`, `status_class` | requests | API request count using bounded route group labels. |
| `ic_env_guard_host_cpu_percent` | gauge | none | percent | Host CPU utilization percentage. |
| `ic_env_guard_host_memory_used_bytes` | gauge | none | bytes | Host memory used. |
| `ic_env_guard_host_memory_total_bytes` | gauge | none | bytes | Host memory total. |
| `ic_env_guard_host_disk_used_bytes` | gauge | `mount` | bytes | Disk used for bounded configured mount labels. |
| `ic_env_guard_host_disk_total_bytes` | gauge | `mount` | bytes | Disk total for bounded configured mount labels. |
| `ic_env_guard_host_network_rx_bytes_total` | counter | `interface` | bytes | Network bytes received for bounded configured interfaces. |
| `ic_env_guard_host_network_tx_bytes_total` | counter | `interface` | bytes | Network bytes transmitted for bounded configured interfaces. |
| `ic_env_guard_service_up` | gauge | `service` | boolean | `1` when configured service is running/healthy enough to be considered up. |
| `ic_env_guard_service_restart_total` | counter | `service` | restarts | Managed service restart count. |
| `ic_env_guard_service_start_time_seconds` | gauge | `service` | seconds | Managed service start timestamp. |
| `ic_env_guard_service_healthcheck_success` | gauge | `service` | boolean | Last health check success. |
| `ic_env_guard_service_healthcheck_latency_seconds` | histogram or gauge | `service` | seconds | Managed service health-check latency. |

## Bounded Labels

Allowed labels must be bounded and documented:

- `version`: agent build version.
- `status`: fixed terminal/service status enums.
- `method`: fixed HTTP methods.
- `route_group`: fixed risk/route groups, not raw URLs.
- `status_class`: `2xx`, `3xx`, `4xx`, `5xx`.
- `mount`: configured bounded mount list.
- `interface`: configured bounded network interface list.
- `service`: configured service ID only.

Forbidden labels:

- terminal session IDs
- arbitrary commands
- raw paths not from a bounded allowlist
- request IDs or correlation IDs
- bearer token or credential identifiers
- unbounded user input
- source IP addresses

## Validation Requirements

- `/metrics` output must parse as Prometheus-compatible text format.
- Metrics scrape must succeed from allowed network sources.
- Metrics scrape must be rejected from disallowed network sources when remote exposure is enabled.
- Cardinality review must confirm no forbidden labels are present.
- SQLite must not store high-frequency metrics samples as a long-term time-series database.

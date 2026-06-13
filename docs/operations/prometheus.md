# Prometheus Metrics

## Endpoint

```text
GET /metrics
```

The endpoint returns Prometheus-compatible text exposition format.

Local requests are allowed by default. If metrics are exposed beyond localhost, configure an explicit CIDR allowlist for the monitoring network. Metrics scraping does not use the browser login bearer token.

## Example Prometheus scrape config

```yaml
scrape_configs:
  - job_name: ic-env-guard
    metrics_path: /metrics
    static_configs:
      - targets:
          - 127.0.0.1:8765
```

For remote scraping, bind the agent intentionally and restrict the configured network allowlist to the scraper network, for example:

```yaml
server:
  bind: 0.0.0.0
  port: 8765
  remote_bind_enabled: true
metrics:
  enabled: true
  collect_interval_seconds: 10
  remote_network_allowlist:
    - 192.0.2.0/24
```

## Required metric families

- `ic_env_guard_build_info{version}`
- `ic_env_guard_websocket_connections`
- `ic_env_guard_terminal_sessions{status}`
- `ic_env_guard_api_requests_total{method,route_group,status_class}`
- `ic_env_guard_host_cpu_percent`
- `ic_env_guard_host_memory_used_bytes`
- `ic_env_guard_host_memory_total_bytes`
- `ic_env_guard_host_disk_used_bytes{mount}`
- `ic_env_guard_host_disk_total_bytes{mount}`
- `ic_env_guard_host_network_rx_bytes_total{interface}`
- `ic_env_guard_host_network_tx_bytes_total{interface}`
- `ic_env_guard_service_up{service}`
- `ic_env_guard_service_restart_total{service}`
- `ic_env_guard_service_start_time_seconds{service}`
- `ic_env_guard_service_healthcheck_success{service}`
- `ic_env_guard_service_healthcheck_latency_seconds{service}`

## Label rules

Allowed labels are bounded: version, fixed status enums, HTTP method, route group, status class, configured mount, configured interface, and configured service ID.

Forbidden labels:

- terminal session IDs
- commands or command arguments
- raw unbounded paths
- request IDs or correlation IDs
- bearer token or credential identifiers
- unbounded user input
- source IP addresses

## Operational notes

Scrape requests render current in-memory metric values. Host inspection and service health work should happen through the collector path rather than expensive synchronous scrape-time operations. SQLite must not be used as a long-term high-frequency time-series store.

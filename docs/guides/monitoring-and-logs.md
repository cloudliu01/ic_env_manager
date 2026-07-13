# Monitoring and Logs Guide

Agent exposes current host, service, Observation, Log Source, API, and Terminal
signals. Prometheus owns time-series history; the Agent and Manager keep only
bounded current/cached operational state.

## Prometheus Scraping

Each Agent exposes Prometheus text format on its Public listener:

```text
GET /metrics
```

Loopback scrapes are allowed by default and do not use the browser bearer token:

```yaml
scrape_configs:
  - job_name: ic-env-guard
    metrics_path: /metrics
    static_configs:
      - targets: [127.0.0.1:8765]
```

For a remote Prometheus server, expose Agent Public deliberately and restrict
the scrape source independently:

```yaml
server:
  bind: 0.0.0.0
  port: 8765
  remote_bind_enabled: true
metrics:
  enabled: true
  collect_interval_seconds: 10
  remote_network_allowlist:
    - 10.20.40.0/24
```

Also apply host firewall and HTTPS/trusted-LAN policy. Local Ingest never serves
metrics. Prometheus should scrape Agents directly; Manager does not proxy,
merge, federate, or store their raw metrics.

## Metric Families

Core families include:

- `ic_env_guard_build_info{version}`;
- `ic_env_guard_websocket_connections`;
- `ic_env_guard_terminal_sessions{status}`;
- `ic_env_guard_api_requests_total{method,route_group,status_class}`;
- host CPU, memory, root-disk, and network byte families;
- service up, restart, start-time, healthcheck success, and latency families;
- `ic_env_observation_value` and `ic_env_observation_status` for fresh data;
- Log Source last-updated and stale gauges;
- cleanup-failure counters for bounded retention work.

Scrapes render current in-memory values. Host and service collection happens on
the configured refresh loop, not as expensive synchronous scrape work.

## Cardinality Rules

Allowed labels come from bounded sets: build version, status enum, HTTP method,
route group, status class, configured mount/interface/service ID, Observation
namespace/name/small labels, and stable Log Source ID.

Never put these values in metric labels:

- Terminal/session/request/correlation/credential IDs;
- commands, arguments, bearer tokens, or service environment values;
- source IP addresses or arbitrary user input;
- raw/unbounded paths;
- process IDs or verbose `details` data.

Observation labels are limited and become metric labels. Put extensible or
high-cardinality context in `details`, which remains in SQLite/API responses and
does not become a Prometheus label. `metrics.max_observation_series` prevents
unbounded current series.

## Current Values and Expiry

The Agent stores one latest Observation per `(namespace, name, labels)` and one
latest metadata record per Log Source ID. `expires_at` is
`observed_at + ttl_seconds`.

- Fresh numeric Observations may be exported as values.
- Stale Observations remain readable only when explicitly requested and are not
  emitted as fresh metric values.
- Log Source metrics expose last-updated time and staleness, never content.
- Expired rows remain only for the configured retention window, then cleanup
  removes them.

Set producer TTL longer than its normal schedule plus an expected delay. A
stale current value signals a producer failure or missed update; it is not a
historical time series.

## Agent Status

The Agent UI and authenticated APIs show:

- current Observations and their status/expiry;
- configured service state and healthcheck results;
- registered Log Source metadata and bounded tails;
- local host monitoring snapshot;
- bounded Agent audit lifecycle events.

Use [Local Data Ingest](local-data-ingest.md) to publish data and the
[API reference](../reference/api-and-endpoints.md) for route families.

## Manager Fleet Status

Manager stores a bounded latest probe summary per registered Agent. Fleet views
separate connection state from workload state and expose `stale_after`, last
success/error, capabilities, and partial errors.

Cached Manager data is not Prometheus history. An offline Agent remains visible
with its previous summary until normal retention/replacement; once the stale
deadline passes, treat workload data as stale. **Refresh all** settles each
probe independently, so one Agent failure does not block healthy Agent results.

For monitoring dashboards, scrape each Agent directly. Use Manager's
authenticated Fleet views for inventory, connection/capability state, cached
summaries, and operations.

## Log Source Metadata and Tails

Local producers register only:

```text
{path, last_updated, observed_at, ttl_seconds}
```

The file must be an existing regular file below a configured absolute
`logs.allowed_roots` path. SQLite stores normalized metadata, not file bytes.

Authenticated Public clients can request a stable ID's tail:

```bash
TOKEN="$(cat /var/lib/ic-env-guard/edaops/token)"
curl --fail -H "Authorization: Bearer ${TOKEN}" \
  'http://127.0.0.1:8765/api/v2/logs/license-server/tail?lines=100'
```

At read time, Agent rechecks the source, allowed root, file identity, freshness,
configured maximum lines, and maximum bytes. Unknown, stale, moved, forbidden,
or unavailable sources fail without returning content. Tail access produces a
bounded audit event; returned content is not copied into that event or SQLite.

The browser Terminal is the fallback for interactive file inspection under the
Agent user's normal permissions.

## Health and Readiness

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
curl --fail http://127.0.0.1:8765/metrics
```

`healthz` is bounded process liveness. `readyz` reports whether mode-specific
startup dependencies are ready; it is not proof that every remote Agent is
online. `/metrics` may return forbidden to a remote source outside the scrape
allowlist.

## Troubleshooting

```bash
systemctl status ic-env-guard@edaops.service --no-pager
journalctl -u ic-env-guard@edaops.service -n 200 --no-pager
ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
```

If observations are stale, compare producer schedule, UTC clock, submitted
`observed_at`, TTL, and its local curl result. For a Log Source, confirm the file
still exists as a regular file under an allowed root and the producer is
updating metadata. For missing metrics, check `metrics.enabled`, collector
errors, series limits, scrape source CIDR, and Public exposure.

For one unavailable Agent in Fleet, troubleshoot that Agent and its TLS/network
path; do not restart every healthy Agent or assume Manager is globally blocked.
Use correlation IDs to join Manager and Agent audit metadata without exposing
secrets.

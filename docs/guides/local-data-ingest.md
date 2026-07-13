# Local Data Ingest Guide

Local Ingest lets any local process publish the latest Observation or Log Source
metadata to its host's Agent. It intentionally has no bearer token, so it must
bind only `127.0.0.1` or `::1` and must never be exposed, forwarded, published,
or reverse proxied.

The production default is `http://127.0.0.1:8766`. In `./start.sh all`, the
Agent uses port `8767` because Manager Public already uses `8765` and Agent
Public uses `8766`.

## Publish an Observation

Use an RFC 3339 timestamp close to current UTC time:

```bash
curl --fail --request PUT http://127.0.0.1:8766/api/v2/observations \
  --header 'Content-Type: application/json' \
  --data '{
    "namespace": "eda",
    "name": "license_server_alive",
    "kind": "gauge",
    "value": 1,
    "status": "ok",
    "labels": {"server": "license01"},
    "details": {"pid": 1234, "unit": "lmgrd.service"},
    "observed_at": "2026-07-13T10:00:00Z",
    "ttl_seconds": 120
  }'
```

The payload fields are:

| Field | Rule |
| --- | --- |
| `namespace` | Lowercase series namespace, up to 63 characters. |
| `name` | Lowercase series name, up to 127 characters. |
| `kind` | `gauge`, `counter`, or `status`; numeric kinds require `value`. |
| `value` | Finite number or `null` for a status-only Observation. |
| `unit` | Optional string up to 32 characters. |
| `status` | `ok`, `warning`, `critical`, or `unknown`. |
| `message` | Optional bounded human-readable summary. |
| `labels` | Up to 16 small stable strings; never use `name`, `namespace`, or `status` as keys. |
| `details` | Extensible JSON object, preserved in SQLite but never converted to metric labels. |
| `observed_at` | Timezone-aware RFC 3339 measurement time. |
| `ttl_seconds` | `1..604800`, measured from `observed_at`. |

`details` is intended for future expansion and diagnostic context. It is
bounded to 16 KiB, JSON-only values, at most four nested levels, 64-byte keys,
and 4096-byte strings. Keep high-cardinality, process-specific, or verbose data
there rather than in `labels`.

The identity of a series is `(namespace, name, sorted labels)`. The Agent stores
only its latest value:

- a newer `observed_at` replaces the stored value;
- the same timestamp and same normalized payload is idempotent;
- an older timestamp is rejected as stale with HTTP `409`;
- the same timestamp with different data is rejected as a conflict;
- already expired or excessively future-dated data is rejected.

Prometheus owns time-series history. The Agent owns current value, receive time,
expiry, `details`, and cleanup retention.

## Register a Log Source

Configure the file's parent under `logs.allowed_roots`, then ensure the path is
an existing regular file. Register metadata only:

```bash
curl --fail --request PUT http://127.0.0.1:8766/api/v2/logs/license-server \
  --header 'Content-Type: application/json' \
  --data '{
    "path": "/var/log/eda/license.log",
    "last_updated": "2026-07-13T10:00:00Z",
    "observed_at": "2026-07-13T10:00:00Z",
    "ttl_seconds": 120
  }'
```

The stable `log_id` in the URL must begin with a lowercase letter and contain
only lowercase letters, digits, `_`, `.`, or `-` (maximum 127 characters).
Payload fields are exactly:

| Field | Rule |
| --- | --- |
| `path` | Absolute path to an existing regular file under an allowed root. |
| `last_updated` | RFC 3339 time the producer observed the file changing. |
| `observed_at` | RFC 3339 time this metadata was collected. |
| `ttl_seconds` | `1..604800`, measured from `observed_at`. |

Log Source ordering matches Observations: newer replaces, identical is
idempotent, older is stale, and different data at the same timestamp conflicts.
The Agent stores only normalized metadata in SQLite. It never stores log content
there, in audit events, or in Prometheus labels.

## Read Through Authenticated Public

Local Ingest provides no read API. Read current data through Agent Public with
its bearer token:

```bash
TOKEN="$(cat /var/lib/ic-env-guard/edaops/token)"

curl --fail -H "Authorization: Bearer ${TOKEN}" \
  'http://127.0.0.1:8765/api/v2/observations?namespace=eda&limit=100'

curl --fail -H "Authorization: Bearer ${TOKEN}" \
  http://127.0.0.1:8765/api/v2/logs

curl --fail -H "Authorization: Bearer ${TOKEN}" \
  'http://127.0.0.1:8765/api/v2/logs/license-server/tail?lines=100'
```

Tail reads revalidate the path and allowed root at request time, reject stale or
moved sources, cap lines and bytes from configuration, and write bounded audit
metadata. The response may contain at most the requested tail; it is not a
stream or arbitrary file API. Operators can also inspect the file using an
Agent Terminal with that Agent user's normal permissions.

## Producer Scheduling Pattern

A producer should:

1. Run locally as a user permitted to perform the check.
2. Capture one UTC timestamp after collecting the value.
3. Send a bounded payload with a TTL longer than its expected schedule plus a
   small failure allowance.
4. Treat `200` (updated/idempotent) and `201` (created) as success.
5. Log or alert on `409`, `422`, or `503` instead of retrying unchanged stale
   data indefinitely.

Example shell pattern:

```bash
observed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl --fail --request PUT http://127.0.0.1:8766/api/v2/observations \
  --header 'Content-Type: application/json' \
  --data "{\"namespace\":\"eda\",\"name\":\"worker_alive\",\"kind\":\"gauge\",\"value\":1,\"status\":\"ok\",\"labels\":{},\"details\":{},\"observed_at\":\"${observed_at}\",\"ttl_seconds\":120}"
```

Scheduling remains outside the Agent: use cron, a systemd timer, or an existing
local supervisor. Review [Monitoring and Logs](monitoring-and-logs.md) for
current/stale semantics and [Security](security.md) for listener isolation.

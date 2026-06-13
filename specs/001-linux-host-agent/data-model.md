# Data Model: IC Design Environment Guard

## Overview

The MVP stores durable operational state in local SQLite with migration-managed schema. Terminal output is not stored in SQLite by default; only terminal metadata and lifecycle events are durable. High-frequency host metrics are exported for Prometheus-compatible scraping and are not stored long-term in SQLite.

## Entity: Local Administrator

Represents the single authenticated human role for the MVP.

### Fields

- `id`: stable local actor identifier; unique.
- `display_name`: human-readable label for audit/UI.
- `credential_type`: `bearer_token` for MVP.
- `created_at`: timestamp when the local administrative credential was created.
- `last_authenticated_at`: timestamp of last successful authentication, nullable.
- `status`: `active`, `rotated`, or `disabled`.

### Validation Rules

- MVP has one active local administrator role.
- Bearer token values are never stored in audit logs, metrics, UI diagnostics, or general state records.
- Token file permissions must restrict reads to the agent runtime user and host administrators.

### Relationships

- Owns Terminal Sessions.
- Performs Service Operations.
- Produces Audit Events through privileged actions.

## Entity: Agent

Represents the local host process and its lifecycle.

### Fields

- `instance_id`: stable ID generated on first startup.
- `version`: running agent version.
- `bind_address`: configured bind address.
- `remote_bind_enabled`: boolean.
- `started_at`: current process start timestamp.
- `last_ready_at`: most recent readiness timestamp.
- `status`: `starting`, `ready`, `degraded`, `stopping`, `failed`.

### Validation Rules

- Default bind address is local-only.
- Remote bind requires explicit remote-bind configuration plus valid authentication settings.
- Invalid security configuration fails closed before exposing privileged routes.

### Relationships

- Loads Configuration Snapshots.
- Emits Agent Lifecycle Events and Audit Events.
- Owns Metrics Exposure behavior.

## Entity: Terminal Session

Represents a server-side PTY session independent of any single browser WebSocket connection.

### Fields

- `id`: unique terminal session ID.
- `owner_id`: Local Administrator ID.
- `title`: display label.
- `command`: shell command or configured shell path used to create the PTY.
- `cwd`: working directory, nullable.
- `pid`: child process ID, nullable after exit/cleanup.
- `rows`: terminal row count.
- `cols`: terminal column count.
- `status`: terminal lifecycle status.
- `output_cursor`: monotonic cursor for emitted output.
- `replay_buffer_start_cursor`: cursor for oldest retained in-memory output.
- `idle_timeout_minutes`: configured timeout, 30 to 120 inclusive, default 60.
- `created_at`: creation timestamp.
- `last_active_at`: last input/output/activity timestamp.
- `last_connected_at`: most recent WebSocket attach timestamp, nullable.
- `exited_at`: process exit timestamp, nullable.
- `closed_at`: user or cleanup close timestamp, nullable.
- `close_reason`: `user_closed`, `shell_exited`, `idle_timeout`, `forced_termination`, `agent_shutdown`, or `error`.

### Validation Rules

- `owner_id`, `created_at`, `last_active_at`, `status`, and cursor fields are required.
- `idle_timeout_minutes` must be between 30 and 120 inclusive; default is 60.
- Terminal content is not persisted by default.
- Reconnect with an older cursor returns retained output tail and marks replay as truncated.
- Reconnect with a future cursor is treated as current and only streams new output.

### State Transitions

```text
creating -> running -> exited -> closed
creating -> failed
running -> closed
running -> timed_out
running -> failed
exited -> closed
```

### Relationships

- Owned by Local Administrator.
- Produces Terminal Lifecycle Events.
- May produce Audit Events for create, attach, detach, close, timeout, and failure.

## Entity: Managed Service

Represents one explicitly configured local service.

### Fields

- `id`: unique service ID from local configuration.
- `name`: human-readable name.
- `description`: optional description.
- `command`: configured command, nullable when using host-service mapping.
- `systemd_unit`: configured systemd unit mapping, nullable when using command.
- `cwd`: working directory, nullable.
- `env_keys`: allowed environment variable keys; values are not exposed in audit/metrics.
- `allowed_operations`: list containing allowed values from `start`, `stop`, `restart`, `status`, `healthcheck`.
- `autostart`: boolean.
- `restart_policy`: `never`, `on-failure`, or `always`.
- `start_timeout_seconds`: positive integer.
- `stop_timeout_seconds`: positive integer.
- `healthcheck`: Health Check Definition, nullable.
- `log_rules`: log capture/rotation/status rules.
- `config_hash`: hash of the validated config entry.
- `updated_at`: timestamp.

### Validation Rules

- Every service must have a unique non-empty `id`.
- Each service must define exactly one execution mapping: configured command or host-service mapping.
- Allowed operations must be explicit.
- Commands come only from local configuration, never API payloads.
- Unsafe, ambiguous, incomplete, or malformed definitions fail validation before service actions are available.

### Relationships

- Has one current Service State.
- Has many Service Runs, Service Operations, Service Events, and Health Check Results.
- Appears in service metrics with bounded service ID/name label rules.

## Entity: Service State

Represents the current reconciled state of a Managed Service.

### Fields

- `service_id`: Managed Service ID; unique.
- `status`: `configured`, `starting`, `running`, `stopping`, `exited`, `failed`, or `unknown`.
- `pid`: process ID, nullable.
- `started_at`: timestamp, nullable.
- `stopped_at`: timestamp, nullable.
- `exit_code`: integer, nullable.
- `restart_count`: non-negative integer.
- `health_status`: `unknown`, `healthy`, `unhealthy`, or `disabled`.
- `health_latency_ms`: non-negative integer, nullable.
- `last_error`: bounded diagnostic text, nullable.
- `updated_at`: timestamp.

### Validation Rules

- Startup reconciliation compares persisted state to actual host state.
- Repeated operations should not create duplicate unmanaged processes.
- `last_error` must not contain secrets.

### State Transitions

```text
configured -> starting -> running -> stopping -> exited
configured -> starting -> failed
running -> failed
running -> exited
failed -> starting
exited -> starting
unknown -> configured/running/exited/failed after reconciliation
```

## Entity: Service Operation

Represents an auditable request to control or inspect a Managed Service.

### Fields

- `id`: unique operation ID.
- `service_id`: Managed Service ID.
- `actor_id`: Local Administrator ID.
- `operation`: `start`, `stop`, `restart`, `status`, or `healthcheck`.
- `requested_at`: timestamp.
- `started_at`: timestamp.
- `completed_at`: timestamp, nullable.
- `result`: `success`, `already_in_state`, `rejected`, `timeout`, or `failed`.
- `failure_reason`: bounded diagnostic text, nullable.
- `source_addr`: source address where available.

### Validation Rules

- `service_id` must refer to a configured Managed Service.
- `operation` must be included in that service's allowed operations.
- Unknown services and unsupported operations are rejected without executing commands.
- Failure reasons must not contain secrets.

## Entity: Service Run

Represents a concrete process run for a Managed Service.

### Fields

- `id`: unique run ID.
- `service_id`: Managed Service ID.
- `pid`: process ID.
- `started_at`: timestamp.
- `stopped_at`: timestamp, nullable.
- `exit_code`: integer, nullable.
- `stop_reason`: `requested`, `healthcheck_failed`, `process_exit`, `restart_policy`, `timeout`, `agent_shutdown`, or `unknown`.

### Relationships

- Belongs to Managed Service.
- May be referenced by Service Events and Health Check Results.

## Entity: Health Check Definition

Represents configured health-check behavior for a Managed Service.

### Fields

- `type`: `http`, `tcp`, `process`, or `none`.
- `target`: target URL, host/port, process rule, or empty for `none`.
- `interval_seconds`: positive integer.
- `timeout_seconds`: positive integer.
- `failure_threshold`: positive integer.

### Validation Rules

- `timeout_seconds` must be less than or equal to `interval_seconds`.
- Targets must be constrained and must not introduce arbitrary command execution.

## Entity: Health Check Result

Represents a bounded-retention health observation.

### Fields

- `id`: unique result ID.
- `service_id`: Managed Service ID.
- `success`: boolean.
- `latency_ms`: non-negative integer, nullable.
- `status_code`: integer, nullable for HTTP checks.
- `error`: bounded diagnostic text, nullable.
- `created_at`: timestamp.

### Validation Rules

- Retention is bounded by configuration.
- Error text must not contain secrets.

## Entity: Service Event

Represents a durable service-related event.

### Fields

- `id`: unique event ID.
- `service_id`: Managed Service ID.
- `event_type`: `config_loaded`, `state_changed`, `operation_requested`, `operation_completed`, `health_changed`, `log_rotated`, or `error`.
- `message`: bounded diagnostic text.
- `metadata`: bounded structured metadata without secrets.
- `created_at`: timestamp.

## Entity: Metrics Exposure

Represents the metrics access model and bounded label rules.

### Fields

- `enabled`: boolean.
- `local_only`: boolean.
- `network_allowlist`: list of configured networks allowed to scrape when exposed remotely.
- `collect_interval_seconds`: positive integer.
- `last_collection_at`: timestamp, nullable.
- `last_collection_status`: `success`, `partial`, or `failed`.

### Validation Rules

- Metrics are Prometheus-compatible text format.
- Remote metrics exposure relies on an explicit network allowlist rather than user login credentials.
- Labels must not include terminal session IDs, arbitrary commands, request IDs, or unbounded user input.
- Long-term high-frequency metrics are not stored in SQLite.

## Entity: Audit Event

Represents durable security and operations audit records.

### Fields

- `id`: unique audit event ID.
- `timestamp`: event timestamp.
- `actor_id`: Local Administrator ID where available, nullable for unauthenticated attempts.
- `source_addr`: source address where available.
- `operation`: operation name.
- `target_type`: target resource type.
- `target_id`: target resource ID, nullable.
- `result`: `success`, `denied`, `rejected`, `failed`, or `timeout`.
- `failure_reason`: bounded diagnostic text, nullable.
- `correlation_id`: request/operation correlation ID, nullable; not exposed as a metrics label.

### Validation Rules

- Audit events must not store terminal content, bearer token values, passwords, private keys, or service environment secrets.
- Authorization failures and invalid security configuration events are recorded where possible.
- Timestamps use a consistent timezone-aware format or epoch convention selected during implementation.

## Entity: Configuration Load Event

Represents a successful or failed configuration load/reload.

### Fields

- `id`: unique event ID.
- `config_path`: local path to configuration file.
- `config_hash`: hash of validated configuration, nullable on parse failure.
- `result`: `success`, `rejected`, or `failed`.
- `failure_reason`: bounded diagnostic text, nullable.
- `loaded_at`: timestamp.

### Validation Rules

- Malformed, incomplete, ambiguous, or unsafe config produces a rejected/failed load event.
- Failure reasons must be actionable and must not include secrets.

## Entity: Local State Store Migration

Represents schema versioning and migration history.

### Fields

- `version`: schema version identifier.
- `applied_at`: timestamp.
- `description`: migration summary.
- `direction`: `upgrade` or `downgrade` when reversible, otherwise `forward_only`.
- `result`: `success` or `failed`.
- `failure_reason`: bounded diagnostic text, nullable.

### Validation Rules

- Schema changes must be migration-managed.
- Forward-only migrations require documented recovery path.
- Startup must detect missing, failed, or incompatible migrations and fail clearly.

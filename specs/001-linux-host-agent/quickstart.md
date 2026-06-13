# Quickstart Validation Guide: IC Design Environment Guard

## Purpose

This guide defines end-to-end validation scenarios for the MVP. It is not an implementation script; it identifies the commands, setup, and expected outcomes that future tasks and tests must make runnable.

## Prerequisites

- A supported Linux host or test environment for each target platform:
  - CentOS 7
  - Red Hat Enterprise Linux 8
  - Ubuntu 24.04
- Ability to install and manage a systemd service in the test environment.
- A packaged agent build that includes or installs a controlled Python runtime.
- A test configuration file matching [contracts/service-config.schema.json](contracts/service-config.schema.json).
- A harmless configured test service, such as a local HTTP health responder or short-running worker.
- A browser or WebSocket-capable test client for terminal validation.
- A Prometheus-compatible metrics parser or scraper.

## 1. Install and Start Agent

### Steps

1. Install the agent package or run the installer.
2. Confirm a generated local bearer token exists with restricted file permissions.
3. Install or verify the systemd unit.
4. Start and enable the service.
5. Check service status and logs.

### Expected Outcomes

- The agent starts under systemd on boot.
- The agent does not depend on modern system Python from the host OS.
- `/healthz` returns liveness.
- `/readyz` reports ready when configuration and security are valid.
- Logs provide actionable diagnostics without secrets.

## 2. Validate Fail-Closed Security

### Steps

1. Attempt to access terminal creation without authentication.
2. Attempt to start or stop a configured service without authentication.
3. Configure remote bind without valid authentication settings.
4. Restart the agent.

### Expected Outcomes

- Terminal and service-control requests are rejected when unauthenticated.
- Invalid remote-bind/security configuration prevents startup or readiness.
- Audit events are recorded for rejected privileged attempts where possible.
- Token values and terminal content are absent from logs and audit records.

## 3. Authenticate as Local Administrator

### Steps

1. Use the generated local bearer token to authenticate.
2. Access the web UI.
3. List visible capabilities.

### Expected Outcomes

- The authenticated user has the single local administrator role.
- The administrator can access terminal, service status/control, logs/status, and metrics guidance.
- There are no separate operator or read-only viewer roles in MVP.

## 4. Validate Browser Terminal Lifecycle

### Steps

1. Create a terminal session.
2. Connect using a one-use WebSocket ticket.
3. Run a harmless command.
4. Resize the terminal.
5. Disconnect the browser or WebSocket client.
6. Reconnect with the last output cursor.
7. Close the terminal session.
8. Inspect process cleanup.

### Expected Outcomes

- Terminal create/resize/close workflow completes within 2 minutes.
- Reconnect to a still-running terminal completes within 10 seconds.
- Retained output is replayed, or a truncation notice is available if the requested cursor is too old.
- Future cursors are treated as current and only new output streams.
- Closed terminals terminate or reap shell processes.
- Terminal lifecycle metadata persists.
- Terminal content is not written to SQLite or audit logs by default.

## 5. Validate Terminal Idle Timeout

### Steps

1. Configure terminal idle timeout to the default 60 minutes.
2. Create a terminal session and leave it inactive until timeout.
3. Repeat with boundary values of 30 and 120 minutes where practical.
4. Attempt to configure values outside 30–120 minutes.

### Expected Outcomes

- Idle terminal sessions time out according to configuration.
- Default timeout is 60 minutes.
- Values below 30 or above 120 minutes are rejected during configuration validation.
- Timed-out sessions leave no orphan shell processes.

## 6. Validate Configured Service Management

### Steps

1. Load a valid service configuration.
2. List configured services through the API/UI.
3. Start a harmless configured service.
4. Stop the service.
5. Repeat an already-satisfied start or stop action.
6. Request an unknown service ID.
7. Request an operation not listed in `allowed_operations`.

### Expected Outcomes

- Only configured services are visible.
- Service start/stop final state appears in the UI and durable history within 10 seconds.
- Repeated already-satisfied actions produce predictable results without duplicate unmanaged processes.
- Unknown services and unsupported operations are rejected without executing arbitrary commands.
- Service state transitions, operations, events, and failure reasons are durable and auditable.

## 7. Validate Configuration Rejection

### Steps

1. Provide malformed configuration.
2. Provide incomplete service definitions.
3. Provide unsafe or ambiguous service definitions.
4. Provide metrics remote exposure without required network allowlist.

### Expected Outcomes

- Invalid service configuration is rejected clearly.
- Invalid security configuration fails closed.
- Configuration load events record success or failure reasons without secrets.
- No service operations are available from rejected configuration.

## 8. Validate Metrics

### Steps

1. Enable metrics collection.
2. Scrape `/metrics` locally.
3. If remote exposure is configured, scrape from an allowed network source.
4. Attempt scrape from outside the configured network allowlist.
5. Parse metrics output with a Prometheus-compatible text parser.
6. Review metric labels for cardinality.

### Expected Outcomes

- `/metrics` returns Prometheus-compatible text format.
- Allowed scrapers can collect host, agent, service, and health-check metrics.
- Disallowed remote scrapers are rejected.
- Labels do not contain terminal session IDs, arbitrary commands, request IDs, source IPs, bearer credentials, or unbounded user input.
- SQLite does not store high-frequency metrics as a long-term time-series database.

## 9. Validate Persistence and Recovery

### Steps

1. Perform authentication, terminal lifecycle, service control, health-check, and configuration load actions.
2. Restart the agent.
3. Reboot the host or restart the test environment where practical.
4. Inspect durable state and audit records.

### Expected Outcomes

- Service state, service events, terminal metadata, configuration events, authentication events, authorization failures, agent lifecycle events, and audit records remain available after restart.
- Startup reconciles persisted state with actual host state.
- Audit records include timestamp, actor where available, source address where available, operation, target, result, and failure reason where applicable.
- Audit records, metrics, logs, and UI output exclude terminal contents, token values, passwords, private keys, and service environment secrets.

## 10. Validate Operator Lifecycle Documentation

### Steps

Follow documented workflows for:

- install
- configure
- validate configuration
- start
- stop
- restart
- status
- inspect logs
- upgrade
- uninstall
- recover from failed startup
- reset or migrate local state

### Expected Outcomes

- Each workflow is documented and executable on supported platforms.
- The systemd unit defines restart behavior, working directory, runtime user, environment behavior if used, log behavior, and dependency ordering.
- No workflow requires undocumented manual recovery steps for normal validation cases.

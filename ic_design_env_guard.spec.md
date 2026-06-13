# IC Design Environment Guard Specification

## 1. Purpose

IC Design Environment Guard is a Linux host agent that provides remote web control for engineering environments. It combines a browser-based terminal, local service management, and Prometheus-compatible monitoring in one local agent.

The first version should be a local web application served by the agent. Desktop packaging with PyQt, Electron, Tauri, or another wrapper can be added later without changing the core backend protocol.

## 2. Goals

- Provide a secure remote web terminal for Linux hosts.
- Start automatically on supported Linux distributions.
- Manage configured local services from a YAML or TOML file.
- Expose service state and host metrics through a Prometheus-compatible `/metrics` endpoint.
- Store service state, run history, health checks, and audit events locally.
- Keep metrics storage compatible with Prometheus instead of building a full custom TSDB in the first version.
- Serve a web interface for terminal access, service status, and links to monitoring tools.

## 3. Non-Goals For MVP

- Reimplement Prometheus, PromQL, alerting, or Grafana-style dashboards.
- Store all high-frequency metrics locally forever.
- Support Windows PTY behavior in the first version.
- Implement a full SSH server.
- Expose unauthenticated remote terminal access.
- Build a native desktop app before the local web app is stable.

## 4. Target Platforms

Supported Linux hosts:

- CentOS 7
- Red Hat Enterprise Linux 8
- Ubuntu 24.04

The agent should run as a `systemd` service. CentOS 7 has older system Python, so packaging should not rely on the OS Python version. Recommended packaging options are a self-contained Python runtime, a virtualenv installed by the agent installer, or a single-file binary produced by a packaging tool.

## 5. High-Level Architecture

```text
Linux Host
  └─ IC Design Environment Guard Agent
       ├─ FastAPI HTTP server
       ├─ Static web UI
       ├─ WebSocket terminal manager
       ├─ Service manager
       ├─ Metrics collector
       ├─ Prometheus exporter at /metrics
       ├─ SQLite state database
       ├─ Local config loader
       └─ systemd unit

Remote Client
  └─ Browser
       ├─ xterm.js terminal
       ├─ service list and controls
       ├─ logs/status views
       └─ link or integration point for Prometheus/Grafana
```

The backend exposes terminal, service management, metrics, and UI routes on the same port. Different route groups have different security requirements.

## 6. Recommended Technology Stack

Backend:

- Python 3.10 or newer
- FastAPI
- Uvicorn
- `websockets` through FastAPI WebSocket support
- `ptyprocess` or `pexpect` for PTY management on Linux
- `psutil` for host and process metrics
- `prometheus_client` for `/metrics`
- SQLite for service state and audit data
- SQLAlchemy, SQLModel, or raw SQLite with migrations
- Pydantic for config and API schemas

Frontend:

- Vite
- TypeScript
- React, SolidJS, or Vue
- `xterm.js`
- `@xterm/addon-fit`
- A lightweight charting library only if local status graphs are needed later

Deployment:

- `systemd`
- YAML or TOML config file
- Local SQLite database
- Optional nginx, Caddy, SSH tunnel, or VPN for remote exposure
- Optional Prometheus and Grafana for monitoring dashboards

## 7. Process And Deployment Layout

Recommended paths:

```text
/etc/ic-env-guard/config.yaml
/etc/ic-env-guard/authorized_keys
/etc/systemd/system/ic-env-guard.service
/usr/local/bin/ic-env-guard
/var/lib/ic-env-guard/state.db
/var/lib/ic-env-guard/runtime/
/var/log/ic-env-guard/
```

The systemd unit should:

- Start the agent on boot.
- Restart on failure.
- Run as a dedicated service user where possible.
- Restrict filesystem access where practical.
- Write stdout/stderr to journald.

The agent should default to binding `127.0.0.1`. Binding `0.0.0.0` must require explicit config.

## 8. Routing Model

All routes can share one port:

```text
GET  /                         Web UI
GET  /assets/*                 Web UI static assets
GET  /healthz                  Agent liveness
GET  /readyz                   Agent readiness

GET  /metrics                  Prometheus exporter

POST /api/auth/login           Token login or key-challenge login
POST /api/auth/challenge       Create public-key auth challenge
POST /api/auth/verify          Verify signed challenge
POST /api/auth/logout          Revoke current session

GET  /api/services             List configured services
GET  /api/services/{id}        Get service detail
POST /api/services/{id}/start  Start service
POST /api/services/{id}/stop   Stop service
POST /api/services/{id}/restart Restart service
GET  /api/services/{id}/events Service event history
GET  /api/services/{id}/logs   Recent service logs
WS   /ws/services/{id}/logs    Stream service logs

POST /api/terminals            Create terminal session
GET  /api/terminals            List active terminal sessions
GET  /api/terminals/{id}       Get terminal session metadata
GET  /api/terminals/{id}/history Get retained terminal output
POST /api/terminals/{id}/connect-token Create one-use WebSocket ticket
POST /api/terminals/{id}/resize Resize terminal PTY
DELETE /api/terminals/{id}     Close terminal session
WS   /ws/terminals/{id}        Terminal input/output stream with cursor replay
```

## 9. Security Model

The terminal and service-control APIs are high-risk remote execution surfaces. They must be authenticated from the first version.

### 9.1 Route-Level Access

Recommended authentication boundaries:

```text
/healthz     unauthenticated or local-only
/readyz      unauthenticated or local-only
/metrics     Prometheus bearer token, basic auth, mTLS, or network allowlist
/api/*       authenticated web session
/ws/*        short-lived WebSocket ticket or authenticated session token
/            authenticated if remote access is enabled
```

### 9.2 MVP Authentication

Use a generated bearer token for the first version:

```text
/var/lib/ic-env-guard/token
```

The token is used for browser login and API requests. It should be created at install time, file-readable only by the agent user and admin users, and rotated through a CLI command.

### 9.3 Public-Key Authentication

Public-key auth can be added after the token-based MVP.

The agent should not read or use user private keys. The safe model is:

```text
1. Agent reads allowed public keys from /etc/ic-env-guard/authorized_keys or ~/.ssh/authorized_keys.
2. Client requests a challenge.
3. Agent returns random nonce and challenge ID.
4. Client signs the challenge with its private key.
5. Agent verifies the signature against allowed public keys.
6. Agent issues a short-lived web session token.
```

Useful Python libraries:

- `cryptography`
- `paramiko`
- `asyncssh`

### 9.4 WebSocket Protection

Terminal WebSocket connections should use a short-lived connection ticket:

```text
1. Browser calls POST /api/terminals.
2. Browser calls POST /api/terminals/{id}/connect-token.
3. Agent issues a one-use token valid for about 60 seconds.
4. Browser connects to WS /ws/terminals/{id}?ticket=...
5. Agent consumes the ticket and opens the stream.
```

This avoids long-lived credentials in WebSocket URLs.

## 10. Terminal Design

The terminal flow should mirror the opencode-style architecture with xterm.js replacing ghostty-web.

```text
User keyboard input
  -> xterm.js onData
  -> WebSocket send
  -> FastAPI terminal websocket handler
  -> PTY process write
  -> shell

Shell output
  -> PTY process read
  -> WebSocket send
  -> xterm.write
  -> browser render
```

Terminal manager responsibilities:

- Create PTY sessions.
- Track terminal ID, title, process ID, shell command, rows, columns, owner, creation time, last activity, status, and output cursor.
- Forward browser input to the PTY.
- Forward PTY output to subscribed WebSocket clients.
- Resize PTY when the frontend terminal size changes.
- Close inactive sessions after a configurable timeout.
- Keep only bounded in-memory output buffers for reconnect support.
- Let authenticated clients list their active terminal sessions and switch between them.
- Replay retained output when a client reconnects to a running PTY session.

Terminal output should not be written unbounded into SQLite. If terminal audit logging is required, it should be explicitly configured and written to rotated files or a bounded audit store.

### 10.1 Terminal Session History And Reconnect

Terminal sessions should be independent of individual browser WebSocket connections. If the browser disconnects, the PTY should keep running until the user closes it, the shell exits, or the idle timeout expires.

Each terminal session should maintain a monotonic output cursor:

```text
Terminal output chunk
  -> append to bounded replay buffer
  -> increment output_cursor by chunk byte length or character length
  -> send chunk to attached WebSocket clients
```

Reconnect flow:

```text
1. Browser creates a terminal and receives terminal_id.
2. Browser connects to WS /ws/terminals/{id}?cursor=0&ticket=...
3. Agent streams retained output and then live output.
4. Browser stores the latest cursor for that terminal.
5. Browser disconnects or reloads.
6. Browser lists existing terminal sessions with GET /api/terminals.
7. Browser reconnects with the last cursor.
8. Agent replays retained output after that cursor if it is still available.
9. Agent resumes live streaming from the same PTY.
```

If the requested cursor is older than the retained buffer, the agent should return the newest retained tail and mark the replay as truncated. The frontend should show a non-blocking warning such as "Terminal history before this point was truncated." If the requested cursor is newer than the current cursor, the agent should treat it as current and only stream new output.

The replay buffer should be bounded per terminal. Recommended defaults:

```text
terminal_replay_buffer_bytes: 2 MiB to 10 MiB
terminal_idle_timeout_minutes: 30 to 120
terminal_exited_retention_minutes: 10 to 60
```

Exited terminals may remain visible as read-only history for the exited retention period. Running terminals should stay attachable until explicitly closed or timed out.

### 10.2 Terminal Context Switching

The web UI should treat terminals as tabs backed by server-side terminal sessions.

Frontend terminal state per tab:

```text
terminal_id
title
status
cursor
rows
cols
local xterm.js instance or serialized xterm buffer
last_connected_at
```

When switching tabs, the preferred behavior is to keep the xterm.js instance alive for open tabs so switching is instant. If the UI destroys an xterm.js instance to save memory, it should restore from a serialized xterm buffer if available and reconnect with the saved cursor. If no local buffer exists, it should ask the server for retained history.

Context switching flow:

```text
1. User selects a terminal tab.
2. UI shows the local xterm.js buffer if available.
3. UI ensures a WebSocket is attached to the selected terminal.
4. UI sends the saved cursor during attach.
5. Agent replays any missing retained output and then streams live output.
6. UI updates the selected terminal cursor as output arrives.
```

The MVP should enforce one authenticated owner per terminal session. Multiple browser tabs for the same user may attach to the same terminal, but cross-user shared terminal viewing should be a later explicit feature because it changes authorization and audit semantics.

### 10.3 Terminal History Storage Boundaries

The default terminal history model should be:

```text
SQLite:
  terminal metadata, state, owner, timestamps, status, cursor, close reason

Memory:
  bounded replay buffer for active/running terminals

Optional rotated files:
  explicit audit-mode terminal transcript retention
```

Raw terminal output should not be stored in SQLite by default. This avoids unbounded database growth and avoids turning the service state database into a log store.

## 11. Service Manager Design

Services are declared in a local config file.

Example config:

```yaml
server:
  bind: 127.0.0.1
  port: 8765

auth:
  mode: token
  token_file: /var/lib/ic-env-guard/token

metrics:
  enabled: true
  scrape_auth: bearer
  collect_interval_seconds: 10

services:
  - id: api-server
    name: API Server
    command: python app.py
    cwd: /opt/myapp
    env:
      PORT: "8000"
    autostart: true
    restart: on-failure
    healthcheck:
      type: http
      url: http://127.0.0.1:8000/health
      interval_seconds: 10
      timeout_seconds: 2

  - id: worker
    name: Background Worker
    command: python worker.py
    cwd: /opt/myapp
    autostart: true
    restart: always
```

Service manager responsibilities:

- Load and validate config on startup.
- Start `autostart` services.
- Start, stop, and restart configured services through API calls.
- Track PID, status, start time, exit time, exit code, restart count, and health status.
- Enforce restart policy.
- Capture service stdout/stderr to rotated logs.
- Persist service state transitions and health check results.
- Publish service state to API and metrics collector.

Supported restart policies for MVP:

```text
never
on-failure
always
```

## 12. Metrics Design

The agent should expose standard Prometheus metrics at `/metrics`. Prometheus should own long-term metrics storage.

Metrics collector responsibilities:

- Collect host CPU, memory, disk, and network metrics using `psutil`.
- Collect agent process metrics.
- Collect managed service status and restart counters.
- Collect health check success and latency.
- Update in-memory `prometheus_client` gauges, counters, and histograms.
- Serve `/metrics` quickly without doing expensive synchronous work during scrape.

Recommended metrics:

```text
ic_env_guard_build_info{version="..."} 1
ic_env_guard_websocket_connections
ic_env_guard_terminal_sessions
ic_env_guard_api_requests_total

ic_env_guard_host_cpu_percent
ic_env_guard_host_memory_used_bytes
ic_env_guard_host_memory_total_bytes
ic_env_guard_host_disk_used_bytes{mount="/"}
ic_env_guard_host_disk_total_bytes{mount="/"}
ic_env_guard_host_network_rx_bytes_total{interface="eth0"}
ic_env_guard_host_network_tx_bytes_total{interface="eth0"}

ic_env_guard_service_up{service="api-server"}
ic_env_guard_service_restart_total{service="api-server"}
ic_env_guard_service_start_time_seconds{service="api-server"}
ic_env_guard_service_healthcheck_success{service="api-server"}
ic_env_guard_service_healthcheck_latency_seconds{service="api-server"}
```

Avoid high-cardinality labels such as terminal session IDs, arbitrary commands, user-provided strings, raw paths, or request IDs.

## 13. Storage Design

Use SQLite for service state, events, health check history, and audit logs. Use Prometheus or another TSDB for high-frequency time series.

### 13.1 SQLite Responsibilities

SQLite should store:

- Service definitions snapshot from config.
- Current service state.
- Service run history.
- Service start/stop/restart events.
- Health check results at bounded retention.
- Terminal metadata and lifecycle state.
- User/API audit logs.
- Agent lifecycle events.

SQLite should not store:

- Unlimited terminal output.
- High-frequency host metrics forever.
- Unbounded service logs.

### 13.2 Suggested Tables

```text
services
  id text primary key
  name text not null
  command text not null
  cwd text
  autostart integer not null
  restart_policy text not null
  config_hash text not null
  updated_at integer not null

service_state
  service_id text primary key
  status text not null
  pid integer
  started_at integer
  stopped_at integer
  exit_code integer
  restart_count integer not null
  health_status text
  health_latency_ms integer
  last_error text
  updated_at integer not null

service_runs
  id integer primary key autoincrement
  service_id text not null
  pid integer
  started_at integer not null
  stopped_at integer
  exit_code integer
  stop_reason text

service_events
  id integer primary key autoincrement
  service_id text not null
  event_type text not null
  message text
  metadata_json text
  created_at integer not null

healthcheck_results
  id integer primary key autoincrement
  service_id text not null
  success integer not null
  latency_ms integer
  status_code integer
  error text
  created_at integer not null

terminal_sessions
  id text primary key
  owner text not null
  title text not null
  command text not null
  cwd text
  pid integer
  rows integer
  cols integer
  status text not null
  output_cursor integer not null
  replay_buffer_start_cursor integer not null
  created_at integer not null
  last_active_at integer not null
  exited_at integer
  closed_at integer
  close_reason text

audit_logs
  id integer primary key autoincrement
  actor text
  action text not null
  target_type text not null
  target_id text
  remote_addr text
  success integer not null
  message text
  created_at integer not null
```

SQLite should use WAL mode. Retention jobs should delete old health check results and events according to config.

### 13.3 Time Series Options

Recommended MVP:

```text
Prometheus scrapes /metrics and owns historical time series.
SQLite stores service state, service events, terminal metadata, and audit records.
```

Optional later modes:

- `sqlite_rollup`: store low-frequency aggregate samples, such as one row per minute, with short retention.
- `victoriametrics`: run VictoriaMetrics single-node for local Prometheus-compatible storage.
- `remote_write`: send metrics to a central Prometheus-compatible backend.

VictoriaMetrics single-node is the preferred embedded-style TSDB option if local time-series storage becomes necessary. It is more appropriate than forcing SQLite to behave like a high-frequency TSDB.

## 14. Web UI Design

MVP pages:

- Login page.
- Host overview page.
- Service list page.
- Service detail page with status, recent events, and controls.
- Terminal page with one or more xterm.js terminal tabs.
- Metrics page with `/metrics` endpoint info and Prometheus/Grafana connection guidance.

The UI should not attempt to replace Prometheus dashboards in the first version. It should show current state and operational controls, then link users to Prometheus or Grafana for monitoring dashboards.

## 15. API And Event Semantics

Service status values:

```text
configured
starting
running
stopping
exited
failed
unknown
```

Health status values:

```text
unknown
healthy
unhealthy
disabled
```

Terminal WebSocket messages can be plain text for MVP:

- Browser to server: raw terminal input text.
- Server to browser: raw terminal output text.

Control messages can be added later if needed using JSON envelopes or binary control frames. Resize should be an HTTP API call in the MVP to keep terminal WebSocket payloads simple.

Terminal status values:

```text
running
exited
closed
timed_out
unknown
```

Terminal history responses should include enough metadata for the UI to know whether replay was complete:

```text
terminal_id
from_cursor
to_cursor
buffer_start_cursor
truncated
status
output
```

## 16. Operational Safety

- Default bind address should be `127.0.0.1`.
- Remote bind should require explicit config.
- All terminal and service-control routes must require auth.
- Config file permissions should prevent unprivileged edits.
- Service commands should only come from local config, not arbitrary API payloads.
- API should support start/stop/restart only for configured services.
- Audit logs should record user, remote address, action, target, success, and timestamp.
- Service logs should be rotated.
- Terminal sessions should have idle timeouts.
- Metrics labels must avoid high cardinality.

## 17. Implementation Phases

### Phase 1: Local Web Terminal

- FastAPI app.
- Static web UI served by backend.
- xterm.js frontend.
- PTY create, resize, stream, and close APIs.
- Terminal listing, tab switching, cursor replay, and reconnect support.
- SQLite terminal metadata persistence.
- Token authentication.
- Local-only bind by default.

### Phase 2: systemd And Packaging

- Installer or setup script.
- systemd unit.
- Config, log, and data directories.
- Token generation.
- Basic health endpoints.

### Phase 3: Service Manager

- YAML/TOML config loading.
- Service start/stop/restart.
- Autostart.
- Restart policy.
- Rotated logs.
- SQLite service state and event persistence.
- Service UI pages.

### Phase 4: Metrics Exporter

- `prometheus_client` integration.
- Host metrics from `psutil`.
- Service status metrics.
- Health check metrics.
- `/metrics` auth mode.
- Prometheus scrape documentation.

### Phase 5: Public-Key Auth

- Challenge-response API.
- Authorized public key parsing.
- Signature verification.
- Short-lived sessions.
- Audit logging.

### Phase 6: Monitoring Extensions

- Grafana dashboard examples.
- Optional VictoriaMetrics integration.
- Optional low-frequency SQLite rollups.
- Optional custom metrics dashboard in the web UI.

### Phase 7: Desktop Wrapper

- PyQt, Electron, or Tauri wrapper.
- Wrapper starts or connects to the local agent.
- Wrapper loads the same web UI.
- No backend protocol changes required.

## 18. Success Criteria

MVP is successful when:

- Agent starts automatically under systemd on target Linux distributions.
- Browser can connect to the local web UI after authentication.
- User can open a terminal, run shell commands, resize terminal, and close the session.
- User can disconnect and reconnect to a still-running terminal and receive retained output history.
- User can switch between multiple terminal tabs and restore each terminal context.
- Agent can start and stop at least one configured service.
- Service state persists across agent restart.
- `/metrics` returns Prometheus-compatible text format.
- Prometheus can scrape the agent.
- SQLite contains service state, service events, terminal metadata, and audit logs.
- Terminal and service-control routes reject unauthenticated access.

## 19. Open Decisions

- Choose frontend framework: React, SolidJS, or Vue.
- Choose config format: YAML or TOML.
- Choose SQLite access layer: SQLAlchemy, SQLModel, or raw SQLite.
- Choose MVP packaging method for CentOS 7 compatibility.
- Decide whether `/metrics` uses bearer token, basic auth, mTLS, or network allowlist in the first release.

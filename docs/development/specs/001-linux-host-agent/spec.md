# Feature Specification: IC Design Environment Guard

**Feature Branch**: `001-linux-host-agent`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "@ic_design_env_guard.spec.md"

## Clarifications

### Session 2026-06-12

- Q: Which human permission model should the MVP use? → A: Single authenticated local administrator role can use terminal, service control, logs/status, and metrics guidance.
- Q: Which authentication method should the MVP require? → A: Generated local bearer token for MVP; public-key authentication is post-MVP.
- Q: What network exposure model should the MVP use? → A: Default bind is local-only; remote bind requires explicit config plus valid authentication settings.
- Q: What access model should remote metrics exposure use? → A: Metrics rely only on network allowlist when exposed remotely.
- Q: What terminal idle timeout should the MVP use? → A: Configurable 30–120 minutes, default 60 minutes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Secure Browser Terminal (Priority: P1)

An authenticated local administrator accesses a Linux host through a browser, opens one or more terminal sessions, runs operational shell commands, resizes the terminal, switches between terminal tabs, disconnects, reconnects, and closes sessions without leaving orphaned processes behind.

**Why this priority**: The remote terminal is the highest-value and highest-risk capability. It must prove secure access control, explicit lifecycle management, and reliable reconnect behavior before broader service-management features depend on the agent.

**Independent Test**: Start the agent on a supported Linux host, authenticate as a local administrator, open a terminal, run a harmless command, resize the terminal, disconnect and reconnect, verify retained output is replayed or clearly marked truncated, then close the session and confirm the shell process ends.

**Acceptance Scenarios**:

1. **Given** the agent is running with valid security configuration, **When** an unauthenticated user attempts to create or connect to a terminal session, **Then** access is rejected and a security-relevant audit event is recorded without terminal content.
2. **Given** an authenticated local administrator has an active terminal session, **When** the browser disconnects and reconnects before the session exits or times out, **Then** the administrator can reattach to the same session and receive retained output or a clear truncation notice.
3. **Given** an authenticated local administrator closes a terminal session, **When** the close request completes, **Then** the server-side session is marked closed and the associated shell process is terminated or reaped.
4. **Given** an inactive terminal exceeds the configured idle timeout, **When** cleanup runs, **Then** the session is terminated, marked timed out, and no orphan shell remains.

---

### User Story 2 - Installable Linux Host Agent (Priority: P2)

An administrator installs the agent on supported Linux hosts, configures it to start automatically, checks health and readiness, inspects logs, restarts the service, upgrades it, and uninstalls it using documented administrative workflows.

**Why this priority**: The product must be operationally reliable on conservative engineering Linux hosts before service management and monitoring can be trusted.

**Independent Test**: On each supported platform, install the agent using the documented workflow, start it automatically under the host service manager, verify health/readiness, restart it, inspect logs, and uninstall or recover from a failed startup.

**Acceptance Scenarios**:

1. **Given** a supported Linux host, **When** an administrator installs and enables the agent, **Then** it starts on boot and exposes health and readiness status according to documented access rules.
2. **Given** the agent service is running, **When** an administrator restarts it, **Then** it shuts down cleanly, starts again, and reconciles persisted state with actual host state.
3. **Given** security or service configuration is invalid, **When** the agent starts or reloads configuration, **Then** it fails closed or clearly rejects the invalid configuration with actionable diagnostics.

---

### User Story 3 - Configured Service Control (Priority: P3)

An authenticated local administrator views locally configured services, checks their current status and recent history, starts or stops allowed services, and sees predictable outcomes for repeated requests.

**Why this priority**: Controlled service management is the second major operational capability and must be constrained to configured services rather than becoming an arbitrary command runner.

**Independent Test**: Configure a harmless local test service, authenticate as a local administrator, list services, start the service, confirm status and health, stop it, repeat start/stop requests, and verify events are persisted and audited.

**Acceptance Scenarios**:

1. **Given** a valid local service configuration, **When** an authenticated local administrator lists services, **Then** only explicitly configured services are visible.
2. **Given** an authenticated local administrator requests start, stop, or restart for a configured service, **When** the operation completes or times out, **Then** the result is predictable, auditable, and reflected in service status.
3. **Given** a request names an unknown service or unsupported operation, **When** the agent handles the request, **Then** it rejects the request without executing arbitrary commands.
4. **Given** the agent restarts after a service operation, **When** it starts again, **Then** it reconciles persisted service state with actual running state.

---

### User Story 4 - Prometheus-Compatible Host and Service Metrics (Priority: P4)

A local administrator connects existing monitoring tools to the agent to collect host and managed-service metrics, while the agent avoids becoming a custom long-term metrics store.

**Why this priority**: Observability is required for operational use, but compatibility with existing monitoring tools is more valuable than building dashboards or time-series storage into the MVP.

**Independent Test**: Configure metrics access, scrape the metrics endpoint with a Prometheus-compatible client, verify host and service metrics are returned in the expected text format, and confirm labels avoid unbounded values.

**Acceptance Scenarios**:

1. **Given** metrics collection is enabled, **When** an authorized scraper requests metrics, **Then** the response uses Prometheus-compatible text format and includes host, agent, service, and health indicators.
2. **Given** metrics are exposed beyond localhost, **When** a request comes from outside the configured network allowlist, **Then** access is denied according to the documented metrics access model.
3. **Given** metrics include labels, **When** they are reviewed, **Then** labels are bounded and do not include terminal session IDs, arbitrary commands, request IDs, or unbounded user input.

---

### User Story 5 - Local State and Audit Trail (Priority: P5)

An administrator can review durable local records for service state, service events, health checks, terminal lifecycle, authentication events, authorization failures, agent lifecycle, and security-relevant errors without exposing secrets or terminal content.

**Why this priority**: Engineering host operations need traceability and recovery, but audit storage must avoid becoming a secret or transcript leak.

**Independent Test**: Perform authentication, terminal lifecycle, service-control, health-check, and restart actions; restart the agent; verify required state and audit records persist and exclude secrets and terminal contents.

**Acceptance Scenarios**:

1. **Given** an authenticated local administrator performs a privileged action, **When** the action succeeds or fails, **Then** an audit event records timestamp, actor where available, source address where available, operation, target, result, and failure reason where applicable.
2. **Given** the agent restarts or the host reboots, **When** the agent starts again, **Then** it uses durable state to recover history and reconciles that state with the real host.
3. **Given** terminal sessions produce output, **When** audit and state records are inspected, **Then** terminal content, passwords, tokens, private keys, and other secrets are not stored by default.

### Edge Cases

- The agent is configured to bind remotely without explicit remote-bind configuration or valid authentication settings.
- A browser disconnects while a terminal command is still running.
- A terminal reconnect requests output older than the retained replay buffer.
- A terminal reconnect provides a cursor newer than the current session cursor.
- A service exits while a start, stop, restart, or health-check operation is in progress.
- A service operation is requested repeatedly while the service is already in the target state.
- The configuration file is missing, unreadable, malformed, incomplete, or attempts to define unsafe service commands.
- The local state store is temporarily locked, corrupted, missing, or requires migration.
- The host reboots while managed services or terminal sessions are active.
- The metrics endpoint is scraped frequently or from outside the configured network allowlist.
- Logs or audit events would otherwise include sensitive credentials, terminal contents, or private material.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a local web application for authenticated local administrators to access terminal sessions, service status, service controls, metrics guidance, and operational diagnostics.
- **FR-002**: The system MUST authenticate users with a generated local bearer token before allowing access to terminal sessions, service-control actions, service logs, configuration-derived state, or privileged operational data.
- **FR-003**: The system MUST treat public-key challenge authentication as post-MVP and MUST NOT require it for the MVP authentication flow.
- **FR-004**: The system MUST deny unauthenticated terminal and service-control access in development, test, demo, and release modes.
- **FR-005**: The system MUST classify exposed routes or views by risk level, including public health/status, authenticated UI, authenticated service status, privileged service control, privileged terminal access, and metrics access.
- **FR-006**: The system MUST support administrative workflows to create, attach to, resize, switch between, reconnect to, and close browser terminal sessions.
- **FR-007**: The system MUST track each terminal session with owner, creation time, last activity time, process identifier, status, output cursor, and close or timeout reason.
- **FR-008**: The system MUST enforce terminal lifecycle cleanup, including clean close, forced termination, browser disconnect handling, orphan-process prevention, and a configurable idle timeout from 30 to 120 minutes with a 60-minute default.
- **FR-009**: The system MUST retain only bounded terminal replay output for reconnect support and MUST indicate when requested terminal history was truncated.
- **FR-010**: The system MUST persist terminal metadata and lifecycle events while excluding terminal content from durable storage by default.
- **FR-011**: The system MUST run as an automatically managed host service on CentOS 7, Red Hat Enterprise Linux 8, and Ubuntu 24.04.
- **FR-012**: The system MUST provide documented administrative workflows for install, configure, validate configuration, start, stop, restart, status, log inspection, upgrade, uninstall, failed-startup recovery, and local-state migration or reset.
- **FR-013**: The system MUST package or install a controlled runtime so operation does not depend on a modern system-provided Python on supported hosts.
- **FR-014**: The system MUST default to local-only network binding; remote binding MUST require explicit remote-bind configuration plus valid authentication settings before startup succeeds.
- **FR-015**: The system MUST load managed-service definitions only from a local human-readable configuration file suitable for version control.
- **FR-016**: The system MUST reject ambiguous, incomplete, malformed, or unsafe service definitions at startup or configuration reload with actionable diagnostics.
- **FR-017**: Each managed-service definition MUST declare identity, allowed operations, command or host-service mapping, health-check behavior, timeout behavior, and log/status collection rules.
- **FR-018**: The system MUST allow authenticated local administrators to list configured services, view service details, inspect recent events/log status, and request start, stop, restart, status, and health-check actions only for configured services.
- **FR-019**: The system MUST NOT accept arbitrary shell commands through the service-management interface.
- **FR-020**: Service actions MUST be idempotent where possible and MUST return predictable results for repeated requests.
- **FR-021**: The system MUST record service state transitions, run history, health-check results, service-control actions, and failure reasons in durable local state.
- **FR-022**: The system MUST expose host, agent, managed-service, and health-check metrics in Prometheus-compatible text format.
- **FR-023**: The system MUST document the metrics access model; when metrics are exposed beyond localhost, access MUST rely on an explicit network allowlist rather than user login credentials.
- **FR-024**: Metrics MUST use documented names, labels, and units with bounded cardinality and MUST avoid unbounded user input, per-command, per-session, request ID, or arbitrary path labels.
- **FR-025**: The system MUST NOT implement a custom Prometheus replacement, query language, alerting engine, Grafana-style dashboard, or high-frequency long-term metrics store in the MVP.
- **FR-026**: The system MUST persist service state, run history, health-check results, configuration load events, authentication events, authorization failures, terminal lifecycle events, service-control actions, agent lifecycle events, and security-relevant errors in migration-managed local durable state.
- **FR-027**: Audit events MUST include timestamp, actor identity where available, source address where available, operation, target resource, result, and failure reason where applicable.
- **FR-028**: Audit, log, metrics, and UI output MUST avoid exposing sensitive secrets, terminal contents, passwords, tokens, and private keys.
- **FR-029**: On startup after agent restart or host reboot, the system MUST reconcile persisted state with actual host state instead of blindly trusting stale records.
- **FR-030**: The system MUST fail closed when security configuration is invalid and fail clearly when service configuration is invalid.
- **FR-031**: The MVP MUST remain a local web application served by the host agent and MUST NOT include a native desktop wrapper, full SSH server, Windows PTY support, unrestricted remote command execution, cloud control plane dependency, or multi-host orchestration.

### Key Entities *(include if feature involves data)*

- **Local Administrator**: An authenticated person using the web interface to inspect host status, manage terminal sessions, control configured services, review operational state, and access metrics guidance for one local host. The MVP has no separate operator or read-only viewer roles.
- **Agent**: The local host process that serves the web application, enforces access rules, manages terminal sessions, manages configured services, exports metrics, and persists state.
- **Terminal Session**: A server-owned PTY lifecycle record with owner, process identity, status, cursor, retained replay metadata, timestamps, and close or timeout reason.
- **Managed Service**: A configured local service entry with identity, allowed operations, command or host-service mapping, health-check rules, timeout rules, log/status collection rules, and current state.
- **Service Operation**: An auditable start, stop, restart, status, or health-check request against a configured service.
- **Health Check Result**: A bounded-retention observation of a managed service's health outcome, latency, timestamp, and failure information where applicable.
- **Metrics Exposure**: A read-only operational view intended for Prometheus-compatible scraping with bounded labels and documented access rules.
- **Audit Event**: A durable security or operations record that captures actor, source, operation, target, result, timestamp, and failure reason where available, without secrets or terminal content.
- **Configuration Load Event**: A record of successful or failed configuration load or reload, including validation outcome and failure reason where applicable.
- **Local State Store**: The durable local store for service state, run history, health checks, terminal metadata, audit events, migrations, and recovery information.

## Constitutional Constraints *(mandatory)*

- **Security / Access Control**: This feature exposes terminal access, service control, logs, host data, metrics, and configuration-derived state. The MVP uses a single authenticated local administrator role and generated local bearer token for terminal access, service control, logs/status, and metrics guidance. Public-key challenge authentication is post-MVP. Invalid security configuration must fail closed. Terminal contents and secrets must be excluded from logs, audit events, metrics, UI output, and durable state by default.
- **Linux / systemd Operations**: The MVP targets CentOS 7, Red Hat Enterprise Linux 8, and Ubuntu 24.04. The agent must run as a managed host service with documented lifecycle operations and a controlled runtime that does not depend on a modern system Python.
- **Configured Services Only**: The service-management feature is limited to explicitly configured services in a local human-readable configuration file. Service definitions must include identity, allowed operations, command or host-service mapping, health checks, timeouts, and log/status collection rules. The API must not become a general remote command runner.
- **Prometheus Observability**: Metrics must be Prometheus-compatible, documented, and protected according to the metrics access model. When exposed beyond localhost, metrics access relies on an explicit network allowlist rather than user login credentials. Labels must have bounded cardinality. Long-term high-frequency metrics storage belongs to existing monitoring tools, not the MVP local state store.
- **Local State and Audit**: Durable local state must be migration-managed and must record service state, run history, health checks, configuration events, authentication and authorization events, service-control actions, terminal lifecycle events, agent lifecycle events, and security-relevant errors. Startup must reconcile persisted state with the actual host.
- **Browser Terminal Safety**: Terminal sessions must have explicit server-side ownership, lifecycle tracking, idle timeouts, forced termination, disconnect cleanup, reconnect behavior, and orphan-process prevention. WebSocket transport concerns must remain separate from unrelated service-management or metrics behavior.
- **MVP Scope Boundary**: The MVP excludes custom SSH, a custom time-series database, PromQL, alerting, Grafana-style dashboards, Windows PTY support, native desktop packaging, unrestricted remote command execution, cloud control plane dependency, and multi-host orchestration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On each supported Linux platform, an administrator can install, start, restart, check status, inspect logs, and uninstall the agent using documented workflows with no undocumented manual recovery steps.
- **SC-002**: 100% of unauthenticated attempts to access terminal creation, terminal connection, and service-control actions are rejected during validation.
- **SC-003**: An authenticated local administrator can open, resize, close, and cleanly terminate a terminal session in under 2 minutes during an end-to-end validation run.
- **SC-004**: After an unplanned browser disconnect, an authenticated local administrator can reconnect to a still-running terminal and recover retained output or a clear truncation notice within 10 seconds.
- **SC-005**: Idle terminal sessions time out according to the configured 30–120 minute range with a 60-minute default, and idle or explicitly closed sessions leave no orphan shell processes after cleanup completes.
- **SC-006**: An authenticated local administrator can list configured services and start or stop at least one configured test service, with the final state reflected in the UI and durable history within 10 seconds.
- **SC-007**: Repeating an already-satisfied service action produces a predictable result and records an auditable outcome without launching duplicate unmanaged processes.
- **SC-008**: After agent restart, service state, service events, terminal metadata, and audit events remain available and are reconciled with actual host state.
- **SC-009**: A Prometheus-compatible scraper from an allowed network can successfully collect metrics from the agent, and the returned metrics pass text-format validation.
- **SC-010**: Metrics review confirms zero labels contain terminal session IDs, arbitrary commands, request IDs, or unbounded user input values.
- **SC-011**: Audit review confirms required privileged actions include actor where available, source address where available, operation, target, result, timestamp, and failure reason where applicable.
- **SC-012**: Security review confirms terminal contents, passwords, bearer credentials, private keys, and other secrets are absent from audit records, metrics, durable state, and default logs.
- **SC-013**: Metrics requests from outside the configured network allowlist are rejected when metrics are exposed beyond localhost.

## Assumptions

- The primary user is a local administrator responsible for an individual Linux host in an IC design environment.
- The first feature slice is the MVP local host agent and browser UI; desktop wrappers, public-key authentication, local metrics rollups, embedded time-series storage, and monitoring-dashboard examples are post-MVP unless separately specified.
- The MVP authentication model uses a generated local bearer token suitable for browser login and API access; public-key challenge authentication is post-MVP.
- Remote exposure is optional and must be explicitly configured; local-only binding is the default, and remote binding requires valid authentication settings before startup succeeds.
- Existing Prometheus-compatible monitoring tools are available when long-term metrics storage, dashboards, or alerting are needed. Remote metrics exposure uses a configured network allowlist rather than user login credentials.
- Service logs and terminal replay buffers are bounded; terminal idle timeout is configurable from 30 to 120 minutes with a 60-minute default; long-term unbounded log or terminal transcript retention is not part of the default MVP.
- Configuration format, persistence access layer, frontend framework, and packaging mechanism will be selected during planning as long as they satisfy the constitution and user-visible behavior in this specification.

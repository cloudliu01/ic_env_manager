# Tasks: IC Design Environment Guard

**Input**: Design documents from `/specs/001-linux-host-agent/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Contract and integration tests are REQUIRED for externally visible interfaces and constitutionally relevant behavior. Unit tests are included where they clarify core logic.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. Complete Phase 1 and Phase 2 before starting user story work.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete tasks.
- **[Story]**: User story label, required only in user story phases.
- Every task includes an exact file path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the repository structure, dependency manifests, and baseline tooling needed by every story.

- [X] T001 Create backend package directories in backend/ic_env_guard/ including api/, auth/, config/, db/, metrics/, services/, terminal/, and systemd/
- [X] T002 Create backend test directories in backend/tests/contract/, backend/tests/integration/, and backend/tests/unit/
- [X] T003 Create frontend application directories in frontend/src/api/, frontend/src/auth/, frontend/src/components/, frontend/src/pages/, frontend/src/terminal/, and frontend/src/styles/
- [X] T004 Create packaging directories in packaging/systemd/, packaging/install/, and packaging/runtime/
- [X] T005 Create operations documentation directory in docs/operations/
- [X] T006 Create Python project manifest with FastAPI, Uvicorn, ptyprocess, psutil, prometheus_client, Pydantic, SQLAlchemy, Alembic, PyYAML, pytest, pytest-asyncio, and httpx dependencies in backend/pyproject.toml
- [X] T007 Create backend pytest configuration with contract, integration, unit, and security markers in backend/pytest.ini
- [X] T008 Create frontend package manifest with Vite, TypeScript, React, xterm.js, @xterm/addon-fit, lint, and test scripts in frontend/package.json
- [X] T009 Create TypeScript and Vite configuration files in frontend/tsconfig.json and frontend/vite.config.ts
- [X] T010 Create root development guide for using Conda venv312 and project commands in README.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared security, configuration, persistence, diagnostics, and application infrastructure that every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Foundational Tests

- [X] T011 [P] Create contract test that rejects unauthenticated privileged API requests in backend/tests/contract/test_auth_required.py
- [X] T012 [P] Create contract tests for /api/auth/login and /api/auth/logout success/failure in backend/tests/contract/test_auth_login_logout_contract.py
- [X] T013 [P] Create unit tests for bearer token loading, file permission validation, and token redaction in backend/tests/unit/test_auth_token.py
- [X] T014 [P] Create unit tests for route risk classification and fail-closed security config validation in backend/tests/unit/test_security_config.py
- [X] T015 [P] Create unit tests for audit event secret redaction in backend/tests/unit/test_audit_redaction.py
- [X] T016 [P] Create migration test for empty, current, and failed SQLite schema states in backend/tests/integration/test_migrations.py

### Foundational Implementation

- [X] T017 Create FastAPI app factory and route mounting skeleton in backend/ic_env_guard/main.py
- [X] T018 Create shared API error response models and exception handlers in backend/ic_env_guard/api/errors.py
- [X] T019 Create route risk classification constants and helpers in backend/ic_env_guard/api/risk.py
- [X] T020 Create bearer token configuration and validation models in backend/ic_env_guard/auth/token.py
- [X] T021 Create FastAPI authentication dependency for generated local bearer token in backend/ic_env_guard/auth/dependencies.py
- [X] T022 Implement auth login/logout HTTP routes in backend/ic_env_guard/api/auth.py
- [X] T023 Create application configuration root models for server, auth, metrics, terminal, and services in backend/ic_env_guard/config/models.py
- [X] T024 Create YAML configuration loader with safe parsing and actionable validation errors in backend/ic_env_guard/config/loader.py
- [X] T025 Create SQLite engine/session setup with WAL mode in backend/ic_env_guard/db/session.py
- [X] T026 Create initial migration with schema version, local administrator, agent lifecycle, configuration load, audit, terminal, service, health-check, metrics exposure, and migration tables in backend/migrations/0001_initial.py
- [X] T027 Create repository base helpers for bounded text fields and secret-safe persistence in backend/ic_env_guard/db/repositories.py
- [X] T028 Create audit event model and repository with secret redaction in backend/ic_env_guard/db/audit.py
- [X] T029 Create agent lifecycle and configuration load event repositories in backend/ic_env_guard/db/agent_state.py
- [X] T030 Create startup validation flow that fails closed on invalid security config in backend/ic_env_guard/main.py
- [X] T031 Create static UI serving hook placeholder for built frontend assets in backend/ic_env_guard/api/static.py
- [X] T032 Create shared frontend API client with bearer token injection and error handling in frontend/src/api/client.ts
- [X] T033 [P] Create frontend auth API wrapper in frontend/src/api/auth.ts
- [X] T034 [P] Create frontend session state helper in frontend/src/auth/session.ts
- [X] T035 Create login page and protected app shell in frontend/src/pages/LoginPage.tsx and frontend/src/pages/AppRoutes.tsx

**Checkpoint**: Foundation ready - user story implementation can now begin in priority order or in parallel where dependencies allow.

---

## Phase 3: User Story 1 - Secure Browser Terminal (Priority: P1) 🎯 MVP

**Goal**: A local administrator can securely create, use, resize, reconnect to, switch between, and close browser terminal sessions without orphan processes or durable terminal content storage.

**Independent Test**: Start the agent, authenticate, create a terminal, connect through WebSocket ticket, run a harmless command, resize, disconnect/reconnect with cursor replay, close the session, and verify no orphan shell process remains.

### Tests for User Story 1 (REQUIRED) ⚠️

- [X] T036 [P] [US1] Create HTTP contract tests for terminal create/list/detail/history/connect-token/resize/close endpoints in backend/tests/contract/test_terminal_http_contract.py
- [X] T037 [P] [US1] Create WebSocket contract tests for ticket validation, cursor replay, and raw text stream behavior in backend/tests/contract/test_terminal_websocket_contract.py
- [X] T038 [P] [US1] Create integration test for PTY create, command output, resize, close, and process reaping in backend/tests/integration/test_terminal_lifecycle.py
- [X] T039 [P] [US1] Create integration test for browser disconnect, reconnect, retained output replay, truncated replay, and future cursor handling in backend/tests/integration/test_terminal_reconnect.py
- [X] T040 [P] [US1] Create security test confirming terminal input/output is absent from SQLite audit records and logs in backend/tests/integration/test_terminal_secret_exclusion.py
- [X] T041 [P] [US1] Create frontend terminal tab interaction tests in frontend/tests/terminal-tabs.test.tsx

### Implementation for User Story 1

- [X] T042 [P] [US1] Create TerminalSession persistence model and repository in backend/ic_env_guard/db/terminal_sessions.py
- [X] T043 [P] [US1] Create bounded replay buffer with cursor and truncation metadata in backend/ic_env_guard/terminal/replay_buffer.py
- [X] T044 [US1] Create PTY session manager with create, write, read, resize, close, timeout, and reap behavior in backend/ic_env_guard/terminal/manager.py
- [X] T045 [US1] Create one-use WebSocket ticket issuer and consumer in backend/ic_env_guard/terminal/tickets.py
- [X] T046 [US1] Implement terminal HTTP routes in backend/ic_env_guard/api/terminals.py
- [X] T047 [US1] Implement terminal WebSocket route separated from PTY manager logic in backend/ic_env_guard/api/terminal_ws.py
- [X] T048 [US1] Add terminal lifecycle audit events without terminal content in backend/ic_env_guard/terminal/audit.py
- [X] T049 [US1] Wire terminal routes into FastAPI app in backend/ic_env_guard/main.py
- [X] T050 [P] [US1] Create frontend terminal API wrapper in frontend/src/api/terminals.ts
- [X] T051 [P] [US1] Create xterm.js terminal component with resize support in frontend/src/terminal/TerminalPane.tsx
- [X] T052 [US1] Create terminal tab page with create/switch/reconnect/close flows in frontend/src/pages/TerminalPage.tsx
- [X] T053 [US1] Add terminal route and navigation entry in frontend/src/pages/AppRoutes.tsx

**Checkpoint**: User Story 1 is independently functional and validates the highest-risk browser terminal workflow.

---

## Phase 4: User Story 2 - Installable Linux Host Agent (Priority: P2)

**Goal**: An administrator can install, configure, start, stop, restart, inspect logs, upgrade, uninstall, and recover the agent under systemd on supported Linux platforms without relying on modern system Python.

**Independent Test**: On each supported platform or representative test environment, install the agent, enable/start it under systemd, verify health/readiness, inspect logs, restart it, validate failed-startup diagnostics, and uninstall or recover.

### Tests for User Story 2 (REQUIRED) ⚠️

- [X] T054 [P] [US2] Create contract tests for /healthz and /readyz responses in backend/tests/contract/test_health_readiness_contract.py
- [X] T055 [P] [US2] Create integration test for valid startup, invalid security config fail-closed startup, and invalid service config diagnostics in backend/tests/integration/test_agent_startup.py
- [X] T056 [P] [US2] Create packaging smoke test for generated token file permissions and controlled runtime detection in backend/tests/integration/test_packaging_runtime.py
- [X] T057 [P] [US2] Create systemd unit validation test for user, working directory, restart policy, logs, and dependency ordering in backend/tests/integration/test_systemd_unit.py

### Implementation for User Story 2

- [X] T058 [US2] Implement /healthz and /readyz routes with readiness diagnostics in backend/ic_env_guard/api/health.py
- [X] T059 [US2] Add health/readiness routes to app factory in backend/ic_env_guard/main.py
- [X] T060 [US2] Create systemd unit template with restart behavior, runtime user, working directory, environment handling, journald logging, and dependencies in packaging/systemd/ic-env-guard.service
- [X] T061 [US2] Create installer script for directories, runtime, token generation, config placement, and systemd unit installation in packaging/install/install.sh
- [X] T062 [US2] Create uninstall script for service disable/stop and optional state retention prompt in packaging/install/uninstall.sh
- [X] T063 [US2] Create upgrade script preserving config, token, and state paths in packaging/install/upgrade.sh
- [X] T064 [US2] Create configuration validation CLI entrypoint in backend/ic_env_guard/systemd/cli.py
- [X] T065 [US2] Create runtime packaging notes and controlled runtime layout in packaging/runtime/README.md
- [X] T066 [US2] Create operator lifecycle documentation in docs/operations/lifecycle.md
- [X] T067 [US2] Create failed-startup recovery and local-state reset documentation in docs/operations/recovery.md
- [X] T068 [US2] Add health/status UI panel to frontend/src/pages/HostOverviewPage.tsx

**Checkpoint**: User Story 2 proves the agent can be installed and operated as a Linux host service.

---

## Phase 5: User Story 3 - Configured Service Control (Priority: P3)

**Goal**: A local administrator can manage only explicitly configured services, see status/history/logs, and receive predictable auditable outcomes for repeated or invalid requests.

**Independent Test**: Configure a harmless local test service, list it, start it, verify health/status, stop it, repeat already-satisfied operations, and confirm unknown services or unsupported operations are rejected without command execution.

### Tests for User Story 3 (REQUIRED) ⚠️

- [X] T069 [P] [US3] Create JSON schema contract tests for valid and invalid service configuration in backend/tests/contract/test_service_config_schema.py
- [X] T070 [P] [US3] Create API contract tests for service list/detail/start/stop/restart/events/logs in backend/tests/contract/test_services_api_contract.py
- [X] T071 [P] [US3] Create integration test for configured command service start/stop/restart/idempotency in backend/tests/integration/test_service_manager_process.py
- [X] T072 [P] [US3] Create integration test for unknown service and unsupported operation rejection without command execution in backend/tests/integration/test_service_rejections.py
- [X] T073 [P] [US3] Create integration test for health checks, timeouts, restart policy, and rotated service logs in backend/tests/integration/test_service_health_logs.py
- [X] T074 [P] [US3] Create frontend service list/detail/control tests in frontend/tests/service-pages.test.tsx

### Implementation for User Story 3

- [X] T075 [P] [US3] Create ManagedService, ServiceState, ServiceRun, ServiceOperation, ServiceEvent, and HealthCheckResult persistence models in backend/ic_env_guard/db/services.py
- [X] T076 [P] [US3] Implement service config schema validation against contracts/service-config.schema.json in backend/ic_env_guard/config/service_schema.py
- [X] T077 [US3] Implement configured process runner with safe command source, cwd, env, start/stop timeouts, and process tracking in backend/ic_env_guard/services/process_runner.py
- [X] T078 [US3] Implement optional systemd unit service adapter limited to configured units in backend/ic_env_guard/services/systemd_adapter.py
- [X] T079 [US3] Implement service manager with list/detail/start/stop/restart/status/idempotency/reconciliation behavior in backend/ic_env_guard/services/manager.py
- [X] T080 [US3] Implement health check runner for none/http/tcp/process checks in backend/ic_env_guard/services/healthchecks.py
- [X] T081 [US3] Implement bounded rotated service log capture and tail retrieval in backend/ic_env_guard/services/logs.py
- [X] T082 [US3] Implement service HTTP routes in backend/ic_env_guard/api/services.py
- [X] T083 [P] [US3] Create frontend service API wrapper in frontend/src/api/services.ts
- [X] T084 [US3] Create service list and service detail/control pages in frontend/src/pages/ServiceListPage.tsx and frontend/src/pages/ServiceDetailPage.tsx

**Checkpoint**: User Story 3 provides controlled service management without arbitrary remote command execution.

---

## Phase 6: User Story 4 - Prometheus-Compatible Host and Service Metrics (Priority: P4)

**Goal**: A local administrator can connect existing Prometheus-compatible monitoring tools to collect host, agent, service, and health metrics with bounded labels and allowlisted remote access.

**Independent Test**: Enable metrics, scrape locally, scrape from an allowed network source, reject a disallowed source, parse Prometheus text format, and verify labels avoid forbidden high-cardinality values.

### Tests for User Story 4 (REQUIRED) ⚠️

- [X] T085 [P] [US4] Create metrics contract test for required metric families and Prometheus text parsing in backend/tests/contract/test_metrics_contract.py
- [X] T086 [P] [US4] Create security test for remote metrics CIDR allowlist rejection/acceptance including IPv4, IPv6, and localhost defaults in backend/tests/integration/test_metrics_allowlist.py
- [X] T087 [P] [US4] Create cardinality test rejecting terminal IDs, commands, request IDs, source IPs, and unbounded user input labels in backend/tests/integration/test_metrics_cardinality.py
- [X] T088 [P] [US4] Create frontend metrics guidance page tests in frontend/tests/metrics-page.test.tsx

### Implementation for User Story 4

- [X] T089 [P] [US4] Create MetricsExposure config validation model with CIDR-based remote_network_allowlist rules for IPv4, IPv6, and localhost defaults in backend/ic_env_guard/config/metrics.py
- [X] T090 [US4] Implement Prometheus registry and metric family definitions in backend/ic_env_guard/metrics/registry.py
- [X] T091 [US4] Implement host CPU, memory, disk, and network collectors with bounded mount/interface labels in backend/ic_env_guard/metrics/host.py
- [X] T092 [US4] Implement agent, API request, WebSocket, and terminal session collectors with bounded route/status labels in backend/ic_env_guard/metrics/agent.py
- [X] T093 [US4] Implement managed service and health-check collectors from service state repositories in backend/ic_env_guard/metrics/services.py
- [X] T094 [US4] Implement background metrics refresh loop to avoid expensive synchronous scrape work in backend/ic_env_guard/metrics/collector.py
- [X] T095 [US4] Implement /metrics route with local access and CIDR-based remote network allowlist enforcement in backend/ic_env_guard/api/metrics.py
- [X] T096 [US4] Wire metrics middleware for bounded API request counters in backend/ic_env_guard/main.py
- [X] T097 [P] [US4] Create frontend metrics guidance API wrapper in frontend/src/api/metrics.ts
- [X] T098 [US4] Create metrics guidance page with scrape endpoint, allowlist notes, and Prometheus/Grafana integration guidance in frontend/src/pages/MetricsPage.tsx

**Checkpoint**: User Story 4 exposes Prometheus-compatible observability without custom TSDB or dashboard scope creep.

---

## Phase 7: User Story 5 - Local State and Audit Trail (Priority: P5)

**Goal**: An administrator can rely on durable local state and audit records for terminal lifecycle, service operations, health checks, authentication/authorization failures, configuration events, agent lifecycle, and restart recovery without secret leakage.

**Independent Test**: Perform authentication, terminal, service, health-check, config load, and restart actions; restart or reboot; verify durable state remains available, startup reconciles actual host state, and audit/log/metrics/state output excludes secrets and terminal content.

### Tests for User Story 5 (REQUIRED) ⚠️

- [X] T099 [P] [US5] Create integration test for audit event completeness across auth, terminal, service, config, and agent lifecycle in backend/tests/integration/test_audit_completeness.py
- [X] T100 [P] [US5] Create integration test for restart recovery and persisted state reconciliation in backend/tests/integration/test_state_reconciliation.py
- [X] T101 [P] [US5] Create migration rollback/forward-only recovery contract tests in backend/tests/contract/test_migration_contract.py
- [X] T102 [P] [US5] Create security test for secret exclusion across audit, logs, metrics, UI diagnostics, and SQLite state in backend/tests/integration/test_secret_exclusion_global.py
- [X] T103 [P] [US5] Create frontend audit/status view tests in frontend/tests/audit-status.test.tsx

### Implementation for User Story 5

- [X] T104 [US5] Create state/audit retention indexes and migration metadata updates in backend/migrations/0002_state_audit_indexes.py
- [X] T105 [US5] Implement migration runner with reversible/forward-only metadata and failed migration diagnostics in backend/ic_env_guard/db/migrations.py
- [X] T106 [US5] Implement startup reconciliation service for terminal and service state vs actual host processes in backend/ic_env_guard/db/reconciliation.py
- [X] T107 [US5] Implement authentication success/failure audit hooks in backend/ic_env_guard/auth/audit.py
- [X] T108 [US5] Implement authorization failure audit hooks for terminal, service, logs, and metrics access in backend/ic_env_guard/api/audit.py
- [X] T109 [US5] Implement configuration load event persistence in backend/ic_env_guard/config/audit.py
- [X] T110 [US5] Implement audit query repository with bounded filters and no secret fields in backend/ic_env_guard/db/audit_queries.py
- [X] T111 [US5] Implement audit/status HTTP routes for local administrator review in backend/ic_env_guard/api/audit.py
- [X] T112 [P] [US5] Create frontend audit/status API wrapper in frontend/src/api/audit.ts
- [X] T113 [US5] Create audit/status page showing lifecycle and operation records without secrets in frontend/src/pages/AuditStatusPage.tsx

**Checkpoint**: User Story 5 proves durable, migration-managed, secret-safe state and auditability across restart/recovery scenarios.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Complete validation, documentation, packaging, and cleanup across all user stories.

- [X] T114 Run full backend contract test suite and document command/results in docs/operations/test-results.md
- [ ] T115 Run full backend integration test suite on Ubuntu 24.04 and document command/results in docs/operations/test-results.md
- [ ] T116 Run CentOS 7 packaging/runtime smoke validation and document limitations/results in docs/operations/platform-validation.md
- [ ] T117 Run RHEL 8 packaging/runtime smoke validation and document limitations/results in docs/operations/platform-validation.md
- [X] T118 Run frontend build and tests and document command/results in docs/operations/test-results.md
- [X] T119 [P] Validate quickstart scenarios end-to-end and update docs/operations/quickstart-validation.md
- [X] T120 [P] Create Prometheus scrape documentation and example scrape config in docs/operations/prometheus.md
- [X] T121 [P] Create service configuration reference and safe examples in docs/operations/service-config.md
- [X] T122 [P] Create terminal safety and privacy documentation in docs/operations/terminal-safety.md
- [X] T123 [P] Create security review checklist for auth, remote bind, metrics allowlist, audit redaction, and command constraints in docs/operations/security-review.md
- [X] T124 Run code formatting and lint checks for backend and frontend and fix reported issues in backend/pyproject.toml and frontend/package.json
- [X] T125 Verify no MVP scope creep by checking for desktop wrapper, SSH server, custom TSDB, PromQL, alerting, unrestricted command API, cloud control plane, Windows PTY, or multi-host orchestration in docs/operations/security-review.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; recommended MVP slice.
- **User Story 2 (Phase 4)**: Depends on Foundational; can proceed after or alongside US1 if staffed.
- **User Story 3 (Phase 5)**: Depends on Foundational and benefits from US2 configuration/startup flow; can be validated independently with a test service.
- **User Story 4 (Phase 6)**: Depends on Foundational; service metrics depend on US3 service state, but host/agent metrics can start independently.
- **User Story 5 (Phase 7)**: Depends on Foundational and integrates audit/state from US1-US4.
- **Polish (Phase 8)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **US1 Secure Browser Terminal**: Independent after foundation and provides the MVP's highest-risk capability.
- **US2 Installable Linux Host Agent**: Independent after foundation and validates Linux/systemd lifecycle.
- **US3 Configured Service Control**: Independent after foundation but uses shared config/auth/db/audit infrastructure.
- **US4 Prometheus-Compatible Metrics**: Host/agent metrics independent after foundation; service metrics integrate with US3.
- **US5 Local State and Audit Trail**: Cross-cutting story that validates persisted/audited behavior from all earlier stories.

### Within Each User Story

- Contract and integration tests MUST be written before implementation tasks in that story.
- Persistence models before repositories/services.
- Services/managers before API routes.
- API wrappers before frontend pages.
- Story checkpoint validation before moving to the next priority if working sequentially.

---

## Parallel Opportunities

- Setup tasks T001-T010 can be split across backend, frontend, packaging, and docs.
- Foundational tests T011-T016 can run in parallel before implementing shared infrastructure.
- US1 tests T036-T041 can run in parallel; T042 and T043 can run in parallel before T044.
- US2 tests T054-T057 can run in parallel; documentation tasks T066-T067 can run after scripts are drafted.
- US3 tests T069-T074 can run in parallel; T075 and T076 can run in parallel before service manager implementation.
- US4 tests T085-T088 can run in parallel; metrics collectors T091-T093 can run in parallel after registry T090.
- US5 tests T099-T103 can run in parallel; audit/frontend tasks T112-T113 can run after API route T111.
- Polish documentation tasks T120-T123 can run in parallel.

## Parallel Example: User Story 1

```bash
Task: "T036 [US1] Create HTTP contract tests for terminal endpoints in backend/tests/contract/test_terminal_http_contract.py"
Task: "T037 [US1] Create WebSocket contract tests in backend/tests/contract/test_terminal_websocket_contract.py"
Task: "T038 [US1] Create PTY lifecycle integration test in backend/tests/integration/test_terminal_lifecycle.py"
Task: "T039 [US1] Create reconnect integration test in backend/tests/integration/test_terminal_reconnect.py"
Task: "T040 [US1] Create terminal secret-exclusion test in backend/tests/integration/test_terminal_secret_exclusion.py"
Task: "T041 [US1] Create frontend terminal tab tests in frontend/tests/terminal-tabs.test.tsx"
```

## Parallel Example: User Story 3

```bash
Task: "T069 [US3] Create service config schema contract tests in backend/tests/contract/test_service_config_schema.py"
Task: "T070 [US3] Create service API contract tests in backend/tests/contract/test_services_api_contract.py"
Task: "T071 [US3] Create service process integration test in backend/tests/integration/test_service_manager_process.py"
Task: "T072 [US3] Create service rejection integration test in backend/tests/integration/test_service_rejections.py"
Task: "T073 [US3] Create service health/log integration test in backend/tests/integration/test_service_health_logs.py"
Task: "T074 [US3] Create frontend service page tests in frontend/tests/service-pages.test.tsx"
```

## Parallel Example: User Story 4

```bash
Task: "T085 [US4] Create metrics contract test in backend/tests/contract/test_metrics_contract.py"
Task: "T086 [US4] Create metrics allowlist integration test in backend/tests/integration/test_metrics_allowlist.py"
Task: "T087 [US4] Create metrics cardinality test in backend/tests/integration/test_metrics_cardinality.py"
Task: "T088 [US4] Create frontend metrics page tests in frontend/tests/metrics-page.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational infrastructure.
3. Complete Phase 3: Secure Browser Terminal.
4. Stop and validate US1 independently with contract, integration, and frontend terminal tests.
5. Demonstrate authenticated browser terminal create/resize/reconnect/close with no orphan process and no durable terminal content storage.

### Incremental Delivery

1. Setup + Foundation → authenticated app shell, config, SQLite, audit, and route infrastructure.
2. US1 → secure terminal MVP.
3. US2 → systemd/installable Linux host agent.
4. US3 → configured service management.
5. US4 → Prometheus-compatible metrics.
6. US5 → full audit/state recovery validation.
7. Polish → platform validation, docs, and security review.

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup and Foundational phases together.
2. One developer implements US1 terminal backend while another builds frontend terminal UI after API contracts are stable.
3. A second stream handles US2 packaging/systemd while US1 proceeds.
4. US3 service management and US4 host/agent metrics can proceed in parallel after shared config/db/auth are available.
5. US5 audit/recovery integrates continuously as US1-US4 events become available.

---

## Notes

- [P] tasks are parallelizable only when assigned to different files and do not depend on incomplete implementation tasks.
- Every user story includes contract and integration tests because the constitution requires testable contracts and realistic Linux validation.
- Service management tasks must never introduce arbitrary command execution through API payloads.
- Terminal tasks must never persist terminal content by default.
- Metrics tasks must avoid high-cardinality labels and must not store high-frequency time-series data in SQLite.
- Stop at each checkpoint to validate the story independently before expanding scope.

# Tasks: Multi-Agent Control Plane

**Input**: Design documents from `/specs/002-multi-agent-control-plane/`

**Prerequisites**: `plan.md`, `spec.md`, `architecture.md`, `contracts/http-api.md`, `contracts/terminal-websocket.md`, `contracts/control-plane-config.md`

**Tests**: Contract and integration tests are required for privileged HTTP/WebSocket routes, configuration validation, persistence, audit, terminal lifecycle, packaging, and frontend routing.

**Organization**: Tasks are grouped by user story after shared setup and foundational work. The detailed implementation notes remain in `plan.md`; this file is the executable Spec Kit task artifact.

## Phase 1: Setup and Packaging Baseline

**Purpose**: Prepare migration packaging and test isolation needed by all later work.

- [X] T001 Move `backend/migrations/` to `backend/ic_env_guard/migrations/` and add `backend/ic_env_guard/migrations/__init__.py`
- [X] T002 Update `backend/ic_env_guard/db/migrations.py` so `MIGRATIONS_DIR` resolves inside `backend/ic_env_guard/migrations/`
- [X] T003 [P] Update migration fixture paths in `backend/tests/integration/test_migrations.py`
- [X] T004 [P] Update migration fixture paths in `backend/tests/integration/test_terminal_secret_exclusion.py`
- [X] T005 [P] Extend packaging assertions for package-contained migrations in `backend/tests/integration/test_packaging_runtime.py`
- [X] T006 Run packaging and migration baseline tests from `backend/tests/integration/`

---

## Phase 2: Foundational Prerequisites

**Purpose**: Complete security, audit, configuration, and routing foundations that block all user stories.

**Critical**: No user-story implementation should begin until this phase is complete.

- [X] T007 Add `state_database: Path | None = None` and control-plane config models in `backend/ic_env_guard/config/models.py`
- [X] T008 Implement `_resolve_state_db()` and mode-aware application setup in `backend/ic_env_guard/main.py`
- [X] T009 Add function-scoped `IC_ENV_GUARD_STATE_DB` isolation fixture in `backend/tests/conftest.py`
- [X] T010 [P] Add state database resolution tests in `backend/tests/unit/test_state_db_resolution.py`
- [X] T011 Add agent audit durability restart test in `backend/tests/integration/test_agent_audit_durability.py`
- [X] T012 Replace agent audit `create_all()` usage with migrations in `backend/ic_env_guard/config/audit.py`
- [X] T013 Add `check_same_thread=False` support in `backend/ic_env_guard/db/session.py`
- [X] T014 Update generated config template with `state_database` in `packaging/install/install.sh`
- [X] T015 [P] Update example configuration docs with `state_database` in `README.md`
- [X] T016 [P] Add runtime mode and configuration contract tests in `backend/tests/contract/test_control_plane_config.py`
- [X] T017 [P] Add security configuration tests in `backend/tests/unit/test_security_config.py`
- [X] T018 Implement `mode: Literal["agent", "control-plane"]`, agent config, TLS config, and control-plane config in `backend/ic_env_guard/config/models.py`
- [X] T019 Refactor router mounting by mode in `backend/ic_env_guard/main.py`
- [X] T020 Add control-plane database non-creation regression test in `backend/tests/contract/test_control_plane_config.py`
- [X] T021 Create gateway audit migration runner in `backend/ic_env_guard/db/control_plane_migrations.py`
- [X] T022 Create gateway audit migration package in `backend/ic_env_guard/control_plane_migrations/__init__.py`
- [X] T023 Create gateway audit migration in `backend/ic_env_guard/control_plane_migrations/0001_control_plane_audit.py`
- [X] T024 Implement gateway audit repository in `backend/ic_env_guard/db/control_plane_audit.py`
- [X] T025 Add gateway audit persistence, isolation, and failure-mode tests in `backend/tests/integration/test_control_plane_audit.py`
- [X] T026 Add migration runner contract tests in `backend/tests/contract/test_migration_contract.py`
- [X] T027 Add bounded gateway audit query routes in `backend/ic_env_guard/api/control_plane_audit.py`
- [X] T028 Add `build>=1.2`, promote `httpx` to a runtime dependency, and add runtime dependency metadata assertions in `backend/pyproject.toml` and `backend/tests/integration/test_packaging_runtime.py`
- [X] T029 Add foundational constrained HTTP client tests in `backend/tests/unit/test_agent_client.py` and implement the shared client in `backend/ic_env_guard/agents/client.py`

---

## Phase 3: User Story 1 - Select and Inspect an Agent (Priority: P1) MVP

**Goal**: Authenticated administrators can select configured agents, see availability, and inspect host overview and configured services without stale cross-agent UI updates.

**Independent Test**: Configure two test agents, stop one, sign in, switch between both, and verify pages show data/errors only for the selected agent.

### Tests for User Story 1

- [X] T030 [P] [US1] Add agent registry unit tests in `backend/tests/unit/test_agent_registry.py`
- [X] T031 [P] [US1] Add agent inventory and probe contract tests in `backend/tests/contract/test_agents_api.py`
- [X] T032 [P] [US1] Add availability transition tests in `backend/tests/integration/test_agent_availability.py`
- [X] T033 [P] [US1] Add frontend agent context tests in `frontend/tests/agent-context.test.tsx`
- [X] T034 [P] [US1] Add stale response and routing tests in `frontend/tests/agent-routing.test.tsx`

### Implementation for User Story 1

- [X] T035 [P] [US1] Create agent status and capability models in `backend/ic_env_guard/agents/models.py`
- [X] T036 [P] [US1] Create immutable agent registry in `backend/ic_env_guard/agents/registry.py`
- [X] T037 [US1] Add local `GET /api/capabilities` and control-plane inventory routes in `backend/ic_env_guard/api/agents.py`
- [X] T038 [US1] Add constrained availability probes and stale status handling in `backend/ic_env_guard/agents/availability.py`
- [X] T039 [US1] Mount agent inventory and availability services in `backend/ic_env_guard/main.py`
- [X] T040 [P] [US1] Create frontend agent API helpers in `frontend/src/api/agents.ts`
- [X] T041 [US1] Create active agent provider in `frontend/src/agents/AgentContext.tsx`
- [X] T042 [US1] Create global agent selector in `frontend/src/agents/AgentSelector.tsx`
- [X] T043 [US1] Wire active agent identity into `frontend/src/pages/AppRoutes.tsx`
- [X] T044 [US1] Scope host overview requests by agent in `frontend/src/pages/HostOverviewPage.tsx`
- [X] T045 [US1] Scope service list requests by agent in `frontend/src/pages/ServiceListPage.tsx`
- [X] T046 [US1] Run US1 backend and frontend tests from `backend/tests/` and `frontend/tests/`

---

## Phase 4: User Story 2 - Control Configured Remote Services (Priority: P1)

**Goal**: Authenticated administrators list, start, stop, and restart only configured services on the selected remote agent.

**Independent Test**: Start and stop a harmless service through the control plane, verify the result on the target agent, repeat an already-satisfied operation, and inspect gateway and agent audit records.

### Tests for User Story 2

- [X] T047 [P] [US2] Extend constrained HTTP client tests for service-route redirects, forwarded headers, response limits, and normalized errors in `backend/tests/unit/test_agent_client.py`
- [X] T048 [P] [US2] Add service route contract tests in `backend/tests/contract/test_agent_services_api.py`
- [X] T049 [P] [US2] Add multi-agent service and monitoring integration tests in `backend/tests/integration/test_multi_agent_monitoring.py`

### Implementation for User Story 2

- [X] T050 [US2] Verify `httpx` runtime dependency metadata remains asserted in `backend/tests/integration/test_packaging_runtime.py`
- [X] T051 [US2] Extend constrained HTTP client service dispatch behavior in `backend/ic_env_guard/agents/client.py`
- [X] T052 [US2] Implement explicit service gateway routes in `backend/ic_env_guard/api/agent_services.py`
- [X] T053 [US2] Preserve service status codes and idempotent mutation semantics in `backend/ic_env_guard/api/agent_services.py`
- [X] T054 [US2] Create gateway audit intent and outcome records around service routes in `backend/ic_env_guard/api/agent_services.py`
- [X] T055 [US2] Pass `agentId` through service API calls in `frontend/src/api/services.ts`
- [X] T056 [US2] Run US2 backend and frontend tests from `backend/tests/` and `frontend/tests/`

---

## Phase 5: User Story 3 - Use a Remote Terminal (Priority: P2)

**Goal**: Authenticated administrators create, attach to, resize, reconnect to, and close terminal sessions on the selected agent without cross-routing terminal input.

**Independent Test**: Create terminals with identical local IDs on two agents, attach to each through the gateway, reconnect from retained output, and close both without orphan processes.

### Tests for User Story 3

- [X] T057 [P] [US3] Add terminal HTTP contract tests in `backend/tests/contract/test_agent_terminal_http_contract.py`
- [X] T058 [P] [US3] Add gateway ticket unit tests in `backend/tests/unit/test_gateway_terminal_tickets.py`
- [X] T059 [P] [US3] Add terminal WebSocket integration tests, including attach audit outcomes, in `backend/tests/integration/test_agent_terminal_websocket.py`
- [X] T060 [P] [US3] Add frontend terminal routing tests in `frontend/tests/terminal-agent-routing.test.tsx`

### Implementation for User Story 3

- [X] T061 [US3] Create terminal HTTP gateway routes in `backend/ic_env_guard/api/agent_terminals.py`
- [X] T062 [US3] Create bounded gateway ticket store in `backend/ic_env_guard/agents/terminal_proxy.py`
- [X] T063 [US3] Bind gateway tickets to actor, agent, terminal, intended WebSocket path, and expiry in `backend/ic_env_guard/agents/terminal_proxy.py`
- [X] T064 [US3] Add terminal route gateway audit for create, resize, connect-token, close, authorization denial, and pre-dispatch failures in `backend/ic_env_guard/api/agent_terminals.py`
- [X] T065 [US3] Add `websockets` runtime dependency in `backend/pyproject.toml`
- [X] T066 [US3] Implement terminal WebSocket proxy and attach audit intent/outcome recording in `backend/ic_env_guard/api/agent_terminal_ws.py`
- [X] T067 [US3] Enforce frame limits, backpressure, paired cancellation, reconnect cursor, and sanitized close codes in `backend/ic_env_guard/agents/terminal_proxy.py`
- [X] T068 [US3] Key frontend terminal API calls by agent in `frontend/src/api/terminals.ts`
- [X] T069 [US3] Key terminal page state by `(agentId, terminalId)` in `frontend/src/pages/TerminalPage.tsx`
- [X] T070 [US3] Remount and cancel terminal panes on agent switch in `frontend/src/terminal/TerminalPane.tsx`
- [X] T071 [US3] Run US3 backend and frontend tests from `backend/tests/` and `frontend/tests/`

---

## Phase 6: User Story 4 - Review Agent Audit and Monitoring Data (Priority: P3)

**Goal**: Authenticated administrators view agent-scoped audit events and host snapshots while Prometheus scraping remains agent-local.

**Independent Test**: Generate actions on two agents, verify audit queries and monitoring snapshots stay agent-scoped, and validate each agent's `/metrics` endpoint remains Prometheus compatible.

### Tests for User Story 4

- [X] T072 [P] [US4] Add monitoring contract tests in `backend/tests/contract/test_monitoring_api_contract.py`
- [X] T073 [P] [US4] Add agent audit routing tests in `backend/tests/integration/test_agent_audit_routing.py`
- [X] T074 [P] [US4] Add mixed-version compatibility tests in `backend/tests/integration/test_mixed_agent_versions.py`
- [X] T075 [P] [US4] Add metrics page tests in `frontend/tests/metrics-page.test.tsx`

### Implementation for User Story 4

- [X] T076 [US4] Implement agent monitoring snapshot gateway route in `backend/ic_env_guard/api/agent_monitoring.py`
- [X] T077 [US4] Replace frontend monitoring API calls with agent-scoped snapshots in `frontend/src/api/monitoring.ts`
- [X] T078 [US4] Remove browser-managed machine credential forms from `frontend/src/pages/MetricsPage.tsx`
- [X] T079 [US4] Deprecate `/api/monitoring/machines` mutation routes for one compatibility release in `backend/ic_env_guard/api/monitoring.py`
- [X] T080 [US4] Prevent new frontend usage of `MachineRegistry` in `backend/ic_env_guard/monitoring/machines.py`
- [X] T081 [US4] Implement agent-scoped audit routes in `backend/ic_env_guard/api/agent_audit.py`
- [X] T082 [US4] Add frontend audit API support in `frontend/src/api/audit.ts`
- [X] T083 [US4] Add gateway and agent audit views in `frontend/src/pages/AuditStatusPage.tsx`
- [X] T084 [US4] Run US4 backend and frontend tests from `backend/tests/` and `frontend/tests/`

---

## Phase 7: Polish and Operational Hardening

**Purpose**: Documentation, compatibility, full verification, and release readiness across all stories.

- [X] T085 [P] Document control-plane operations, TLS, per-agent tokens, migration, rollback, outage recovery, and deprecated route removal in `docs/operations/control-plane.md`
- [X] T086 [P] Update development commands for `agent` and `control-plane` modes in `start.sh`
- [X] T087 [P] Update operator-facing feature documentation in `README.md`
- [X] T088 [P] Add or update security review guidance in `docs/operations/security-review.md`
- [X] T089 Run full backend verification from `backend/`
- [X] T090 Run full frontend verification from `frontend/`
- [X] T091 Verify completion checklist in `specs/002-multi-agent-control-plane/checklists/requirements.md`

---

## Dependencies and Execution Order

### Phase Dependencies

- **Phase 1** has no dependencies.
- **Phase 2** depends on Phase 1 and blocks all user stories.
- **US1 and US2** both depend on Phase 2. They are both P1, but US1 should land first when only one implementer is available because it establishes the agent selector and registry surface.
- **US3** depends on Phase 2 and reuses registry, client, and audit foundations.
- **US4** depends on Phase 2 and benefits from US1 frontend context, but its backend audit and monitoring routes can be developed in parallel after the foundation.
- **Phase 7** depends on the desired user stories being complete.

### Parallel Opportunities

- T003, T004, and T005 can run in parallel after T001 and T002.
- T010, T015, T016, and T017 can run in parallel while T008 and T018 are being implemented.
- Tests marked `[P]` within each user story can be written in parallel before implementation.
- Backend route implementation and frontend state work can run in parallel within US1 after T035 through T039 define backend contracts.
- US3 backend ticket-store work and frontend terminal state tests can run in parallel once terminal contracts are fixed.

## Parallel Example: User Story 1

```text
Task: "Add agent registry unit tests in backend/tests/unit/test_agent_registry.py"
Task: "Add agent inventory and probe contract tests in backend/tests/contract/test_agents_api.py"
Task: "Add availability transition tests in backend/tests/integration/test_agent_availability.py"
Task: "Add frontend agent context tests in frontend/tests/agent-context.test.tsx"
```

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 for agent selection, inventory, capability negotiation, and frontend scoping.
3. Complete US2 for service control on selected agents.
4. Stop and validate P1 behavior end to end before starting terminal work.

### Incremental Delivery

1. Deliver US1 + US2 as the P1 control-plane MVP.
2. Deliver US3 terminal gateway as a separate safety-sensitive increment.
3. Deliver US4 audit/monitoring views and operational hardening.
4. Run Phase 7 before marking feature `002` complete.

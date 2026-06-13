# Quickstart Validation

This document records the end-to-end MVP scenarios from `specs/001-linux-host-agent/quickstart.md` and maps them to automated tests or manual platform checks.

## 1. Install and start agent

Coverage:

- `backend/tests/integration/test_packaging_runtime.py`
- `backend/tests/integration/test_systemd_unit.py`
- `docs/operations/platform-validation.md`

Manual systemd validation is still required on CentOS 7, RHEL 8, and Ubuntu 24.04.

## 2. Validate fail-closed security

Coverage:

- `backend/tests/contract/test_auth_required.py`
- `backend/tests/integration/test_agent_startup.py`
- `backend/tests/unit/test_security_config.py`

Expected outcome: privileged terminal and service-control routes require authentication, and invalid security configuration prevents readiness/startup.

## 3. Authenticate as local administrator

Coverage:

- `backend/tests/contract/test_auth_login_logout_contract.py`
- frontend login/app-shell coverage through page tests

Expected outcome: a generated local bearer token authenticates the single local administrator role.

## 4. Validate browser terminal lifecycle

Coverage:

- `backend/tests/contract/test_terminal_http_contract.py`
- `backend/tests/contract/test_terminal_websocket_contract.py`
- `backend/tests/integration/test_terminal_lifecycle.py`
- `backend/tests/integration/test_terminal_reconnect.py`
- `frontend/tests/terminal-tabs.test.tsx`

Expected outcome: create, attach, command output, resize, reconnect, close, and process cleanup are covered by contract and integration tests.

## 5. Validate terminal idle timeout

Coverage:

- `backend/ic_env_guard/terminal/manager.py` idle cleanup path
- terminal lifecycle integration coverage

Manual long-duration boundary validation can be run on Linux by configuring 30-, 60-, and 120-minute values.

## 6. Validate configured service management

Coverage:

- `backend/tests/contract/test_service_config_schema.py`
- `backend/tests/contract/test_services_api_contract.py`
- `backend/tests/integration/test_service_manager_process.py`
- `backend/tests/integration/test_service_rejections.py`
- `backend/tests/integration/test_service_health_logs.py`
- `frontend/tests/service-pages.test.tsx`

Expected outcome: only configured services are visible and controllable, repeated operations are idempotent where possible, and unknown/unsupported operations are rejected without command execution.

## 7. Validate configuration rejection

Coverage:

- `backend/tests/contract/test_service_config_schema.py`
- `backend/tests/integration/test_agent_startup.py`

Expected outcome: malformed, incomplete, ambiguous, or unsafe service definitions are rejected with actionable diagnostics.

## 8. Validate metrics

Coverage:

- `backend/tests/contract/test_metrics_contract.py`
- `backend/tests/integration/test_metrics_allowlist.py`
- `backend/tests/integration/test_metrics_cardinality.py`
- `frontend/tests/metrics-page.test.tsx`

Expected outcome: `/metrics` renders Prometheus-compatible text, local scrapes work by default, remote CIDR allowlists are enforced, and forbidden high-cardinality labels are absent.

## 9. Validate persistence and recovery

Coverage:

- `backend/tests/integration/test_migrations.py`
- `backend/tests/contract/test_migration_contract.py` when enabled in the suite
- database migration runner and reconciliation implementation

Expected outcome: schema migration history is recorded and startup detects failed/incompatible migration states clearly.

## 10. Validate operator lifecycle documentation

Coverage:

- `docs/operations/lifecycle.md`
- `docs/operations/recovery.md`
- `docs/operations/platform-validation.md`

Expected outcome: install, configure, validate, start, stop, restart, status, logs, upgrade, uninstall, and recovery workflows are documented.

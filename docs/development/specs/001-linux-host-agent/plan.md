# Implementation Plan: IC Design Environment Guard

**Branch**: `001-linux-host-agent` | **Date**: 2026-06-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-linux-host-agent/spec.md`

## Summary

Build the IC Design Environment Guard MVP as a Linux-first host agent that serves a local web application for one authenticated local administrator. The agent provides controlled browser terminal sessions, configured-service management, Prometheus-compatible metrics, and migration-managed local audit/state persistence. The implementation will use the constitution-preferred Python/FastAPI backend with a static Vite/TypeScript web UI, SQLite durable state, Prometheus-compatible metrics, systemd packaging, and a controlled Python runtime so supported hosts do not depend on modern system Python.

## Technical Context

**Language/Version**: Python 3.11+ for the backend/agent runtime; TypeScript for the static web UI.

**Primary Dependencies**: FastAPI, Uvicorn, FastAPI WebSocket support, ptyprocess, psutil, prometheus_client, Pydantic, SQLAlchemy 2.x, Alembic or equivalent explicit migration runner, PyYAML, Vite, React, xterm.js, @xterm/addon-fit.

**Storage**: SQLite in WAL mode for service state, run history, health-check results, configuration load events, authentication and authorization events, terminal metadata/lifecycle events, agent lifecycle events, and audit records. Terminal output remains in bounded in-memory replay buffers by default. Prometheus-compatible systems own long-term high-frequency metrics storage.

**Testing**: pytest for unit, contract, and integration tests; pytest-asyncio/httpx for HTTP and WebSocket tests; prometheus_client parser or text-format validation for `/metrics`; subprocess-based Linux process/PTY tests; packaging/systemd validation through Linux integration tests or container/VM smoke tests where practical.

**Target Platform**: Linux host agent with browser UI, supported on CentOS 7, Red Hat Enterprise Linux 8, and Ubuntu 24.04. The agent runs under systemd on supported platforms.

**Project Type**: Local web application served by a host agent; backend service plus mostly static frontend.

**Performance Goals**: Terminal create/resize/close workflow completes within 2 minutes end-to-end; reconnect to retained terminal output completes within 10 seconds; configured service start/stop visible in UI and durable history within 10 seconds; `/metrics` scrape returns quickly from pre-collected in-memory metric values without expensive synchronous collection.

**Constraints**: Local-only network binding by default. Remote bind requires explicit remote-bind configuration plus valid authentication settings. MVP authentication uses a generated local bearer token. Remote metrics exposure relies on configured network allowlist rather than user login credentials. Terminal idle timeout is configurable from 30 to 120 minutes with a 60-minute default. No desktop wrapper, custom SSH server, custom TSDB, PromQL, alerting engine, Grafana-style dashboard, unrestricted command API, cloud control plane dependency, Windows PTY support, or multi-host orchestration in MVP.

**Scale/Scope**: Single-host agent for one authenticated local administrator role. Supports multiple terminal sessions for that administrator, explicitly configured local services, local SQLite state retention, and Prometheus-compatible scraping by existing monitoring tools.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Security Gate

- [x] Routes and features that expose terminal, service-control, host data, logs, metrics, or configuration are identified and risk-classified.
  - Covered by [contracts/openapi.yaml](contracts/openapi.yaml), [contracts/terminal-websocket.md](contracts/terminal-websocket.md), and the route-risk model in the spec.
- [x] Authentication and authorization are defined for privileged service-control and terminal operations.
  - MVP uses a generated local bearer token and single local administrator role; terminal and service-control routes require authentication.
- [x] Secrets are protected from logs, audit events, metrics, UI output, and persisted state.
  - Audit/state contracts exclude terminal content, bearer token values, passwords, private keys, and other secrets.
- [x] Invalid security configuration fails closed.
  - Remote bind requires explicit configuration plus valid authentication settings; invalid auth/security settings block startup.

### Linux Operations Gate

- [x] Supported MVP platforms are addressed: CentOS 7, RHEL 8, and Ubuntu 24.04, or limitations are explicitly documented.
  - Packaging and quickstart validation cover all three supported platforms.
- [x] The plan avoids relying on a modern system Python and defines a controlled runtime or packaging approach when packaging is affected.
  - Release packaging uses a controlled Python runtime or installer-managed virtual environment.
- [x] systemd install, start, stop, restart, status, logs, upgrade, and uninstall behavior remains compatible where relevant.
  - systemd unit behavior and operator workflows are included in [quickstart.md](quickstart.md).

### Service Management Gate

- [x] Managed actions are limited to explicitly configured services.
  - Service API operates only on IDs from validated local configuration.
- [x] Service commands are allowlisted or mapped through safe service definitions; no arbitrary command runner is introduced.
  - [contracts/service-config.schema.json](contracts/service-config.schema.json) requires configured commands/mappings and allowed operations.
- [x] Timeout, health-check, error, idempotency, and audit behavior are specified.
  - Data model and contracts define service operation outcomes, health checks, timeouts, and audit records.

### Observability Gate

- [x] Metrics are Prometheus-compatible and exposed through `/metrics` when observability is affected.
  - [contracts/metrics.md](contracts/metrics.md) defines Prometheus-compatible metric families.
- [x] Metric names, labels, units, and cardinality are documented; unbounded labels are avoided.
  - Metrics contract bounds labels and excludes terminal session IDs, commands, request IDs, and unbounded user input.
- [x] Local storage is limited to state, history, health, and audit data rather than a custom time-series database.
  - SQLite stores durable operational state only; Prometheus-compatible tools own long-term metrics history.

### Terminal Safety Gate

- [x] PTY sessions have owner, creation time, last activity, process identifier, idle timeout, and termination path.
  - Terminal Session entity and WebSocket contract define required metadata/lifecycle.
- [x] Browser disconnects, forced termination, and orphan-process cleanup are handled.
  - Terminal lifecycle states and quickstart validation include disconnect/reconnect/cleanup checks.
- [x] Terminal content is excluded from logs and audit records by default.
  - Data model stores terminal metadata and bounded in-memory replay buffers only.
- [x] WebSocket transport remains separated from PTY/service/metrics logic.
  - Contracts split HTTP/service APIs, terminal WebSocket behavior, service config, and metrics.

### Persistence and Audit Gate

- [x] SQLite or equivalent local durable state is migration-managed.
  - Plan selects SQLite plus migration management.
- [x] Audit events include timestamp, actor, source address, operation, target, result, and failure reason where available.
  - Audit Event entity defines the required fields.
- [x] Startup reconciles persisted state with actual host state.
  - Startup recovery is included in functional requirements, data transitions, and quickstart validation.

### Simplicity Gate

- [x] The feature is required for the MVP or clearly documented as post-MVP.
  - MVP scope is the host agent, browser UI, terminal, configured services, metrics, persistence, security, and Linux lifecycle.
- [x] The plan avoids custom SSH, custom TSDB, PromQL, alerting, desktop packaging, Windows PTY behavior, unrestricted command execution, cloud control plane dependency, and multi-host orchestration.
  - These are explicitly excluded by spec and plan constraints.
- [x] Any abstraction is justified by current requirements rather than future speculation.
  - Components align directly with current contracts: auth, terminal, service manager, metrics, persistence, config, and UI.

### Testing Gate

- [x] Contracts are defined before implementation for externally visible interfaces.
  - HTTP, WebSocket, config schema, and metrics contracts are generated in `contracts/`.
- [x] Integration tests cover realistic Linux behavior where practical.
  - Quickstart and future tasks will include Linux PTY/process, systemd, SQLite, and metrics integration tests.
- [x] Security failure paths are tested.
  - Unauthenticated privileged routes, invalid security configuration, remote bind, metrics allowlist, and secret-exclusion checks are planned.
- [x] systemd, metrics, service-control, database, and terminal lifecycle behaviors are tested where relevant.
  - Each behavior maps to a contract or quickstart scenario.

## Project Structure

### Documentation (this feature)

```text
specs/001-linux-host-agent/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── terminal-websocket.md
│   ├── service-config.schema.json
│   └── metrics.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
backend/
├── ic_env_guard/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory and route mounting
│   ├── api/                     # HTTP route handlers and auth dependencies
│   ├── auth/                    # bearer token loading, validation, session helpers
│   ├── config/                  # local config models and validation
│   ├── db/                      # SQLite engine, migrations, repositories
│   ├── metrics/                 # collectors and Prometheus exporter
│   ├── services/                # configured service manager and health checks
│   ├── terminal/                # PTY manager, replay buffers, lifecycle cleanup
│   └── systemd/                 # unit template and lifecycle helpers
├── migrations/
└── tests/
    ├── contract/
    ├── integration/
    └── unit/

frontend/
├── src/
│   ├── api/
│   ├── components/
│   ├── pages/
│   ├── terminal/
│   └── styles/
└── tests/

packaging/
├── systemd/
├── install/
└── runtime/

docs/
└── operations/
```

**Structure Decision**: Use a backend/frontend split because the feature is a host agent serving a browser UI. The backend owns security, PTY, services, metrics, SQLite, and systemd integration. The frontend remains static build output served by the agent. Packaging and operator documentation live outside application source so lifecycle artifacts remain testable independently.

## Complexity Tracking

No constitutional violations or justified exceptions are planned.

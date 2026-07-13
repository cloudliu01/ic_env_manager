# Research: IC Design Environment Guard

## Decision: Python 3.11+ FastAPI host agent

**Rationale**: Python 3.11+ with FastAPI matches the constitution-preferred stack, provides mature HTTP/WebSocket support, and keeps the backend protocol usable without desktop packaging. A controlled runtime avoids depending on CentOS 7 system Python.

**Alternatives considered**:
- Python 3.10: acceptable but 3.11 provides better performance and longer practical support for new packaging work.
- Go: strong single-binary packaging, but diverges from constitution-preferred stack and increases PTY/Web UI integration planning work.
- Node.js: suitable for WebSocket services, but less aligned with Linux process/service management and the constitution's preferred backend stack.

## Decision: Static Vite + TypeScript + React frontend served by the agent

**Rationale**: Vite/TypeScript provides a lightweight static build suitable for serving from the host agent. React is a common xterm.js integration target with mature ecosystem support. The UI remains static/mostly static, satisfying the constitution's MVP simplicity requirement.

**Alternatives considered**:
- SolidJS or Vue: both are viable, but React minimizes ecosystem uncertainty and hiring/onboarding risk.
- Server-rendered templates only: simpler, but terminal tab state and xterm.js lifecycle are easier to maintain in a client-side app.
- Desktop wrapper first: explicitly post-MVP by constitution and spec.

## Decision: Generated local bearer token for MVP authentication

**Rationale**: The clarified spec selects a generated local bearer token for browser login and API access. This provides an explicit authentication boundary from the first version without taking on public-key challenge flows in MVP.

**Alternatives considered**:
- Public-key challenge authentication in MVP: stronger and useful later, but increases scope and is explicitly post-MVP.
- Both token and public-key auth in MVP: unnecessary complexity for first release.
- No authentication for local-only mode: violates the constitution because terminal and service-control access must not be unauthenticated.

## Decision: Single authenticated local administrator role

**Rationale**: The clarified MVP has one human permission role. This reduces authorization complexity while preserving authentication and auditability for all privileged operations. The data model can store actor identity consistently and later support role expansion through an explicit amendment/spec.

**Alternatives considered**:
- Administrator/operator split: useful later but unnecessary for a single-host MVP.
- Administrator/operator/viewer roles: increases UI, API, test, and audit matrix without MVP requirement.

## Decision: Local-only bind by default; remote bind requires explicit config plus valid auth

**Rationale**: Local-only default minimizes exposure. Requiring explicit remote-bind configuration and valid authentication settings before startup matches fail-closed security requirements while preserving a controlled path for remote browser use.

**Alternatives considered**:
- Strictly local-only MVP: safest but less useful for the remote web-control purpose.
- Remote bind requiring TLS in MVP: desirable for direct internet exposure but not required by the clarified spec; network-level protections such as SSH tunnel/VPN/reverse proxy can be documented.

## Decision: Remote metrics exposure protected by network allowlist

**Rationale**: The clarified spec selects network allowlist for metrics exposure beyond localhost. This aligns with common Prometheus deployment patterns where scrapers reach targets from known monitoring networks and avoids mixing user browser credentials with machine scraping.

**Alternatives considered**:
- Reuse bearer token for metrics: simple but couples human login credentials to scraper access.
- Separate scrape token: strong and common, but user selected network allowlist.
- Local-only metrics in MVP: would limit integration with existing monitoring tools.

## Decision: SQLite WAL with migration-managed schema

**Rationale**: SQLite fits a single-host local agent, avoids a separate database service, supports durable state and audit records, and satisfies the constitution when schema changes are migration-managed. WAL improves concurrent read/write behavior for web requests and background health/audit writes.

**Alternatives considered**:
- Plain JSON files: simpler, but poor for audit queries, migrations, concurrency, and recovery.
- PostgreSQL: overkill and adds an external service dependency for a single-host MVP.
- SQLite as time-series store: rejected because Prometheus-compatible tools own high-frequency metrics history.

## Decision: SQLAlchemy 2.x plus Alembic-style migrations

**Rationale**: SQLAlchemy gives explicit schema models and query control while Alembic-style migrations provide a well-understood migration path. This supports durable state, audit records, and future schema evolution.

**Alternatives considered**:
- SQLModel: convenient, but adds tighter coupling between validation and persistence models.
- Raw SQLite only: fewer dependencies but more custom migration/repository code.
- No migration framework: violates the constitution.

## Decision: ptyprocess for Linux PTY management

**Rationale**: ptyprocess directly manages child PTY processes and is focused enough for server-side lifecycle control. It supports explicit process IDs, reads/writes, resize, and termination behavior required by the terminal contract.

**Alternatives considered**:
- pexpect: mature and built on ptyprocess, but includes expect-style automation not required for browser terminal streaming.
- Python stdlib pty/subprocess only: possible, but more custom lifecycle and edge-case handling.

## Decision: Bounded in-memory terminal replay buffer, durable metadata only

**Rationale**: The spec and constitution require reconnect support but prohibit default durable terminal content storage. A bounded per-session memory buffer with cursor metadata supports reconnect/tail replay while avoiding unbounded SQLite growth and transcript leakage.

**Alternatives considered**:
- Store terminal output in SQLite: rejected due to secret leakage and unbounded growth risk.
- Store output in rotated files by default: more durable but changes privacy/audit posture; keep as explicit future audit mode.
- No replay buffer: simpler but fails reconnect requirements.

## Decision: Terminal idle timeout configurable 30–120 minutes, default 60 minutes

**Rationale**: The clarified timeout balances operational pauses and cleanup safety. A bounded range prevents unsafe indefinite sessions while allowing local administrators to tune for their environment.

**Alternatives considered**:
- Fixed 30 minutes: safer but may interrupt normal engineering operations.
- Fixed 120 minutes: more convenient but leaves abandoned shells longer.
- No timeout: violates terminal safety requirements.

## Decision: Configured-service manager only; no arbitrary command API

**Rationale**: The agent manages only local services declared in a human-readable configuration file. Each service declares identity, allowed operations, command or host-service mapping, health checks, timeouts, and log/status collection rules. This satisfies the config-driven service-management principle.

**Alternatives considered**:
- General remote command endpoint: rejected by constitution and spec.
- Full systemd manager wrapper for all units: too broad and may expose services not explicitly approved.
- Hard-coded service list: not maintainable for different engineering host environments.

## Decision: Prometheus-compatible metrics generated from background collectors

**Rationale**: `/metrics` should be fast and scrape-friendly. Background or cached collectors update in-memory metric families for host, agent, service, and health-check state; scrape requests render the current values without expensive synchronous host inspection.

**Alternatives considered**:
- Collect all metrics synchronously during scrape: simpler but risks slow scrapes and unpredictable load.
- Build custom dashboard and metrics storage: rejected by MVP simplicity and observability principles.

## Decision: systemd service with controlled runtime packaging

**Rationale**: Supported hosts require systemd lifecycle behavior and CentOS 7 cannot be assumed to have modern Python. Packaging should provide a controlled runtime, such as an installer-managed virtual environment or self-contained executable, with unit files and documented lifecycle commands.

**Alternatives considered**:
- Use system Python directly: rejected for CentOS 7 compatibility.
- Docker-only deployment: not guaranteed in IC design environments and does not satisfy host-agent systemd contract by itself.
- Native desktop package: post-MVP.

## Decision: Contract and integration-first validation

**Rationale**: The feature controls real host terminals and services. HTTP, WebSocket, config schema, metrics, database migration, terminal lifecycle, systemd, and security failure paths need contract/integration tests before implementation is considered complete.

**Alternatives considered**:
- Unit-test-heavy approach only: insufficient for real Linux process, PTY, systemd, and audit behavior.
- Manual validation only: not repeatable enough for release confidence.

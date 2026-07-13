# Agent Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an independently runnable Agent v2 with modular composition, loopback-only tokenless local ingestion, latest-value Observations and Log Sources, bounded log tail, Prometheus and summary output, Manager-specific credentials/enrollment, and a standalone-first web UI without changing the existing PTY v1 contract.

**Architecture:** Keep one modular-monolith process, but create explicit domain/application ports and SQLite adapters. Run the authenticated Public API and unauthenticated Local Ingest API as separate FastAPI applications/listeners. The Agent owns all local state; Manager integration is limited to a versioned credential/enrollment contract and never introduces a Manager dependency into Agent modules.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite/WAL, prometheus-client, pytest/httpx; React, TypeScript, React Router, TanStack Query, Vitest, Testing Library, xterm.

## Global Constraints

- Treat [the approved design spec](../specs/2026-07-11-agent-observability-refactor-design.md) as the source of truth. This plan implements Workstream A only.
- Preserve every existing `/api`, `/ws`, PTY, service, audit, and metrics v1 contract until its existing tests and the new regression tests pass unchanged.
- Domain modules must not import FastAPI, SQLAlchemy, `prometheus_client`, or frontend types. API handlers call application services; they never issue SQL.
- Local Ingest is available only on a separate `127.0.0.1` or `::1` listener, never trusts forwarding headers, never authenticates a token, and exposes only Observation/Log upserts.
- `producer_id` is always written as `local` by the repository. A request containing producer identity is rejected.
- Do not persist log content, Terminal content, plaintext Manager tokens, SSH output, or Observation `details` in audit events.
- All v2 JSON errors use `{ "error": { "code", "message", "correlation_id" } }`; all v2 responses carry `X-Correlation-ID`.
- Run backend commands inside the documented `venv312` Conda environment. If it is not already active, replace `pytest` with `conda run -n venv312 pytest` and `python` with `conda run -n venv312 python`.
- Use TDD for every behavior change. Run the narrow test first, then the enclosing suite before each task commit.
- Commit only files listed by the current task. Preserve unrelated working-tree changes.

---

### Task 1: Freeze Agent v1 and PTY compatibility

**Files:**
- Modify: `backend/tests/contract/test_terminal_http_contract.py`
- Modify: `backend/tests/contract/test_terminal_websocket_contract.py`
- Modify: `backend/tests/integration/test_terminal_lifecycle.py`
- Modify: `backend/tests/integration/test_terminal_reconnect.py`
- Create: `backend/tests/contract/test_agent_v1_compatibility.py`

**Interfaces:** Existing `create_app(...)`, `/api/capabilities`, `/api/terminals`, `/ws/terminals/{terminal_id}`, service routes, and bearer-token semantics are frozen. No production interface changes in this task.

- [ ] **Step 1: Add explicit compatibility assertions**

```python
@pytest.mark.contract
def test_v1_capabilities_and_terminal_routes_remain_available(agent_client):
    headers = {"Authorization": "Bearer secret-token"}
    capabilities = agent_client.get("/api/capabilities", headers=headers)
    created = agent_client.post(
        "/api/terminals",
        headers=headers,
        json={"title": "Compatibility shell", "rows": 24, "cols": 80},
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["api_version"] == "1"
    assert "terminals.v1" in capabilities.json()["capabilities"]
    assert created.status_code == 201
    assert created.json()["title"] == "Compatibility shell"
```

- [ ] **Step 2: Add PTY permission-semantics and reconnect regression cases**

```python
terminal = terminal_manager.create(owner_id="local-admin", title="identity", rows=24, cols=80)
terminal_manager.write(terminal.id, b"id -u\nprintf '__PTY_OK__\\n'\n")
history = terminal_manager.history(terminal.id, cursor=0)
assert b"__PTY_OK__" in history.output
```

- [ ] **Step 3: Run the compatibility slice**

Run: `cd backend && pytest -q tests/contract/test_agent_v1_compatibility.py tests/contract/test_terminal_http_contract.py tests/contract/test_terminal_websocket_contract.py tests/integration/test_terminal_lifecycle.py tests/integration/test_terminal_reconnect.py`

Expected: PASS. These tests establish the refactor baseline; if they fail before production changes, fix the fixture/assertion rather than changing behavior.

- [ ] **Step 4: Commit the regression baseline**

```bash
git add backend/tests/contract/test_agent_v1_compatibility.py backend/tests/contract/test_terminal_http_contract.py backend/tests/contract/test_terminal_websocket_contract.py backend/tests/integration/test_terminal_lifecycle.py backend/tests/integration/test_terminal_reconnect.py
git commit -m "test: freeze agent v1 and pty contracts"
```

---

### Task 2: Introduce Agent composition root and validated configuration

**Files:**
- Create: `backend/ic_env_guard/bootstrap/__init__.py`
- Create: `backend/ic_env_guard/bootstrap/composition.py`
- Create: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/main.py`
- Create: `backend/tests/unit/test_agent_composition.py`
- Modify: `backend/tests/contract/test_control_plane_config.py`
- Modify: `backend/tests/integration/test_agent_startup.py`

**Interfaces:** `build_agent_container(config, state_database) -> AgentContainer`; `build_manager_container(config) -> ManagerContainer`; new config models `IngestConfig`, `ObservationConfig`, `LogsConfig`, `EnrollmentConfig`, and `TrustedLanHttpServerConfig`; `create_app(...)` remains compatible and delegates construction.

- [ ] **Step 1: Write failing configuration and composition tests**

```python
def test_ingest_listener_rejects_non_loopback_and_public_port_collision(token_file):
    with pytest.raises(ValueError, match="ingest bind must be loopback"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            ingest=IngestConfig(bind="0.0.0.0", port=8766),
        )

    with pytest.raises(ValueError, match="public and ingest ports must differ"):
        AppConfig(
            auth=AuthConfig(token_file=token_file),
            server=ServerConfig(port=8765),
            ingest=IngestConfig(port=8765),
        )
```

Also test that `server.trusted_lan_http.enabled=true` requires non-empty private client CIDRs and explicit remote bind, and that enabling it causes runtime metadata to advertise `trusted-lan-http.v1` without exposing the CIDRs.

- [ ] **Step 2: Run the new tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_agent_composition.py tests/contract/test_control_plane_config.py tests/integration/test_agent_startup.py`

Expected: FAIL because `IngestConfig` and the composition root do not exist.

- [ ] **Step 3: Add bounded configuration models**

```python
class IngestConfig(BaseModel):
    bind: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65535)
    max_request_bytes: int = Field(default=32768, ge=1024, le=1024 * 1024)
    max_concurrent_requests: int = Field(default=16, ge=1, le=128)


class ObservationConfig(BaseModel):
    expired_retention_seconds: int = Field(default=86400, ge=0, le=604800)
    cleanup_interval_seconds: int = Field(default=60, ge=1, le=3600)


class LogsConfig(BaseModel):
    allowed_roots: list[Path] = Field(default_factory=list)
    max_tail_lines: int = Field(default=1000, ge=1, le=1000)
    default_tail_lines: int = Field(default=100, ge=1, le=1000)
    max_tail_bytes: int = Field(default=983040, ge=1024, le=983040)


class TrustedLanHttpServerConfig(BaseModel):
    enabled: bool = False
    client_cidrs: list[IPvAnyNetwork] = Field(default_factory=list)
```

- [ ] **Step 4: Extract construction from `main.py` into typed containers**

```python
@dataclass
class AgentContainer:
    config: AppConfig
    terminal_manager: TerminalManager
    service_manager: ServiceManager
    session_factory: sessionmaker
    metrics_registry: CollectorRegistry


def build_agent_container(config: AppConfig, state_database: Path) -> AgentContainer:
    run_migrations(state_database)
    engine = create_sqlite_engine(state_database)
    return AgentContainer(
        config=config,
        terminal_manager=TerminalManager(),
        service_manager=ServiceManager([_service_runtime(item) for item in config.services]),
        session_factory=create_session_factory(engine),
        metrics_registry=create_registry(),
    )
```

Keep the existing FastAPI dependency overrides as adapters around the container. Do not move route behavior in this task.

- [ ] **Step 5: Run focused and existing startup tests**

Run: `cd backend && pytest -q tests/unit/test_agent_composition.py tests/contract/test_control_plane_config.py tests/integration/test_agent_startup.py tests/contract/test_auth_required.py tests/contract/test_terminal_http_contract.py`

Expected: PASS.

- [ ] **Step 6: Commit the composition boundary**

```bash
git add backend/ic_env_guard/bootstrap backend/ic_env_guard/config/models.py backend/ic_env_guard/main.py backend/tests/unit/test_agent_composition.py backend/tests/contract/test_control_plane_config.py backend/tests/integration/test_agent_startup.py
git commit -m "refactor: add agent composition root"
```

---

### Task 3: Add stable Agent identity, runtime metadata, and v2 errors

**Files:**
- Create: `backend/ic_env_guard/bootstrap/identity.py`
- Create: `backend/ic_env_guard/api/runtime.py`
- Create: `backend/ic_env_guard/api/v2_errors.py`
- Create: `backend/ic_env_guard/auth/rate_limit.py`
- Modify: `backend/ic_env_guard/api/auth.py`
- Modify: `backend/ic_env_guard/agents/models.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Modify: `backend/ic_env_guard/main.py`
- Create: `backend/tests/unit/test_instance_identity.py`
- Create: `backend/tests/contract/test_runtime_api.py`
- Create: `backend/tests/contract/test_v2_error_contract.py`
- Modify: `backend/tests/contract/test_auth_login_logout_contract.py`

**Interfaces:** `load_or_create_instance_id(path: Path, *, allow_create: bool) -> UUID`; unauthenticated `GET /api/v2/runtime`; authenticated `GET /api/v2/capabilities`; `V2ApiError(status_code, code, message)`; bounded source-address login limiter with durable success/failure audit.

- [ ] **Step 1: Write identity persistence and malformed-file tests**

```python
def test_instance_id_is_created_once_and_reused(tmp_path):
    path = tmp_path / "instance-id"
    first = load_or_create_instance_id(path, allow_create=True)
    second = load_or_create_instance_id(path, allow_create=False)
    assert first == second
    assert path.read_text(encoding="utf-8") == f"{first}\n"


def test_malformed_instance_id_fails_closed(tmp_path):
    path = tmp_path / "instance-id"
    path.write_text("not-a-uuid\n", encoding="utf-8")
    with pytest.raises(InstanceIdentityError, match="invalid instance identity"):
        load_or_create_instance_id(path, allow_create=False)
```

- [ ] **Step 2: Write runtime/capabilities/error contract tests**

```python
assert client.get("/api/v2/runtime").json() == {
    "mode": "agent",
    "capabilities": ["runtime.v2"],
}
response = client.get("/api/v2/capabilities", headers=auth_headers)
assert UUID(response.json()["instance_id"])
assert response.headers["Cache-Control"] == "no-store"
```

Also assert overlong/invalid incoming correlation IDs are replaced rather than reflected, unexpected v2 exceptions use `500 internal_error`, repeated invalid logins receive `429`, and both successful and rejected login attempts create redacted audit events.

- [ ] **Step 3: Run tests and verify missing endpoint failures**

Run: `cd backend && pytest -q tests/unit/test_instance_identity.py tests/contract/test_runtime_api.py tests/contract/test_v2_error_contract.py tests/contract/test_auth_login_logout_contract.py`

Expected: FAIL with missing modules/routes.

- [ ] **Step 4: Implement atomic identity and v2 error envelope**

```python
class V2ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


async def v2_error_handler(request: Request, exc: V2ApiError) -> JSONResponse:
    correlation_id = request.state.correlation_id
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message,
                           "correlation_id": correlation_id}},
        headers={"X-Correlation-ID": correlation_id},
    )
```

Write the identity file with owner-only permissions, flush, `fsync`, and atomic rename. Existing configured installations without a file create it once during the migration release; a malformed existing file fails startup.

Validate client-supplied correlation IDs against a bounded ASCII pattern before reuse. Add a bounded in-memory token-bucket keyed by actual source address for login attempts; forwarding headers do not select the bucket. Audit login success/failure without recording the submitted token, and return a safe `429` when exhausted.

- [ ] **Step 5: Register runtime and capabilities without touching v1**

```python
@router.get("/runtime")
def runtime(request: Request) -> Response:
    return JSONResponse(
        {"mode": "agent", "capabilities": ["runtime.v2"]},
        headers={"Cache-Control": "no-store"},
    )
```

The v2 capability list will be extended by later tasks; keep `/api/capabilities` unchanged.

- [ ] **Step 6: Run compatibility plus v2 contracts and commit**

Run: `cd backend && pytest -q tests/unit/test_instance_identity.py tests/contract/test_runtime_api.py tests/contract/test_v2_error_contract.py tests/contract/test_auth_login_logout_contract.py tests/contract/test_agent_v1_compatibility.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/bootstrap/identity.py backend/ic_env_guard/api/runtime.py backend/ic_env_guard/api/v2_errors.py backend/ic_env_guard/auth/rate_limit.py backend/ic_env_guard/api/auth.py backend/ic_env_guard/agents/models.py backend/ic_env_guard/bootstrap/composition.py backend/ic_env_guard/main.py backend/tests/unit/test_instance_identity.py backend/tests/contract/test_runtime_api.py backend/tests/contract/test_v2_error_contract.py backend/tests/contract/test_auth_login_logout_contract.py
git commit -m "feat: add agent v2 runtime identity"
```

---

### Task 4: Implement Observation domain rules and repository

**Files:**
- Create: `backend/ic_env_guard/observations/__init__.py`
- Create: `backend/ic_env_guard/observations/models.py`
- Create: `backend/ic_env_guard/observations/service.py`
- Create: `backend/ic_env_guard/observations/ports.py`
- Create: `backend/ic_env_guard/storage/observations.py`
- Create: `backend/ic_env_guard/migrations/0003_observability.py`
- Create: `backend/tests/unit/test_observation_model.py`
- Create: `backend/tests/unit/test_observation_service.py`
- Modify: `backend/tests/contract/test_migration_contract.py`
- Modify: `backend/tests/integration/test_migrations.py`

**Interfaces:** `ObservationInput`, `Observation`, `ObservationRepository` Protocol, `ObservationService.upsert/get/list/delete_expired`; repository writes use atomic `compare_and_swap(record, expected_observed_at)`; stable SHA-256 `identity_key` over namespace, name, and compact sorted labels JSON.

- [ ] **Step 1: Write validation and identity tests**

```python
def test_identity_is_independent_of_label_order():
    left = ObservationInput.model_validate({**BASE, "labels": {"vendor": "synopsys", "server": "a"}})
    right = ObservationInput.model_validate({**BASE, "labels": {"server": "a", "vendor": "synopsys"}})
    assert left.identity_key() == right.identity_key()


@pytest.mark.parametrize("details", [
    {"nested": {"one": {"two": {"three": {"too_deep": True}}}}},
    {"blob": "x" * 16385},
])
def test_details_limits(details):
    with pytest.raises(ValidationError):
        ObservationInput.model_validate({**BASE, "details": details})
```

- [ ] **Step 2: Write ordering, idempotency, TTL, and producer tests with an in-memory fake**

```python
created = service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)
same = service.upsert(input_at("2026-07-11T10:00:00Z"), now=NOW)
assert created.record == same.record
assert same.created is False
assert same.record.producer_id == "local"

with pytest.raises(ObservationConflict, match="stale_observation"):
    service.upsert(input_at("2026-07-11T09:59:59Z"), now=NOW)
```

Add a fake Repository case that forces one CAS miss, changes the current record, and proves the Service re-reads and re-applies ordering/conflict rules rather than allowing the stale candidate to overwrite it.

- [ ] **Step 3: Run the unit/migration tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_observation_model.py tests/unit/test_observation_service.py tests/contract/test_migration_contract.py tests/integration/test_migrations.py`

Expected: FAIL because the domain and migration do not exist.

- [ ] **Step 4: Implement pure domain models and port**

```python
class ObservationRepository(Protocol):
    def get(self, identity_key: str) -> Observation | None: ...
    def compare_and_swap(
        self,
        record: Observation,
        expected_observed_at: datetime | None,
    ) -> bool: ...
    def list(self, query: ObservationQuery) -> ObservationPage: ...
    def delete_expired(self, cutoff: datetime, limit: int) -> int: ...


@dataclass(frozen=True)
class UpsertResult:
    record: Observation
    created: bool
```

Pydantic may validate the transport input, but the Service owns ordering/idempotency/TTL rules. Compare normalized complete content for same-timestamp retries. A CAS miss must trigger a bounded re-read/re-evaluation loop; exhausting the bound returns a stable storage-contention error. For inserts, `expected_observed_at=None` means insert only if the identity is still absent. For updates, the SQLite predicate matches the previously read `observed_at`, which is sufficient because different content at the same timestamp is already a domain conflict.

- [ ] **Step 5: Add SQLite migration and adapter**

Create exactly the spec's `observations` columns and indexes. The adapter serializes labels with `json.dumps(labels, sort_keys=True, separators=(",", ":"))`, serializes details compactly, forces `producer_id="local"`, uses one short transaction, and translates integrity/storage errors into domain storage errors. Implement create CAS with `INSERT ... ON CONFLICT DO NOTHING`; implement update CAS with `UPDATE ... WHERE identity_key = ? AND observed_at = ?`. Return whether exactly one row changed and never duplicate ordering rules in SQL.

```python
connection.execute(
    "CREATE INDEX IF NOT EXISTS idx_observations_namespace_name "
    "ON observations(namespace, name)"
)
connection.execute(
    "CREATE INDEX IF NOT EXISTS idx_observations_status_expiry "
    "ON observations(status, expires_at)"
)
```

- [ ] **Step 6: Run domain and migration suites**

Run: `cd backend && pytest -q tests/unit/test_observation_model.py tests/unit/test_observation_service.py tests/contract/test_migration_contract.py tests/integration/test_migrations.py`

Expected: PASS, including migration `0003_observability` recorded once.

- [ ] **Step 7: Commit the Observation core**

```bash
git add backend/ic_env_guard/observations backend/ic_env_guard/storage/observations.py backend/ic_env_guard/migrations/0003_observability.py backend/tests/unit/test_observation_model.py backend/tests/unit/test_observation_service.py backend/tests/contract/test_migration_contract.py backend/tests/integration/test_migrations.py
git commit -m "feat: add observation domain storage"
```

---

### Task 5: Expose isolated Observation ingest and public read APIs

**Files:**
- Create: `backend/ic_env_guard/api/ingest_observations.py`
- Create: `backend/ic_env_guard/api/observations.py`
- Create: `backend/ic_env_guard/api/ingest_guard.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Modify: `backend/ic_env_guard/main.py`
- Create: `backend/tests/contract/test_observation_ingest_api.py`
- Create: `backend/tests/contract/test_observation_read_api.py`
- Create: `backend/tests/security/test_ingest_listener_isolation.py`
- Create: `backend/tests/integration/test_observation_round_trip.py`

**Interfaces:** `PUT /api/v2/observations` on Ingest only; authenticated `GET /api/v2/observations` and `GET /api/v2/observations/{identity_key}` on Public only; `create_public_app(container)` and `create_ingest_app(container)`.

- [ ] **Step 1: Write listener-isolation and no-token contract tests**

```python
payload = valid_observation_payload(details={"pid": 1234})
assert ingest_client.put("/api/v2/observations", json=payload).status_code == 201
assert public_client.put("/api/v2/observations", json=payload).status_code == 404
assert ingest_client.get("/api/v2/observations").status_code == 404
assert ingest_client.post("/api/terminals", json={"title": "escape"}).status_code == 404
```

Also assert a non-loopback actual peer is rejected even when `X-Forwarded-For: 127.0.0.1` is supplied, and that request JSON containing `producer_id` receives `422`.

- [ ] **Step 2: Write list/filter/stale/cursor and restart round-trip tests**

```python
response = public_client.get(
    "/api/v2/observations?namespace=eda&status=warning&limit=10",
    headers=auth_headers,
)
assert response.status_code == 200
assert response.json()["items"][0]["details"] == {"pid": 1234}
assert "next_cursor" in response.json()
```

- [ ] **Step 3: Run tests and verify route failures**

Run: `cd backend && pytest -q tests/contract/test_observation_ingest_api.py tests/contract/test_observation_read_api.py tests/security/test_ingest_listener_isolation.py tests/integration/test_observation_round_trip.py`

Expected: FAIL with 404/missing app factory behavior.

- [ ] **Step 4: Split public and ingest app assembly**

```python
def create_ingest_app(container: AgentContainer) -> FastAPI:
    app = FastAPI(title="IC Env Guard Local Ingest", docs_url=None, redoc_url=None)
    app.include_router(ingest_observations_router)
    app.add_middleware(
        IngestCapacityMiddleware,
        maximum=container.config.ingest.max_concurrent_requests,
        max_request_bytes=container.config.ingest.max_request_bytes,
    )
    return app
```

The peer guard uses `request.client.host`; it does not inspect proxy headers. Map capacity overflow to `503 ingest_capacity_exceeded`.

- [ ] **Step 5: Add response mapping and opaque pagination**

Use URL-safe base64 of a versioned JSON cursor containing the final sort tuple; reject malformed cursors with `422 invalid_cursor`. Return `201` only when the repository reports creation, otherwise `200`. Public handlers depend on `require_auth`.

```python
@router.put("/observations", response_model=ObservationResponse)
def put_observation(payload: ObservationRequest, service: ObservationService = Depends(get_service)):
    result = service.upsert(payload.to_domain(), now=datetime.now(UTC))
    return JSONResponse(result.record.to_public_dict(), status_code=201 if result.created else 200)
```

- [ ] **Step 6: Run contracts, integration, and auth regression**

Run: `cd backend && pytest -q tests/contract/test_observation_ingest_api.py tests/contract/test_observation_read_api.py tests/security/test_ingest_listener_isolation.py tests/integration/test_observation_round_trip.py tests/contract/test_auth_required.py`

Expected: PASS.

- [ ] **Step 7: Commit Observation adapters**

```bash
git add backend/ic_env_guard/api/ingest_observations.py backend/ic_env_guard/api/observations.py backend/ic_env_guard/api/ingest_guard.py backend/ic_env_guard/bootstrap/composition.py backend/ic_env_guard/main.py backend/tests/contract/test_observation_ingest_api.py backend/tests/contract/test_observation_read_api.py backend/tests/security/test_ingest_listener_isolation.py backend/tests/integration/test_observation_round_trip.py
git commit -m "feat: expose observation v2 APIs"
```

---

### Task 6: Implement Log Source policy, storage, and bounded tail

**Files:**
- Create: `backend/ic_env_guard/logs/__init__.py`
- Create: `backend/ic_env_guard/logs/models.py`
- Create: `backend/ic_env_guard/logs/ports.py`
- Create: `backend/ic_env_guard/logs/policy.py`
- Create: `backend/ic_env_guard/logs/service.py`
- Create: `backend/ic_env_guard/storage/log_sources.py`
- Modify: `backend/ic_env_guard/migrations/0003_observability.py`
- Create: `backend/tests/unit/test_log_path_policy.py`
- Create: `backend/tests/unit/test_log_source_service.py`
- Create: `backend/tests/unit/test_log_tail.py`
- Modify: `backend/tests/integration/test_migrations.py`

**Interfaces:** `LogSourceService.upsert/get/list/tail`; `LogPathPolicy.resolve_regular_file(path) -> Path`; `LogTailReader.read(path, lines, max_bytes) -> TailResult`; `LogSourceRepository` Protocol.

- [ ] **Step 1: Write path, symlink, file-type, and tail-boundary tests**

```python
def test_symlink_escape_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside.log"
    allowed.mkdir()
    outside.write_text("secret", encoding="utf-8")
    (allowed / "run.log").symlink_to(outside)
    with pytest.raises(LogPathForbidden):
        LogPathPolicy([allowed]).resolve_regular_file(allowed / "run.log")


def test_tail_replaces_invalid_utf8_and_respects_byte_limit(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes(b"first\ninvalid-\xff\nlast\n")
    result = LogTailReader(max_bytes=16).read(path, lines=100)
    assert "\ufffd" in "".join(result.lines)
    assert result.truncated is True
```

- [ ] **Step 2: Write Log Source ordering/idempotency/stale tests**

Mirror the Observation time rules, assert `producer_id == "local"`, and assert tail revalidates the path after registration.

- [ ] **Step 3: Run the focused tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_log_path_policy.py tests/unit/test_log_source_service.py tests/unit/test_log_tail.py tests/integration/test_migrations.py`

Expected: FAIL because Log modules/table are missing.

- [ ] **Step 4: Implement path containment without string-prefix checks**

```python
resolved = candidate.resolve(strict=True)
if not resolved.is_file():
    raise LogFileUnavailable("log target is not a regular file")
if not any(resolved.is_relative_to(root) for root in self._roots):
    raise LogPathForbidden("log path is outside allowed roots")
```

For tail, open with `os.open(..., os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)`, validate `fstat()` is a regular file, then resolve `/proc/self/fd/{fd}` and confirm the opened inode is still beneath an allowed root before reading from that descriptor. This ordering closes the check/open symlink race. Do not hold a database session while opening or reading. Reject directory, device, FIFO, socket, missing file, and post-registration replacement.

- [ ] **Step 5: Implement reverse chunk tailing and wire-size headroom**

Read from the end in bounded binary chunks until enough newline separators or `max_tail_bytes` are reached. Decode with `errors="replace"`; return at most the requested lines. Reserve JSON overhead so a 960 KiB content cap stays below the 1 MiB response cap.

- [ ] **Step 6: Add the `log_sources` table and adapter**

Add the exact columns and `expires_at`/`last_updated` indexes to `0003_observability.py`; force `producer_id="local"` on all writes.

- [ ] **Step 7: Run unit and migration tests, then commit**

Run: `cd backend && pytest -q tests/unit/test_log_path_policy.py tests/unit/test_log_source_service.py tests/unit/test_log_tail.py tests/integration/test_migrations.py tests/contract/test_migration_contract.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/logs backend/ic_env_guard/storage/log_sources.py backend/ic_env_guard/migrations/0003_observability.py backend/tests/unit/test_log_path_policy.py backend/tests/unit/test_log_source_service.py backend/tests/unit/test_log_tail.py backend/tests/integration/test_migrations.py
git commit -m "feat: add safe log source domain"
```

---

### Task 7: Expose Log Source ingest/read/tail APIs and audit tail access

**Files:**
- Create: `backend/ic_env_guard/api/ingest_logs.py`
- Create: `backend/ic_env_guard/api/logs.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Modify: `backend/ic_env_guard/main.py`
- Create: `backend/tests/contract/test_log_ingest_api.py`
- Create: `backend/tests/contract/test_log_read_api.py`
- Create: `backend/tests/security/test_log_tail_security.py`
- Create: `backend/tests/integration/test_log_tail_lifecycle.py`

**Interfaces:** Ingest-only `PUT /api/v2/logs/{log_id}`; authenticated Public `GET /api/v2/logs`, `GET /api/v2/logs/{log_id}`, and `GET /api/v2/logs/{log_id}/tail?lines=100`.

- [ ] **Step 1: Write API and listener isolation tests**

```python
created = ingest_client.put("/api/v2/logs/run-log", json=payload)
assert created.status_code == 201
assert public_client.put("/api/v2/logs/run-log", json=payload).status_code == 404
assert public_client.get("/api/v2/logs/run-log/tail", headers=auth_headers).json()["id"] == "run-log"
```

Cover 404 unknown ID, 410 stale/missing file, 403 moved path, 1–1000 line validation, 960 KiB truncation, invalid UTF-8, and total response size under 1 MiB.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && pytest -q tests/contract/test_log_ingest_api.py tests/contract/test_log_read_api.py tests/security/test_log_tail_security.py tests/integration/test_log_tail_lifecycle.py`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement thin route adapters**

```python
@router.get("/logs/{log_id}/tail", response_model=LogTailResponse)
def tail_log(
    log_id: str,
    lines: int = Query(default=100, ge=1, le=1000),
    actor: AuthContext = Depends(require_auth),
    service: LogSourceService = Depends(get_log_source_service),
) -> LogTailResponse:
    return LogTailResponse.from_domain(service.tail(log_id, lines=lines, now=datetime.now(UTC)))
```

Map domain codes exactly to the spec and audit only actor, log ID, requested line count, result, source address, and correlation ID.

- [ ] **Step 4: Verify SQLite and audit contain no log content**

In integration tests, query every text column in `log_sources` and `audit_events`; assert a unique log line never appears.

- [ ] **Step 5: Run log, audit, and secret-exclusion suites**

Run: `cd backend && pytest -q tests/contract/test_log_ingest_api.py tests/contract/test_log_read_api.py tests/security/test_log_tail_security.py tests/integration/test_log_tail_lifecycle.py tests/integration/test_secret_exclusion_global.py`

Expected: PASS.

- [ ] **Step 6: Commit Log API adapters**

```bash
git add backend/ic_env_guard/api/ingest_logs.py backend/ic_env_guard/api/logs.py backend/ic_env_guard/bootstrap/composition.py backend/ic_env_guard/main.py backend/tests/contract/test_log_ingest_api.py backend/tests/contract/test_log_read_api.py backend/tests/security/test_log_tail_security.py backend/tests/integration/test_log_tail_lifecycle.py
git commit -m "feat: expose safe log source APIs"
```

---

### Task 8: Add expiration cleanup, Agent summary, and Prometheus projection

**Files:**
- Create: `backend/ic_env_guard/observations/cleanup.py`
- Create: `backend/ic_env_guard/logs/cleanup.py`
- Create: `backend/ic_env_guard/summary/__init__.py`
- Create: `backend/ic_env_guard/summary/service.py`
- Create: `backend/ic_env_guard/api/summary.py`
- Create: `backend/ic_env_guard/metrics/observability.py`
- Modify: `backend/ic_env_guard/metrics/collector.py`
- Modify: `backend/ic_env_guard/metrics/registry.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Modify: `backend/ic_env_guard/agents/models.py`
- Create: `backend/tests/unit/test_observability_metrics.py`
- Create: `backend/tests/contract/test_agent_summary_api.py`
- Create: `backend/tests/integration/test_observation_expiration.py`
- Modify: `backend/tests/integration/test_metrics_cardinality.py`

**Interfaces:** authenticated `GET /api/v2/summary`; `ObservabilityCollector.collect()` read-only projection; bounded Observation and Log Source cleanup loops; capabilities add `observations.v2`, `logs.v2`, `summary.v2`; `MetricsConfig.max_observation_series`.

- [ ] **Step 1: Write fresh/stale metrics, series-cap, summary, and cleanup tests**

```python
metrics = scrape(agent_client)
assert 'ic_env_observation_value{namespace="eda",name="license_alive"' in metrics
clock.advance(seconds=121)
metrics = scrape(agent_client)
assert 'ic_env_observation_value{namespace="eda",name="license_alive"' not in metrics
assert 'ic_env_log_source_stale{log_id="run-log"} 1.0' in metrics
```

Assert details/message/unit/path never occur in scrape output and updating an existing identity is allowed at the series cap while a new identity is rejected.

- [ ] **Step 2: Run tests and verify missing projection failures**

Run: `cd backend && pytest -q tests/unit/test_observability_metrics.py tests/contract/test_agent_summary_api.py tests/integration/test_observation_expiration.py tests/integration/test_metrics_cardinality.py`

Expected: FAIL.

- [ ] **Step 3: Implement repository summary/count ports and read-only collector**

```python
@dataclass(frozen=True)
class AgentSummary:
    observed_at: datetime
    observations: ObservationCounts
    logs: LogCounts
    services: ServiceCounts
    terminals: TerminalCounts
```

Use fixed Prometheus metric names. Only validated producer labels are copied to `ic_env_observation_value`; `status` is the only dynamic label on the one-hot status metric; only `log_id` labels log metrics.

- [ ] **Step 4: Add bounded cleanup lifecycle**

```python
async def expiration_loop(service: ObservationService, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        while service.delete_expired(now=datetime.now(UTC), limit=500) == 500:
            await asyncio.sleep(0)
```

Run the same bounded retention behavior for stale Log Sources. Use configured retention cutoff and stop both tasks cleanly in lifespan. Cleanup failure increments an internal metric and does not terminate the Agent.

- [ ] **Step 5: Enforce series capacity atomically with upsert**

The repository/service must distinguish new identity from update. Check and insert in the same short write transaction so concurrent new identities cannot exceed `max_observation_series`.

- [ ] **Step 6: Run observability and existing metrics suites**

Run: `cd backend && pytest -q tests/unit/test_observability_metrics.py tests/contract/test_agent_summary_api.py tests/integration/test_observation_expiration.py tests/integration/test_metrics_cardinality.py tests/integration/test_metrics_allowlist.py tests/integration/test_metrics_refresh_loop.py`

Expected: PASS.

- [ ] **Step 7: Commit summary, cleanup, and metrics**

```bash
git add backend/ic_env_guard/observations/cleanup.py backend/ic_env_guard/logs/cleanup.py backend/ic_env_guard/summary backend/ic_env_guard/api/summary.py backend/ic_env_guard/metrics/observability.py backend/ic_env_guard/metrics/collector.py backend/ic_env_guard/metrics/registry.py backend/ic_env_guard/config/models.py backend/ic_env_guard/bootstrap/lifecycle.py backend/ic_env_guard/agents/models.py backend/tests/unit/test_observability_metrics.py backend/tests/contract/test_agent_summary_api.py backend/tests/integration/test_observation_expiration.py backend/tests/integration/test_metrics_cardinality.py
git commit -m "feat: add agent summary and observability metrics"
```

---

### Task 9: Implement Manager-specific credential domain and Agent auth integration

**Files:**
- Create: `backend/ic_env_guard/enrollment/__init__.py`
- Create: `backend/ic_env_guard/enrollment/models.py`
- Create: `backend/ic_env_guard/enrollment/ports.py`
- Create: `backend/ic_env_guard/enrollment/service.py`
- Create: `backend/ic_env_guard/storage/manager_credentials.py`
- Create: `backend/ic_env_guard/migrations/0004_manager_credentials.py`
- Modify: `backend/ic_env_guard/auth/dependencies.py`
- Create: `backend/tests/unit/test_manager_credentials.py`
- Create: `backend/tests/contract/test_manager_credential_api.py`
- Modify: `backend/tests/contract/test_migration_contract.py`
- Modify: `backend/tests/integration/test_migrations.py`

**Interfaces:** `EnrollmentService.issue_pending/activate/revoke/list`; token verifier returns `pending` or `manager:<manager_id>` context; Agent stores only a keyed password hash/verifier, never plaintext.

- [ ] **Step 1: Write pending TTL, one-time issuance, hash-only, activation, revoke, and authorization tests**

```python
issued = service.issue_pending(manager_id=MANAGER_ID, enrollment_id=ENROLLMENT_ID, now=NOW)
assert issued.token not in repository.dump_serialized_rows()
assert service.verify(issued.token, now=NOW).state == CredentialState.PENDING
assert service.activate(issued.credential_id, ENROLLMENT_ID, issued.token, now=NOW).state == CredentialState.ACTIVE
assert service.activate(issued.credential_id, ENROLLMENT_ID, issued.token, now=NOW).state == CredentialState.ACTIVE
```

Assert duplicate/expired enrollment IDs do not reissue a token, active tokens may revoke only the same Manager's credentials, local-admin may revoke any, and repeated revoke is idempotent.

- [ ] **Step 2: Run unit/contract/migration tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_manager_credentials.py tests/contract/test_manager_credential_api.py tests/contract/test_migration_contract.py tests/integration/test_migrations.py`

Expected: FAIL.

- [ ] **Step 3: Implement credential repository and constant-time verification**

```python
class ManagerCredentialVerifier(Protocol):
    def authenticate(self, token: str, now: datetime) -> ManagerCredentialContext | None: ...


token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
```

The token has at least 256 bits of entropy. Compare candidate/verifier using `hmac.compare_digest`. Persist only hash, state, IDs, and timestamps in the exact `manager_credentials` schema.

- [ ] **Step 4: Extend auth context without weakening local-admin**

Pending credentials may call only v2 capabilities, summary, and activation of their own credential. Active Manager credentials receive actor `manager:<manager_id>`. Local Ingest never calls this verifier.

- [ ] **Step 5: Implement credential metadata endpoints**

```text
GET    /api/v2/manager-credentials
POST   /api/v2/manager-credentials/{credential_id}/activate
DELETE /api/v2/manager-credentials/{credential_id}
```

Responses expose only credential/Manager IDs, state, and time fields. Never expose hash, token, enrollment socket payload, or key data.

- [ ] **Step 6: Run credential, auth, migration, and secret suites**

Run: `cd backend && pytest -q tests/unit/test_manager_credentials.py tests/contract/test_manager_credential_api.py tests/contract/test_auth_required.py tests/contract/test_migration_contract.py tests/integration/test_migrations.py tests/integration/test_secret_exclusion_global.py`

Expected: PASS.

- [ ] **Step 7: Commit Manager credential support**

```bash
git add backend/ic_env_guard/enrollment backend/ic_env_guard/storage/manager_credentials.py backend/ic_env_guard/migrations/0004_manager_credentials.py backend/ic_env_guard/auth/dependencies.py backend/tests/unit/test_manager_credentials.py backend/tests/contract/test_manager_credential_api.py backend/tests/contract/test_migration_contract.py backend/tests/integration/test_migrations.py
git commit -m "feat: add manager credential enrollment"
```

---

### Task 10: Add fixed enrollment helper and protected local Unix socket

**Files:**
- Create: `backend/ic_env_guard/enrollment/protocol.py`
- Create: `backend/ic_env_guard/enrollment/socket_server.py`
- Create: `backend/ic_env_guard/enrollment/helper.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/systemd/cli.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/contract/test_enrollment_helper_contract.py`
- Create: `backend/tests/security/test_enrollment_socket.py`
- Create: `backend/tests/integration/test_enrollment_helper.py`

**Interfaces:** fixed stdin/stdout `manager-enrollment.v1`; `ic-env-guard agent enroll-manager`; protected Agent local socket delegates to `EnrollmentService.issue_pending`.

- [ ] **Step 1: Write protocol size/schema/replay and socket peer tests**

```python
request = EnrollmentRequest.model_validate_json(
    b'{"protocol":"manager-enrollment.v1","manager_id":"2b576727-4f36-4f08-b90b-e8cbe98ebc80","enrollment_id":"01J2W4ABCDEFGHJKMNPQRSTVWXYZ"}'
)
assert request.protocol == "manager-enrollment.v1"
with pytest.raises(EnrollmentProtocolError, match="stdin exceeds 4096 bytes"):
    parse_request(b"x" * 4097)
```

Assert socket parent owner/mode, socket mode no wider than configured, `SO_PEERCRED` authorization, one submission, expiry, and no token in stderr/logs.

- [ ] **Step 2: Run tests and verify missing helper failures**

Run: `cd backend && pytest -q tests/contract/test_enrollment_helper_contract.py tests/security/test_enrollment_socket.py tests/integration/test_enrollment_helper.py`

Expected: FAIL.

- [ ] **Step 3: Implement exact bounded JSON protocol**

```python
class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["manager-enrollment.v1"]
    manager_id: UUID
    enrollment_id: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
```

Read at most 4097 bytes, produce at most 8192 bytes, output exactly one compact JSON object and one newline, and map safe errors to bounded stderr with nonzero exit status.

- [ ] **Step 4: Implement the fixed CLI path**

```python
agent = subparsers.add_parser("agent")
agent_subcommands = agent.add_subparsers(dest="agent_command", required=True)
agent_subcommands.add_parser("enroll-manager")
```

The helper accepts no URL, shell command, SSH option, identity path, or token argument. It talks only to the configured local Agent enrollment socket.

- [ ] **Step 5: Start and stop the socket server from Agent lifecycle**

Create the socket only in Agent mode; fail closed on unsafe directory owner/mode. Remove the owned socket on graceful shutdown. Add `manager-enrollment.v1` only when the server is healthy.

- [ ] **Step 6: Run protocol/security/integration tests and commit**

Run: `cd backend && pytest -q tests/contract/test_enrollment_helper_contract.py tests/security/test_enrollment_socket.py tests/integration/test_enrollment_helper.py tests/contract/test_agent_summary_api.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/enrollment/protocol.py backend/ic_env_guard/enrollment/socket_server.py backend/ic_env_guard/enrollment/helper.py backend/ic_env_guard/config/models.py backend/ic_env_guard/systemd/cli.py backend/pyproject.toml backend/tests/contract/test_enrollment_helper_contract.py backend/tests/security/test_enrollment_socket.py backend/tests/integration/test_enrollment_helper.py
git commit -m "feat: add fixed agent enrollment helper"
```

---

### Task 11: Build standalone-first frontend runtime and Agent features

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/eslint.config.js`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/app/RuntimeProvider.tsx`
- Create: `frontend/src/app/shell/AppShell.tsx`
- Create: `frontend/src/shared/api/client.ts`
- Create: `frontend/src/features/observations/api.ts`
- Create: `frontend/src/features/observations/ObservationsPage.tsx`
- Create: `frontend/src/features/logs/api.ts`
- Create: `frontend/src/features/logs/LogsPage.tsx`
- Create: `frontend/src/features/agent-settings/ManagerAccessPage.tsx`
- Create: `frontend/src/shared/styles/tokens.css`
- Create: `frontend/src/shared/styles/base.css`
- Modify: `frontend/src/pages/AppRoutes.tsx`
- Create: `frontend/tests/runtime-routing.test.tsx`
- Create: `frontend/tests/standalone-observations.test.tsx`
- Create: `frontend/tests/standalone-logs.test.tsx`
- Create: `frontend/tests/manager-access.test.tsx`
- Modify: `frontend/tests/app-routes.test.tsx`

**Interfaces:** shared `/api/v2/runtime` loader; Agent routes `/terminal`, `/services`, `/observations`, `/logs`, `/metrics`, `/audit`, `/settings/manager-access`; Manager mode temporarily mounts the existing `AppRoutes` at `/fleet` until Workstream B replaces it.

- [ ] **Step 1: Pin and install frontend architecture dependencies**

Replace every existing `latest` entry as well as adding the new packages. Use explicit semver ranges compatible with the installed React major and regenerate the lockfile:

```json
{
  "dependencies": {
    "@tanstack/react-query": "^5.81.5",
    "lucide-react": "^0.468.0",
    "react-router-dom": "^7.6.3"
  },
  "devDependencies": {
    "@eslint/js": "^9.29.0",
    "eslint-plugin-react-hooks": "^5.2.0",
    "typescript-eslint": "^8.34.1"
  }
}
```

Run: `cd frontend && npm install`

Expected: lockfile updates successfully and `npm audit` output is reviewed; do not change product scope to chase unrelated advisory major upgrades.

- [ ] **Step 2: Write runtime routing and standalone page tests**

```tsx
it('lands an authenticated standalone agent on terminal without a fleet selector', async () => {
  runtimeRequest.mockResolvedValue({ mode: 'agent', capabilities: ['terminals.v1'] });
  render(<App />);
  expect(await screen.findByText('Standalone Agent')).toBeTruthy();
  expect(screen.getByLabelText('Terminal page')).toBeTruthy();
  expect(screen.queryByLabelText('Active agent')).toBeNull();
});
```

Test expandable Observation details, Log tail default 100/truncation, Manager credential metadata/revoke confirmation, deep links, auth expiry, keyboard focus, and no secret-like fields rendered.

- [ ] **Step 3: Run frontend tests and verify failure**

Run: `cd frontend && npm test -- --run tests/runtime-routing.test.tsx tests/standalone-observations.test.tsx tests/standalone-logs.test.tsx tests/manager-access.test.tsx`

Expected: FAIL because the app/runtime/features do not exist.

- [ ] **Step 4: Add the shared client and runtime gate**

```tsx
const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { element: <AuthenticatedApp />, children: agentRoutes },
]);

export function RuntimeProvider({ children }: PropsWithChildren) {
  const query = useQuery({ queryKey: ['runtime'], queryFn: getRuntime, staleTime: Infinity });
  if (query.isPending) return <RuntimeLoading />;
  if (query.isError) return <RuntimeError onRetry={() => void query.refetch()} />;
  return <RuntimeContext.Provider value={query.data}>{children}</RuntimeContext.Provider>;
}
```

The shared client parses both legacy and v2 error envelopes during migration, attaches correlation IDs, supports AbortSignal, and never stores Agent credentials. Define Agent and Manager route modules with React Router `lazy` imports so Agent mode never downloads Fleet/Registry/Discovery chunks and Manager mode does not eagerly load standalone-only entry code.

- [ ] **Step 5: Add Agent routes while preserving Terminal mount behavior**

Use one Agent AppShell, no Agent selector, capability-gated nav, and a persistent Terminal layout route so tab switches do not destroy xterm sessions. Manager runtime delegates to the old Manager component at `/fleet` only as a temporary compatibility bridge.

- [ ] **Step 6: Add Observation, Log, and Manager Access feature pages**

```tsx
const observationsQuery = useQuery({
  queryKey: ['agent', 'local', 'observations', filters],
  queryFn: ({ signal }) => listObservations(filters, signal),
});
```

Render details in an expandable `<pre>` outside table columns. Tail requests use a stable Log ID and `lines=100`; show `truncated` explicitly. Manager Access lists only safe metadata and requires confirmation before revoke.

- [ ] **Step 7: Introduce semantic tokens and TypeScript-aware ESLint**

```css
:root {
  --color-canvas: #f6f8fb;
  --color-surface: #ffffff;
  --color-text: #111827;
  --color-muted: #4b5563;
  --color-border: #d1d5db;
  --color-action: #1d4ed8;
  --color-focus: #2563eb;
}
```

Configure `typescript-eslint` and `eslint-plugin-react-hooks` for `.ts/.tsx`. Maintain 44px targets, visible focus, text+icon status, reduced motion, and no runtime CDN fonts.

- [ ] **Step 8: Run all frontend checks and commit**

Run: `cd frontend && npm test && npm run build && npm run lint`

Expected: PASS.

```bash
git add frontend/package.json frontend/package-lock.json frontend/eslint.config.js frontend/src/main.tsx frontend/src/app frontend/src/shared frontend/src/features/observations frontend/src/features/logs frontend/src/features/agent-settings frontend/src/pages/AppRoutes.tsx frontend/tests/runtime-routing.test.tsx frontend/tests/standalone-observations.test.tsx frontend/tests/standalone-logs.test.tsx frontend/tests/manager-access.test.tsx frontend/tests/app-routes.test.tsx
git commit -m "feat: add standalone agent web experience"
```

---

### Task 12: Run dual listeners, package, document, and verify Agent Foundation

**Files:**
- Modify: `backend/ic_env_guard/main.py`
- Modify: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Modify: `backend/ic_env_guard/api/static.py`
- Modify: `start.sh`
- Create: `packaging/systemd/ic-env-guard@.service`
- Modify: `packaging/systemd/ic-env-guard.service`
- Modify: `packaging/runtime/README.md`
- Modify: `README.md`
- Create: `docs/agent-v2-operations.md`
- Create: `backend/tests/integration/test_public_ingest_listeners.py`
- Create: `backend/tests/contract/test_spa_static_routes.py`
- Modify: `backend/tests/integration/test_agent_startup.py`

**Interfaces:** `ic-env-guard` starts Public on configured `server.bind:server.port` and Ingest on `ingest.bind:ingest.port` in Agent mode; Manager mode starts Public only. `start.sh agent` uses 8765 Public and 8766 Ingest; `start.sh all` assigns a distinct Agent Public port and Ingest port without collision.

- [ ] **Step 1: Write real-socket listener isolation tests**

```python
async with httpx.AsyncClient(base_url=public_url) as public, httpx.AsyncClient(base_url=ingest_url) as ingest:
    assert (await public.get("/api/v2/runtime")).status_code == 200
    assert (await public.put("/api/v2/observations", json=payload)).status_code == 404
    assert (await ingest.put("/api/v2/observations", json=payload)).status_code == 201
    assert (await ingest.get("/api/v2/runtime")).status_code == 404
```

Also assert control-plane mode never binds the Ingest port and shutdown closes both servers/tasks. Add static-serving tests that `/terminal`, `/observations`, and Manager `/fleet` deep links return `index.html` only for browser HTML requests, while unknown `/api`, `/ws`, `/metrics`, `/healthz`, `/readyz`, and `/assets` paths remain 404 and are never rewritten to the SPA.

- [ ] **Step 2: Run integration tests and verify the current single-listener entrypoint fails**

Run: `cd backend && pytest -q tests/integration/test_public_ingest_listeners.py tests/contract/test_spa_static_routes.py tests/integration/test_agent_startup.py`

Expected: FAIL until the launcher starts both ASGI apps.

- [ ] **Step 3: Implement coordinated Uvicorn server lifecycle**

```python
async def serve_agent(config: AppConfig) -> None:
    container = build_agent_container(config, _resolve_state_db(None, config))
    public = uvicorn.Server(uvicorn.Config(create_public_app(container), host=config.server.bind, port=config.server.port))
    ingest = uvicorn.Server(uvicorn.Config(create_ingest_app(container), host=config.ingest.bind, port=config.ingest.port))
    await asyncio.gather(public.serve(), ingest.serve())
```

Share one container and lifecycle owner; do not run duplicate cleanup loops or create separate database engines per listener. Coordinate failure/shutdown so one failed listener stops the process.

Extend the static adapter with an explicit HTML-only SPA fallback after real asset lookup. Never let fallback intercept API/WebSocket/health/metrics prefixes or non-HTML requests.

- [ ] **Step 4: Update development and systemd packaging**

The service remains one process and one Linux user. Add a template unit whose instance selects the existing Linux account (`ic-env-guard@edaops.service` uses `User=%i`), and retain the old unit only as a clearly deprecated compatibility path. Never omit `User=` because that would run the Agent as root. Document that the selected account determines PTY/sudo authority and that TCP 8766 must never be reverse-proxied, forwarded, or firewalled open.

For `start.sh all`, preserve Manager Public `8765` and Agent Public `8766`, add Agent Ingest `8767`, and expose an `IC_ENV_GUARD_AGENT_INGEST_PORT` override. Standalone `start.sh agent` continues to use Public `8765` and Ingest `8766`.

- [ ] **Step 5: Document exact producer and enrollment examples**

```bash
curl --fail --request PUT http://127.0.0.1:8766/api/v2/observations \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"eda","name":"license_alive","kind":"gauge","value":1,"status":"ok","labels":{"server":"license01"},"details":{"pid":1234},"observed_at":"2026-07-11T10:00:00Z","ttl_seconds":120}'
```

Include upgrade/backup/rollback notes for `instance-id`, state DB, `manager_credentials`, the enrollment socket, allowed log roots, and recovery through legacy admin token before rolling back.

- [ ] **Step 6: Run full verification**

Run: `cd backend && pytest -q`

Expected: PASS.

Run: `cd backend && python -m ruff check ic_env_guard tests`

Expected: PASS.

Run: `cd frontend && npm test && npm run build && npm run lint`

Expected: PASS.

Run: `./start.sh config agent`

Expected: prints a valid Agent config containing distinct Public and Ingest ports.

- [ ] **Step 7: Commit the independently deployable Agent Foundation**

```bash
git add backend/ic_env_guard/main.py backend/ic_env_guard/bootstrap/lifecycle.py backend/ic_env_guard/api/static.py start.sh packaging/systemd/ic-env-guard@.service packaging/systemd/ic-env-guard.service packaging/runtime/README.md README.md docs/agent-v2-operations.md backend/tests/integration/test_public_ingest_listeners.py backend/tests/contract/test_spa_static_routes.py backend/tests/integration/test_agent_startup.py
git commit -m "docs: complete agent v2 deployment"
```

---

## Agent Foundation Completion Gate

Before starting Workstream B, verify all of the following from a clean checkout:

- [ ] Existing v1 Agent/PTY/service/audit tests pass unchanged.
- [ ] Standalone login lands on local Terminal and shows no Fleet selector.
- [ ] Public and Ingest listeners are distinct; Ingest is loopback-only, tokenless, and write-only.
- [ ] Observation details round-trip through SQLite, respect ordering/TTL, and never enter Prometheus labels.
- [ ] Log SQLite rows contain metadata only; tail cannot escape allowed roots and stays below wire limits.
- [ ] `/api/v2/summary` and `/api/v2/capabilities` are bounded and expose stable `instance_id`.
- [ ] Agent stores only Manager token hashes; helper/socket issuance, activation, and revoke are tested.
- [ ] Backend tests/lint and frontend tests/build/lint pass.

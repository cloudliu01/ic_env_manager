# Fleet Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static-YAML control plane with a durable, secure Manager that can register, enroll, validate, discover, monitor, proxy, edit, rotate, and remove multiple Agents through a usable route-based Fleet Console while keeping one lightweight Manager process and SQLite database.

**Architecture:** Build Manager-only modules behind explicit ports: SQLite Registry/status/journal repositories, owner-only Credential Store, target policy, enrollment saga with system OpenSSH/CLI adapters, bounded Discovery, and allowlisted HTTP/WebSocket proxies. The browser receives only Manager-owned session data and safe Agent metadata. Frontend server state lives in TanStack Query and Agent identity comes from URL route params, not a global selector.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite/WAL, httpx, system OpenSSH, Unix sockets, pytest; React, TypeScript, React Router, TanStack Query, lucide-react, Vitest, Testing Library.

## Global Constraints

- Complete and verify [the Agent Foundation plan](2026-07-11-agent-foundation-implementation-plan.md) first. This plan consumes `/api/v2/capabilities`, `/api/v2/summary`, and `manager-enrollment.v1`; Agent code must not import Manager modules.
- Treat [the approved design spec](../specs/2026-07-11-agent-observability-refactor-design.md) as the source of truth. This plan implements Workstream B only.
- Continue serving existing `/api/agents`, `/api/fleet/overview`, and Agent-scoped v1 proxy contracts during the compatibility period, but make SQLite Registry their runtime source.
- Manager SQLite stores configuration, safe status cache, job journal, and credential references only. It never copies Observation details, log content, Terminal content, plaintext tokens, SSH output, or private-key data.
- Plaintext Agent tokens exist only in owner-only Credential Store files. Registry and journal rows contain opaque references; browser responses/query cache/storage never contain tokens or credential paths.
- All dynamic endpoint validation, enrollment, probing, and proxying use one `AgentTargetPolicy`. Never accept an arbitrary upstream URL or generic method/path proxy from the browser.
- Invoke SSH with an argv array, never `shell=True`. Browser input cannot select an identity file, SSH option, ProxyJump, ProxyCommand, or remote command.
- Enrollment/rotation is a recoverable saga. Persist/fsync the temporary credential and journal before remote activation; never delete the last recovery reference while a non-terminal journal exists.
- Discovery scans only configured named scopes, bounded private CIDRs, and configured endpoints; a result is never registered without explicit enrollment.
- Every task starts with a failing test, ends with the narrow suite plus relevant compatibility tests, and is committed independently.
- Run backend commands inside the documented `venv312` Conda environment. If it is not already active, replace `pytest` with `conda run -n venv312 pytest` and `python` with `conda run -n venv312 python`.
- Commit only files listed by the current task. Preserve unrelated working-tree changes.

---

### Task 1: Freeze current Manager and Agent-scoped proxy contracts

**Files:**
- Modify: `backend/tests/contract/test_agents_api.py`
- Modify: `backend/tests/integration/test_multi_agent_monitoring.py`
- Modify: `backend/tests/integration/test_agent_terminal_websocket.py`
- Create: `backend/tests/contract/test_manager_v1_compatibility.py`
- Modify: `frontend/tests/agent-routing.test.tsx`
- Modify: `frontend/tests/fleet-overview.test.tsx`

**Interfaces:** Existing Manager login, `/api/agents`, `/api/fleet/overview`, `/api/agents/{agent_id}/...`, and Manager Terminal ticket/WebSocket behavior are frozen. No production changes in this task.

- [ ] **Step 1: Add explicit safe-response and scoped-routing assertions**

```python
@pytest.mark.contract
def test_manager_v1_inventory_and_scoped_routes_remain_available(manager_client):
    agents = manager_client.get("/api/agents", headers=AUTH).json()["agents"]
    assert agents[0]["id"] == "lab-01"
    assert "token_file" not in agents[0]
    assert "credential_ref" not in agents[0]
    response = manager_client.get("/api/agents/lab-01/services", headers=AUTH)
    assert response.status_code == 200
```

- [ ] **Step 2: Add WebSocket Agent binding regression**

Assert a Manager Terminal ticket issued for `lab-01` cannot attach to `lab-02`, is single-use, and still enforces proxy capacity/frame limits.

- [ ] **Step 3: Run Manager compatibility slices**

Run: `cd backend && pytest -q tests/contract/test_manager_v1_compatibility.py tests/contract/test_agents_api.py tests/integration/test_multi_agent_monitoring.py tests/integration/test_agent_terminal_websocket.py`

Expected: PASS before refactoring.

Run: `cd frontend && npm test -- --run tests/agent-routing.test.tsx tests/fleet-overview.test.tsx`

Expected: PASS before refactoring.

- [ ] **Step 4: Commit the compatibility baseline**

```bash
git add backend/tests/contract/test_manager_v1_compatibility.py backend/tests/contract/test_agents_api.py backend/tests/integration/test_multi_agent_monitoring.py backend/tests/integration/test_agent_terminal_websocket.py frontend/tests/agent-routing.test.tsx frontend/tests/fleet-overview.test.tsx
git commit -m "test: freeze manager compatibility contracts"
```

---

### Task 2: Add Manager identity, Registry schema, and Credential Store

**Files:**
- Create: `backend/ic_env_guard/fleet/__init__.py`
- Create: `backend/ic_env_guard/fleet/models.py`
- Create: `backend/ic_env_guard/fleet/ports.py`
- Create: `backend/ic_env_guard/storage/manager_registry.py`
- Create: `backend/ic_env_guard/storage/enrollment_journal.py`
- Create: `backend/ic_env_guard/enrollment/credential_store.py`
- Create: `backend/ic_env_guard/control_plane_migrations/0002_fleet_registry.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Create: `backend/tests/unit/test_credential_store.py`
- Create: `backend/tests/unit/test_manager_identity.py`
- Create: `backend/tests/unit/test_registry_repository.py`
- Modify: `backend/tests/contract/test_migration_contract.py`

**Interfaces:** `ManagerRegistryRepository`, `AgentStatusRepository`, `EnrollmentJournalRepository`; `CredentialStore.put/read/replace/delete`; stable `manager_id` in Manager metadata; exact `agents`, `agent_status`, and `agent_enrollment_jobs` schemas from the spec.

- [ ] **Step 1: Write migration, Manager identity, repository invariant, and file-permission tests**

```python
def test_manager_id_is_created_once_and_persisted(registry):
    first = registry.get_or_create_manager_id()
    second = registry.get_or_create_manager_id()
    assert first == second
    assert str(first) == str(UUID(str(first)))


def test_credential_store_creates_owner_only_atomic_file(tmp_path):
    store = CredentialStore(tmp_path)
    reference = store.put(b"manager-token")
    path = store.resolve_for_test(reference)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == b"manager-token"
```

Assert duplicate agent ID, non-null instance ID, normalized endpoint, and journal state invariants are rejected atomically.

- [ ] **Step 2: Run tests and verify missing schema/modules**

Run: `cd backend && pytest -q tests/unit/test_credential_store.py tests/unit/test_manager_identity.py tests/unit/test_registry_repository.py tests/contract/test_migration_contract.py`

Expected: FAIL.

- [ ] **Step 3: Create the Manager migration and pure port models**

```python
class ManagerRegistryRepository(Protocol):
    def create(self, record: AgentRecord) -> AgentRecord: ...
    def get(self, agent_id: str) -> AgentRecord | None: ...
    def list(self, query: AgentQuery) -> AgentPage: ...
    def update_if_revision(self, record: AgentRecord, expected_revision: int) -> AgentRecord: ...
    def delete(self, agent_id: str) -> None: ...
```

Create Manager metadata, `agents`, `agent_status`, and `agent_enrollment_jobs`; keep them separate from Agent state migrations. Add all unique constraints and foreign keys specified in the design.

- [ ] **Step 4: Implement fail-closed Credential Store**

Validate directory owner and mode no wider than `0700`. Use opaque random filenames, same-directory temp file, `0600`, flush, `fsync`, atomic rename, and directory `fsync`. References must not contain a path separator.

```python
reference = secrets.token_hex(24)
if "/" in reference or reference in {".", ".."}:
    raise CredentialStoreError("invalid credential reference")
```

- [ ] **Step 5: Implement orphan cleanup with journal awareness**

Delete a credential file only when neither `agents.credential_ref` nor any non-terminal enrollment/rotation journal references it. Return cleanup findings as safe metadata for startup audit.

- [ ] **Step 6: Run migration/storage tests and commit**

Run: `cd backend && pytest -q tests/unit/test_credential_store.py tests/unit/test_manager_identity.py tests/unit/test_registry_repository.py tests/contract/test_migration_contract.py tests/integration/test_control_plane_audit.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/fleet backend/ic_env_guard/storage/manager_registry.py backend/ic_env_guard/storage/enrollment_journal.py backend/ic_env_guard/enrollment/credential_store.py backend/ic_env_guard/control_plane_migrations/0002_fleet_registry.py backend/ic_env_guard/config/models.py backend/ic_env_guard/bootstrap/composition.py backend/tests/unit/test_credential_store.py backend/tests/unit/test_manager_identity.py backend/tests/unit/test_registry_repository.py backend/tests/contract/test_migration_contract.py
git commit -m "feat: add durable manager registry storage"
```

---

### Task 3: Implement transport profiles and a shared Agent target policy

**Files:**
- Create: `backend/ic_env_guard/fleet/target_policy.py`
- Create: `backend/ic_env_guard/fleet/transport.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/agents/client.py`
- Create: `backend/tests/unit/test_agent_target_policy.py`
- Create: `backend/tests/unit/test_transport_profiles.py`
- Create: `backend/tests/security/test_dynamic_agent_ssrf.py`

**Interfaces:** `AgentTargetPolicy.resolve(endpoint, profile) -> ValidatedTarget`; `TransportProfile` discriminated union for `verified_tls` and `trusted_lan_http`; HTTP client connects to pinned validated IP while preserving TLS SNI/HTTP Host.

- [ ] **Step 1: Write scheme/CIDR/DNS/self-target/rebinding/redirect tests**

```python
@pytest.mark.parametrize("address", [
    "127.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0",
])
def test_dynamic_targets_reject_forbidden_ranges(policy, address):
    with pytest.raises(TargetPolicyError):
        policy.resolve(f"https://{address}:8765", VERIFIED_TLS)


def test_trusted_lan_profile_requires_http_and_private_allowlist(policy):
    target = policy.resolve("http://10.20.30.10:8765", TRUSTED_LAN)
    assert target.warning_code == "trusted_lan_http_unencrypted"
```

Assert all DNS A/AAAA answers are allowed, no redirects are followed, and Manager self-target is rejected.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_agent_target_policy.py tests/unit/test_transport_profiles.py tests/security/test_dynamic_agent_ssrf.py`

Expected: FAIL.

- [ ] **Step 3: Add discriminated config and fail-closed validation**

```python
class VerifiedTlsProfile(BaseModel):
    id: str
    type: Literal["verified_tls"]
    ca_bundle: Path | None = None


class TrustedLanHttpProfile(BaseModel):
    id: str
    type: Literal["trusted_lan_http"]
    allowed_cidrs: list[IPvAnyNetwork]
```

Validate unique IDs, private CIDRs, subset relation to `allowed_agent_cidrs`, CA file safety, and reserved `system-tls` semantics at startup. The Public API exposes profile ID/type/warning only, never CA paths.

- [ ] **Step 4: Implement one reusable target policy**

Normalize scheme/IDNA hostname/effective port with no path/query/fragment. Resolve once, validate every result, select/pin one address, preserve original hostname for TLS SNI and HTTP Host, and return immutable validated target data.

- [ ] **Step 5: Adapt `AgentHttpClient` to credential references and validated targets**

```python
async def request(
    self,
    target: ValidatedTarget,
    credential: bytes,
    method: str,
    path: str,
    *,
    correlation_id: str | None = None,
) -> httpx.Response:
    ...
```

The client remains bounded to 1 MiB JSON for normal endpoints, refuses redirects, disables environment proxies, and maps network/TLS/auth/protocol errors to stable categories. Log tail uses a separately bounded response path.

- [ ] **Step 6: Run policy/client/security tests and commit**

Run: `cd backend && pytest -q tests/unit/test_agent_target_policy.py tests/unit/test_transport_profiles.py tests/security/test_dynamic_agent_ssrf.py tests/unit/test_agent_client.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/fleet/target_policy.py backend/ic_env_guard/fleet/transport.py backend/ic_env_guard/config/models.py backend/ic_env_guard/agents/client.py backend/tests/unit/test_agent_target_policy.py backend/tests/unit/test_transport_profiles.py backend/tests/security/test_dynamic_agent_ssrf.py backend/tests/unit/test_agent_client.py
git commit -m "feat: enforce manager agent target policy"
```

---

### Task 4: Migrate YAML Agents once and switch v1 inventory to SQLite

**Files:**
- Create: `backend/ic_env_guard/fleet/importer.py`
- Create: `backend/ic_env_guard/fleet/registry.py`
- Modify: `backend/ic_env_guard/agents/registry.py`
- Modify: `backend/ic_env_guard/agents/availability.py`
- Modify: `backend/ic_env_guard/api/agents.py`
- Modify: `backend/ic_env_guard/api/fleet.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Create: `backend/tests/integration/test_yaml_agent_import.py`
- Create: `backend/tests/integration/test_registry_restart.py`
- Modify: `backend/tests/contract/test_manager_v1_compatibility.py`

**Interfaces:** SQLite is the only runtime Registry source after first import. YAML Agents import atomically only when the table is empty; source `config_import`, method `legacy_admin_token`, `instance_id=None`, status `unknown`/`disabled`.

- [ ] **Step 1: Write all-or-nothing import and restart persistence tests**

```python
def test_first_start_imports_yaml_once_and_web_changes_survive_restart(manager_factory):
    first = manager_factory(agents=[legacy_agent("lab-01")])
    first.registry.rename("lab-01", "Renamed")
    first.close()
    second = manager_factory(agents=[legacy_agent("lab-01", name="YAML Name")])
    assert second.registry.get("lab-01").display_name == "Renamed"
```

Cover duplicate ID/endpoint, unsafe token permissions, credential copy failure rollback, offline/old Agent not blocking local import, source token preservation, and disabled value.

Also reject import when the Manager administrator token equals any Agent token; Manager and Agent credentials must remain independent.

- [ ] **Step 2: Run tests and verify static Registry behavior fails persistence**

Run: `cd backend && pytest -q tests/integration/test_yaml_agent_import.py tests/integration/test_registry_restart.py tests/contract/test_manager_v1_compatibility.py`

Expected: FAIL.

- [ ] **Step 3: Implement importer transaction and credential copy compensation**

Validate every local input before writing. Copy every token into temporary Credential Store files, then commit Registry rows/status rows in one database transaction. On any failure, roll back DB and remove only newly copied files.

- [ ] **Step 4: Replace in-memory AgentRegistry internals**

Keep the v1-facing `get/list_summaries/set_enabled` methods temporarily, but delegate to the Fleet Registry service and status repository. Remove `app_config.agents` as the live source after bootstrap import.

- [ ] **Step 5: Make v1 API responses read safe SQLite projections**

Do not expose endpoint or credential data through legacy safe-summary endpoints unless the approved v2 Registry response explicitly allows the endpoint. Preserve existing status/capability field names.

- [ ] **Step 6: Run import, restart, v1 contract, and multi-Agent tests**

Run: `cd backend && pytest -q tests/integration/test_yaml_agent_import.py tests/integration/test_registry_restart.py tests/contract/test_manager_v1_compatibility.py tests/contract/test_agents_api.py tests/integration/test_multi_agent_monitoring.py`

Expected: PASS.

- [ ] **Step 7: Commit Registry source migration**

```bash
git add backend/ic_env_guard/fleet/importer.py backend/ic_env_guard/fleet/registry.py backend/ic_env_guard/agents/registry.py backend/ic_env_guard/agents/availability.py backend/ic_env_guard/api/agents.py backend/ic_env_guard/api/fleet.py backend/ic_env_guard/bootstrap/composition.py backend/tests/integration/test_yaml_agent_import.py backend/tests/integration/test_registry_restart.py backend/tests/contract/test_manager_v1_compatibility.py
git commit -m "feat: migrate manager agents to sqlite"
```

---

### Task 5: Add v2 Registry, status cache, probe, and Fleet summary APIs

**Files:**
- Create: `backend/ic_env_guard/fleet/status.py`
- Create: `backend/ic_env_guard/fleet/probes.py`
- Create: `backend/ic_env_guard/api/agent_registry.py`
- Create: `backend/ic_env_guard/api/fleet_v2.py`
- Modify: `backend/ic_env_guard/api/runtime.py`
- Modify: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Modify: `backend/ic_env_guard/bootstrap/composition.py`
- Create: `backend/tests/unit/test_fleet_status.py`
- Create: `backend/tests/contract/test_agent_registry_v2.py`
- Create: `backend/tests/contract/test_fleet_overview_v2.py`
- Modify: `backend/tests/contract/test_runtime_api.py`
- Create: `backend/tests/integration/test_fleet_probe_cache.py`

**Interfaces:** authenticated `GET /api/v2/agents`, `GET /api/v2/agents/{agent_id}`, `POST /api/v2/agents/{agent_id}/probe`, `POST /api/v2/agents/{agent_id}/enabled`, `GET /api/v2/fleet/overview`; unauthenticated low-risk `GET /api/v2/runtime` reports `mode=manager` and only locally available capability IDs; revision-checked status writes.

- [ ] **Step 1: Write connection/workload transition and partial-failure tests**

```python
assert derive_connection_status(agent_enabled=False, probe=None, now=NOW) == "disabled"
assert derive_connection_status(agent_enabled=True, probe=stale_probe(), now=NOW) == "unknown"
assert derive_workload_status({"observations": {"critical": 1}}) == "critical"
```

Assert one timed-out Agent does not block others, last-known summary stays visible/stale, disabled Agents are never probed/routed, and a late probe with old target revision cannot update status. If probes reveal the same non-null `instance_id` on multiple imported rows, mark every conflicting row unavailable with `agent_identity_conflict` and block privileged routing until an administrator resolves it; never silently overwrite either row.

- [ ] **Step 2: Write Registry list/detail/filter/cursor safe-response tests**

Cover query, connection/workload/capability filters, limit 1–1000, opaque cursor, all statuses, and absence of token/path/Authorization/SSH fields.

Add Manager runtime tests for `fleet.v2`, `agent-registry.v2`, and capabilities that reflect healthy local adapters only. The response is `Cache-Control: no-store` and never exposes socket paths, key paths, usernames, CIDRs, profiles, or Agent inventory.

- [ ] **Step 3: Run tests and verify missing endpoints**

Run: `cd backend && pytest -q tests/unit/test_fleet_status.py tests/contract/test_agent_registry_v2.py tests/contract/test_fleet_overview_v2.py tests/contract/test_runtime_api.py tests/integration/test_fleet_probe_cache.py`

Expected: FAIL.

- [ ] **Step 4: Implement bounded v2 probing**

Read Agent row and revision, target policy, and Credential Store token; request `/api/v2/capabilities` then `/api/v2/summary`. Use semaphore, jitter, per-Agent timeouts, and a 15-second default interval. Write status only if revision still matches.

```python
updated = status_repository.update_if_target_revision(
    agent_id=agent.agent_id,
    expected_revision=agent.revision,
    observation=observation,
)
```

- [ ] **Step 5: Implement safe API projections and audit intent/outcome**

Enable/disable and manual probe use existing durable control-plane audit. Audit intent failure prevents mutation/dispatch. Workload status remains separate from connection status and trusted-LAN warning.

- [ ] **Step 6: Run v2, v1, audit, and concurrency tests**

Run: `cd backend && pytest -q tests/unit/test_fleet_status.py tests/contract/test_agent_registry_v2.py tests/contract/test_fleet_overview_v2.py tests/contract/test_runtime_api.py tests/integration/test_fleet_probe_cache.py tests/contract/test_manager_v1_compatibility.py tests/integration/test_control_plane_audit.py`

Expected: PASS.

- [ ] **Step 7: Commit Fleet status APIs**

```bash
git add backend/ic_env_guard/fleet/status.py backend/ic_env_guard/fleet/probes.py backend/ic_env_guard/api/agent_registry.py backend/ic_env_guard/api/fleet_v2.py backend/ic_env_guard/api/runtime.py backend/ic_env_guard/bootstrap/lifecycle.py backend/ic_env_guard/bootstrap/composition.py backend/tests/unit/test_fleet_status.py backend/tests/contract/test_agent_registry_v2.py backend/tests/contract/test_fleet_overview_v2.py backend/tests/contract/test_runtime_api.py backend/tests/integration/test_fleet_probe_cache.py
git commit -m "feat: add manager registry and fleet v2 APIs"
```

---

### Task 6: Implement durable enrollment jobs and validation preview

**Files:**
- Create: `backend/ic_env_guard/enrollment/jobs.py`
- Create: `backend/ic_env_guard/enrollment/orchestrator.py`
- Create: `backend/ic_env_guard/enrollment/agent_client.py`
- Create: `backend/ic_env_guard/api/agent_enrollments.py`
- Modify: `backend/ic_env_guard/storage/enrollment_journal.py`
- Create: `backend/tests/unit/test_enrollment_jobs.py`
- Create: `backend/tests/contract/test_agent_enrollment_api.py`
- Create: `backend/tests/integration/test_enrollment_recovery.py`

**Interfaces:** `POST /api/v2/agent-enrollments`, `GET /api/v2/agent-enrollments/{id}`, `POST /api/v2/agent-enrollments/{id}/cancel`, advanced `POST /api/v2/agents/validate`; public states and phase preview from the spec; legacy token is write-only.

- [ ] **Step 1: Write job TTL/capacity/state/single-consume/input-binding tests**

```python
job = jobs.create(request, now=NOW)
assert job.state == EnrollmentState.PENDING
assert jobs.cancel(job.id, now=NOW).state == EnrollmentState.CANCELLED
with pytest.raises(EnrollmentConflict, match="agent_enrollment_consumed"):
    jobs.consume(consumed_job.id, display_name="Lab", now=NOW)
```

Assert journal never serializes token, SSH output, private-key path, passphrase, Authorization, or `SSH_AUTH_SOCK`.

- [ ] **Step 2: Write preview and legacy v1 degradation contracts**

Preview exposes network/ssh/transport/authentication/protocol/identity/capabilities/readiness phases. Legacy `/api/capabilities` without `instance_id` creates a verified one-time job with identity warning, Manager-generated Agent ID, `instance_id=null`, and no v2 routes assumed.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_enrollment_jobs.py tests/contract/test_agent_enrollment_api.py tests/integration/test_enrollment_recovery.py`

Expected: FAIL.

- [ ] **Step 4: Implement durable job state machine**

```python
ALLOWED_TRANSITIONS = {
    "pending": {"running", "awaiting_cli", "cancelled", "expired"},
    "credential_issued": {"verifying", "cancelled", "expired"},
    "verifying": {"verified", "failed", "cancelled", "expired"},
    "verified": {"activation_requested", "cancelled", "expired"},
    "activation_requested": {"activated", "failed"},
    "activated": {"consumed", "failed"},
}
```

Repository transitions compare current state and update atomically. Persist remote credential ID and owner-only temporary credential reference immediately after issuance, before validation.

- [ ] **Step 5: Implement validation client and safe preview**

Pending token may call only capabilities/summary/activation. Verify helper and HTTP `instance_id` match, API version/capabilities are supported, endpoint/profile/input fingerprint is unchanged, and summary/readiness failure is warning-only. Return stable codes, not raw exceptions.

- [ ] **Step 6: Implement startup recovery for every phase boundary**

At startup scan non-terminal jobs. Resume verification for valid pending credentials; for `activation_requested/activated` plus `save_requested`, finish local commit or retain a visible residual; never run orphan cleanup first.

- [ ] **Step 7: Run enrollment/recovery/secret tests and commit**

Run: `cd backend && pytest -q tests/unit/test_enrollment_jobs.py tests/contract/test_agent_enrollment_api.py tests/integration/test_enrollment_recovery.py tests/integration/test_secret_exclusion_global.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/enrollment/jobs.py backend/ic_env_guard/enrollment/orchestrator.py backend/ic_env_guard/enrollment/agent_client.py backend/ic_env_guard/api/agent_enrollments.py backend/ic_env_guard/storage/enrollment_journal.py backend/tests/unit/test_enrollment_jobs.py backend/tests/contract/test_agent_enrollment_api.py backend/tests/integration/test_enrollment_recovery.py
git commit -m "feat: add recoverable agent enrollment jobs"
```

---

### Task 7: Add safe system OpenSSH auto enrollment

**Files:**
- Create: `backend/ic_env_guard/enrollment/ssh.py`
- Create: `backend/ic_env_guard/enrollment/ssh_config.py`
- Modify: `backend/ic_env_guard/enrollment/orchestrator.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/api/runtime.py`
- Create: `backend/tests/unit/test_ssh_argv.py`
- Create: `backend/tests/security/test_ssh_enrollment_security.py`
- Create: `backend/tests/integration/test_ssh_auto_enrollment.py`

**Interfaces:** `SshEnrollmentAdapter.issue(request, profile) -> EnrollmentHelperResult`; fixed `/usr/bin/ssh` or configured absolute executable; fixed remote helper `ic-env-guard agent enroll-manager`; 4 KiB stdin, 8 KiB stdout, bounded stderr/time.

- [ ] **Step 1: Write user/host/port/option injection and effective-config tests**

```python
argv = build_ssh_argv(validated_target, profile=TRUSTED_LAN, batch_mode=True)
assert argv[0] == "/usr/bin/ssh"
assert argv[-1] == "ic-env-guard agent enroll-manager"
assert "PasswordAuthentication=no" in argv
assert "KbdInteractiveAuthentication=no" in argv
assert "ProxyCommand=none" in argv
assert "ProxyJump=none" in argv
assert all(";" not in item and "\n" not in item for item in argv)
```

Reject option-like username/host, control characters, port outside 1–65535, shell metacharacters, browser identity path/remote command/options, and config rewriting effective target.

- [ ] **Step 2: Write host-key profile and interaction-fallback tests**

Trusted-LAN first connection uses `accept-new`; verified-TLS auto uses strict `yes`; all changed host keys map to `ssh_host_key_changed`. Password, keyboard-interactive, encrypted-key prompt, or sudo prompt returns quickly as `ssh_interaction_required` and never blocks Web.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd backend && pytest -q tests/unit/test_ssh_argv.py tests/security/test_ssh_enrollment_security.py tests/integration/test_ssh_auto_enrollment.py`

Expected: FAIL.

- [ ] **Step 4: Implement target-pinned argv and `ssh -G` verification**

Use `Hostname=<validated-ip>`, validated User/Port, stable HostKeyAlias, disabled forwarding/local commands/canonicalization, fixed authentication methods, no TTY, and no shell. Run `ssh -G` with the same overrides; parse bounded output and verify effective hostname/user/port/proxy/command settings before actual connection.

- [ ] **Step 5: Implement bounded async subprocess execution**

```python
process = await asyncio.create_subprocess_exec(
    *argv,
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await asyncio.wait_for(process.communicate(stdin_payload), timeout=timeout)
```

Terminate then kill on timeout; bound stdout/stderr while streaming, validate exact helper JSON, and redact diagnostics. Never log argv if it contains sensitive local environment-derived data.

- [ ] **Step 6: Integrate auto path and CLI fallback state**

User-process Manager uses `BatchMode=yes` and existing OpenSSH config/agent. Interaction-required transitions to `awaiting_cli`; auth/host-key-changed/protocol errors transition to safe failed codes.

Advertise `ssh-enrollment.auto.v1` only after the configured SSH executable and safe effective-config check are available; runtime does not expose its path or environment.

- [ ] **Step 7: Run SSH/enrollment/security tests and commit**

Run: `cd backend && pytest -q tests/unit/test_ssh_argv.py tests/security/test_ssh_enrollment_security.py tests/integration/test_ssh_auto_enrollment.py tests/contract/test_agent_enrollment_api.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/enrollment/ssh.py backend/ic_env_guard/enrollment/ssh_config.py backend/ic_env_guard/enrollment/orchestrator.py backend/ic_env_guard/config/models.py backend/ic_env_guard/api/runtime.py backend/tests/unit/test_ssh_argv.py backend/tests/security/test_ssh_enrollment_security.py backend/tests/integration/test_ssh_auto_enrollment.py
git commit -m "feat: add safe ssh agent enrollment"
```

---

### Task 8: Add systemd CLI submission socket and optional restricted service key

**Files:**
- Create: `backend/ic_env_guard/enrollment/manager_socket.py`
- Create: `backend/ic_env_guard/enrollment/cli.py`
- Create: `backend/ic_env_guard/enrollment/service_key.py`
- Modify: `backend/ic_env_guard/systemd/cli.py`
- Modify: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Modify: `backend/ic_env_guard/api/runtime.py`
- Modify: `backend/pyproject.toml`
- Create: `packaging/ssh/ic-env-guard-enrollment-authorized-key.example`
- Create: `backend/tests/security/test_manager_enrollment_socket.py`
- Create: `backend/tests/unit/test_service_key_policy.py`
- Create: `backend/tests/integration/test_cli_enrollment.py`

**Interfaces:** `ic-env-guardctl agent enroll --manager-socket <configured> --enrollment-id <id> --ssh user@host`; protected Manager local socket; optional local-config-only service Ed25519 key.

- [ ] **Step 1: Write peer credential/mode/replay and CLI-secret tests**

Assert parent/owner/group/mode fail closed, `SO_PEERCRED` matches configured policy, enrollment ID/TTL/target are bound, second submission is rejected, and token never enters argv, stdout, shell history, logs, or browser responses.

- [ ] **Step 2: Write service-key file and forced-command policy tests**

Require owner-only regular Ed25519 private key, strict known_hosts/Host CA, and local configuration only. Verify the generated authorized-key template contains forced command plus `restrict`, `no-pty`, and forwarding prohibitions.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd backend && pytest -q tests/security/test_manager_enrollment_socket.py tests/unit/test_service_key_policy.py tests/integration/test_cli_enrollment.py`

Expected: FAIL.

- [ ] **Step 4: Implement CLI with the current user's OpenSSH environment**

The CLI may interact with the user's terminal for host-key confirmation/key unlock, but captures helper stdout, never echoes the token, and submits one bounded versioned JSON object to the Manager socket. It accepts only enrollment ID, configured socket, and parsed `user@host[:port]` target.

- [ ] **Step 5: Implement Manager socket one-way submission**

Validate peer credentials before reading payload, atomically claim awaiting job, verify input fingerprint and helper result, persist credential/journal, then continue through the same orchestrator as auto SSH. Reply with safe status only.

- [ ] **Step 6: Implement service-key adapter as the same SSH adapter profile**

Set explicit local identity/known_hosts from validated local config, strict host checking, and all fixed safety options. The browser capability `ssh-enrollment.service-key.v1` appears only when startup validation succeeds.

The runtime endpoint advertises `ssh-enrollment.cli.v1` only while the protected Manager socket is healthy, and `ssh-enrollment.service-key.v1` only while service-key validation is healthy. It never reports socket/key paths, group members, usernames, or SSH environment values.

- [ ] **Step 7: Run CLI/socket/service-key tests and commit**

Run: `cd backend && pytest -q tests/security/test_manager_enrollment_socket.py tests/unit/test_service_key_policy.py tests/integration/test_cli_enrollment.py tests/security/test_ssh_enrollment_security.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/enrollment/manager_socket.py backend/ic_env_guard/enrollment/cli.py backend/ic_env_guard/enrollment/service_key.py backend/ic_env_guard/systemd/cli.py backend/ic_env_guard/bootstrap/lifecycle.py backend/ic_env_guard/api/runtime.py backend/pyproject.toml packaging/ssh/ic-env-guard-enrollment-authorized-key.example backend/tests/security/test_manager_enrollment_socket.py backend/tests/unit/test_service_key_policy.py backend/tests/integration/test_cli_enrollment.py
git commit -m "feat: add cli and service-key enrollment"
```

---

### Task 9: Complete add/edit/remove/rotation enrollment saga

**Files:**
- Modify: `backend/ic_env_guard/enrollment/orchestrator.py`
- Modify: `backend/ic_env_guard/fleet/registry.py`
- Modify: `backend/ic_env_guard/api/agent_registry.py`
- Create: `backend/tests/contract/test_agent_mutation_v2.py`
- Create: `backend/tests/integration/test_agent_enrollment_saga.py`
- Create: `backend/tests/integration/test_credential_rotation.py`
- Create: `backend/tests/integration/test_agent_removal.py`

**Interfaces:** `POST /api/v2/agents` consumes verified enrollment; `PUT /api/v2/agents/{id}`; `DELETE /api/v2/agents/{id}` with explicit local-only confirmation; `POST /api/v2/agents/{id}/credential-rotation`.

- [ ] **Step 1: Write add identity/endpoint dedupe and one-time consume tests**

```python
created = client.post("/api/v2/agents", headers=AUTH, json={
    "enrollment_id": enrollment_id,
    "display_name": "EDA Host 01",
})
assert created.status_code == 201
assert "credential_ref" not in created.json()["agent"]
assert client.post("/api/v2/agents", headers=AUTH, json={
    "enrollment_id": enrollment_id,
    "display_name": "Again",
}).status_code == 409
```

- [ ] **Step 2: Write crash-boundary, rotation, edit, and removal tests**

Cover crash after credential issued, verified, activation requested, remote activated, and before local commit. Assert recovery commits or retains retryable residual. Rotation keeps old reference until new token is verified/activated and old revoke succeeds. URL/profile edit validates same instance ID and rolls back on failure. Active Terminal returns `409 agent_in_use`. Offline delete requires explicit local-only confirmation and leaves a visible remote-residual warning.

- [ ] **Step 3: Run saga tests and verify failure**

Run: `cd backend && pytest -q tests/contract/test_agent_mutation_v2.py tests/integration/test_agent_enrollment_saga.py tests/integration/test_credential_rotation.py tests/integration/test_agent_removal.py`

Expected: FAIL.

- [ ] **Step 4: Implement save-before-activate state transitions**

Persist `save_requested=true` and final display name in the journal transaction before remote activation. Activation is idempotent. After activation, atomically create Registry/status rows and transition consumed; only then release temporary references.

- [ ] **Step 5: Implement safe edit/rotation/removal compensation**

All mutations begin with durable audit intent. Update endpoint/profile only after same-identity validation at a captured revision. Rotate by preserving old and new references in journal until revoke confirmation. Online remove revokes remote credential first; local-only delete requires a distinct confirmed request field and audit outcome.

- [ ] **Step 6: Run mutation, recovery, audit, and secret tests**

Run: `cd backend && pytest -q tests/contract/test_agent_mutation_v2.py tests/integration/test_agent_enrollment_saga.py tests/integration/test_credential_rotation.py tests/integration/test_agent_removal.py tests/integration/test_control_plane_audit.py tests/integration/test_secret_exclusion_global.py`

Expected: PASS.

- [ ] **Step 7: Commit complete Registry mutation saga**

```bash
git add backend/ic_env_guard/enrollment/orchestrator.py backend/ic_env_guard/fleet/registry.py backend/ic_env_guard/api/agent_registry.py backend/tests/contract/test_agent_mutation_v2.py backend/tests/integration/test_agent_enrollment_saga.py backend/tests/integration/test_credential_rotation.py backend/tests/integration/test_agent_removal.py
git commit -m "feat: complete agent registry enrollment saga"
```

---

### Task 10: Implement bounded Discovery and candidate-to-enrollment binding

**Files:**
- Create: `backend/ic_env_guard/discovery/__init__.py`
- Create: `backend/ic_env_guard/discovery/models.py`
- Create: `backend/ic_env_guard/discovery/ports.py`
- Create: `backend/ic_env_guard/discovery/service.py`
- Create: `backend/ic_env_guard/storage/discovery.py`
- Create: `backend/ic_env_guard/control_plane_migrations/0003_discovery.py`
- Create: `backend/ic_env_guard/api/discovery.py`
- Modify: `backend/ic_env_guard/config/models.py`
- Modify: `backend/ic_env_guard/enrollment/jobs.py`
- Create: `backend/tests/unit/test_discovery_scope.py`
- Create: `backend/tests/contract/test_discovery_api.py`
- Create: `backend/tests/integration/test_discovery_jobs.py`
- Create: `backend/tests/security/test_discovery_boundaries.py`

**Interfaces:** named configured scopes; `POST /api/v2/discovery/jobs`, `GET /api/v2/discovery/jobs/{id}`, `POST /api/v2/discovery/jobs/{id}/cancel`, `GET /api/v2/discovery/jobs/{id}/results`; opaque result ID binds URL/IP/port/profile to a later enrollment.

- [ ] **Step 1: Write scope size/endpoint/concurrency/time/retention tests**

```python
with pytest.raises(ValueError, match="at most 256 addresses"):
    DiscoveryScope(id="too-large", name="Too large", cidr="10.0.0.0/16", endpoints=[])
```

Assert scope CIDR is private and a subset of Manager allowlist, at most eight configured endpoints, max concurrency, connect/fingerprint/job timeouts, cancel, dedupe, 24-hour cleanup, and no arbitrary CIDR/port in Public request.

- [ ] **Step 2: Write exact Agent fingerprint and binding tests**

Candidate requires exact low-risk runtime/fingerprint response with no redirect and bounded body. Discovery does not use Agent credentials. Enrollment with result ID must match stored normalized URL, resolved IP, port, and profile; `source=discovery` is derived server-side.

- [ ] **Step 3: Run tests and verify missing modules/endpoints**

Run: `cd backend && pytest -q tests/unit/test_discovery_scope.py tests/contract/test_discovery_api.py tests/integration/test_discovery_jobs.py tests/security/test_discovery_boundaries.py`

Expected: FAIL.

- [ ] **Step 4: Implement bounded job runner and repository**

Use one job task with semaphore, monotonic deadline, per-connect timeout, no redirects, safe aggregate errors, cancellation checkpoints, and unique result key. Persist checked/total/found counts and candidate states; do not persist raw socket errors.

- [ ] **Step 5: Add API and durable audit**

Start/cancel requires successful audit intent. API accepts only `scope_id`; results classify `new`, `already_registered`, or `enrollment_required`. Initial version never bulk-enrolls.

- [ ] **Step 6: Bind candidate to enrollment transactionally**

When an enrollment is created from a result, atomically claim/bind it, revalidate TTL/state and all target fields, and derive Registry source. Input mismatch returns stable `agent_validation_changed` or `transport_profile_mismatch`.

- [ ] **Step 7: Run discovery/security/enrollment suites and commit**

Run: `cd backend && pytest -q tests/unit/test_discovery_scope.py tests/contract/test_discovery_api.py tests/integration/test_discovery_jobs.py tests/security/test_discovery_boundaries.py tests/contract/test_agent_enrollment_api.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/discovery backend/ic_env_guard/storage/discovery.py backend/ic_env_guard/control_plane_migrations/0003_discovery.py backend/ic_env_guard/api/discovery.py backend/ic_env_guard/config/models.py backend/ic_env_guard/enrollment/jobs.py backend/tests/unit/test_discovery_scope.py backend/tests/contract/test_discovery_api.py backend/tests/integration/test_discovery_jobs.py backend/tests/security/test_discovery_boundaries.py
git commit -m "feat: add bounded agent discovery"
```

---

### Task 11: Add explicit v2 Agent-scoped proxies and preserve Terminal proxy

**Files:**
- Create: `backend/ic_env_guard/proxy/__init__.py`
- Create: `backend/ic_env_guard/proxy/http.py`
- Create: `backend/ic_env_guard/api/agent_observations.py`
- Create: `backend/ic_env_guard/api/agent_logs.py`
- Modify: `backend/ic_env_guard/api/agent_services.py`
- Modify: `backend/ic_env_guard/api/agent_audit.py`
- Modify: `backend/ic_env_guard/api/agent_terminals.py`
- Modify: `backend/ic_env_guard/api/agent_terminal_ws.py`
- Modify: `backend/ic_env_guard/agents/terminal_proxy.py`
- Create: `backend/tests/contract/test_agent_observation_proxy.py`
- Create: `backend/tests/contract/test_agent_log_proxy.py`
- Create: `backend/tests/security/test_agent_proxy_boundaries.py`
- Modify: `backend/tests/integration/test_agent_terminal_websocket.py`

**Interfaces:** explicit GET routes for Agent services/observations/logs/tail/audit; existing explicit service mutation and Terminal contracts; no generic proxy endpoint.

- [ ] **Step 1: Write allowed-route, capability, disabled, response-limit, and no-upstream-URL tests**

```python
response = manager_client.get(
    "/api/v2/agents/lab-01/observations?include_stale=true",
    headers=AUTH,
)
assert response.status_code == 200
assert upstream_requests == ["/api/v2/observations?include_stale=true"]
assert manager_client.post(
    "/api/v2/agents/lab-01/proxy",
    headers=AUTH,
    json={"url": "http://127.0.0.1:22"},
).status_code == 404
```

- [ ] **Step 2: Add Terminal ticket/Agent/revision/slot/backpressure regressions**

Assert Manager ticket binds actor+Agent, acquires local slot before upstream ticket, credential stays server-side, profile selects WSS/WS correctly, and closing/revising/removing Agent terminates or blocks new attach safely.

- [ ] **Step 3: Run proxy tests and verify missing routes/Registry integration**

Run: `cd backend && pytest -q tests/contract/test_agent_observation_proxy.py tests/contract/test_agent_log_proxy.py tests/security/test_agent_proxy_boundaries.py tests/integration/test_agent_terminal_websocket.py`

Expected: FAIL.

- [ ] **Step 4: Implement one explicit forwarding service**

```python
class AgentHttpProxy:
    async def get_json(self, agent_id: str, capability: str, upstream_path: str,
                       query: Mapping[str, str], correlation_id: str) -> ProxyResponse:
        agent = self._registry.require_routable(agent_id, capability)
        target = self._target_policy.resolve(agent.endpoint, agent.transport_profile)
        credential = self._credentials.read(agent.credential_ref)
        return await self._client.get_json(target, credential, upstream_path, query, correlation_id)
```

Each API module supplies a fixed upstream path template and validated query schema. Tail uses its dedicated 1 MiB response bound. Do not store proxied bodies in Manager DB/cache/audit.

- [ ] **Step 5: Migrate v1 proxy dependencies to SQLite Registry and Credential Store**

Preserve wire behavior but remove `AgentConfig.token_file` reliance. Disabled/unavailable/capability errors stay stable and safe. Privileged proxy intent/outcome keeps existing indeterminate mutation semantics.

- [ ] **Step 6: Run all proxy/v1/secret tests and commit**

Run: `cd backend && pytest -q tests/contract/test_agent_observation_proxy.py tests/contract/test_agent_log_proxy.py tests/security/test_agent_proxy_boundaries.py tests/integration/test_agent_terminal_websocket.py tests/contract/test_manager_v1_compatibility.py tests/integration/test_secret_exclusion_global.py`

Expected: PASS.

```bash
git add backend/ic_env_guard/proxy backend/ic_env_guard/api/agent_observations.py backend/ic_env_guard/api/agent_logs.py backend/ic_env_guard/api/agent_services.py backend/ic_env_guard/api/agent_audit.py backend/ic_env_guard/api/agent_terminals.py backend/ic_env_guard/api/agent_terminal_ws.py backend/ic_env_guard/agents/terminal_proxy.py backend/tests/contract/test_agent_observation_proxy.py backend/tests/contract/test_agent_log_proxy.py backend/tests/security/test_agent_proxy_boundaries.py backend/tests/integration/test_agent_terminal_websocket.py
git commit -m "feat: add registry-backed agent proxies"
```

---

### Task 12: Replace Manager view state with Router and Query architecture

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/RuntimeProvider.tsx`
- Modify: `frontend/src/app/shell/AppShell.tsx`
- Create: `frontend/src/features/agent-registry/types.ts`
- Create: `frontend/src/features/agent-registry/api.ts`
- Create: `frontend/src/features/agent-registry/queries.ts`
- Create: `frontend/src/features/fleet/api.ts`
- Create: `frontend/src/features/fleet/queries.ts`
- Create: `frontend/src/shared/components/CapabilityRoute.tsx`
- Create: `frontend/src/shared/components/RouteFocus.tsx`
- Modify: `frontend/src/shared/api/client.ts`
- Create: `frontend/tests/manager-router.test.tsx`
- Create: `frontend/tests/query-isolation.test.tsx`
- Modify: `frontend/tests/app-routes.test.tsx`

**Interfaces:** route source of truth exactly matches the spec; one canonical `Agent` type; query keys include Agent ID; no global active-Agent context.

- [ ] **Step 1: Write deep-link, refresh, back/forward, capability, and query-isolation tests**

```tsx
it('loads an agent deep link from the route id', async () => {
  renderManagerAt('/agents/agent-b/observations');
  expect(await screen.findByRole('heading', { name: 'Observations' })).toBeTruthy();
  expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-b/observations', expect.anything());
});
```

Simulate a slow `agent-a` response after navigation to `agent-b`; assert it cannot overwrite `agent-b`. Test query filters survive URL/back/refresh and unsupported capability gives an explanation/return link.

- [ ] **Step 2: Run router tests and verify current state machine failures**

Run: `cd frontend && npm test -- --run tests/manager-router.test.tsx tests/query-isolation.test.tsx tests/app-routes.test.tsx`

Expected: FAIL.

- [ ] **Step 3: Define the complete route tree**

```tsx
const managerRoutes: RouteObject[] = [
  { path: '/fleet', element: <FleetPage /> },
  { path: '/agents/new', element: <AddAgentPage /> },
  { path: '/discovery', element: <DiscoveryPage /> },
  { path: '/monitoring', element: <MonitoringPage /> },
  { path: '/audit', element: <ManagerAuditPage /> },
  { path: '/agents/:agentId', element: <AgentLayout />, children: agentDetailRoutes },
];
```

Use `/agents/:agentId/overview|terminal|services|observations|logs|metrics|audit|settings`. Agent route params, not context/sessionStorage, identify the target.

- [ ] **Step 4: Create canonical Agent types/query keys**

```typescript
export const agentKeys = {
  all: ['agents'] as const,
  list: (filters: AgentFilters) => ['agents', 'list', filters] as const,
  detail: (agentId: string) => ['agents', 'detail', agentId] as const,
  observations: (agentId: string, filters: ObservationFilters) =>
    ['agents', agentId, 'observations', filters] as const,
};
```

Delete duplicate `AgentSummary`/`FleetHost` use as features migrate. Pause polling when `document.visibilityState !== 'visible'`; mutations do not auto-retry.

- [ ] **Step 5: Add capability routing, route focus, and shared v2 error handling**

Render visible disabled tabs with reason; do not silently remove them. Focus the main heading after navigation. Errors use `role=alert`, status updates `aria-live=polite`, and correlation ID copy action.

- [ ] **Step 6: Run frontend tests/build/lint and commit**

Run: `cd frontend && npm test -- --run tests/manager-router.test.tsx tests/query-isolation.test.tsx tests/app-routes.test.tsx && npm run build && npm run lint`

Expected: PASS.

```bash
git add frontend/src/app/router.tsx frontend/src/app/RuntimeProvider.tsx frontend/src/app/shell/AppShell.tsx frontend/src/features/agent-registry frontend/src/features/fleet/api.ts frontend/src/features/fleet/queries.ts frontend/src/shared/components/CapabilityRoute.tsx frontend/src/shared/components/RouteFocus.tsx frontend/src/shared/api/client.ts frontend/tests/manager-router.test.tsx frontend/tests/query-isolation.test.tsx frontend/tests/app-routes.test.tsx
git commit -m "refactor: add route-based manager architecture"
```

---

### Task 13: Build Fleet table, Monitoring, and Agent detail shell

**Files:**
- Create: `frontend/src/features/fleet/FleetPage.tsx`
- Create: `frontend/src/features/fleet/FleetTable.tsx`
- Create: `frontend/src/features/fleet/FleetFilters.tsx`
- Create: `frontend/src/features/fleet/FleetCardList.tsx`
- Create: `frontend/src/features/fleet/MonitoringPage.tsx`
- Create: `frontend/src/features/agent-registry/AgentLayout.tsx`
- Create: `frontend/src/features/agent-registry/AgentOverviewPage.tsx`
- Create: `frontend/src/features/agent-registry/AgentSettingsPage.tsx`
- Modify: `frontend/src/shared/styles/tokens.css`
- Modify: `frontend/src/shared/styles/base.css`
- Create: `frontend/tests/fleet-table.test.tsx`
- Create: `frontend/tests/monitoring-page.test.tsx`
- Create: `frontend/tests/agent-detail-layout.test.tsx`

**Interfaces:** Manager `/fleet`, `/monitoring`, and Agent detail shell; responsive table-to-card behavior; query-string filters.

- [ ] **Step 1: Write table/filter/partial-error/responsive/accessibility tests**

Test the exact columns, status text+icon, URL/transport badge, version, Observation/Service counts, last probe, stable sorting, deferred search, query filters, empty state, per-row last-known error, click row/open/probe/actions, and 768px card fallback.

```tsx
expect(within(row).getByText('Degraded')).toBeTruthy();
expect(within(row).getByText('1 critical')).toBeTruthy();
expect(within(row).getByText('Unencrypted')).toBeTruthy();
```

- [ ] **Step 2: Write Agent detail header/tab and Monitoring tests**

Assert connection/workload are separate, trusted-LAN warning persists, missing-capability tabs show reason, last-known stale counts remain visible, Monitoring defaults to problems and links to Agent Overview.

- [ ] **Step 3: Run tests and verify missing components**

Run: `cd frontend && npm test -- --run tests/fleet-table.test.tsx tests/monitoring-page.test.tsx tests/agent-detail-layout.test.tsx`

Expected: FAIL.

- [ ] **Step 4: Implement data-dense Fleet and responsive card view**

Keep row height 44–48px, sticky header, semantic table markup, keyboard-open behavior, and an actions menu. Under 768px render a compact list card with status/name/problem summary/Open; do not horizontally squeeze all columns.

- [ ] **Step 5: Implement detail shell and Monitoring from cached summaries**

No global Agent selector. The detail layout fetches Registry detail by route ID and renders shared nested outlet. Monitoring uses Fleet summary data only; it does not fan out Observation/Log queries.

- [ ] **Step 6: Verify visual tokens and interactions**

Use semantic colors/z-index, visible focus, 44px targets, reduced motion, loading delay/skeleton, `role=alert`, and no color-only statuses. Test 375, 768, 1024, and 1440 px component layouts through deterministic `matchMedia`/container mocks.

- [ ] **Step 7: Run frontend checks and commit**

Run: `cd frontend && npm test -- --run tests/fleet-table.test.tsx tests/monitoring-page.test.tsx tests/agent-detail-layout.test.tsx && npm run build && npm run lint`

Expected: PASS.

```bash
git add frontend/src/features/fleet frontend/src/features/agent-registry/AgentLayout.tsx frontend/src/features/agent-registry/AgentOverviewPage.tsx frontend/src/features/agent-registry/AgentSettingsPage.tsx frontend/src/shared/styles/tokens.css frontend/src/shared/styles/base.css frontend/tests/fleet-table.test.tsx frontend/tests/monitoring-page.test.tsx frontend/tests/agent-detail-layout.test.tsx
git commit -m "feat: build manager fleet workspace"
```

---

### Task 14: Build Add/Edit/Enrollment/Discovery flows

**Files:**
- Create: `frontend/src/features/agent-registry/AddAgentPage.tsx`
- Create: `frontend/src/features/agent-registry/ConnectionStep.tsx`
- Create: `frontend/src/features/agent-registry/EnrollmentStep.tsx`
- Create: `frontend/src/features/agent-registry/VerifySaveStep.tsx`
- Create: `frontend/src/features/agent-registry/EditAgentForm.tsx`
- Create: `frontend/src/features/agent-registry/RemoveAgentDialog.tsx`
- Create: `frontend/src/features/agent-registry/enrollment-api.ts`
- Create: `frontend/src/features/agent-registry/enrollment-queries.ts`
- Create: `frontend/src/features/discovery/api.ts`
- Create: `frontend/src/features/discovery/queries.ts`
- Create: `frontend/src/features/discovery/DiscoveryPage.tsx`
- Create: `frontend/tests/add-agent-flow.test.tsx`
- Create: `frontend/tests/discovery-flow.test.tsx`
- Create: `frontend/tests/agent-settings-mutations.test.tsx`

**Interfaces:** three-step Add flow, advanced legacy import, editable safe fields, rotation/removal, named-scope Discovery with candidate prefill and one-by-one enrollment.

- [ ] **Step 1: Write automatic SSH, CLI waiting, expiry/retry/cancel, and secret-exclusion tests**

```tsx
await user.type(screen.getByLabelText('SSH user'), 'edaops');
await user.click(screen.getByRole('button', { name: 'Start enrollment' }));
expect(await screen.findByText('Waiting for CLI')).toBeTruthy();
expect(screen.getByText(/ic-env-guardctl agent enroll/)).toBeTruthy();
expect(document.body.textContent).not.toContain('write-only-pending-token');
```

Assert changing any Step 1 field cancels/clears old job, verified job can save once, refresh restores job by URL ID, failure shows stable recovery, trusted-LAN warning cannot be dismissed, and legacy token is a separate write-only advanced path.

- [ ] **Step 2: Write Discovery progress/cancel/result/binding tests**

Test named scope only, displayed CIDR/ports/address bound, progress/checked/total/found, cancel/retry, result states, and candidate link prepopulates immutable result ID plus URL/profile/SSH host while requiring user-confirmed SSH user.

- [ ] **Step 3: Write edit/rotation/remove recovery tests**

Display-name/enable edits are simple. Endpoint/profile change shows same-identity verification. Rotation reports residual cleanup. Remove says “Remove from Manager”; `agent_in_use` keeps dialog actionable; offline removal requires a second explicit local-only confirmation.

- [ ] **Step 4: Run tests and verify missing flow failures**

Run: `cd frontend && npm test -- --run tests/add-agent-flow.test.tsx tests/discovery-flow.test.tsx tests/agent-settings-mutations.test.tsx`

Expected: FAIL.

- [ ] **Step 5: Implement controlled forms and enrollment polling**

Use permanent labels/helper text, shape validation on blur, field errors plus summary, disabled progress button, and AbortSignal. Poll only the current job while visible/non-terminal; invalidate Fleet/Agent queries after save. Never place job secret data in route, storage, query cache, telemetry, or render tree.

- [ ] **Step 6: Implement Discovery and candidate handoff**

Job continues server-side across route changes. Store job ID in URL, fetch results by ID, and navigate to `/agents/new?discoveryResult=<opaque-id>`. Server data rehydrates all target fields; do not trust query-string copies.

- [ ] **Step 7: Run flow/accessibility/build/lint and commit**

Run: `cd frontend && npm test -- --run tests/add-agent-flow.test.tsx tests/discovery-flow.test.tsx tests/agent-settings-mutations.test.tsx tests/manager-router.test.tsx && npm run build && npm run lint`

Expected: PASS.

```bash
git add frontend/src/features/agent-registry/AddAgentPage.tsx frontend/src/features/agent-registry/ConnectionStep.tsx frontend/src/features/agent-registry/EnrollmentStep.tsx frontend/src/features/agent-registry/VerifySaveStep.tsx frontend/src/features/agent-registry/EditAgentForm.tsx frontend/src/features/agent-registry/RemoveAgentDialog.tsx frontend/src/features/agent-registry/enrollment-api.ts frontend/src/features/agent-registry/enrollment-queries.ts frontend/src/features/discovery frontend/tests/add-agent-flow.test.tsx frontend/tests/discovery-flow.test.tsx frontend/tests/agent-settings-mutations.test.tsx
git commit -m "feat: add agent enrollment and discovery ui"
```

---

### Task 15: Migrate Agent detail features and remove obsolete global state

**Files:**
- Move/Modify: `frontend/src/pages/TerminalPage.tsx` -> `frontend/src/features/terminal/TerminalPage.tsx`
- Move/Modify: `frontend/src/terminal/TerminalPane.tsx` -> `frontend/src/features/terminal/TerminalPane.tsx`
- Move/Modify: `frontend/src/pages/ServiceListPage.tsx` -> `frontend/src/features/services/ServicesPage.tsx`
- Move/Modify: `frontend/src/pages/MetricsPage.tsx` -> `frontend/src/features/metrics/MetricsPage.tsx`
- Move/Modify: `frontend/src/pages/AuditStatusPage.tsx` -> `frontend/src/features/audit/AuditPage.tsx`
- Modify: `frontend/src/features/observations/ObservationsPage.tsx`
- Modify: `frontend/src/features/logs/LogsPage.tsx`
- Delete: `frontend/src/agents/AgentContext.tsx`
- Delete: `frontend/src/agents/AgentSelector.tsx`
- Delete: `frontend/src/pages/AppRoutes.tsx`
- Delete: `frontend/src/pages/HostOverviewPage.tsx`
- Delete: `frontend/src/api/agents.ts`
- Delete: `frontend/src/api/fleet.ts`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/shared/styles/base.css`
- Modify: `frontend/tests/terminal-tabs.test.tsx`
- Modify: `frontend/tests/service-pages.test.tsx`
- Modify: `frontend/tests/monitoring-page.test.tsx`
- Modify: `frontend/tests/audit-status.test.tsx`
- Modify: `frontend/tests/metrics-page.test.tsx`

**Interfaces:** all Agent detail pages use route `agentId`; standalone pages reuse the same components with local target adapter; Terminal state/cache is keyed and isolated by Agent ID and remains mounted across sibling navigation where required.

- [ ] **Step 1: Rewrite feature tests around explicit `agentId` and route wrappers**

```tsx
renderWithRoute('/agents/agent-b/services');
expect(await screen.findByText('Beta service')).toBeTruthy();
expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-b/services', expect.anything());
```

Test service mutations do not retry, Observation details expand, Log tail truncation, Audit pagination/filters, metrics, Terminal tabs/reconnect/hidden mount, and switching Agent does not leak terminal/query state.

- [ ] **Step 2: Run migrated feature tests and verify old context assumptions fail**

Run: `cd frontend && npm test -- --run tests/terminal-tabs.test.tsx tests/service-pages.test.tsx tests/monitoring-page.test.tsx tests/audit-status.test.tsx tests/metrics-page.test.tsx`

Expected: FAIL until components use route/query architecture.

- [ ] **Step 3: Move each feature with feature-owned API/query modules**

Each feature owns endpoint functions, response types, query keys, components, and tests. Standalone routes pass `target="local"`; Manager routes pass URL Agent ID. Keep Terminal WebSocket outside Query cache and derive its URL/ticket server-side.

- [ ] **Step 4: Remove global active Agent and legacy duplicate APIs/styles**

Delete `AgentContext`, selector, view state machine, old Fleet cards, duplicate `AgentSummary`/`FleetHost`, and CSS selectors used only by deleted components. Do not remove v1 backend compatibility routes.

- [ ] **Step 5: Run search-based architecture checks**

Run: `rg -n "AgentContext|AgentSelector|activeAgentId|type AgentSummary|type FleetHost|AppRoutes" frontend/src frontend/tests`

Expected: no matches.

Run: `rg -n "fetch\(|apiClient\.request" frontend/src/features`

Expected: direct calls appear only in feature `api.ts` modules, not page components.

- [ ] **Step 6: Run the full frontend suite and commit**

Run: `cd frontend && npm test && npm run build && npm run lint`

Expected: PASS.

```bash
git add -A frontend/src frontend/tests
git commit -m "refactor: complete manager feature architecture"
```

---

### Task 16: Package, document, and verify Fleet Console end to end

**Files:**
- Modify: `start.sh`
- Modify: `packaging/runtime/README.md`
- Modify: `README.md`
- Create: `docs/manager-fleet-operations.md`
- Create: `docs/manager-enrollment-security.md`
- Create: `docs/manager-backup-and-rollback.md`
- Create: `backend/tests/integration/test_fleet_end_to_end.py`
- Create: `backend/tests/integration/test_manager_restart_recovery.py`
- Create: `frontend/tests/fleet-accessibility.test.tsx`

**Interfaces:** `start.sh all` launches one Agent and one Manager with distinct ports/databases/credential directories; docs cover user-process SSH, systemd CLI, optional restricted service key, trusted-LAN, Discovery, backup/restore, YAML migration, rollback, and residual credential recovery.

- [ ] **Step 1: Write end-to-end backend workflow tests**

```python
candidate = await discover(scope_id="eda-lab")
enrollment = await enroll(candidate, ssh_user="edaops")
agent = await save_agent(enrollment.id, display_name="EDA Host 01")
await probe(agent.agent_id)
observations = await manager_get(f"/api/v2/agents/{agent.agent_id}/observations")
assert observations.status_code == 200
```

Cover SSH-auto and CLI paths, restart between every saga phase, add/probe/proxy/Terminal, rotate, online remove, offline local-only residual, and single-Agent failure not blocking Fleet.

- [ ] **Step 2: Write final responsive/accessibility workflow tests**

Test keyboard-only Add/Discovery/Fleet/detail/remove, focus restore, alerts/live regions, status text+icon, 375/768/1024/1440 layouts, reduced motion, deep-link refresh, and no secret-like response data rendered or stored.

- [ ] **Step 3: Run end-to-end tests and address only in-scope failures**

Run: `cd backend && pytest -q tests/integration/test_fleet_end_to_end.py tests/integration/test_manager_restart_recovery.py`

Expected: PASS.

Run: `cd frontend && npm test -- --run tests/fleet-accessibility.test.tsx`

Expected: PASS.

- [ ] **Step 4: Update development launcher and operational docs**

Document exact local paths/modes, `allowed_agent_cidrs`, transport profiles, Credential Store `0700`/`0600`, Manager DB plus credential directory plus journal as one backup unit, systemd socket group, SSH host-key behavior, forced-command template, Discovery limits, local-only removal, and rollback/legacy-token recovery.

- [ ] **Step 5: Run full project verification**

Run: `cd backend && pytest -q`

Expected: PASS.

Run: `cd backend && python -m ruff check ic_env_guard tests`

Expected: PASS.

Run: `cd frontend && npm test && npm run build && npm run lint`

Expected: PASS.

Run: `./start.sh config control-plane`

Expected: prints a valid Manager config with SQLite audit/Registry DB, owner-only credential directory, allowlist/profile/enrollment/discovery sections, and no Ingest listener.

- [ ] **Step 6: Search for prohibited secret and proxy patterns**

Run: `rg -n "shell=True|ProxyCommand(?!\s*=\s*none)|ProxyJump(?!\s*=\s*none)|credential_ref.*response|token_file.*response|SSH_AUTH_SOCK.*(json|response)|/proxy" backend/ic_env_guard frontend/src --pcre2`

Expected: no prohibited generic proxy, secret response, or unsafe SSH execution match; fixed defensive `ProxyCommand=none`/`ProxyJump=none` may remain.

- [ ] **Step 7: Commit the completed Fleet Console delivery**

```bash
git add start.sh packaging/runtime/README.md README.md docs/manager-fleet-operations.md docs/manager-enrollment-security.md docs/manager-backup-and-rollback.md backend/tests/integration/test_fleet_end_to_end.py backend/tests/integration/test_manager_restart_recovery.py frontend/tests/fleet-accessibility.test.tsx
git commit -m "docs: complete fleet console operations"
```

---

## Fleet Console Completion Gate

Before declaring the refactor complete, verify all of the following from a clean checkout with Agent Foundation already applied:

- [ ] SQLite Registry is the runtime source; YAML imports once and never overwrites Web changes.
- [ ] Manager-specific tokens are hash-only on Agent, plaintext only in Manager owner-only files, and absent from browser/DB/audit/logs.
- [ ] SSH auto, CLI socket, and optional service-key paths share the same bounded fixed helper protocol.
- [ ] Enrollment and rotation recover safely from every remote/local phase boundary without losing the only credential reference.
- [ ] Dynamic endpoint validation, probe, and proxy share target policy and cannot become SSRF or a generic proxy.
- [ ] Discovery cannot exceed configured named scopes and never registers a Candidate directly.
- [ ] Fleet tolerates partial Agent failure and separates connection, workload, freshness, and transport security.
- [ ] All Manager detail routes deep-link by Agent ID; global Agent selector/context and `AppRoutes` state machine are gone.
- [ ] Standalone Agent and Manager reuse feature components while loading only mode-appropriate routes.
- [ ] Backend tests/lint, frontend tests/build/lint, end-to-end enrollment/proxy/Terminal, and responsive accessibility checks pass.

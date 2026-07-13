# Local v2 Agent Bootstrap and Terminal Proxy Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./start.sh all` create a pure v2 local Fleet whose managed `local-agent` supports Agent-scoped HTTP and Terminal WebSocket proxying without static legacy Agent import.

**Architecture:** Add a development-gated `LOCAL_SOCKET` enrollment method that reuses the existing managed enrollment journal, credential store, activation, Registry commit, and compensation flow while replacing only the SSH helper transport with the Agent's owner-only Unix socket. Add a record-aware target resolver that permits literal loopback HTTP only for a committed `local_dev_bootstrap` record, then make the launcher rebuild generated Fleet state, enroll through the Manager socket, and verify a real PTY through the Manager.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy/SQLite, Unix domain sockets, httpx, websockets, Bash, React/Vite, pytest, Ruff.

## Global Constraints

- The generated Manager configuration must not contain static `agents:` entries or `legacy-config-http`.
- Local bootstrap must not be exposed through a public HTTP or WebSocket endpoint.
- `DevelopmentConfig.local_agent_bootstrap` must default to `False` and production target policy must continue to reject loopback.
- The local Agent URL must be literal `http://127.0.0.1:<port>` and use the configured `local-loopback-http` trusted-LAN profile.
- Manager-to-Agent authentication must use a newly issued managed credential, never the Agent administrator token.
- Valid non-empty Agent and Manager public login tokens must survive `./start.sh all` restarts.
- Local bootstrap messages, logs, command output, browser state, and audit data must never expose managed credential bytes.
- Existing SSH enrollment, legacy compatibility import, discovery, credential rotation, Terminal revision checks, and Terminal slot limits must retain their current behavior.
- Every network, socket, and readiness wait must have a finite deadline.
- Preserve the user's unrelated `CLAUDE.md`, `.kilo/`, and `AGENTS.md` workspace changes.

## File Map

- `backend/ic_env_guard/config/models.py`: development gate and fail-closed configuration invariants.
- `backend/ic_env_guard/fleet/models.py`: `EnrollmentMethod.LOCAL_SOCKET`.
- `backend/ic_env_guard/fleet/target_policy.py`: dedicated literal-loopback validation and pinned revalidation.
- `backend/ic_env_guard/fleet/registered_target.py`: the only runtime dispatcher between ordinary and local record resolution.
- `backend/ic_env_guard/enrollment/local_socket.py`: bounded Manager-to-Agent enrollment socket client.
- `backend/ic_env_guard/enrollment/jobs.py`: deterministic `local-agent` enrollment job creation.
- `backend/ic_env_guard/enrollment/orchestrator.py`: local issuance, validation, activation, Registry commit, and compensation through the existing saga.
- `backend/ic_env_guard/enrollment/manager_socket.py`: owner-only local-bootstrap request protocol.
- `backend/ic_env_guard/enrollment/local_cli.py`: token-free client for that Manager socket protocol.
- `backend/ic_env_guard/enrollment/agent_client.py`: local initial and pinned enrollment target preparation.
- `backend/ic_env_guard/systemd/cli.py`: `ic-env-guardctl agent bootstrap-local` command.
- `backend/ic_env_guard/bootstrap/composition.py`: construct and wire the local client and development gate.
- `backend/ic_env_guard/fleet/probes.py`: use record-aware resolution for periodic probes.
- `backend/ic_env_guard/proxy/http.py`: use record-aware resolution for scoped HTTP and captured WebSocket routes.
- `backend/ic_env_guard/agents/terminal_proxy.py`: retain local trust metadata in ticket reservations and tickets.
- `backend/ic_env_guard/api/agent_terminals.py`: capture enrollment method and source with the Terminal route.
- `backend/ic_env_guard/api/agent_terminal_ws.py`: compare the complete captured route before and during attachment.
- `backend/ic_env_guard/development/readiness.py`: bounded HTTP + WebSocket/PTY verification through the Manager.
- `start.sh`: generated v2 configuration, safe state reset, bootstrap invocation, PID lifecycle, and readiness sequencing.
- `backend/tests/...`: focused unit, security, contract, integration, and full-stack lifecycle coverage.
- `docs/guides/development.md`, `docs/guides/getting-started.md`, `docs/guides/manager-fleet.md`: supported local-stack behavior.

---

### Task 1: Fail-closed local enrollment identity and target policy

**Files:**
- Create: `backend/ic_env_guard/fleet/registered_target.py`
- Modify: `backend/ic_env_guard/config/models.py:73-75, 510-558`
- Modify: `backend/ic_env_guard/fleet/models.py:23-28`
- Modify: `backend/ic_env_guard/fleet/target_policy.py:64-225`
- Modify: `backend/ic_env_guard/storage/enrollment_journal.py:106-166`
- Test: `backend/tests/unit/test_agent_target_policy.py`
- Test: `backend/tests/unit/test_security_config.py`
- Test: `backend/tests/unit/test_enrollment_jobs.py`

**Interfaces:**
- Produces: `EnrollmentMethod.LOCAL_SOCKET` with value `"local_socket"`.
- Produces: `DevelopmentConfig.local_agent_bootstrap: bool = False`.
- Produces: `AgentTargetPolicy.resolve_local_socket(endpoint, profile) -> ValidatedTarget`.
- Produces: `AgentTargetPolicy.revalidate_local_socket_target(endpoint, profile, stored_ip) -> ValidatedTarget`.
- Produces: `resolve_registered_target(policy, record, profile, *, local_bootstrap_enabled) -> ValidatedTarget`.
- Consumes: existing `TrustedLanHttpProfile`, global allowlist, profile allowlist, and Manager self-target list.

- [ ] **Step 1: Write failing target-policy tests**

Add tests proving the local path is narrow while the generic path remains closed:

```python
LOCAL_HTTP = TrustedLanHttpProfile(
    id="local-loopback-http", allowed_cidrs=["127.0.0.0/8"]
)


@pytest.mark.unit
def test_local_socket_target_accepts_literal_loopback_only():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    )

    target = policy.resolve_local_socket("http://127.0.0.1:8766", LOCAL_HTTP)

    assert str(target.pinned_address) == "127.0.0.1"
    assert target.normalized_endpoint == "http://127.0.0.1:8766"
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve("http://127.0.0.1:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_url_invalid"):
        policy.resolve_local_socket("http://localhost:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        policy.resolve_local_socket("http://10.0.0.9:8766", LOCAL_HTTP)
    with pytest.raises(TargetPolicyError, match="target_is_manager"):
        policy.resolve_local_socket("http://127.0.0.1:8765", LOCAL_HTTP)


@pytest.mark.unit
def test_registered_local_target_requires_gate_method_source_and_profile():
    policy = AgentTargetPolicy(
        allowed_agent_cidrs=["127.0.0.0/8"],
        self_targets=[("127.0.0.1", 8765)],
    )
    record = _agent_record(
        endpoint="http://127.0.0.1:8766",
        method=EnrollmentMethod.LOCAL_SOCKET,
        source="local_dev_bootstrap",
        profile="local-loopback-http",
    )

    target = resolve_registered_target(
        policy, record, LOCAL_HTTP, local_bootstrap_enabled=True
    )
    assert target.port == 8766

    for changed in (
        replace(record, enrollment_method=EnrollmentMethod.SSH_AUTO),
        replace(record, source="manual"),
        replace(record, transport_profile_id="another-profile"),
    ):
        with pytest.raises(TargetPolicyError):
            resolve_registered_target(
                policy, changed, LOCAL_HTTP, local_bootstrap_enabled=True
            )
    with pytest.raises(TargetPolicyError, match="target_address_forbidden"):
        resolve_registered_target(
            policy, record, LOCAL_HTTP, local_bootstrap_enabled=False
        )
```

- [ ] **Step 2: Write failing configuration and journal-invariant tests**

```python
@pytest.mark.unit
def test_local_bootstrap_requires_local_manager_and_insecure_dev_opt_in(tmp_path):
    token = _token_file(tmp_path)
    with pytest.raises(ValueError, match="local Agent bootstrap"):
        AppConfig(
            mode="control-plane",
            server=ServerConfig(bind="0.0.0.0", remote_bind_enabled=True),
            auth=AuthConfig(token_file=token),
            development=DevelopmentConfig(
                allow_insecure_http=True, local_agent_bootstrap=True
            ),
        )


@pytest.mark.unit
def test_local_socket_enrollment_has_no_ssh_fields(tmp_path):
    jobs = _jobs(tmp_path)
    job = jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint="http://127.0.0.1:8766",
            transport_profile_id="local-loopback-http",
            enrollment_method=EnrollmentMethod.LOCAL_SOCKET,
        ),
        enrollment_id="local-agent",
    )
    assert job.enrollment_id == "local-agent"
    assert (job.ssh_user, job.ssh_host, job.ssh_port) == (None, None, None)
```

- [ ] **Step 3: Run the focused tests and confirm the expected failures**

Run:

```bash
cd backend
pytest -q tests/unit/test_agent_target_policy.py tests/unit/test_security_config.py tests/unit/test_enrollment_jobs.py
```

Expected: FAIL because `LOCAL_SOCKET`, the development gate, explicit enrollment IDs, and local resolver do not exist.

- [ ] **Step 4: Add the enum, gate, local policy, and record-aware resolver**

Add the enum and gate:

```python
class DevelopmentConfig(BaseModel):
    allow_insecure_http: bool = False
    local_agent_bootstrap: bool = False


class EnrollmentMethod(str, Enum):
    SSH_AUTO = "ssh_auto"
    SSH_CLI = "ssh_cli"
    SSH_SERVICE_KEY = "ssh_service_key"
    LOCAL_SOCKET = "local_socket"
    LEGACY_ADMIN_TOKEN = "legacy_admin_token"
```

Require `mode == "control-plane"`, `server.is_local_only`,
`allow_insecure_http is True`, and `enrollment.manager_socket_path is not None`
when `local_agent_bootstrap` is enabled. Update the journal invariant so both
`LEGACY_ADMIN_TOKEN` and `LOCAL_SOCKET` require all SSH fields to be `None`;
all managed credential and remote identity invariants still apply to
`LOCAL_SOCKET`.

Implement dedicated policy methods without calling the public legacy-import
method. Require `http`, `TrustedLanHttpProfile`, a literal canonical loopback
IP, global/profile allowlist membership, and a non-Manager port. Pinned
revalidation must not perform DNS.

Create `registered_target.py` with the exact dispatcher:

```python
LOCAL_BOOTSTRAP_PROFILE_ID = "local-loopback-http"
LOCAL_BOOTSTRAP_SOURCE = "local_dev_bootstrap"


def resolve_registered_target(
    policy: AgentTargetPolicy,
    record: AgentRecord,
    profile: TransportProfile,
    *,
    local_bootstrap_enabled: bool,
) -> ValidatedTarget:
    if record.enrollment_method is not EnrollmentMethod.LOCAL_SOCKET:
        return policy.resolve(record.normalized_endpoint, profile)
    if (
        not local_bootstrap_enabled
        or record.source != LOCAL_BOOTSTRAP_SOURCE
        or record.transport_profile_id != LOCAL_BOOTSTRAP_PROFILE_ID
        or profile.id != LOCAL_BOOTSTRAP_PROFILE_ID
    ):
        raise TargetPolicyError(
            "target_address_forbidden", "local Agent bootstrap target is forbidden"
        )
    return policy.resolve_local_socket(record.normalized_endpoint, profile)
```

- [ ] **Step 5: Run focused tests, Ruff, and commit**

Run:

```bash
cd backend
pytest -q tests/unit/test_agent_target_policy.py tests/unit/test_security_config.py tests/unit/test_enrollment_jobs.py
ruff check ic_env_guard/config/models.py ic_env_guard/fleet/models.py ic_env_guard/fleet/target_policy.py ic_env_guard/fleet/registered_target.py ic_env_guard/storage/enrollment_journal.py tests/unit/test_agent_target_policy.py tests/unit/test_security_config.py tests/unit/test_enrollment_jobs.py
```

Expected: all selected tests pass and Ruff reports no errors.

Commit:

```bash
git add backend/ic_env_guard/config/models.py backend/ic_env_guard/fleet/models.py backend/ic_env_guard/fleet/target_policy.py backend/ic_env_guard/fleet/registered_target.py backend/ic_env_guard/storage/enrollment_journal.py backend/tests/unit/test_agent_target_policy.py backend/tests/unit/test_security_config.py backend/tests/unit/test_enrollment_jobs.py
git commit -m "feat: define guarded local Agent targets"
```

---

### Task 2: Bounded owner-only Agent enrollment socket client

**Files:**
- Create: `backend/ic_env_guard/enrollment/local_socket.py`
- Test: `backend/tests/security/test_local_enrollment_socket_client.py`

**Interfaces:**
- Consumes: `EnrollmentRequest`, `parse_response`, `ValidatedTarget`, and `EnrollmentHelperResult`.
- Produces: `LocalEnrollmentSocketError` with stable non-secret `code`.
- Produces: `LocalEnrollmentSocketClient(allowed_root: Path, timeout_seconds: float = 3.0)`.
- Produces: `await LocalEnrollmentSocketClient.issue(socket_path, manager_id, enrollment_id, validation_target) -> EnrollmentHelperResult`.

- [ ] **Step 1: Write the failing real-socket success and boundary tests**

Create an owner-only temporary directory and a small AF_UNIX server. Assert
that the request is exactly `manager-enrollment.v1`, the returned token is
available only in the result object, and these cases fail before or during
dispatch: path outside `allowed_root`, symlink, non-socket, group/world access,
oversized response, timeout, invalid JSON, expired credential, and response
with a mismatched enrollment protocol.

The success test must include:

```python
@pytest.mark.security
async def test_local_client_exchanges_one_bounded_enrollment_message(socket_dir):
    response = EnrollmentResponse(
        protocol="manager-enrollment.v1",
        instance_id="33333333-3333-4333-8333-333333333333",
        credential_id="44444444-4444-4444-8444-444444444444",
        token="managed-secret",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    server, socket_path, received = _one_shot_server(socket_dir, encode_response(response))
    target = _local_target()

    result = await LocalEnrollmentSocketClient(socket_dir).issue(
        socket_path=socket_path,
        manager_id="11111111-1111-4111-8111-111111111111",
        enrollment_id="local-agent",
        validation_target=target,
    )
    server.join(timeout=2)

    assert parse_request(received[0]).enrollment_id == "local-agent"
    assert result.instance_id == str(response.instance_id)
    assert result.credential_id == str(response.credential_id)
    assert result.token == b"managed-secret"
    assert result.validation_target is target
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
cd backend
pytest -q tests/security/test_local_enrollment_socket_client.py
```

Expected: collection fails because `ic_env_guard.enrollment.local_socket` does not exist.

- [ ] **Step 3: Implement the socket client**

Use `asyncio.to_thread` around a synchronous socket exchange. Before connect,
resolve the parent without following a socket symlink, require it to equal the
configured owner-only root, and require `lstat` to report an owner-owned socket
whose mode has no group/world bits. Set a finite timeout, send one bounded JSON
request, `shutdown(SHUT_WR)`, read at most `MAX_RESPONSE_BYTES + 1`, and parse
the one-line response.

The public method must have this shape:

```python
class LocalEnrollmentSocketClient:
    def __init__(self, allowed_root: Path, timeout_seconds: float = 3.0) -> None:
        self._allowed_root = allowed_root.resolve(strict=True)
        self._timeout_seconds = timeout_seconds

    async def issue(
        self,
        *,
        socket_path: Path,
        manager_id: str,
        enrollment_id: str,
        validation_target: ValidatedTarget,
    ) -> EnrollmentHelperResult:
        request = EnrollmentRequest(
            protocol="manager-enrollment.v1",
            manager_id=manager_id,
            enrollment_id=enrollment_id,
        )
        payload = request.model_dump_json().encode("ascii")
        response = await asyncio.to_thread(
            self._exchange, socket_path, payload
        )
        parsed = parse_response(response)
        if parsed.expires_at <= datetime.now(UTC):
            raise LocalEnrollmentSocketError("local_credential_expired")
        return EnrollmentHelperResult(
            instance_id=str(parsed.instance_id),
            credential_id=str(parsed.credential_id),
            token=parsed.token.encode("ascii"),
            validation_target=validation_target,
        )
```

Map filesystem, framing, timeout, and protocol details to stable safe error
codes; never include an exception string, token, or response body in the
exception message.

- [ ] **Step 4: Run tests, Ruff, and commit**

Run:

```bash
cd backend
pytest -q tests/security/test_local_enrollment_socket_client.py tests/security/test_enrollment_socket.py
ruff check ic_env_guard/enrollment/local_socket.py tests/security/test_local_enrollment_socket_client.py
```

Expected: both security test modules pass; Ruff reports no errors.

Commit:

```bash
git add backend/ic_env_guard/enrollment/local_socket.py backend/tests/security/test_local_enrollment_socket_client.py
git commit -m "feat: add local Agent enrollment socket client"
```

---

### Task 3: Reuse the managed enrollment saga for `local-agent`

**Files:**
- Modify: `backend/ic_env_guard/enrollment/jobs.py:31-111`
- Modify: `backend/ic_env_guard/enrollment/agent_client.py:38-118`
- Modify: `backend/ic_env_guard/enrollment/orchestrator.py:95-220, 250-620, 1280-1450, 1490-1895`
- Test: `backend/tests/integration/test_local_socket_enrollment.py`
- Test: `backend/tests/integration/test_enrollment_recovery.py`

**Interfaces:**
- Consumes: `LocalEnrollmentSocketClient.issue`, local target policy methods, and existing `consume`/recovery flow.
- Produces: `LocalBootstrapRequest` with `agent_id`, `display_name`, `base_url`, `transport_profile_id`, and `agent_socket_path`.
- Produces: `await EnrollmentOrchestrator.bootstrap_local(request, context) -> AgentRecord`.
- Produces: `EnrollmentAgentClient.prepare_local` and `prepare_local_pinned`.

- [ ] **Step 1: Write a failing successful-saga integration test**

Build an Agent and Manager container with separate databases and real
credential stores. Start the Agent enrollment socket, inject an HTTP transport
that routes Manager validation/activation calls to the Agent ASGI app, and run:

```python
record = await manager.enrollment_orchestrator.bootstrap_local(
    LocalBootstrapRequest(
        agent_id="local-agent",
        display_name="Local development agent",
        base_url="http://127.0.0.1:8766",
        transport_profile_id="local-loopback-http",
        agent_socket_path=agent_config.enrollment.socket_path,
    ),
    AutoEnrollmentAuditContext(
        actor_id=f"local-cli:{os.geteuid()}",
        source_addr="local-unix",
        correlation_id=None,
    ),
)

assert record.agent_id == "local-agent"
assert record.enrollment_method is EnrollmentMethod.LOCAL_SOCKET
assert record.source == "local_dev_bootstrap"
assert record.transport_profile_id == "local-loopback-http"
assert record.remote_credential_id is not None
assert manager.credential_store.read(record.credential_ref) != agent_admin_token
assert manager.enrollment_journal_repository.get("local-agent").state is EnrollmentState.CONSUMED
```

Also authenticate the stored Manager token against the Agent credential
repository and assert its state is `ACTIVE`.

- [ ] **Step 2: Write failing compensation and restart-recovery tests**

Parameterize failures after issue, after Manager secret storage, during pending
validation, during activation, and during Registry commit. For each case assert
that no usable partial Registry record remains, Manager secret material is
removed or retained only as a recoverable journal residual, and the remote
credential is revoked when dispatch is possible. Add a restart case beginning
at `CREDENTIAL_ISSUED` with `EnrollmentMethod.LOCAL_SOCKET` and assert recovery
uses `prepare_local_pinned` without DNS.

- [ ] **Step 3: Run the new tests and verify failure**

Run:

```bash
cd backend
pytest -q tests/integration/test_local_socket_enrollment.py tests/integration/test_enrollment_recovery.py
```

Expected: FAIL because the local request and orchestration entry point do not exist.

- [ ] **Step 4: Add deterministic job creation and local target preparation**

Extend `EnrollmentJobs.create` with a keyword-only `enrollment_id: str | None = None`.
Use the provided identifier only for the guarded local path; repository
validation remains the canonical syntax check.

Add these methods without changing existing callers:

```python
def prepare_local(self, endpoint: str, profile_id: str) -> ValidatedTarget:
    try:
        profile = self._profiles[profile_id]
        return self._policy.resolve_local_socket(endpoint, profile)
    except KeyError as exc:
        raise EnrollmentValidationError(
            "transport_profile_invalid", dispatch_state="not_dispatched"
        ) from exc
    except TargetPolicyError as exc:
        raise EnrollmentValidationError(exc.code, dispatch_state="not_dispatched") from exc


def prepare_local_pinned(
    self, endpoint: str, profile_id: str, stored_ip: str
) -> ValidatedTarget:
    try:
        profile = self._profiles[profile_id]
        return self._policy.revalidate_local_socket_target(endpoint, profile, stored_ip)
    except KeyError as exc:
        raise EnrollmentValidationError(
            "transport_profile_invalid", dispatch_state="not_dispatched"
        ) from exc
    except TargetPolicyError as exc:
        raise EnrollmentValidationError(exc.code, dispatch_state="not_dispatched") from exc
```

- [ ] **Step 5: Implement the local orchestration entry point by extracting the shared managed-helper publication**

Add `LocalBootstrapRequest` and constructor dependencies
`local_socket_client: LocalEnrollmentSocketClient | None` and
`local_bootstrap_enabled: bool = False`.

Refactor the duplicate managed helper publication in auto and CLI enrollment
into one private `_publish_managed_helper(job, helper, *, expected_method)`
that performs the existing transitions:

```text
RUNNING -> CREDENTIAL_ISSUED -> VERIFYING -> VERIFIED
```

It must store the helper token under the credential-store lifecycle lease,
persist remote instance/credential IDs and the validated pinned address,
validate pending capabilities/summary, and delete the local reference if the
journal transition fails.

Implement `bootstrap_local` with this sequence:

```python
async def bootstrap_local(
    self,
    request: LocalBootstrapRequest,
    context: AutoEnrollmentAuditContext,
) -> AgentRecord:
    if (
        not self._local_bootstrap_enabled
        or self._local_socket_client is None
        or self.agent_client is None
        or self._closing
    ):
        raise EnrollmentValidationError(
            "local_bootstrap_disabled", dispatch_state="not_dispatched"
        )
    target = self.agent_client.prepare_local(
        request.base_url, request.transport_profile_id
    )
    pending = self.jobs.create(
        EnrollmentJobRequest(
            normalized_endpoint=target.normalized_endpoint,
            transport_profile_id=request.transport_profile_id,
            display_name=request.display_name,
            enrollment_method=EnrollmentMethod.LOCAL_SOCKET,
        ),
        enrollment_id=request.agent_id,
        now=self._clock(),
    )
    running = self.journal.replace_if_state(
        replace(pending, state=EnrollmentState.RUNNING, updated_at=self._clock()),
        expected_state=EnrollmentState.PENDING,
    )
    helper = await self._local_socket_client.issue(
        socket_path=request.agent_socket_path,
        manager_id=running.manager_id,
        enrollment_id=running.enrollment_id,
        validation_target=target,
    )
    await self._publish_managed_helper(
        running, helper, expected_method=EnrollmentMethod.LOCAL_SOCKET
    )
    return await self.consume(
        running.enrollment_id,
        display_name=request.display_name,
        input_fingerprint=job_input_fingerprint(running),
    )
```

Wrap the sequence with the same safe audit-outcome and compensation semantics
as SSH auto enrollment. Update all activation/validation/revocation recovery
sites to call a private `_prepare_pinned_job(job)` that selects
`prepare_local_pinned` only for `LOCAL_SOCKET`. When committing an add, set
`source="local_dev_bootstrap"` for that method and preserve `manual` and
`discovery` for existing methods.

- [ ] **Step 6: Run saga, recovery, and existing SSH tests**

Run:

```bash
cd backend
pytest -q tests/integration/test_local_socket_enrollment.py tests/integration/test_enrollment_recovery.py tests/integration/test_agent_enrollment_saga.py tests/integration/test_ssh_auto_enrollment.py tests/integration/test_cli_enrollment.py
ruff check ic_env_guard/enrollment/jobs.py ic_env_guard/enrollment/agent_client.py ic_env_guard/enrollment/orchestrator.py tests/integration/test_local_socket_enrollment.py
```

Expected: all selected tests pass; existing SSH and CLI behavior remains green.

- [ ] **Step 7: Commit**

```bash
git add backend/ic_env_guard/enrollment/jobs.py backend/ic_env_guard/enrollment/agent_client.py backend/ic_env_guard/enrollment/orchestrator.py backend/tests/integration/test_local_socket_enrollment.py backend/tests/integration/test_enrollment_recovery.py
git commit -m "feat: enroll local Agent through managed saga"
```

---

### Task 4: Owner-only Manager bootstrap protocol and CLI command

**Files:**
- Create: `backend/ic_env_guard/enrollment/local_cli.py`
- Modify: `backend/ic_env_guard/enrollment/manager_socket.py:120-280`
- Modify: `backend/ic_env_guard/systemd/cli.py:80-115`
- Modify: `backend/ic_env_guard/bootstrap/composition.py:120-510`
- Test: `backend/tests/security/test_manager_enrollment_socket.py`
- Test: `backend/tests/integration/test_local_bootstrap_cli.py`
- Test: `backend/tests/unit/test_runtime_config_resolution.py`

**Interfaces:**
- Consumes: `EnrollmentOrchestrator.bootstrap_local` and `LocalEnrollmentSocketClient`.
- Produces Manager request protocol `manager-local-bootstrap.request.v1`.
- Produces Manager response protocol `manager-local-bootstrap.result.v1`.
- Produces CLI: `ic-env-guardctl agent bootstrap-local --manager-socket ... --agent-socket ... --base-url ... --transport-profile ... --agent-id local-agent --display-name ...`.

- [ ] **Step 1: Write failing protocol security tests**

Add a success test that connects as the Manager owner, sends exactly:

```json
{"protocol":"manager-local-bootstrap.request.v1","agent_id":"local-agent","display_name":"Local development agent","base_url":"http://127.0.0.1:8766","transport_profile_id":"local-loopback-http","agent_socket_path":"/tmp/ieg/agent-enrollment.sock"}
```

and expects only:

```json
{"protocol":"manager-local-bootstrap.result.v1","status":"enrolled","agent_id":"local-agent","revision":1}
```

Assert that unknown/extra keys, duplicate JSON keys, an oversized frame,
group-only authorization, disabled gate, a socket path outside the configured
root, and a non-loopback URL all return a stable safe error without calling
the orchestrator.

- [ ] **Step 2: Write the failing CLI integration test**

Use a real temporary Manager Unix socket and call `ctl_main` with
`bootstrap-local`. Assert stdout is exactly `Local Agent enrolled.\n`, stderr
is empty, the request contains no public or managed token, and a server error
returns exit code 1 with `ic-env-guardctl: local bootstrap failed`.

- [ ] **Step 3: Run the focused tests and verify failure**

Run:

```bash
cd backend
pytest -q tests/security/test_manager_enrollment_socket.py tests/integration/test_local_bootstrap_cli.py tests/unit/test_runtime_config_resolution.py
```

Expected: FAIL because the new protocol and command are absent.

- [ ] **Step 4: Add local protocol dispatch without weakening SSH CLI framing**

Split `ManagerEnrollmentSocket._handle` after its bounded first-frame read:

```python
protocol = header.get("protocol")
if protocol == "manager-local-bootstrap.request.v1":
    await self._handle_local_bootstrap(header, writer, peer_uid)
    return
if protocol != "manager-cli-enrollment.header.v1":
    raise ManagerSocketError("invalid_request")
await self._handle_ssh_cli(header, reader, writer, peer_uid)
```

The local handler must require `peer_uid == allowed_uid`, exact keys, and the
configured development gate. It awaits `orchestrator.bootstrap_local` and
returns only the Agent ID and Registry revision. Preserve the existing
semaphore, read limits, timeouts, SSH resume protocol, and error normalization.

- [ ] **Step 5: Add the token-free local CLI client and parser entry**

Implement `run_local_bootstrap` in `local_cli.py` as one bounded Unix socket
request/response exchange. Validate the exact result keys and close the socket
on every outcome. Add the parser branch in `systemd/cli.py` and dispatch it
without constructing a `CliSshRunner`.

- [ ] **Step 6: Wire the client in Manager composition**

When `config.development.local_agent_bootstrap` is true, construct:

```python
local_socket_client = LocalEnrollmentSocketClient(
    config.enrollment.manager_socket_path.parent
)
```

Pass it and the gate to `EnrollmentOrchestrator` and `ManagerEnrollmentSocket`.
When false, pass `None`/`False`; no production socket behavior changes.

- [ ] **Step 7: Run tests, Ruff, and commit**

Run:

```bash
cd backend
pytest -q tests/security/test_manager_enrollment_socket.py tests/integration/test_local_bootstrap_cli.py tests/integration/test_cli_enrollment.py tests/unit/test_runtime_config_resolution.py
ruff check ic_env_guard/enrollment/local_cli.py ic_env_guard/enrollment/manager_socket.py ic_env_guard/systemd/cli.py ic_env_guard/bootstrap/composition.py tests/integration/test_local_bootstrap_cli.py
```

Expected: all selected tests pass and the original SSH CLI tests remain green.

Commit:

```bash
git add backend/ic_env_guard/enrollment/local_cli.py backend/ic_env_guard/enrollment/manager_socket.py backend/ic_env_guard/systemd/cli.py backend/ic_env_guard/bootstrap/composition.py backend/tests/security/test_manager_enrollment_socket.py backend/tests/integration/test_local_bootstrap_cli.py backend/tests/unit/test_runtime_config_resolution.py
git commit -m "feat: expose owner-only local bootstrap command"
```

---

### Task 5: Record-aware probes, scoped HTTP, and Terminal revision capture

**Files:**
- Modify: `backend/ic_env_guard/fleet/probes.py:50-180`
- Modify: `backend/ic_env_guard/proxy/http.py:25-260`
- Modify: `backend/ic_env_guard/agents/terminal_proxy.py:10-145`
- Modify: `backend/ic_env_guard/api/agent_terminals.py:380-435`
- Modify: `backend/ic_env_guard/api/agent_terminal_ws.py:205-245, 390-490`
- Modify: `backend/ic_env_guard/bootstrap/composition.py:320-475`
- Test: `backend/tests/security/test_agent_proxy_boundaries.py`
- Test: `backend/tests/integration/test_agent_terminal_websocket.py`
- Test: `backend/tests/integration/test_local_socket_enrollment.py`

**Interfaces:**
- Consumes: `resolve_registered_target` and `DevelopmentConfig.local_agent_bootstrap`.
- Extends: `AgentRouteCapture`, `GatewayTicketReservation`, and `GatewayTicket` with `enrollment_method` and `source`.
- Preserves: revision, credential reference, profile, endpoint, slot, actor, one-use ticket, and active-watch checks.

- [ ] **Step 1: Write failing proxy and probe tests for a committed local record**

Create a `LOCAL_SOCKET`/`local_dev_bootstrap` record with the local profile and
managed credential. Assert Agent-scoped observations, logs, services, audit,
and terminals dispatch when the gate is enabled. Assert each fails before
credential read when the gate is false, source changes, method changes, or
profile changes. Add a real `FleetProbeService.probe("local-agent")` test that
reaches `/api/v2/capabilities` and `/api/v2/summary` through loopback target
resolution and records `connection_status == "ready"`.

- [ ] **Step 2: Write failing Terminal capture mutation tests**

After issuing a gateway ticket, mutate each captured field independently:
revision, credential reference, profile, endpoint, enrollment method, and
source. Every mutation must close attach with code 4409 before upstream
WebSocket connect. Mutating the record during an active connection must close
it and release the proxy slot.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```bash
cd backend
pytest -q tests/security/test_agent_proxy_boundaries.py tests/integration/test_agent_terminal_websocket.py tests/integration/test_local_socket_enrollment.py
```

Expected: local requests fail under the generic loopback policy and new capture assertions fail.

- [ ] **Step 4: Use the record-aware resolver in probes and HTTP proxy**

Add `local_bootstrap_enabled: bool = False` to `FleetProbeService` and
`AgentHttpProxy`; preserve it in `AgentHttpProxy.with_runtime`. Replace direct
`target_policy.resolve(...)` calls with:

```python
target = resolve_registered_target(
    self._target_policy,
    captured,
    profile,
    local_bootstrap_enabled=self._local_bootstrap_enabled,
)
```

Keep the existing legacy-import branch only in `FleetProbeService`; a local
record never enters it. Pass the gate from composition to both services.

- [ ] **Step 5: Capture method and source through the full Terminal ticket lifecycle**

Add `enrollment_method: EnrollmentMethod | None` and `source: str | None` to
the route capture, reservation, and ticket dataclasses. Populate them from the
Registry record in `create_agent_terminal_connect_token`. Extend all capture
comparisons, `_ticket_matches_record`, `resolve_captured_route`, and the active
revision watcher. Resolve the current captured Registry record through
`resolve_registered_target`; never trust ticket metadata alone to grant the
local exception.

- [ ] **Step 6: Run focused and adjacent regressions**

Run:

```bash
cd backend
pytest -q tests/security/test_agent_proxy_boundaries.py tests/contract/test_agent_observation_proxy.py tests/contract/test_agent_log_proxy.py tests/contract/test_agent_terminal_http_contract.py tests/integration/test_agent_terminal_websocket.py tests/unit/test_gateway_terminal_tickets.py tests/integration/test_local_socket_enrollment.py
ruff check ic_env_guard/fleet/probes.py ic_env_guard/proxy/http.py ic_env_guard/agents/terminal_proxy.py ic_env_guard/api/agent_terminals.py ic_env_guard/api/agent_terminal_ws.py ic_env_guard/bootstrap/composition.py
```

Expected: all scoped proxy, ticket, WebSocket, and local probe tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/ic_env_guard/fleet/probes.py backend/ic_env_guard/proxy/http.py backend/ic_env_guard/agents/terminal_proxy.py backend/ic_env_guard/api/agent_terminals.py backend/ic_env_guard/api/agent_terminal_ws.py backend/ic_env_guard/bootstrap/composition.py backend/tests/security/test_agent_proxy_boundaries.py backend/tests/integration/test_agent_terminal_websocket.py backend/tests/integration/test_local_socket_enrollment.py
git commit -m "fix: proxy managed local Agent routes"
```

---

### Task 6: Generate and bootstrap a clean v2 development Fleet

**Files:**
- Modify: `start.sh:8-230, 330-390`
- Modify: `backend/tests/integration/test_manager_restart_recovery.py`

**Interfaces:**
- Consumes CLI: `ic-env-guardctl agent bootstrap-local`.
- Produces Manager profile `local-loopback-http` with `127.0.0.0/8`.
- Produces no generated Manager `agents:` key.
- Preserves non-empty `agent.token` and `control-plane.token`.

- [ ] **Step 1: Strengthen the launcher config and lifecycle tests first**

Change the existing assertions and add restart assertions:

```python
assert control_plane["transport_profiles"] == [
    {
        "id": "local-loopback-http",
        "type": "trusted_lan_http",
        "allowed_cidrs": ["127.0.0.0/8"],
    }
]
assert config["development"] == {
    "allow_insecure_http": True,
    "local_agent_bootstrap": True,
}
assert "agents" not in config
assert "legacy-config-http" not in (tmp_path / "control-plane.yaml").read_text()
```

Pre-create old `control-plane.db`, `state.db`, credential directory, sockets,
and YAML containing the legacy Agent. Run `start.sh all`; assert generated
state is replaced, both login token values are unchanged, and the Registry
contains `local-agent` with `local_socket` and `local_dev_bootstrap`.

Add two process-ownership cases: a previously recorded backend process whose
same-user command line contains the exact generated config path is terminated
before reset; a PID file pointing at an unrelated process causes startup to
fail without signaling that process.

- [ ] **Step 2: Run the launcher tests and verify failure**

Run:

```bash
cd backend
pytest -q tests/integration/test_manager_restart_recovery.py
```

Expected: FAIL because the launcher still generates static legacy Agent configuration.

- [ ] **Step 3: Generate current Manager configuration**

Remove Manager use of `AGENT_TOKEN_FILE`. Generate this control-plane section:

```yaml
development:
  allow_insecure_http: true
  local_agent_bootstrap: true
control_plane:
  audit_database: ${DEV_DIR}/control-plane.db
  credential_directory: ${DEV_DIR}/manager-credentials
  allowed_agent_cidrs:
    - 127.0.0.0/8
  transport_profiles:
    - id: local-loopback-http
      type: trusted_lan_http
      allowed_cidrs:
        - 127.0.0.0/8
  discovery:
    scopes: []
enrollment:
  manager_socket_path: ${DEV_DIR}/manager-enrollment.sock
  manager_socket_mode: "0600"
```

Always regenerate `agent.yaml` and `control-plane.yaml` for `all`; retain the
existing behavior of explicit `config`, `agent`, and `control-plane` commands
unless their generated file is missing.

- [ ] **Step 4: Add a bounded generated-state reset and exact child lifecycle**

Before deletion, require the resolved development directory to be absolute,
not `/`, not the home directory, and owner-only. Delete only the enumerated
generated databases, SQLite sidecars, credential directory, sockets, PID
metadata, and the two generated YAML files. Do not delete `*.token`.

Change backend launch so the background subshell `exec`s the Python runtime;
then the captured PID is the runtime PID. In the trap, send TERM to only the
two captured PIDs, wait with a finite loop, then use KILL only for those same
PIDs if they did not exit. Remove PID/socket metadata owned by this invocation.

Write one PID file per backend after launch. On a later `all` run, stop a
recorded process only after `ps` confirms the current UID and the exact Agent
or Manager config path in the runtime command. If PID metadata is malformed,
the PID has been reused, or the command does not match, leave the process
untouched and fail with `development process identity mismatch`.

- [ ] **Step 5: Invoke local bootstrap after both sockets are ready**

Add a bounded `wait_for_socket` and run the CLI after Agent and Manager health:

```bash
python - "${DEV_DIR}" "${BACKEND_HOST}" "${AGENT_PORT}" <<'PY'
import sys
from pathlib import Path
from ic_env_guard.systemd.cli import ctl_main

dev_dir = Path(sys.argv[1])
raise SystemExit(ctl_main([
    "agent", "bootstrap-local",
    "--manager-socket", str(dev_dir / "manager-enrollment.sock"),
    "--agent-socket", str(dev_dir / "agent-enrollment.sock"),
    "--base-url", f"http://{sys.argv[2]}:{sys.argv[3]}",
    "--transport-profile", "local-loopback-http",
    "--agent-id", "local-agent",
    "--display-name", "Local development agent",
]))
PY
```

Do not pass either public token or any managed token on argv, stdin, stdout,
or environment for this command.

- [ ] **Step 6: Run launcher tests, configuration validation, and commit**

Run:

```bash
cd backend
pytest -q tests/integration/test_manager_restart_recovery.py tests/unit/test_runtime_config_resolution.py
cd ..
SKIP_INSTALL=1 ./start.sh config agent
SKIP_INSTALL=1 ./start.sh config control-plane
```

Expected: tests pass; both generated configurations report `configuration valid`.

Commit:

```bash
git add start.sh backend/tests/integration/test_manager_restart_recovery.py
git commit -m "fix: bootstrap clean local v2 Fleet"
```

---

### Task 7: Verify a real Terminal HTTP and WebSocket/PTY path before frontend startup

**Files:**
- Create: `backend/ic_env_guard/development/__init__.py`
- Create: `backend/ic_env_guard/development/readiness.py`
- Modify: `start.sh:330-390`
- Modify: `backend/tests/integration/test_manager_restart_recovery.py`
- Test: `backend/tests/integration/test_local_stack_terminal.py`

**Interfaces:**
- Produces CLI module: `python -m ic_env_guard.development.readiness --manager-url ... --token-file ... --agent-id local-agent`.
- Consumes Manager Agent-scoped Terminal HTTP and WebSocket APIs only; it never connects directly to the Agent public API.

- [ ] **Step 1: Write a failing full-stack Terminal test**

Reserve unused Manager, Agent, ingest, and frontend TCP ports, start
`./start.sh all` with those environment overrides in an isolated dev
directory, and wait for the launcher message `Local Terminal proxy ready.`.
Using the Manager public token, assert:

```python
agents = _json("GET", "/api/v2/agents")
local = next(item for item in agents["agents"] if item["agent_id"] == "local-agent")
assert local["transport_profile_id"] == "local-loopback-http"

listed = _json("GET", "/api/agents/local-agent/terminals")
assert listed == {"terminals": []}
```

Then create a terminal through the Manager, resize it, request a gateway
connect token, connect to `/ws/agents/local-agent/terminals/{id}`, send
`printf '__LOCAL_V2_TERMINAL_OK__\\n'\r`, receive the sentinel, close the
terminal through the Manager, and assert a second use of the gateway ticket is
rejected.

- [ ] **Step 2: Run the full-stack test and verify failure**

Run:

```bash
cd backend
pytest -q tests/integration/test_local_stack_terminal.py
```

Expected: FAIL because the readiness module/message does not exist.

- [ ] **Step 3: Implement the bounded readiness module**

Use `urllib.request` or `httpx` for Manager HTTP calls and `websockets` for the
Manager WebSocket. The module must:

1. Read and strip the Manager public token from the protected token file.
2. GET `/api/agents/local-agent/terminals` and require 200.
3. POST `/api/agents/local-agent/terminals` with
   `{"title":"Startup verification","rows":24,"cols":80}`.
4. POST resize with `{"rows":30,"cols":100}` and require 204.
5. POST connect-token and open only the Manager gateway WebSocket.
6. Send `printf '__LOCAL_V2_TERMINAL_OK__\\n'\r` and require the sentinel
   before a ten-second deadline.
7. Close the WebSocket and DELETE the terminal in `finally`.
8. Print only `Local Terminal proxy ready.` on success and a stable safe error
   on failure.

Pass the Manager token as an Authorization header; never place it in a URL,
WebSocket query, exception message, or success output.

- [ ] **Step 4: Call readiness before starting Vite**

In `start_all`, after local bootstrap and before `start_frontend`, invoke the
module with the Manager URL, `control-plane.token`, and `local-agent`. Any
non-zero result must trigger the existing child cleanup trap.

- [ ] **Step 5: Run full-stack, Terminal, and launcher tests**

Run:

```bash
cd backend
pytest -q tests/integration/test_local_stack_terminal.py tests/integration/test_manager_restart_recovery.py tests/contract/test_agent_terminal_http_contract.py tests/integration/test_agent_terminal_websocket.py tests/contract/test_terminal_websocket_contract.py tests/integration/test_terminal_lifecycle.py
ruff check ic_env_guard/development/readiness.py tests/integration/test_local_stack_terminal.py
```

Expected: all tests pass; the full-stack test observes the PTY sentinel through the Manager.

- [ ] **Step 6: Commit**

```bash
git add backend/ic_env_guard/development/__init__.py backend/ic_env_guard/development/readiness.py start.sh backend/tests/integration/test_manager_restart_recovery.py backend/tests/integration/test_local_stack_terminal.py
git commit -m "test: gate local startup on Terminal proxy"
```

---

### Task 8: User documentation, browser acceptance, and complete regression

**Files:**
- Modify: `README.md`
- Modify: `docs/guides/development.md`
- Modify: `docs/guides/getting-started.md`
- Modify: `docs/guides/manager-fleet.md`

**Interfaces:**
- Documents: disposable generated Fleet state, preserved login tokens, pure v2 local enrollment, expected readiness output, and troubleshooting.
- Verifies: the actual React route `/agents/local-agent/terminal`.

- [ ] **Step 1: Update supported-user documentation**

Document that `./start.sh all`:

- starts Agent on 8766, local ingest on 8767, Manager on 8765, and Vite on 5173;
- rebuilds generated Agent/Manager databases and managed credentials;
- preserves valid `agent.token` and `control-plane.token`;
- registers `local-agent` through owner-only local v2 enrollment;
- prints `Local Terminal proxy ready.` only after a real proxied PTY succeeds;
- does not require SSH for the same-host development Agent; and
- retains SSH enrollment for remote Agents.

Remove any statement that the current local stack relies on static Agent YAML
compatibility import. Keep compatibility import documented only as a legacy
recovery path where it is already described.

- [ ] **Step 2: Run all automated regressions from a clean test process**

Run:

```bash
cd backend
pytest -q
ruff check .
cd ../frontend
npm test
npm run build
```

Expected: complete backend suite passes; Ruff reports no errors; frontend tests and production build pass.

- [ ] **Step 3: Run the actual stack and inspect the Registry and generated config**

Run:

```bash
./start.sh all
```

Expected startup includes `Local Agent enrolled.` and
`Local Terminal proxy ready.`. In a second terminal, inspect the Manager API
with the protected token and assert `local-agent` reports
`local-loopback-http`. Inspect the local development Registry database with a
read-only query and assert its enrollment method/source are `local_socket` and
`local_dev_bootstrap`. Confirm the generated Manager YAML has no `agents:` key
and no `legacy-config-http` string.

- [ ] **Step 4: Perform browser acceptance**

Use the browser-control skill to open
`http://127.0.0.1:5173/agents/local-agent/terminal`, authenticate with the
Manager public token if prompted, create a terminal, run
`printf '__BROWSER_TERMINAL_OK__\\n'`, verify the output is visible, resize the
browser panel, close the terminal, and confirm no `agent request failed`
message appears.

- [ ] **Step 5: Review the final diff and commit documentation**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; unrelated user files are unstaged and unchanged.

Commit:

```bash
git add README.md docs/guides/development.md docs/guides/getting-started.md docs/guides/manager-fleet.md
git commit -m "docs: describe pure v2 local Fleet startup"
```

- [ ] **Step 6: Request final code review and prepare delivery evidence**

Use the requesting-code-review skill against the design and this plan. Resolve
all confirmed findings, rerun the affected focused tests plus the complete
regression commands above, and report exact command results, commit IDs, and
the browser acceptance outcome. Do not claim completion from earlier cached
test output.

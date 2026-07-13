# Documentation Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed README and scattered documentation with an English, task-oriented documentation hub for operators and developers, while archiving historical specs and plans under `docs/development/` and repairing every repository-relative Markdown link.

**Architecture:** The root README becomes a concise dual-entry landing page. Canonical current guidance lives in focused files under `docs/guides/` and `docs/reference/`; historical design material moves unchanged in substance to `docs/development/`. Each task consolidates one coherent topic, checks it against the implementation and tests, and removes the superseded source only after the canonical replacement exists.

**Tech Stack:** Markdown, YAML examples, Bash commands, Python 3.12/Pydantic configuration validation, Git-aware renames, `rg`, pytest.

## Global Constraints

- All user-facing and development documentation must be English.
- This is documentation-only work: do not change runtime behavior, APIs, configuration semantics, packaging behavior, or security policy.
- Preserve unrelated user-owned changes in `CLAUDE.md`, `.kilo/`, and `AGENTS.md`; never stage them.
- The root README must remain approximately 150–220 lines.
- Current operational guidance must have one canonical location per topic.
- Do not recommend the obsolete static Manager `agents:` configuration as the normal Fleet workflow.
- Local Ingest must always be described as tokenless, loopback-only, and forbidden from remote exposure, forwarding, or reverse proxying.
- Manager credentials must always be described as plaintext owner-only files in a `0700` directory with `0600` files.
- The Agent and Manager use existing Linux accounts; the project does not create users or modify sudoers.
- Historical files remain available but are explicitly non-normative for current operation.
- Use Git-aware renames for `docs/superpowers/` and root `specs/`.
- Every relative Markdown link must resolve after the final moves.

---

### Task 1: Build the documentation landing pages and local-development path

**Files:**
- Modify: `README.md`
- Create: `docs/README.md`
- Create: `docs/guides/getting-started.md`
- Create: `docs/guides/development.md`
- Read: `start.sh`
- Read: `frontend/vite.config.ts`
- Read: `backend/ic_env_guard/systemd/cli.py`

**Interfaces:**
- Consumes: Current `start.sh` command names, ports, generated paths, environment overrides, and test commands.
- Produces: The canonical repository landing page, documentation index, deployment-mode chooser, and developer workflow used by every later guide.

- [ ] **Step 1: Record the exact current wrapper contract before editing**

Run:

```bash
./start.sh help
rg -n 'Usage:|Commands:|Environment overrides:|IC_ENV_GUARD_|SKIP_INSTALL' start.sh
```

Expected: the output lists `agent`, `control-plane`, `backend`, `frontend`, `all`, `config`, and `help`, plus the documented environment variables. Copy exact names and defaults into the new docs; do not infer aliases.

- [ ] **Step 2: Rewrite the root README as a short dual-entry landing page**

Replace the existing mixed manual with these exact top-level sections:

```markdown
# IC Design Environment Guard
## What It Runs
## Choose Your Path
## Five-Minute Local Demo
## Production Installation Summary
## Minimal Configuration
## Validate the Installation
## Security Boundaries
## Documentation
## Development Checks
## Repository Layout
```

Required content:

- Define Standalone Agent, Manager Fleet, and Local Ingest in `What It Runs`.
- Link the operator path to `docs/guides/agent-deployment.md` and `docs/guides/manager-fleet.md`.
- Link the developer path to `docs/guides/development.md`.
- Show `./start.sh all` and the exact default Manager, Agent Public, Agent Local Ingest, and Vite ports.
- Show the existing-user installer command and template systemd unit.
- Include minimal Agent and Manager YAML blocks, with advanced fields delegated to `docs/guides/configuration.md`.
- State that Local Ingest has no token and must remain loopback-only.
- State that browser Terminal authority equals the selected Agent Linux user, including that user's existing sudo authority.
- Keep the final file between 150 and 220 lines.

- [ ] **Step 3: Create the documentation index**

Create `docs/README.md` with these sections and links:

```markdown
# Documentation
## Start Here
## Deployment and Operation
## Reference
## Development History
```

`Start Here` links to `guides/getting-started.md`. `Deployment and Operation` lists all nine guide files defined by the approved spec. `Reference` lists both reference files. `Development History` links to `development/README.md` and warns that archived plans are not current operator instructions.

- [ ] **Step 4: Create the mode chooser and local-development guide**

Create `docs/guides/getting-started.md` with:

- a three-row Agent / Manager / development decision table;
- prerequisites for each path;
- listener and state ownership for each runtime mode;
- links to the configuration and deployment guides;
- health, readiness, login, and UI success checks.

Create `docs/guides/development.md` with:

- Conda environment `venv312` and editable install commands;
- npm installation and Vite behavior;
- every `start.sh` command and environment override from `./start.sh help`;
- generated development paths under `/tmp/ic-env-guard-dev`;
- backend pytest/Ruff and frontend test/build/lint commands;
- the Linux-only boundary for systemd and packaging validation.

- [ ] **Step 5: Verify landing-page commands and links**

Run:

```bash
test "$(wc -l < README.md)" -ge 150
test "$(wc -l < README.md)" -le 220
./start.sh config agent
./start.sh config control-plane
rg -n 'docs/guides/(agent-deployment|manager-fleet|development)\.md' README.md
git diff --check -- README.md docs/README.md docs/guides/getting-started.md docs/guides/development.md
```

Expected: both configs validate, the required links exist, README is within the approved size, and `git diff --check` prints nothing.

- [ ] **Step 6: Commit the landing pages**

```bash
git add README.md docs/README.md docs/guides/getting-started.md docs/guides/development.md
git commit -m "docs: add operator and developer entry points"
```

---

### Task 2: Write practical configuration guidance and the field reference

**Files:**
- Create: `docs/guides/configuration.md`
- Create: `docs/reference/configuration.md`
- Modify: `README.md`
- Read: `backend/ic_env_guard/config/models.py`
- Read: `backend/tests/contract/test_control_plane_config.py`
- Read: `backend/tests/contract/test_runtime_api.py`
- Read: `backend/tests/unit/test_runtime_config_resolution.py`
- Read: `backend/tests/unit/test_transport_profiles.py`

**Interfaces:**
- Consumes: Current Pydantic field names, defaults, validation constraints, mode rules, and transport-profile behavior.
- Produces: Canonical practical Agent/Manager YAML examples and the mode-aware configuration reference linked by all later guides.

- [ ] **Step 1: Inventory configuration fields from the source of truth**

Run:

```bash
rg -n '^class .*Config|^[[:space:]]+[a-z_][a-z0-9_]*:' backend/ic_env_guard/config/models.py
rg -n 'mode:|server:|ingest:|auth:|metrics:|terminal:|logs:|observations:|control_plane:|enrollment:|services:' backend/tests/contract/test_control_plane_config.py backend/tests/contract/test_runtime_api.py
```

Expected: every section documented in the new reference can be traced to a model or tested configuration contract.

- [ ] **Step 2: Write the practical configuration guide**

Create `docs/guides/configuration.md` with these sections:

```markdown
# Configuration Guide
## File Ownership and Validation
## Agent Configuration
## Manager Configuration
## Network Exposure and Transport Profiles
## Services, Logs, and Observations
## Enrollment Configuration
## Validate Before Restarting
```

Include one complete Agent YAML example and one complete Manager YAML example. The Agent example must show Public, Local Ingest, bearer-token file, SQLite state, metrics, terminal, observations, logs allowed roots, enrollment socket, and an empty `services` list. The Manager example must show Public, bearer-token file, audit DB, credential directory, Agent CIDR allowlist, probe settings, discovery settings, and Manager enrollment socket. Do not include a static `agents:` list in the recommended Manager example.

- [ ] **Step 3: Write the mode-aware field reference**

Create `docs/reference/configuration.md` with one table per top-level section. Each row has:

```text
Field | Mode | Default | Constraints | Purpose
```

Cover all operator-facing fields from `AppConfig` and its nested config models. Explicitly document:

- `server.remote_bind_enabled` and trusted-LAN requirements;
- Local Ingest bind restricted to `127.0.0.1` or `::1`;
- terminal timeout and replay limits;
- Observation retention and cleanup limits;
- log-root and bounded-tail rules;
- enrollment socket modes, TTL range, SSH timeouts, and service-key pair requirement;
- discovery scope private-CIDR and size limits;
- Manager credential-directory and probe limits;
- transport profile types and CA-bundle rules;
- exactly-one-of `command` / `systemd_unit` for services.

- [ ] **Step 4: Validate the two complete YAML examples**

The first two `yaml` fences in `docs/guides/configuration.md` must be the
complete Agent and Manager examples, in that order. Extract them into `/tmp`:

```bash
python - <<'PY'
import re
from pathlib import Path

source = Path('docs/guides/configuration.md').read_text(encoding='utf-8')
blocks = re.findall(r'^```yaml\n(.*?)^```$', source, flags=re.MULTILINE | re.DOTALL)
if len(blocks) < 2:
    raise SystemExit('expected complete Agent and Manager YAML examples')
Path('/tmp/ic-env-guard-doc-agent.yaml').write_text(blocks[0], encoding='utf-8')
Path('/tmp/ic-env-guard-doc-manager.yaml').write_text(blocks[1], encoding='utf-8')
PY
```

Invoke the same validator entry function used by the packaged console script:

```bash
cd backend
conda run -n venv312 python -c 'from ic_env_guard.systemd.cli import main; import sys; raise SystemExit(main(["validate", sys.argv[1]]))' /tmp/ic-env-guard-doc-agent.yaml
conda run -n venv312 python -c 'from ic_env_guard.systemd.cli import main; import sys; raise SystemExit(main(["validate", sys.argv[1]]))' /tmp/ic-env-guard-doc-manager.yaml
```

Expected: both commands exit 0 and print `configuration valid`.

- [ ] **Step 5: Run configuration contracts**

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_control_plane_config.py \
  tests/contract/test_runtime_api.py \
  tests/unit/test_runtime_config_resolution.py \
  tests/unit/test_transport_profiles.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit configuration documentation**

```bash
git add README.md docs/guides/configuration.md docs/reference/configuration.md
git commit -m "docs: document agent and manager configuration"
```

---

### Task 3: Consolidate Agent deployment and Local Ingest guidance

**Files:**
- Create: `docs/guides/agent-deployment.md`
- Create: `docs/guides/local-data-ingest.md`
- Read: `docs/agent-v2-operations.md`
- Read: `docs/operations/lifecycle.md`
- Read: `docs/operations/service-config.md`
- Read: `packaging/systemd/ic-env-guard@.service`
- Read: `packaging/install/install.sh`
- Read: `backend/ic_env_guard/api/ingest_observations.py`
- Read: `backend/ic_env_guard/api/ingest_logs.py`
- Read: `backend/ic_env_guard/observations/models.py`
- Read: `backend/ic_env_guard/logs/models.py`

**Interfaces:**
- Consumes: Existing-user installer/unit behavior, Public/Ingest separation, Observation and Log Source wire contracts.
- Produces: Canonical Agent lifecycle and local-producer guides referenced by README, security, monitoring, and recovery documents.

- [ ] **Step 1: Verify packaging and listener facts**

```bash
rg -n 'User=|RuntimeDirectory|ExecStart|EnvironmentFile|IC_ENV_GUARD_CONFIG' packaging/systemd/ic-env-guard@.service
rg -n 'existing|root|user|0600|0700|ic-env-guard@' packaging/install/install.sh
rg -n 'APIRouter|/api/v2/observations|/api/v2/logs|ttl_seconds|details|last_updated' \
  backend/ic_env_guard/api/ingest_observations.py \
  backend/ic_env_guard/api/ingest_logs.py \
  backend/ic_env_guard/observations/models.py \
  backend/ic_env_guard/logs/models.py
```

Expected: every command, file mode, endpoint, and payload field used in the new guides is supported by implementation or packaging.

- [ ] **Step 2: Write the Agent deployment guide**

Create `docs/guides/agent-deployment.md` with:

- existing non-root account selection and terminal/sudo authority;
- installer and template-unit commands;
- `/etc/ic-env-guard/<user>.yaml` and owner-only state/token paths;
- Public and Local Ingest listener responsibilities;
- configuration validation before start/restart;
- service mapping with exactly one of command or systemd unit;
- systemctl and journalctl workflows;
- health, readiness, login, metrics, Terminal, and enrollment-socket checks;
- links to configuration, security, ingest, monitoring, and recovery guides.

- [ ] **Step 3: Write the Local Ingest producer guide**

Create `docs/guides/local-data-ingest.md` with exact curl examples for:

```text
PUT http://127.0.0.1:8766/api/v2/observations
PUT http://127.0.0.1:8766/api/v2/logs/{log_id}
```

The Observation example must include `namespace`, `name`, `kind`, `value`, `status`, `labels`, `details`, `observed_at`, and `ttl_seconds`. The Log Source example must include `path`, `last_updated`, `observed_at`, and `ttl_seconds`. Explain ordering, expiry, latest-value storage, bounded labels, preserved `details`, allowed log roots, metadata-only SQLite storage, and authenticated bounded tail reads through Public.

- [ ] **Step 4: Run ingest and deployment contract tests**

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_observation_ingest_api.py \
  tests/contract/test_log_ingest_api.py \
  tests/contract/test_observation_read_api.py \
  tests/contract/test_log_read_api.py \
  tests/integration/test_agent_deployment_contract.py \
  tests/security/test_ingest_listener_isolation.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Agent and producer guides**

```bash
git add docs/guides/agent-deployment.md docs/guides/local-data-ingest.md
git commit -m "docs: consolidate agent deployment and local ingest"
```

---

### Task 4: Consolidate Manager Fleet operations

**Files:**
- Create: `docs/guides/manager-fleet.md`
- Read: `docs/manager-fleet-operations.md`
- Read: `docs/manager-enrollment-security.md`
- Read: `docs/operations/control-plane.md`
- Read: `backend/ic_env_guard/api/agent_registry.py`
- Read: `backend/ic_env_guard/api/discovery.py`
- Read: `backend/ic_env_guard/api/fleet_v2.py`
- Read: `frontend/src/features/agent-registry/AddAgentPage.tsx`
- Read: `frontend/src/features/fleet/FleetPage.tsx`

**Interfaces:**
- Consumes: Current SQLite Registry, discovery, enrollment, rotation, probe, proxy, removal, and Audit workflows.
- Produces: The canonical Manager/Fleet operator guide used by README and all security/recovery cross-links.

- [ ] **Step 1: Inventory current Manager capabilities and UI operations**

```bash
rg -n 'router\.(get|post|put|delete)|legacy_revalidation|required|credential-rotation|probe|local_only' backend/ic_env_guard/api/agent_registry.py
rg -n 'router\.(get|post)|scope|cancel|results' backend/ic_env_guard/api/discovery.py
rg -n 'ssh-enrollment|agent-registry|fleet\.v2|terminal' backend/ic_env_guard/api/runtime.py frontend/src/app/ManagerEntry.tsx
```

Expected: the guide outline maps to actual APIs and runtime capability gates.

- [ ] **Step 2: Write the Manager Fleet guide**

Create `docs/guides/manager-fleet.md` with these sections:

```markdown
# Manager Fleet Guide
## Manager Responsibilities
## Install and Configure the Manager
## Add an Agent by Address
## Discover Agents in a Bounded Scope
## SSH Enrollment and CLI Fallback
## Legacy Token Recovery
## Probe and Interpret Fleet Status
## Use Agent-Scoped Pages and Terminal
## Edit, Rotate, Disable, and Remove
## Control-Plane Audit
## Failure Isolation and Troubleshooting
```

Required semantics:

- Browser users authenticate only to Manager.
- Manager stores Agent credentials in owner-only files, not the browser or DB.
- The Web-managed SQLite Registry is authoritative after import.
- Discovery accepts configured named scopes only and remains bounded.
- SSH enrollment uses existing users, host-key verification, and fixed helper behavior.
- Legacy tokens are compatibility/recovery inputs and provide no stable instance identity.
- One offline Agent must not block other Fleet results.
- Terminal proxying is Agent-scoped and does not create a generic proxy.
- Local-only removal leaves a documented remote credential residual.

- [ ] **Step 3: Run Manager workflow contracts**

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_agent_registry_v2.py \
  tests/contract/test_agent_enrollment_api.py \
  tests/contract/test_agent_mutation_v2.py \
  tests/contract/test_discovery_api.py \
  tests/contract/test_fleet_overview_v2.py \
  tests/integration/test_fleet_end_to_end.py
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit Manager Fleet guidance**

```bash
git add docs/guides/manager-fleet.md
git commit -m "docs: consolidate manager fleet operations"
```

---

### Task 5: Consolidate monitoring, security, and recovery guidance

**Files:**
- Create: `docs/guides/monitoring-and-logs.md`
- Create: `docs/guides/security.md`
- Create: `docs/guides/backup-upgrade-recovery.md`
- Read: `docs/operations/prometheus.md`
- Read: `docs/operations/terminal-safety.md`
- Read: `docs/operations/security-review.md`
- Read: `docs/operations/recovery.md`
- Read: `docs/manager-enrollment-security.md`
- Read: `docs/manager-backup-and-rollback.md`
- Read: `docs/agent-v2-operations.md`
- Read: `packaging/install/upgrade.sh`

**Interfaces:**
- Consumes: Existing metrics, terminal privacy, TLS/enrollment, backup-unit, upgrade, rollback, and residual-credential requirements.
- Produces: Three canonical cross-mode guides, replacing the scattered security and recovery documents.

- [ ] **Step 1: Write the monitoring and logs guide**

Create `docs/guides/monitoring-and-logs.md` covering:

- Agent `/metrics` and direct Prometheus scraping;
- local scrape default and remote CIDR allowlist;
- current-value versus historical time-series ownership;
- bounded metric labels and forbidden high-cardinality data;
- Agent Observation and service status;
- Manager cached Fleet summaries, staleness, and partial errors;
- Log Source metadata and authenticated bounded tails;
- health/readiness endpoints and troubleshooting commands.

- [ ] **Step 2: Write the consolidated security guide**

Create `docs/guides/security.md` covering:

- bearer-token and token-file permissions;
- browser-to-Agent versus browser-to-Manager authentication;
- Public versus Local Ingest exposure;
- Verified TLS and explicit trusted-LAN HTTP profiles;
- Manager CIDR/target/SSRF boundaries;
- SSH host keys, CLI socket, restricted service key, and fixed helper;
- existing-user Terminal authority and sudo implications;
- one-use WebSocket tickets and non-persistent terminal output;
- owner-only Manager credentials and secret-exclusion rules;
- audit boundaries and residual credential warnings.

Use the exact forced-command template from `packaging/ssh/ic-env-guard-enrollment-authorized-key.example` rather than inventing a variant.

- [ ] **Step 3: Write the backup, upgrade, and recovery guide**

Create `docs/guides/backup-upgrade-recovery.md` covering two separate atomic units:

```text
Agent: config + legacy token + instance-id + SQLite state DB
Manager: config + control-plane SQLite DB/journals + credential directory
```

Document stop-before-backup, original ownership/modes, no runtime socket backup, identity fail-closed behavior, installer upgrade paths, interrupted-upgrade rerun behavior, YAML-to-SQLite import, rollback, legacy-token recovery, rotation, local-only removal residuals, and post-restore validation.

- [ ] **Step 4: Run security, metrics, and recovery tests**

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_metrics_contract.py \
  tests/contract/test_terminal_websocket_contract.py \
  tests/integration/test_manager_restart_recovery.py \
  tests/integration/test_credential_rotation.py \
  tests/integration/test_packaging_runtime.py \
  tests/security/test_dynamic_agent_ssrf.py \
  tests/security/test_enrollment_socket.py \
  tests/security/test_manager_enrollment_socket.py \
  tests/security/test_ssh_enrollment_security.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit monitoring, security, and recovery guides**

```bash
git add docs/guides/monitoring-and-logs.md docs/guides/security.md docs/guides/backup-upgrade-recovery.md
git commit -m "docs: consolidate security monitoring and recovery"
```

---

### Task 6: Create the listener and endpoint reference

**Files:**
- Create: `docs/reference/api-and-endpoints.md`
- Read: `backend/ic_env_guard/main.py`
- Read: `backend/ic_env_guard/api/runtime.py`
- Read: `backend/ic_env_guard/api/ingest_guard.py`
- Read: `backend/ic_env_guard/api/agent_proxy.py`
- Read: `backend/ic_env_guard/api/agent_terminal_ws.py`
- Read: `backend/ic_env_guard/enrollment/manager_socket.py`
- Read: `backend/ic_env_guard/enrollment/socket_server.py`

**Interfaces:**
- Consumes: Registered FastAPI routers, listener separation, runtime-mode capabilities, WebSocket routes, and Unix-socket enrollment protocols.
- Produces: A canonical operator-facing endpoint map linked from configuration, ingest, Fleet, monitoring, and security guides.

- [ ] **Step 1: Generate a route inventory from the application**

Run:

```bash
cd backend
PYTHONPATH=. conda run -n venv312 python -c 'from pathlib import Path
from tempfile import TemporaryDirectory
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app
with TemporaryDirectory() as directory:
    root = Path(directory)
    token = root / "token"
    token.write_text("documentation-route-inventory-token\n", encoding="utf-8")
    token.chmod(0o600)
    configs = (
        AppConfig(mode="agent", auth=AuthConfig(token_file=token), state_database=root / "agent.db"),
        AppConfig(mode="control-plane", auth=AuthConfig(token_file=token), control_plane=ControlPlaneConfig(audit_database=root / "manager.db", credential_directory=root / "credentials", allowed_agent_cidrs=["10.0.0.0/8"])),
    )
    for config in configs:
        app = create_app(config=config)
        print(f"== {config.mode} Public ==")
        for route in sorted(app.routes, key=lambda item: item.path):
            methods = ",".join(sorted(getattr(route, "methods", ()) or ()))
            print(methods if methods else type(route).__name__.upper(), route.path)'
rg -n 'websocket|APIRouter\(|@router\.(get|post|put|delete)' ic_env_guard/api ic_env_guard/enrollment
```

Expected: a concrete Public-route inventory for both modes plus source-level
inventory for Local Ingest, WebSocket, metrics/health, and enrollment
transports.

- [ ] **Step 2: Write the endpoint reference**

Create `docs/reference/api-and-endpoints.md` with sections:

```markdown
# API and Endpoint Reference
## Agent Public Listener
## Agent Local Ingest Listener
## Manager Public Listener
## Prometheus and Health
## Terminal WebSockets
## Enrollment Unix Sockets
## Authentication and Exposure Matrix
```

For each group, state intended caller, authentication, exposure boundary, route families, and links to request examples. Include an exposure matrix with columns:

```text
Surface | Default bind/path | Authentication | Runtime mode | Remote exposure
```

Do not reproduce OpenAPI schemas or describe any route as a generic proxy.

- [ ] **Step 3: Verify API contracts and listener isolation**

```bash
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_runtime_api.py \
  tests/contract/test_agent_v1_compatibility.py \
  tests/contract/test_manager_v1_compatibility.py \
  tests/contract/test_terminal_http_contract.py \
  tests/contract/test_terminal_websocket_contract.py \
  tests/security/test_ingest_listener_isolation.py \
  tests/security/test_agent_proxy_boundaries.py
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit the endpoint reference**

```bash
git add docs/reference/api-and-endpoints.md
git commit -m "docs: add listener and endpoint reference"
```

---

### Task 7: Replace scattered operator documents with canonical links

**Files:**
- Modify: `docs/README.md`
- Modify: `packaging/runtime/README.md`
- Modify: `spec.refactorize_1.md`
- Delete after consolidation: `docs/agent-v2-operations.md`
- Delete after consolidation: `docs/manager-fleet-operations.md`
- Delete after consolidation: `docs/manager-enrollment-security.md`
- Delete after consolidation: `docs/manager-backup-and-rollback.md`
- Delete after consolidation: `docs/operations/control-plane.md`
- Delete after consolidation: `docs/operations/lifecycle.md`
- Delete after consolidation: `docs/operations/prometheus.md`
- Delete after consolidation: `docs/operations/recovery.md`
- Delete after consolidation: `docs/operations/security-review.md`
- Delete after consolidation: `docs/operations/service-config.md`
- Delete after consolidation: `docs/operations/terminal-safety.md`

**Interfaces:**
- Consumes: Canonical guides and references from Tasks 1–6.
- Produces: One canonical current document per topic, with packaging and root-level Markdown pointing to the new locations.

- [ ] **Step 1: Compare every old operator heading with the canonical replacements**

```bash
for file in \
  docs/agent-v2-operations.md \
  docs/manager-fleet-operations.md \
  docs/manager-enrollment-security.md \
  docs/manager-backup-and-rollback.md \
  docs/operations/*.md; do
  echo "===== ${file}"
  rg '^#{1,3} ' "${file}"
done
```

Expected: each substantive current-operation heading maps to a section in `docs/guides/` or `docs/reference/`. Move any missing accurate content before deleting a source file.

- [ ] **Step 2: Update packaging and root-level links**

Update `packaging/runtime/README.md` to link to:

- `../../docs/guides/agent-deployment.md`
- `../../docs/guides/local-data-ingest.md`
- `../../docs/guides/manager-fleet.md`
- `../../docs/guides/security.md`
- `../../docs/guides/backup-upgrade-recovery.md`

Update `spec.refactorize_1.md` links to the future `docs/development/specs/002-multi-agent-control-plane/` paths so they remain valid after Task 8.

- [ ] **Step 3: Remove superseded operator files**

Use `git rm` only for the old operator files listed in this task after confirming their accurate content exists in the canonical guides. Do not remove `docs/operations/platform-validation.md`, `docs/operations/quickstart-validation.md`, or `docs/operations/test-results.md`; Task 8 moves them into the development archive.

- [ ] **Step 4: Search for stale operator links**

```bash
rg -n 'docs/(agent-v2-operations|manager-fleet-operations|manager-enrollment-security|manager-backup-and-rollback)\.md|docs/operations/(control-plane|lifecycle|prometheus|recovery|security-review|service-config|terminal-safety)\.md' \
  --glob '*.md' .
```

Expected: no matches.

- [ ] **Step 5: Commit operator-document consolidation**

```bash
git add docs/README.md packaging/runtime/README.md spec.refactorize_1.md docs/guides docs/reference
git add -u docs
git commit -m "docs: replace scattered operator manuals"
```

---

### Task 8: Move development history and repair archive links

**Files:**
- Create: `docs/development/README.md`
- Move: `docs/superpowers/` → `docs/development/superpowers/`
- Move: `specs/` → `docs/development/specs/`
- Move: `docs/operations/platform-validation.md` → `docs/development/validation/platform-validation.md`
- Move: `docs/operations/quickstart-validation.md` → `docs/development/validation/quickstart-validation.md`
- Move: `docs/operations/test-results.md` → `docs/development/validation/test-results.md`
- Modify: moved Markdown files whose relative links would otherwise break
- Modify: `docs/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Stable canonical docs from Tasks 1–7 and the complete historical source trees.
- Produces: The approved `docs/development/` archive, including this plan at `docs/development/superpowers/plans/2026-07-13-documentation-reorganization-implementation-plan.md`.

- [ ] **Step 1: Create the archive notice**

Create `docs/development/README.md` with:

```markdown
# Development History

These files preserve design, planning, contract, and validation history. They
are not the source of truth for current deployment or operation. Use the root
README, `docs/guides/`, `docs/reference/`, current code, and tests for supported
behavior.

Relative links are maintained where they identify repository artifacts.
Literal paths inside historical task instructions may retain their original
spelling when changing them would misrepresent the historical record.
```

Add sections linking to `superpowers/`, `specs/`, and `validation/`.

- [ ] **Step 2: Move the three development trees with Git-aware renames**

```bash
mkdir -p docs/development/validation
git mv docs/superpowers docs/development/superpowers
git mv specs docs/development/specs
git mv docs/operations/platform-validation.md docs/development/validation/platform-validation.md
git mv docs/operations/quickstart-validation.md docs/development/validation/quickstart-validation.md
git mv docs/operations/test-results.md docs/development/validation/test-results.md
```

Expected: `git status --short` reports renames where Git can detect them; no historical file is missing.

- [ ] **Step 3: Repair relative links in current docs and archive indexes**

Update current links to the new paths. At minimum, repair:

- README and `docs/README.md` development-history links;
- `spec.refactorize_1.md` spec/contract/plan links;
- moved Superpowers plans linking to their approved design spec;
- moved validation docs linking to old root `specs/` paths;
- Markdown links inside moved specs that target repository files outside their own directory.

Do not mechanically rewrite literal paths inside shell commands or historical file manifests unless those paths are intended to be executable in the current repository.

- [ ] **Step 4: Prove the old development locations are gone**

```bash
test ! -e docs/superpowers
test ! -e specs
test -d docs/development/superpowers/plans
test -d docs/development/superpowers/specs
test -d docs/development/specs/001-linux-host-agent
test -d docs/development/specs/002-multi-agent-control-plane
test -d docs/development/validation
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the development archive move**

```bash
git add README.md docs/README.md docs/development spec.refactorize_1.md
git add -u docs
git commit -m "docs: archive development specifications and plans"
```

---

### Task 9: Run full documentation validation and final review

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/guides/getting-started.md`
- Modify: `docs/guides/configuration.md`
- Modify: `docs/guides/agent-deployment.md`
- Modify: `docs/guides/manager-fleet.md`
- Modify: `docs/guides/local-data-ingest.md`
- Modify: `docs/guides/monitoring-and-logs.md`
- Modify: `docs/guides/security.md`
- Modify: `docs/guides/backup-upgrade-recovery.md`
- Modify: `docs/guides/development.md`
- Modify: `docs/reference/configuration.md`
- Modify: `docs/reference/api-and-endpoints.md`
- Modify: `docs/development/README.md`
- Modify: `packaging/runtime/README.md`
- Modify: `spec.refactorize_1.md`

**Interfaces:**
- Consumes: The complete reorganized documentation tree.
- Produces: A link-clean, implementation-accurate, review-ready documentation delivery with no stale current-operation paths.

- [ ] **Step 1: Run an offline relative Markdown link scan**

Run this complete local checker from the repository root:

```bash
python - <<'PY'
import re
import sys
from pathlib import Path
from urllib.parse import unquote

root = Path.cwd().resolve()
broken = []
pattern = re.compile(r'(?<!!)\[[^\]]*\]\(([^)]+)\)')

def heading_anchors(path):
    counts = {}
    anchors = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if not match:
            continue
        text = re.sub(r'[`*_~]', '', match.group(1)).lower()
        base = re.sub(r'[^\w\s-]', '', text)
        base = re.sub(r'[\s-]+', '-', base).strip('-')
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f'{base}-{count}')
    return anchors

for source in sorted(root.rglob('*.md')):
    if any(part in {'.git', 'node_modules'} for part in source.parts):
        continue
    text = source.read_text(encoding='utf-8')
    for raw in pattern.findall(text):
        destination = raw.strip().split()[0].strip('<>')
        if not destination or destination.startswith(('http://', 'https://', 'mailto:')):
            continue
        file_part, separator, fragment = destination.partition('#')
        target = source if not file_part else (source.parent / unquote(file_part)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            broken.append((source.relative_to(root), destination, 'outside repository'))
            continue
        if not target.exists():
            broken.append((source.relative_to(root), destination, 'missing'))
            continue
        if separator and fragment and target.suffix.lower() == '.md':
            if unquote(fragment).lower() not in heading_anchors(target):
                broken.append((source.relative_to(root), destination, 'missing heading'))

for source, destination, reason in broken:
    print(f'{source}: {destination} ({reason})')
sys.exit(1 if broken else 0)
PY
```

Expected: no output and exit code 0.

- [ ] **Step 2: Search for stale paths and obsolete recommendations**

```bash
rg -n 'docs/superpowers/|(^|[(`/])specs/(001|002)-|docs/operations/|docs/(agent-v2-operations|manager-fleet-operations|manager-enrollment-security|manager-backup-and-rollback)\.md' \
  --glob '*.md' .
rg -n 'agents:' README.md docs/guides docs/reference
```

Expected:

- no current-document links point to removed paths;
- matches inside the development archive are either repaired links or clearly historical literal paths;
- `agents:` does not appear as the recommended Manager Registry configuration. If it appears in migration guidance, the surrounding text labels it compatibility-only.

- [ ] **Step 3: Validate current commands and configuration examples**

```bash
./start.sh help
./start.sh config agent
./start.sh config control-plane
cd backend
PYTHONPATH=. conda run -n venv312 pytest -q \
  tests/contract/test_control_plane_config.py \
  tests/contract/test_runtime_api.py \
  tests/integration/test_agent_deployment_contract.py \
  tests/integration/test_fleet_end_to_end.py \
  tests/integration/test_packaging_runtime.py
conda run -n venv312 ruff check .
```

Expected: wrapper configs validate, all selected backend tests pass, and Ruff passes.

- [ ] **Step 4: Run repository hygiene checks**

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints nothing. `git status --short` shows only intended documentation changes plus the pre-existing user-owned `CLAUDE.md`, `.kilo/`, and `AGENTS.md` entries; none of those user-owned paths are staged.

- [ ] **Step 5: Perform a documentation review from both user paths**

Operator review:

1. Start at README.
2. Choose Agent deployment and reach a valid config, install command, validation command, login check, and security guidance without opening development history.
3. Choose Manager Fleet and reach Manager config, Agent enrollment, probing, failure isolation, credential storage, removal, and backup guidance.

Developer review:

1. Start at README.
2. Reach Conda/npm setup, `./start.sh all`, generated paths/ports, tests, build, lint, and Linux validation boundaries.
3. Reach the development archive and see the non-normative warning before historical plans/specs.

Expected: no missing step, contradictory command, duplicated normative topic, or link to obsolete operator guidance.

- [ ] **Step 6: Commit final documentation corrections**

```bash
git add README.md docs packaging/runtime/README.md spec.refactorize_1.md
git commit -m "docs: complete configuration and usage manual"
```

- [ ] **Step 7: Verify the final commit and clean staged state**

```bash
git show --stat --oneline HEAD
git diff HEAD^ --check
git status --short
```

Expected: the final commit contains documentation only; the diff check passes; only the user's pre-existing unrelated changes remain in the working tree.

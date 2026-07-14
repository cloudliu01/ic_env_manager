# Developer Architecture Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one current developer architecture guide centered on Mermaid diagrams and compact interface contracts, then make it discoverable from the root and documentation READMEs.

**Architecture:** `docs/guides/developer-architecture.md` is the single developer onboarding view of structure, runtime boundaries, flows, and interface families. It links to current normative references and runtime `/openapi.json` instead of duplicating complete schemas. Root `README.md` and `docs/README.md` provide the two supported discovery paths.

**Tech Stack:** GitHub-flavored Markdown, GitHub Mermaid, OpenAPI-like YAML, AsyncAPI/RPC-style YAML, FastAPI route definitions, React/TypeScript source references, shell validation.

## Global Constraints

- All current documentation changes are in English.
- Use only Mermaid diagram types rendered by GitHub Markdown: `flowchart` and `sequenceDiagram`.
- Runtime `/openapi.json` is authoritative for mounted HTTP operations and schemas; do not check in a generated `openapi.yaml`.
- Configuration modes are exactly `agent` and `control-plane`; frontend runtime metadata calls the latter `manager`.
- Local Ingest is an Agent-only second listener sharing the Agent container, not a third runtime mode.
- Do not claim Terminal sessions/replay or configured-service runtime state are currently persisted by SQLite.
- Do not present the uncomposed systemd adapter as the active configured-service backend.
- Prometheus owns metric history; the Agent stores only latest Observation state plus freshness metadata.
- Do not add dependencies, generated assets, API behavior, or unrelated documentation reorganization.
- Preserve unrelated working-tree changes in `CLAUDE.md`, `.kilo/`, and `AGENTS.md`.

---

### Task 1: Write the developer architecture guide

**Files:**
- Create: `docs/guides/developer-architecture.md`
- Reference: `docs/development/superpowers/specs/2026-07-14-developer-architecture-documentation-design.md`
- Reference: `backend/ic_env_guard/main.py`
- Reference: `backend/ic_env_guard/bootstrap/composition.py`
- Reference: `backend/ic_env_guard/bootstrap/lifecycle.py`
- Reference: `frontend/src/app/RuntimeProvider.tsx`
- Reference: `frontend/src/app/AgentEntry.tsx`
- Reference: `frontend/src/app/ManagerEntry.tsx`
- Reference: `docs/reference/api-and-endpoints.md`

**Interfaces:**
- Consumes: current mounted route families, runtime composition boundaries, frontend feature boundaries, storage ownership, and the approved design specification.
- Produces: `docs/guides/developer-architecture.md` with stable headings, repository-relative source links, Mermaid diagrams, and compact HTTP/WebSocket/Unix-socket catalogs.

- [ ] **Step 1: Reconfirm the implementation evidence before drafting**

Run:

```bash
rg -n "include_router|websocket|create_agent_ingest_app|build_agent_container|build_manager_container" backend/ic_env_guard/main.py backend/ic_env_guard/bootstrap backend/ic_env_guard/api
rg -n "RuntimeProvider|AgentEntry|ManagerEntry|CapabilityRoute|agentId === 'local'" frontend/src
```

Expected: output identifies the shared, Agent, Local Ingest, and Manager route/composition boundaries plus frontend runtime and Agent-target selection.

- [ ] **Step 2: Create the guide with the exact top-level structure**

Use `apply_patch` to create a complete English document with these exact headings:

```markdown
# Developer Architecture
## Reading This Document
## System Vocabulary and Invariants
## Repository Map
## Runtime Topologies
## Backend Composition and State Ownership
## Frontend Architecture
## End-to-End Flows
## Interface Catalog
## Security and Failure Boundaries
## Where to Make Changes
## Development Verification
```

The body must include:

- repository, topology, backend composition, state ownership, and frontend data-flow diagrams;
- Local Ingest, Fleet probe isolation, Terminal proxy, enrollment, and credential rotation sequences;
- compact OpenAPI-like route groups for Shared Public, Agent Public, Agent Local Ingest, and Manager Public;
- AsyncAPI-like standalone/Fleet terminal channels and RPC-like Agent/Manager Unix socket families;
- repository-relative links to the concrete backend/frontend source files and existing guides/references;
- an explicit note that the catalog is architectural and `/openapi.json` is authoritative for HTTP details.

- [ ] **Step 3: Run structural and accuracy checks**

Run:

```bash
rg -n '^## ' docs/guides/developer-architecture.md
rg -n '^```(mermaid|yaml)$|^(flowchart|sequenceDiagram)' docs/guides/developer-architecture.md
rg -n 'mode: agent|mode: control-plane|/openapi.json|Local Ingest|in memory|Prometheus' docs/guides/developer-architecture.md
git diff --check -- docs/guides/developer-architecture.md
```

Expected: all eleven headings are present; each Mermaid fence begins with `flowchart` or `sequenceDiagram`; all architectural invariants are stated; `git diff --check` prints nothing.

- [ ] **Step 4: Commit the guide**

```bash
git add docs/guides/developer-architecture.md
git commit -m "docs: add developer architecture guide"
```

### Task 2: Link, validate, and review the documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Verify: `docs/guides/developer-architecture.md`

**Interfaces:**
- Consumes: the guide produced by Task 1 at `docs/guides/developer-architecture.md`.
- Produces: root onboarding and documentation-index links plus repository-local validation evidence for the complete deliverable.

- [ ] **Step 1: Confirm the new guide is not already linked**

Run:

```bash
rg -n 'developer-architecture.md|Developer Architecture' README.md docs/README.md
```

Expected before editing: exit status `1` with no matches.

- [ ] **Step 2: Add the two supported discovery paths**

Use `apply_patch` to make these exact additions:

```markdown
| Understand the architecture and interfaces | [Read the developer architecture guide](docs/guides/developer-architecture.md) |
```

Add that row to the root `README.md` `Choose Your Path` table. Add this item to its `Documentation` topic list:

```markdown
- [Developer architecture](docs/guides/developer-architecture.md)
```

Add this item to `docs/README.md` immediately after `Getting Started`:

```markdown
- [Developer Architecture](guides/developer-architecture.md) — repository
  structure, runtime boundaries, data flows, interface families, and extension
  points.
```

- [ ] **Step 3: Validate repository-local links and document fences**

Run from the repository root:

```bash
python -c 'import pathlib,re,sys; files=[pathlib.Path("README.md"),pathlib.Path("docs/README.md"),pathlib.Path("docs/guides/developer-architecture.md")]; bad=[]; pattern=re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)"); [(bad.append(f"{f}:{target}") if not ((f.parent/target).resolve()).exists() else None) for f in files for target in pattern.findall(f.read_text()) if "://" not in target and not target.startswith("/")]; print("\n".join(bad)); sys.exit(bool(bad))'
python -c 'import pathlib,re,sys; text=pathlib.Path("docs/guides/developer-architecture.md").read_text(); blocks=re.findall(r"```mermaid\n(.*?)```",text,re.S); ok=bool(blocks) and text.count("```")%2==0 and all(re.match(r"(?:flowchart|sequenceDiagram)\b",b.lstrip()) for b in blocks); sys.exit(0 if ok else 1)'
git diff --check -- README.md docs/README.md docs/guides/developer-architecture.md
```

Expected: both Python commands exit `0` without output and `git diff --check` prints nothing.

- [ ] **Step 4: Reconcile route families with current code and references**

Run:

```bash
rg -n '^  /(api|ws|metrics|healthz|readyz)|^(sharedPublic|agentPublic|localIngest|managerPublic|channels|unixSockets):' docs/guides/developer-architecture.md
rg -n '/api/v2/observations|/api/v2/logs|/api/terminals|/api/services|/api/v2/agents|/api/v2/discovery|/api/v2/fleet|/api/control-plane/audit' docs/reference/api-and-endpoints.md backend/ic_env_guard/api frontend/src/features
rg -n 'T[B]D|T[O]DO|mode: manager|systemd adapter.*active|Terminal.*persisted|service.*persisted' docs/guides/developer-architecture.md README.md docs/README.md
```

Expected: every catalog family is supported by the current API reference or code; the final placeholder/false-claim scan exits `1` with no matches.

- [ ] **Step 5: Commit README links**

```bash
git add README.md docs/README.md
git commit -m "docs: link developer architecture guide"
```

- [ ] **Step 6: Record final documentation verification**

Run:

```bash
git status --short
git log -3 --oneline
```

Expected: only the user's pre-existing unrelated changes remain, and the two implementation commits follow the design/plan history commits.

# Documentation Reorganization Design

**Date:** 2026-07-13  
**Status:** Approved for implementation planning  
**Language:** English

## 1. Purpose

Reorganize the project documentation so that Linux operators and project
developers each have a clear entry point. The root README must explain how to
choose, configure, start, and validate the supported runtime modes without
becoming a complete reference manual. Detailed current-user guidance belongs
under `docs/guides/` and `docs/reference/`; historical specifications and
implementation plans belong under `docs/development/`.

This work is documentation-only. It must describe the current implementation
accurately and must not change runtime behavior, configuration semantics, APIs,
packaging, or security policy.

## 2. Audience and Principles

The documentation serves two equal audiences:

1. Linux operators deploying a standalone Agent or a Manager-controlled Fleet.
2. Developers running, testing, and modifying the project locally.

The documentation follows these principles:

- Use task-oriented guides for setup and operations.
- Use reference pages for stable fields, limits, defaults, and endpoints.
- Keep the README short enough to scan while still providing working minimal
  examples.
- Describe the current SQLite/Web-managed Agent Registry and enrollment flows;
  do not present the obsolete static Manager `agents:` configuration as the
  recommended Fleet workflow.
- State security boundaries at the point where a user makes a security-relevant
  choice.
- Avoid copying the same normative instructions into multiple files.
- Preserve historical design material, but label it clearly as development
  history rather than current operator guidance.

## 3. Target Information Architecture

```text
README.md
docs/
  README.md
  guides/
    getting-started.md
    configuration.md
    agent-deployment.md
    manager-fleet.md
    local-data-ingest.md
    monitoring-and-logs.md
    security.md
    backup-upgrade-recovery.md
    development.md
  reference/
    configuration.md
    api-and-endpoints.md
  development/
    README.md
    superpowers/
      plans/
      specs/
    specs/
      001-linux-host-agent/
      002-multi-agent-control-plane/
```

`docs/superpowers/` moves to `docs/development/superpowers/`. The root `specs/`
directory moves to `docs/development/specs/`. Git-aware renames must be used so
file history remains traceable.

The existing current-user documents under `docs/`, `docs/operations/`, and
`packaging/runtime/README.md` are source material for the new guides and
references. Their accurate content is consolidated into the target structure.
Obsolete duplicates are removed after links are repaired. Packaging-specific
notes may remain beside packaging artifacts when they are meaningful only in
that context, but they must link to the canonical user guide rather than repeat
it.

## 4. Root README Design

The root README is the stable landing page and should remain approximately
150–220 lines. It contains:

1. A concise project description and supported deployment modes.
2. A small architecture summary covering Standalone Agent, Manager Fleet, and
   the loopback-only Local Ingest listener.
3. A “Choose your path” section linking to:
   - production Agent deployment;
   - production Manager/Fleet deployment;
   - local development.
4. A five-minute local demonstration using `./start.sh all`.
5. A production installation summary using an existing non-root Linux account.
6. Minimal valid Agent and Manager YAML examples.
7. Common start, validation, health, test, build, and lint commands.
8. Essential security warnings.
9. A documentation index.
10. A concise repository layout.

README examples must be immediately usable, but advanced fields and operational
edge cases must link to the appropriate guide or reference page.

## 5. Guide Responsibilities

### `docs/guides/getting-started.md`

Explain the three supported starting paths: Standalone Agent, Manager Fleet,
and local development. Define prerequisites, expected listeners, generated
files, login flow, and basic success checks.

### `docs/guides/configuration.md`

Explain how Agent and Manager configuration sections fit together. Include
complete practical examples for both modes, safe file locations and modes, and
links to the field reference. Explain TLS versus explicitly bounded trusted-LAN
HTTP without normalizing unsafe defaults.

### `docs/guides/agent-deployment.md`

Document installation with an existing Linux user, the systemd template unit,
Public and Local Ingest listeners, terminal authority, service configuration,
validation, and routine lifecycle commands. The project does not create users
or change sudoers.

### `docs/guides/manager-fleet.md`

Document Manager installation, the SQLite-backed Agent Registry, adding and
editing Agents, bounded discovery, SSH and legacy-token enrollment, credential
rotation, probing, partial Fleet failure, Terminal proxying, removal, and audit.
Static YAML Agent import is described only as migration compatibility where it
still exists.

### `docs/guides/local-data-ingest.md`

Document tokenless loopback-only producer writes for Observations and Log
Sources. Cover `details`, ordering, `observed_at`, TTL behavior, bounded labels,
Log Source metadata, allowed roots, and on-demand log tailing. State that Local
Ingest must never be exposed, forwarded, or reverse proxied.

### `docs/guides/monitoring-and-logs.md`

Document Agent and Fleet status, Prometheus scraping, remote scrape allowlists,
metric label limits, service health, bounded log tails, and troubleshooting.
Clarify that the Agent stores latest values rather than time-series history.

### `docs/guides/security.md`

Consolidate authentication, token file permissions, TLS profiles, trusted-LAN
warnings, SSH host-key verification, enrollment sockets, restricted service
keys, Terminal authority and privacy, SSRF boundaries, audit, and secrets that
must not appear in browser-visible data.

### `docs/guides/backup-upgrade-recovery.md`

Define the separate atomic backup units for Agent and Manager, stable Agent
identity handling, Manager credential-directory requirements, upgrade and
rollback workflows, residual credentials, interrupted operations, and
post-restore validation.

### `docs/guides/development.md`

Document Conda and npm prerequisites, editable backend installation,
`start.sh` commands and environment overrides, local ports/files, frontend
proxy behavior, tests, builds, linting, supported Linux validation, and the
development documentation archive.

## 6. Reference Responsibilities

### `docs/reference/configuration.md`

Provide a mode-aware reference for supported configuration sections and fields.
For each field, state its purpose, default where defined, important constraints,
and whether it applies to Agent, Manager, or both. The reference must be checked
against `backend/ic_env_guard/config/models.py` and configuration contract tests.
It need not reproduce internal Pydantic implementation details.

### `docs/reference/api-and-endpoints.md`

Group endpoints by listener and runtime mode:

- Agent Public;
- Agent Local Ingest;
- Manager Public;
- Prometheus metrics;
- Terminal WebSockets and enrollment Unix sockets.

For each group, explain authentication, intended caller, exposure boundary, and
where to find request examples. This is an operator-facing endpoint map, not a
full generated OpenAPI copy.

## 7. Development Archive

`docs/development/README.md` explains that archived files capture design and
implementation history. Current behavior is defined by the code, tests, root
README, guides, and references. Archived documents may contain historical
paths, task states, or decisions that have since changed.

Relative links inside moved development documents must be repaired when they
refer to other repository files. Literal paths embedded as historical task
instructions may remain only when changing them would falsify the historical
record; the archive notice must explain this distinction. Links from current
user documentation must never target obsolete operational instructions when a
current guide exists.

## 8. Migration Rules

- Use Git-aware file moves.
- Preserve all substantive historical development files.
- Consolidate current-user documentation before removing old duplicates.
- Repair links in README, packaging notes, root-level Markdown, guides,
  references, plans, and specs.
- Do not add compatibility stub pages solely to preserve old repository paths;
  internal links are updated to canonical destinations.
- Preserve unrelated working-tree changes, including user-owned `CLAUDE.md`,
  `.kilo/`, and `AGENTS.md` changes.
- Do not change source code, runtime configuration, packaging behavior, or APIs.

## 9. Accuracy and Validation

Implementation is complete only when:

1. Every relative Markdown file link resolves after the moves.
2. No current documentation references `docs/superpowers/`, a root `specs/`
   path, or removed operator-document paths.
3. README commands match `start.sh` and installed CLI syntax.
4. Agent and Manager YAML examples validate against the current configuration
   model where the examples are intended to be complete.
5. Listener, authentication, token, TLS, enrollment, terminal, ingest, backup,
   and registry descriptions match current tests and implementation.
6. Documentation-related backend contract, packaging, and configuration tests
   pass.
7. `git diff --check` passes.
8. A local Markdown link scan reports no broken relative file links.

External HTTP links are out of scope for offline validation. Heading-anchor
validation is included only for repository Markdown links that specify an
anchor and can be evaluated deterministically by the local checker.

## 10. Out of Scope

- Runtime or API changes.
- New deployment modes.
- Generated OpenAPI or schema documentation tooling.
- A hosted documentation website.
- Translation or bilingual documentation.
- Rewriting historical decisions to match current behavior.
- Exhaustive tutorials for third-party reverse proxies, VPNs, Prometheus, or
  Linux account management.

## 11. Acceptance Criteria

- A new operator can identify the correct deployment mode and reach a valid
  configuration from README without reading development plans.
- A developer can run the full local stack and find test/build commands from
  README or one linked development guide.
- Agent, Manager, Local Ingest, and Fleet security boundaries are explicit.
- Current operational guidance has one canonical location per topic.
- Historical specifications remain accessible under `docs/development/` and
  are clearly separated from current usage documentation.
- All documentation is English and all repository-relative links resolve.

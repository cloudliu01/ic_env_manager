# Developer Architecture Documentation Design

**Date:** 2026-07-14
**Status:** Approved design

## Goal

Add one current, developer-oriented architecture guide that explains the
repository structure, runtime topology, component boundaries, storage
ownership, major end-to-end flows, and interface families primarily through
GitHub-renderable Mermaid diagrams. Make the guide discoverable from the root
README and the documentation index.

## Deliverables

- Create `docs/guides/developer-architecture.md` in English.
- Add a developer-architecture entry to the root `README.md` in both the path
  chooser and documentation section.
- Add the guide to `docs/README.md`.
- Do not add generated assets, a documentation framework, or a checked-in full
  OpenAPI document.

## Source-of-Truth Policy

The guide is a navigational and architectural reference, not a second API
schema. Runtime FastAPI `/openapi.json` output is authoritative for mounted
HTTP operations and schemas. The guide contains a compact OpenAPI-like route
catalog for stable interface families. WebSocket channels and Unix socket
commands use compact AsyncAPI/RPC-style YAML because OpenAPI does not describe
those transports completely.

Current code, tests, `docs/reference/api-and-endpoints.md`, and
`docs/reference/configuration.md` remain authoritative when behavior changes.
The new guide must link to those references instead of duplicating detailed
request and response schemas.

## Document Structure

The guide will contain these sections:

1. Purpose, audience, and source-of-truth note.
2. System vocabulary and invariants.
3. Repository map.
4. Runtime topologies for standalone Agent, Manager Fleet, and local
   development.
5. Backend composition, lifecycle, persistence, and credential boundaries.
6. Frontend runtime selection, routes, capability gates, and feature data flow.
7. End-to-end sequences for Local Ingest, Fleet probe/proxy, Terminal
   WebSocket proxy, enrollment, and credential rotation.
8. HTTP, WebSocket, and Unix socket interface catalogs.
9. Security and failure boundaries.
10. Extension map and test commands for developers.

## Diagram Set

Use only Mermaid syntax supported by GitHub Markdown. Prefer small diagrams
with a single purpose over one dense system diagram. The guide will include:

- a repository/module ownership flowchart;
- standalone and Fleet topology flowcharts;
- an Agent/Manager backend composition flowchart;
- a storage and credential ownership flowchart;
- a frontend runtime and feature data-flow diagram;
- a Local Ingest sequence;
- a Manager Terminal HTTP/WebSocket proxy sequence;
- an enrollment and credential rotation sequence;
- a bounded Fleet probing sequence that shows one Agent failure does not block
  other results.

## Required Architectural Accuracy

- Configuration accepts `mode: agent` or `mode: control-plane`; runtime metadata
  presents the latter as `manager` to the frontend.
- Local Ingest is an Agent-only second listener sharing the Agent container,
  services, and SQLite database. It is not a third runtime mode.
- Agent Public and Manager Public require bearer/session authentication for
  protected routes. Local Ingest is tokenless and restricted to an actual
  loopback peer.
- Manager Agent-scoped HTTP and WebSocket routes are bounded proxies using
  server-held Agent credentials; the browser never receives those credentials.
- Agent SQLite persists current observations, log-source metadata, audit,
  identity/lifecycle metadata, and Manager credential hashes/status.
- Manager SQLite persists registry, latest status, enrollment/removal/discovery
  jobs, and control-plane audit. Manager plaintext Agent credentials live in
  owner-only files referenced from SQLite.
- Terminal sessions/replay and configured-service runtime state are currently
  primarily in memory; the guide must not claim they are runtime-persisted by
  the existing SQLite repository definitions.
- The active configured-service execution path is the configured process
  runner. The guide must not present the uncomposed systemd adapter as an active
  backend.
- Prometheus owns metric history. Agent observations hold only the latest value
  plus freshness, TTL, labels, and details.

## Interface Catalog Design

The HTTP catalog will group routes by listener and responsibility:

- Shared Public: health, readiness, authentication, runtime, capabilities, and
  static UI.
- Agent Public: observations/log reads, summary/metrics, monitoring, services,
  terminals, Manager credential administration, and Agent audit.
- Agent Local Ingest: Observation and Log Source writes only.
- Manager Public: registry, enrollment, discovery, Fleet overview,
  control-plane audit, and Agent-scoped resource proxies.

Every group will state its authentication boundary and link to runtime
`/openapi.json` plus the existing endpoint reference. The WebSocket catalog
will describe the standalone and Manager terminal paths, one-use tickets,
cursor replay, and bounded proxy slots. The Unix socket catalog will describe
the fixed Agent enrollment helper and optional Manager CLI orchestration at a
family level without exposing secret payloads.

## README Placement

The root `README.md` will add “Understand the architecture and interfaces” to
the `Choose Your Path` table and add the new guide to the documentation topic
list. `docs/README.md` will add the guide near Development and API Reference so
both onboarding paths reach it.

## Validation

- Confirm all new relative Markdown links resolve inside the repository.
- Check that every Mermaid fence is closed and uses supported diagram types.
- Compare documented HTTP route families with mounted routers and the existing
  API reference.
- Scan for placeholder markers, contradictory mode names, false persistence
  claims, and accidental secret examples.
- Review the final diff independently for accuracy, readability, and scope.

## Out of Scope

- Changing application behavior or API routes.
- Generating or checking in a complete `openapi.yaml`.
- Adding Swagger UI, Redoc, AsyncAPI tooling, Mermaid CLI, or documentation
  build dependencies.
- Reorganizing existing guides or historical development documents.

# Getting Started

IC Design Environment Guard has two runtime modes and one development workflow.
Select the smallest mode that matches the job.

| Need | Choose | Continue with |
| --- | --- | --- |
| Browser shell, services, observations, logs, and metrics for one Linux host | Standalone Agent | [Agent Deployment](agent-deployment.md) |
| One browser entry point for several Agents | Manager Fleet | [Manager Fleet](manager-fleet.md) |
| Change or test the project locally | Development | [Development](development.md) |

## Prerequisites

### Standalone Agent

- A Linux host with systemd for the supported production lifecycle.
- An existing non-root account whose shell permissions should be exposed.
- Python 3.11 or newer and the packaged `ic-env-guard` executables.
- A bearer-token file readable only by the Agent account.

The project does not create users or modify sudoers. The Web Terminal can run
everything the selected account can run, including its existing sudo commands.

### Manager Fleet

- One Manager host plus one or more reachable Agent Public listeners.
- An existing non-root Manager account and separate Manager bearer token.
- Verified TLS for non-loopback Agent links, or a deliberately configured
  trusted-LAN HTTP transport profile.
- SSH access to existing Agent accounts when using one-time enrollment.

### Development

- Conda environment `venv312` with Python 3.11 or newer.
- Node.js and npm.
- A Linux host or VM for systemd and packaging validation. macOS is supported
  for local application, backend, and frontend development.

## Runtime Ownership

| Runtime | Listener/state it owns | It does not own |
| --- | --- | --- |
| Agent Public | Web UI, authenticated Agent APIs, metrics, Terminal WebSockets | Tokenless local producer writes |
| Agent Local Ingest | Loopback-only Observation and Log Source writes | Browser access or remote traffic |
| Manager Public | Fleet UI, SQLite Agent Registry, cached status, audit, scoped proxy routes | Agent PTYs, Agent state DBs, or local producer data |

An Agent's SQLite database stores latest observations, log-source metadata,
service/terminal lifecycle state, and audit data. The Manager stores registry,
Fleet, enrollment, and audit state in its own SQLite database; Agent credentials
are owner-only files outside that database.

## Configure the Selected Mode

Use the complete examples in the [Configuration Guide](configuration.md).
Validate every edit before restarting:

```bash
ic-env-guard-config validate /etc/ic-env-guard/edaops.yaml
```

For local development, generate validated configurations instead:

```bash
./start.sh config agent
./start.sh config control-plane
```

## First Success Checks

For an Agent or Manager Public listener on port `8765`:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
```

`healthz` proves the process is responding. `readyz` proves the selected runtime
has completed its startup checks. Next, open the UI, sign in with that runtime's
bearer token, and verify the correct mode-specific landing page appears.

For an Agent:

1. Open Services and confirm configured services are listed.
2. Open Observations and confirm current values and expiry state appear.
3. Create a Terminal, run `id`, and close it.
4. Scrape `/metrics` from an allowed address.
5. If local producers are used, send one loopback Observation and read it back
   through authenticated Public API.

For a Manager:

1. Open Agents and add or enroll one Agent.
2. Probe it and confirm its instance identity and capability state.
3. Open an Agent-scoped Services or Observations page.
4. Create an Agent-scoped Terminal.
5. Confirm an unreachable second Agent produces a partial error without hiding
   the healthy Agent.

For the full local UI workflow:

```bash
./start.sh all
```

Open `http://127.0.0.1:5173`. The default Manager is on `8765`, Agent Public on
`8766`, and Agent Local Ingest on `8767`.

## Next Steps

- Define listeners, state, services, roots, and Fleet scope in
  [Configuration](configuration.md).
- Review [Security](security.md) before any non-loopback exposure.
- Configure local producers through [Local Data Ingest](local-data-ingest.md).
- Plan ongoing operation with [Monitoring and Logs](monitoring-and-logs.md) and
  [Backup, Upgrade, and Recovery](backup-upgrade-recovery.md).

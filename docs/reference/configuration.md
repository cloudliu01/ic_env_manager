# Configuration Reference

This reference follows the current Pydantic configuration models. “Agent” and
“Manager” mean `mode: agent` and `mode: control-plane`. Paths described as
absolute must begin at the filesystem root.

## Top-Level Fields

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `mode` | Both | `agent` | `agent` or `control-plane` | Select runtime capabilities and UI. |
| `server` | Both | See below | Public and Ingest ports must differ on Agent. | Public HTTP/WebSocket listener. |
| `auth` | Both | Required | Bearer token file must pass permission checks. | Public API/UI authentication. |
| `state_database` | Agent | `null` | Path; use an absolute owner-controlled path in production. | Agent SQLite state and audit database. |
| `development` | Manager dev | Secure default | Never enables insecure non-loopback links. | Development-only loopback controls. |
| `control_plane` | Manager | See below | Absolute storage paths and bounded targets. | Fleet Registry, probes, discovery, proxy limits. |
| `agents` | Manager recovery | `[]` | Unique IDs; enabled entries require safe token files. | Legacy recovery import only; use SQLite Registry normally. |
| `metrics` | Agent | See below | Collection/series bounds. | Prometheus publication and scrape policy. |
| `terminal` | Agent | See below | Bounded time and replay retention. | PTY session lifecycle. |
| `enrollment` | Both | See below | Absolute sockets/SSH paths and bounded TTLs. | Agent helper and Manager CLI orchestration. |
| `ingest` | Agent | See below | Loopback only. | Tokenless local producer listener. |
| `observations` | Agent | See below | Bounded cleanup intervals/retention. | Expired Observation cleanup. |
| `logs` | Agent | See below | Absolute roots and bounded tail. | On-demand log read policy. |
| `services` | Agent | `[]` | Each item maps exactly one execution source. | Configured service control/status. |

## Public Server

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `server.bind` | Both | `127.0.0.1` | Non-loopback requires `remote_bind_enabled`. | Public bind address. |
| `server.port` | Both | `8765` | `1..65535`; differs from Agent Ingest port. | Public TCP port. |
| `server.remote_bind_enabled` | Both | `false` | Must be true for non-loopback bind. | Explicit acknowledgement of remote exposure. |
| `server.trusted_lan_http.enabled` | Both | `false` | Requires remote bind and private client CIDRs. | Permit HTTP within an explicit trusted LAN. |
| `server.trusted_lan_http.client_cidrs` | Both | `[]` | Non-empty private CIDRs when enabled. | Restrict trusted-LAN Public clients. |

`trusted_lan_http` describes browser/API clients reaching this runtime. It is
not a Manager-to-Agent transport profile and does not make Local Ingest remote.

## Authentication and Development

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `auth.mode` | Both | `bearer_token` | Only `bearer_token`. | Select Public authentication. |
| `auth.token_file` | Both | Required | Safe regular token file; normally `0600`. | Store Public bearer token outside YAML. |
| `development.allow_insecure_http` | Manager dev | `false` | Only loopback Agent plus loopback Manager. | Opt into local HTTP compatibility. |
| `development.local_agent_bootstrap` | Manager dev | `false` | Requires control-plane mode, local-only Manager, insecure-development opt-in, and Manager socket. | Enable owner-only local v2 enrollment for the generated development Fleet. |

## Agent Local Ingest

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `ingest.bind` | Agent | `127.0.0.1` | Exactly `127.0.0.1` or `::1`. | Keep tokenless writes on the host. |
| `ingest.port` | Agent | `8766` | `1..65535`; not Public port. | Local producer TCP port. |
| `ingest.max_request_bytes` | Agent | `32768` | `1024..1048576`. | Bound each producer payload. |
| `ingest.max_concurrent_requests` | Agent | `16` | `1..128`. | Bound concurrent local writes. |

## Metrics, Terminal, Observations, and Logs

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `metrics.enabled` | Agent | `true` | Boolean. | Enable Prometheus output/refresh. |
| `metrics.collect_interval_seconds` | Agent | `10` | At least `1`. | Refresh current metrics. |
| `metrics.max_observation_series` | Agent | `10000` | At least `1`. | Bound Observation metric series. |
| `metrics.remote_network_allowlist` | Agent | `[]` | Valid CIDR strings. | Permit remote `/metrics`; local scrapes are default. |
| `terminal.idle_timeout_minutes` | Agent | `60` | `30..120`. | Close idle PTYs. |
| `terminal.replay_buffer_bytes` | Agent | `2097152` | `1048576..10485760`. | Bound reconnect replay per session. |
| `terminal.exited_retention_minutes` | Agent | `30` | `0..120`; `0` purges metadata/replay on the next session list/get. | Retain exited session metadata/replay. |
| `observations.expired_retention_seconds` | Agent | `86400` | `0..604800`. | Retain expired rows before deletion. |
| `observations.cleanup_interval_seconds` | Agent | `60` | `1..3600`. | Expired-row cleanup cadence. |
| `logs.allowed_roots` | Agent | `[]` | Absolute, resolved, deduplicated paths. | Restrict registered/tail-readable files. |
| `logs.max_tail_lines` | Agent | `1000` | `1..1000`. | Hard requested line limit. |
| `logs.default_tail_lines` | Agent | `100` | `1..1000` and no more than max. | Default bounded tail size. |
| `logs.max_tail_bytes` | Agent | `983040` | `1024..983040`. | Hard response-byte limit. |

## Enrollment

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `enrollment.socket_path` | Agent | `/run/ic-env-guard/agent-enrollment.sock` | Absolute. | Agent fixed-helper Unix socket. |
| `enrollment.socket_mode` | Agent | `0600` | `0600` or `0660`. | Agent socket access mode. |
| `enrollment.manager_socket_path` | Manager | `null` | Absolute when enabled. | Manager `ic-env-guardctl` Unix socket. |
| `enrollment.manager_socket_mode` | Manager | `0600` | `0600` or `0660`. | Manager socket access mode. |
| `enrollment.manager_socket_gid` | Manager | `null` | Non-negative; primary GID only. | Optional peer-GID authorization. |
| `enrollment.pending_ttl_seconds` | Both | `600` | `60..900`. | One-use pending credential lifetime. |
| `enrollment.max_pending` | Both | `16` | `1..128`. | Bound pending enrollments. |
| `enrollment.ssh_binary` | Manager | `/usr/bin/ssh` | Absolute. | SSH executable for enrollment. |
| `enrollment.ssh_connect_timeout_seconds` | Manager | `10` | `1..60`. | SSH connection timeout. |
| `enrollment.ssh_total_timeout_seconds` | Manager | `15` | `2..120`; at least connect timeout. | Complete enrollment SSH timeout. |
| `enrollment.service_key_identity_file` | Manager | `null` | Absolute; paired with known_hosts. | Restricted service private key. |
| `enrollment.service_key_known_hosts_file` | Manager | `null` | Absolute; paired with identity. | Dedicated verified host keys. |

The Agent and Manager socket fields share one schema but only their respective
mode starts each socket. `0660` should be used only with a deliberate local
group/primary-GID design.

## Manager Control Plane

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `control_plane.poll_interval_seconds` | Manager | `15` | At least `1`. | Scheduled Fleet probe cadence. |
| `control_plane.status_stale_after_seconds` | Manager | `30` | At least `1`. | Mark cached Agent status stale. |
| `control_plane.max_parallel_probes` | Manager | `8` | At least `1`. | Bound simultaneous Agent probes. |
| `control_plane.audit_database` | Manager | `/var/lib/ic-env-guard/control-plane.db` | Absolute. | Manager Registry/audit SQLite DB. |
| `control_plane.credential_directory` | Manager | Adjacent `agent-credentials` | Absolute when set; runtime requires owner-only modes. | Plaintext Agent credential files. |
| `control_plane.max_active_terminal_proxies` | Manager | `64` | At least `1`. | Fleet-wide active Terminal proxy cap. |
| `control_plane.max_outstanding_tickets` | Manager | `128` | At least `1`. | Fleet-wide pending WS ticket cap. |
| `control_plane.allowed_agent_cidrs` | Manager | `[]` | Network list; covers profiles/scopes/targets. | SSRF and Fleet target boundary. |
| `control_plane.transport_profiles` | Manager | `system-tls` | Unique profile IDs; reserved built-in cannot be overridden. | Approved Manager-to-Agent transport. |
| `control_plane.discovery` | Manager | See below | Named private scopes and bounded work. | Port discovery policy. |

## Transport Profiles

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `transport_profiles[].id` | Manager | Required | Lowercase ID, max 64; `system-tls` reserved. | Registry/discovery transport selector. |
| `transport_profiles[].type` | Manager | `verified_tls` | `verified_tls` or `trusted_lan_http`. | Verification policy discriminator. |
| `verified_tls.ca_bundle` | Manager | System trust | Absolute regular file; no symlink; root/Manager owned; not group/world writable; valid certs. | Custom Agent CA roots. |
| `trusted_lan_http.allowed_cidrs` | Manager | Required | Non-empty private CIDRs; subset of global allowed CIDRs. | Explicit plain-HTTP Agent boundary. |

Every config automatically includes `system-tls`. A discovery endpoint using a
trusted-LAN profile must also lie inside that profile's CIDRs.

## Discovery

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `discovery.max_concurrency` | Manager | `32` | `1..32`. | Concurrent connection probes. |
| `discovery.connect_timeout_ms` | Manager | `500` | `50..5000`. | Per-target TCP connection limit. |
| `discovery.fingerprint_timeout_seconds` | Manager | `2` | Greater than `0`, at most `10`. | Agent fingerprint request limit. |
| `discovery.job_timeout_seconds` | Manager | `120` | `1..600`. | Whole job deadline. |
| `discovery.retention_seconds` | Manager | `86400` | `3600..604800`. | Completed job/result retention. |
| `discovery.max_targets` | Manager | `2048` | `1..2048`. | Whole scope endpoint-target cap. |
| `discovery.scopes` | Manager | `[]` | Unique named scopes. | Only scan choices offered to browser. |
| `scopes[].id` | Manager | Required | Lowercase ID, max 64. | Stable scope key. |
| `scopes[].name` | Manager | Required | `1..128` characters. | Operator label. |
| `scopes[].cidr` | Manager | Required | Private, non-loopback/non-reserved, at most 256 addresses, subset of global allowlist. | Fixed scan network. |
| `scopes[].endpoints` | Manager | Required | `1..8`, unique `(port, profile)` pairs. | Ports/transports tested per address. |
| `endpoints[].port` | Manager | Required | `1..65535`. | Agent Public candidate port. |
| `endpoints[].transport_profile_id` | Manager | Required | Existing profile ID. | Transport used for fingerprint. |

## Compatibility Agent Entries

Static entries are accepted only for legacy recovery import into the Manager's
Web-managed SQLite Registry. The current local development stack uses owner-only
v2 enrollment instead. Do not maintain static entries as the normal Fleet
source of truth.

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `agents[].id` | Manager compatibility | Required | Lowercase ID, max 64; unique. | Legacy Agent key. |
| `agents[].name` | Manager compatibility | Required | Non-empty. | Display name. |
| `agents[].base_url` | Manager compatibility | Required | Scheme/host/optional port only; no credentials/path/query/fragment; target policy applies. | Agent Public origin. |
| `agents[].token_file` | Manager compatibility | `null` | Required for enabled legacy entry; safe permissions. | Legacy Agent bearer token. |
| `agents[].tls.verify` | Manager compatibility | `true` | Must remain true for non-loopback. | Certificate verification switch. |
| `agents[].tls.ca_bundle` | Manager compatibility | `null` | CA path when needed. | Legacy custom trust root. |
| `agents[].connect_timeout_seconds` | Manager compatibility | `3` | At least `1`. | Connect timeout. |
| `agents[].request_timeout_seconds` | Manager compatibility | `10` | At least `1`. | Request timeout. |
| `agents[].enabled` | Manager compatibility | `true` | Boolean. | Import/compatibility activation. |

## Services

| Field | Mode | Default | Constraints | Purpose |
| --- | --- | --- | --- | --- |
| `services[].id` | Agent | Required | Letters, digits, `_`, `.`, `-`; unique operational key. | Service identifier. |
| `services[].name` | Agent | Required | Non-empty. | Display name. |
| `services[].description` | Agent | `null` | String. | Operator context. |
| `services[].command` | Agent | `null` | Exactly one of command/systemd unit. | Direct child-process mapping. |
| `services[].systemd_unit` | Agent | `null` | Full safe name ending `.service`; exactly one source. | Existing systemd mapping. |
| `services[].cwd` | Agent | `null` | String path used by command. | Command working directory. |
| `services[].env` | Agent | `{}` | String key/value map. | Command environment additions. |
| `services[].allowed_operations` | Agent | Required | Non-empty subset of start/stop/restart/status/healthcheck. | UI/API operation allowlist. |
| `services[].autostart` | Agent | `false` | Boolean. | Start command mapping with Agent. |
| `services[].restart` | Agent | `never` | `never`, `on-failure`, or `always`. | Command restart policy. |
| `services[].start_timeout_seconds` | Agent | `30` | At least `1`. | Start deadline. |
| `services[].stop_timeout_seconds` | Agent | `30` | At least `1`. | Stop deadline. |
| `services[].healthcheck.type` | Agent | `none` | `none`, `http`, `tcp`, or `process`. | Check implementation. |
| `services[].healthcheck.target` | Agent | `null` | Type-specific string. | HTTP URL, TCP address, or process target. |
| `services[].healthcheck.interval_seconds` | Agent | `10` | At least `1`. | Check cadence. |
| `services[].healthcheck.timeout_seconds` | Agent | `2` | At least `1`, no more than interval. | Check deadline. |
| `services[].healthcheck.failure_threshold` | Agent | `3` | At least `1`. | Failures before unhealthy. |
| `services[].logs.capture` | Agent | `true` | Boolean. | Capture command output for service view. |
| `services[].logs.path` | Agent | `null` | String path when configured. | External service log path metadata. |
| `services[].logs.max_tail_lines` | Agent | `200` | `0..1000`. | Service-view tail bound. |

import socket
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from ic_env_guard.auth.token import validate_token_file_permissions


class ServerConfig(BaseModel):
    bind: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    remote_bind_enabled: bool = False

    @property
    def is_local_only(self) -> bool:
        return self.bind in {"127.0.0.1", "localhost", "::1"}


class AuthConfig(BaseModel):
    mode: Literal["bearer_token"] = "bearer_token"
    token_file: Path


class DevelopmentConfig(BaseModel):
    allow_insecure_http: bool = False


class MetricsConfig(BaseModel):
    enabled: bool = True
    collect_interval_seconds: int = Field(default=10, ge=1)
    remote_network_allowlist: list[str] = Field(default_factory=list)

    @field_validator("remote_network_allowlist")
    @classmethod
    def validate_networks(cls, values: list[str]) -> list[str]:
        for value in values:
            ip_network(value, strict=False)
        return values


class TerminalConfig(BaseModel):
    idle_timeout_minutes: int = Field(default=60, ge=30, le=120)
    replay_buffer_bytes: int = Field(default=2 * 1024 * 1024, ge=1024 * 1024, le=10 * 1024 * 1024)
    exited_retention_minutes: int = Field(default=30, ge=1, le=120)


class HealthCheckConfig(BaseModel):
    type: Literal["none", "http", "tcp", "process"] = "none"
    target: str | None = None
    interval_seconds: int = Field(default=10, ge=1)
    timeout_seconds: int = Field(default=2, ge=1)
    failure_threshold: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def validate_timeout(self) -> "HealthCheckConfig":
        if self.timeout_seconds > self.interval_seconds:
            raise ValueError("healthcheck timeout_seconds must be <= interval_seconds")
        return self


class LogConfig(BaseModel):
    capture: bool = True
    path: str | None = None
    max_tail_lines: int = Field(default=200, ge=0, le=1000)


class ServiceConfig(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1)
    description: str | None = None
    command: str | None = None
    systemd_unit: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.@-]+\.service$")
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    allowed_operations: list[Literal["start", "stop", "restart", "status", "healthcheck"]]
    autostart: bool = False
    restart: Literal["never", "on-failure", "always"] = "never"
    start_timeout_seconds: int = Field(default=30, ge=1)
    stop_timeout_seconds: int = Field(default=30, ge=1)
    healthcheck: HealthCheckConfig | None = None
    logs: LogConfig = Field(default_factory=LogConfig)

    @model_validator(mode="after")
    def validate_execution_mapping(self) -> "ServiceConfig":
        if not self.allowed_operations:
            raise ValueError("service allowed_operations must not be empty")
        if bool(self.command) == bool(self.systemd_unit):
            raise ValueError("service must define exactly one of command or systemd_unit")
        return self


class AgentTlsConfig(BaseModel):
    verify: bool = True
    ca_bundle: Path | None = None


class AgentConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1)
    base_url: str
    token_file: Path | None = None
    tls: AgentTlsConfig = Field(default_factory=AgentTlsConfig)
    connect_timeout_seconds: int = Field(default=3, ge=1)
    request_timeout_seconds: int = Field(default=10, ge=1)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url_shape(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("agent base_url must include http or https scheme and host")
        if parsed.username or parsed.password:
            raise ValueError("agent base_url must not include credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("agent base_url must contain only scheme, host, and optional port")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_credentials(self) -> "AgentConfig":
        if not self.enabled:
            return self
        if self.token_file is None:
            raise ValueError("enabled agents require a token_file")
        try:
            validate_token_file_permissions(self.token_file)
        except (OSError, ValueError) as exc:
            raise ValueError(f"agent token_file permissions are invalid: {exc}") from exc
        return self


class ControlPlaneConfig(BaseModel):
    poll_interval_seconds: int = Field(default=10, ge=1)
    status_stale_after_seconds: int = Field(default=30, ge=1)
    max_parallel_probes: int = Field(default=8, ge=1)
    audit_database: Path = Path("/var/lib/ic-env-guard/control-plane.db")
    max_active_terminal_proxies: int = Field(default=64, ge=1)
    max_outstanding_tickets: int = Field(default=128, ge=1)


def _is_loopback_url(value: str) -> bool:
    host = urlsplit(value).hostname
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname_ips(host: str) -> set[str]:
    try:
        return {result[4][0] for result in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return set()


def _host_candidates(host: str) -> set[str]:
    candidates = {host}
    if host.lower() == "localhost":
        candidates.update({"127.0.0.1", "::1"})
    candidates.update(_hostname_ips(host))
    return candidates


def _local_interface_ips() -> set[str]:
    return _hostname_ips(socket.gethostname()) | {"127.0.0.1", "::1"}


def _is_forbidden_agent_address(host: str) -> bool:
    candidates = _host_candidates(host)
    for candidate in candidates:
        try:
            address = ip_address(candidate)
        except ValueError:
            continue
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            return True
    return False


def _is_self_target(agent_url: str, server: ServerConfig) -> bool:
    parsed = urlsplit(agent_url)
    if parsed.hostname is None:
        return False
    agent_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if agent_port != server.port:
        return False
    agent_hosts = _host_candidates(parsed.hostname)
    try:
        server_bind = ip_address(server.bind)
    except ValueError:
        server_hosts = _host_candidates(server.bind)
    else:
        server_hosts = _local_interface_ips() if server_bind.is_unspecified else {server.bind}
    return bool(server_hosts & agent_hosts)


class AppConfig(BaseModel):
    mode: Literal["agent", "control-plane"] = "agent"
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig
    state_database: Path | None = None
    development: DevelopmentConfig = Field(default_factory=DevelopmentConfig)
    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)
    agents: list[AgentConfig] = Field(default_factory=list)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    services: list[ServiceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_security(self) -> "AppConfig":
        if not self.server.is_local_only:
            if not self.server.remote_bind_enabled:
                raise ValueError("remote bind requires remote_bind_enabled=true")
            if not self.auth.token_file:
                raise ValueError("remote bind requires valid authentication settings")
        agent_ids = [agent.id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent IDs must be unique")
        for agent in self.agents:
            if not agent.enabled:
                continue
            host = urlsplit(agent.base_url).hostname
            if _is_self_target(agent.base_url, self.server):
                raise ValueError("agent base_url must not target the control plane itself")
            if host is not None and _is_forbidden_agent_address(host):
                raise ValueError("agent base_url targets a forbidden address range")
            is_loopback = _is_loopback_url(agent.base_url)
            if agent.base_url.startswith("http://"):
                if not is_loopback:
                    raise ValueError("non-loopback agents require HTTPS")
                if not self.development.allow_insecure_http:
                    raise ValueError("loopback insecure HTTP requires development opt-in")
                if not self.server.is_local_only:
                    raise ValueError(
                        "loopback insecure HTTP requires local-only control-plane bind"
                    )
            if not is_loopback and not agent.tls.verify:
                raise ValueError("non-loopback agents require verified TLS")
        return self

from ipaddress import ip_network
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig
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
        return self

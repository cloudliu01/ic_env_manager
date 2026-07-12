import os
import re
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import Engine

from ic_env_guard.config.models import AgentConfig
from ic_env_guard.enrollment.credential_store import CredentialStore
from ic_env_guard.fleet.transport import TransportProfile, VerifiedTlsProfile


class AgentConfigImportError(Exception):
    pass


_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class _PreparedAgent:
    config: AgentConfig
    endpoint: str
    token: bytes
    transport_profile_id: str


def import_yaml_agents_once(
    engine: Engine,
    credential_store: CredentialStore,
    agents: list[AgentConfig],
    *,
    manager_token: bytes,
    transport_profiles: tuple[TransportProfile, ...] = (),
) -> bool:
    """Import legacy YAML inventory once, without contacting any Agent."""
    if _registry_has_agents(engine):
        return False
    try:
        prepared = _prepare_all(agents, manager_token, transport_profiles)
    except AgentConfigImportError:
        raise
    except Exception as exc:
        raise AgentConfigImportError("YAML Agent validation failed") from exc

    new_references: list[str] = []
    try:
        with credential_store.lifecycle_lease():
            for item in prepared:
                new_references.append(credential_store.put(item.token))
            _commit_import(engine, prepared, new_references)
    except Exception as exc:
        cleanup_errors = _delete_new_credentials(credential_store, new_references)
        if cleanup_errors:
            raise AgentConfigImportError(
                "YAML Agent import failed and credential compensation was incomplete"
            ) from exc
        if isinstance(exc, AgentConfigImportError):
            raise
        raise AgentConfigImportError("YAML Agent import failed") from exc
    return True


def _registry_has_agents(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.exec_driver_sql("SELECT 1 FROM agents LIMIT 1").first() is not None
    except Exception as exc:
        raise AgentConfigImportError("Agent Registry is unavailable") from exc


def _prepare_all(
    agents: list[AgentConfig],
    manager_token: bytes,
    transport_profiles: tuple[TransportProfile, ...],
) -> tuple[_PreparedAgent, ...]:
    if not isinstance(manager_token, bytes) or not manager_token:
        raise AgentConfigImportError("Manager token is unavailable")
    prepared: list[_PreparedAgent] = []
    ids: set[str] = set()
    endpoints: set[str] = set()
    for agent in agents:
        if not _AGENT_ID.fullmatch(agent.id) or not agent.name.strip():
            raise AgentConfigImportError("YAML Agent identity is invalid")
        endpoint = _canonical_endpoint(agent.base_url)
        if agent.id in ids or endpoint in endpoints:
            raise AgentConfigImportError("YAML Agent ID or endpoint is duplicated")
        token = _read_source_token(agent.token_file, enabled=agent.enabled)
        if agent.token_file is not None and secrets.compare_digest(token, manager_token):
            raise AgentConfigImportError("Manager and Agent tokens must be independent")
        ids.add(agent.id)
        endpoints.add(endpoint)
        prepared.append(
            _PreparedAgent(
                config=agent,
                endpoint=endpoint,
                token=token,
                transport_profile_id=(
                    _profile_for(agent, transport_profiles)
                    if agent.token_file is not None
                    else "legacy-disabled-no-credential"
                ),
            )
        )
    return tuple(prepared)


def _read_source_token(path: Path | None, *, enabled: bool) -> bytes:
    if path is None:
        if enabled:
            raise AgentConfigImportError("enabled imported Agents require a token file")
        return secrets.token_bytes(32)
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise AgentConfigImportError("Agent token file must be a regular file")
        if metadata.st_uid != os.geteuid():
            raise AgentConfigImportError("Agent token file has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AgentConfigImportError("Agent token file permissions are too broad")
        token = path.read_text(encoding="utf-8").strip().encode("utf-8")
    except AgentConfigImportError:
        raise
    except (OSError, UnicodeError) as exc:
        raise AgentConfigImportError("Agent token file is unavailable") from exc
    if not token:
        raise AgentConfigImportError("Agent token file is empty")
    return token


def _canonical_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except (AttributeError, UnicodeError, ValueError) as exc:
        raise AgentConfigImportError("YAML Agent endpoint is invalid") from exc
    if (
        scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise AgentConfigImportError("YAML Agent endpoint is invalid")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{rendered_host}:{port}"


def _profile_for(
    agent: AgentConfig, transport_profiles: tuple[TransportProfile, ...]
) -> str:
    if agent.base_url.lower().startswith("https://"):
        if not agent.tls.verify:
            raise AgentConfigImportError("HTTPS Agent import requires TLS verification")
        if agent.tls.ca_bundle is None:
            return "system-tls"
        for profile in transport_profiles:
            if isinstance(profile, VerifiedTlsProfile) and profile.ca_bundle == agent.tls.ca_bundle:
                return profile.id
        raise AgentConfigImportError("Agent CA bundle has no transport profile")
    for profile in transport_profiles:
        if profile.type == "trusted_lan_http":
            return profile.id
    # Existing loopback development Agents remain routable through the v1 adapter.
    return "legacy-config-http"


def _commit_import(
    engine: Engine,
    prepared: tuple[_PreparedAgent, ...],
    references: list[str],
) -> None:
    raw = engine.raw_connection()
    connection = raw.driver_connection
    now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM agents LIMIT 1").fetchone() is not None:
            raise AgentConfigImportError("Agent Registry was populated during import")
        for item, reference in zip(prepared, references, strict=True):
            _insert_agent(connection, item, reference, now)
            _insert_initial_status(
                connection, item.config.id, item.config.enabled, now
            )
        _commit_transaction(connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        raw.close()


def _insert_agent(
    connection: sqlite3.Connection,
    item: _PreparedAgent,
    reference: str,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO agents (agent_id, instance_id, display_name, "
        "normalized_endpoint, credential_ref, remote_credential_id, "
        "transport_profile_id, enrollment_method, enabled, source, revision, "
        "created_at, updated_at) VALUES (?, NULL, ?, ?, ?, NULL, ?, "
        "'legacy_admin_token', ?, 'config_import', 1, ?, ?)",
        (
            item.config.id,
            item.config.name,
            item.endpoint,
            reference,
            item.transport_profile_id,
            int(item.config.enabled),
            now,
            now,
        ),
    )


def _commit_transaction(connection: sqlite3.Connection) -> None:
    connection.commit()


def _insert_initial_status(
    connection: sqlite3.Connection, agent_id: str, enabled: bool, now: str
) -> None:
    connection.execute(
        "INSERT INTO agent_status (agent_id, target_revision, connection_status, "
        "workload_status, observed_at, stale_after, api_version, agent_version, "
        "capabilities_json, summary_json, last_error_code, updated_at) "
        "VALUES (?, 1, ?, 'unknown', NULL, NULL, NULL, NULL, '[]', '{}', NULL, ?)",
        (agent_id, "unknown" if enabled else "disabled", now),
    )


def _delete_new_credentials(
    credential_store: CredentialStore, references: list[str]
) -> tuple[Exception, ...]:
    errors: list[Exception] = []
    for reference in references:
        try:
            credential_store.delete(reference)
        except Exception as exc:
            errors.append(exc)
    return tuple(errors)

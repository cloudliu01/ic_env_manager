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


class AgentConfigImportOutcomeUncertain(AgentConfigImportError):
    pass


class _ConcurrentImportComplete(Exception):
    pass


_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_IMPORT_MARKER_KEY = "yaml_agents_imported_v1"
_IMPORT_MARKER_VALUE = "complete"
_MAX_TOKEN_BYTES = 64 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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
    if not _prepare_import_if_needed(engine):
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
    except _ConcurrentImportComplete as exc:
        cleanup_errors = _delete_new_credentials(credential_store, new_references)
        if cleanup_errors:
            raise AgentConfigImportError(
                "concurrent YAML import completed but credential cleanup failed"
            ) from exc
        return False
    except AgentConfigImportOutcomeUncertain:
        # The only safe recovery is to retain every new reference for operator audit.
        raise
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


def _prepare_import_if_needed(engine: Engine) -> bool:
    raw = engine.raw_connection()
    connection = raw.driver_connection
    try:
        connection.execute("BEGIN IMMEDIATE")
        if _marker_complete(connection):
            _rollback_transaction(connection)
            return False
        if connection.execute("SELECT 1 FROM agents LIMIT 1").fetchone() is not None:
            _insert_marker(connection)
            _commit_transaction(connection)
            return False
        _rollback_transaction(connection)
        return True
    except Exception as exc:
        try:
            _rollback_transaction(connection)
        except Exception:
            pass
        raise AgentConfigImportError("Agent import state is unavailable") from exc
    finally:
        raw.close()


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
    fd = -1
    try:
        before = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(before.st_mode):
            raise AgentConfigImportError("Agent token file must be a regular file")
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise AgentConfigImportError("Agent token file changed during access")
        if metadata.st_uid != os.geteuid():
            raise AgentConfigImportError("Agent token file has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AgentConfigImportError("Agent token file permissions are too broad")
        if metadata.st_size > _MAX_TOKEN_BYTES:
            raise AgentConfigImportError("Agent token file is too large")
        chunks: list[bytes] = []
        remaining = _MAX_TOKEN_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_token = b"".join(chunks)
        if len(raw_token) > _MAX_TOKEN_BYTES:
            raise AgentConfigImportError("Agent token file is too large")
        token = raw_token.decode("utf-8").strip().encode("utf-8")
    except AgentConfigImportError:
        raise
    except (OSError, UnicodeError) as exc:
        raise AgentConfigImportError("Agent token file is unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
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
        if _marker_complete(connection):
            raise _ConcurrentImportComplete
        if connection.execute("SELECT 1 FROM agents LIMIT 1").fetchone() is not None:
            _insert_marker(connection)
            _commit_transaction(connection)
            raise _ConcurrentImportComplete
        for item, reference in zip(prepared, references, strict=True):
            _insert_agent(connection, item, reference, now)
            _insert_initial_status(
                connection, item.config.id, item.config.enabled, now
            )
        _insert_marker(connection)
    except Exception:
        try:
            _rollback_transaction(connection)
        finally:
            raw.close()
        raise
    try:
        _commit_transaction(connection)
    except Exception as commit_error:
        try:
            _rollback_transaction(connection)
        except Exception:
            pass
        raw.close()
        expected = {
            item.config.id: reference
            for item, reference in zip(prepared, references, strict=True)
        }
        try:
            outcome = _verify_committed_import(engine, expected)
        except Exception as verification_error:
            raise AgentConfigImportOutcomeUncertain(
                "YAML Agent commit outcome is uncertain; credentials were retained"
            ) from verification_error
        if outcome == "own":
            return
        if outcome == "other":
            raise _ConcurrentImportComplete from commit_error
        raise commit_error
    else:
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


def _rollback_transaction(connection: sqlite3.Connection) -> None:
    connection.rollback()


def _marker_complete(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM manager_metadata WHERE key = ?", (_IMPORT_MARKER_KEY,)
    ).fetchone()
    return row is not None and row[0] == _IMPORT_MARKER_VALUE


def _insert_marker(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO manager_metadata(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_IMPORT_MARKER_KEY, _IMPORT_MARKER_VALUE),
    )


def _verify_committed_import(engine: Engine, expected: dict[str, str]) -> str:
    with engine.connect() as connection:
        marker = connection.exec_driver_sql(
            "SELECT value FROM manager_metadata WHERE key = ?", (_IMPORT_MARKER_KEY,)
        ).first()
        if marker is None or marker[0] != _IMPORT_MARKER_VALUE:
            return "absent"
        for agent_id, reference in expected.items():
            row = connection.exec_driver_sql(
                "SELECT credential_ref, source FROM agents WHERE agent_id = ?", (agent_id,)
            ).first()
            if row is None or row[0] != reference or row[1] != "config_import":
                return "other"
    return "own"


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

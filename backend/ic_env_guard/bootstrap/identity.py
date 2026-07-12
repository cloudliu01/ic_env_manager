import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4


class InstanceIdentityError(RuntimeError):
    pass


_MARKER_KEY = "instance_identity_initialized"
_BINDING_KEY = "instance_identity_path"


def identity_bootstrap_allowed(database: Path) -> bool:
    if not database.exists() or database.stat().st_size == 0:
        return True
    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_versions'"
        ).fetchone()
        if table is None:
            return True
        v2 = connection.execute(
            "SELECT 1 FROM schema_versions WHERE version = '0004_manager_credentials'"
        ).fetchone()
    return v2 is None


class SQLiteIdentityInitialization:
    def __init__(
        self,
        database: Path,
        *,
        identity_path: Path,
        bootstrap_allowed: bool,
    ) -> None:
        self._database = database
        self._identity_path = str(identity_path.resolve())
        self.bootstrap_allowed = bootstrap_allowed

    def is_initialized(self) -> bool:
        with sqlite3.connect(self._database) as connection:
            initialized = connection.execute(
                "SELECT value FROM agent_metadata WHERE key = ?", (_MARKER_KEY,)
            ).fetchone()
            binding = connection.execute(
                "SELECT value FROM agent_metadata WHERE key = ?", (_BINDING_KEY,)
            ).fetchone()
        self._validate_binding(binding)
        return initialized == ("initialized",)

    def mark_initialized(self) -> None:
        with sqlite3.connect(self._database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = connection.execute(
                "SELECT value FROM agent_metadata WHERE key = ?", (_BINDING_KEY,)
            ).fetchone()
            self._validate_binding(binding)
            connection.execute(
                "INSERT OR IGNORE INTO agent_metadata(key, value) VALUES (?, ?)",
                (_BINDING_KEY, self._identity_path),
            )
            connection.execute(
                "INSERT INTO agent_metadata(key, value) VALUES (?, 'initialized') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_MARKER_KEY,),
            )
            connection.commit()

    def _validate_binding(self, binding: tuple[str] | None) -> None:
        if binding is not None and binding != (self._identity_path,):
            raise InstanceIdentityError(
                "configured instance identity path does not match initialized identity"
            )


@contextmanager
def _storage_locks(*paths: Path):
    parents = sorted({path.resolve().parent for path in paths}, key=lambda path: str(path))
    descriptors: list[int] = []
    try:
        for parent in parents:
            try:
                descriptor = os.open(
                    parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
            except OSError as exc:
                raise InstanceIdentityError(
                    f"identity storage directory is unavailable: {parent}"
                ) from exc
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            descriptors.append(descriptor)
        yield
    finally:
        for descriptor in reversed(descriptors):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read_instance_id(path: Path) -> UUID:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InstanceIdentityError("invalid instance identity file") from exc
    try:
        metadata = os.fstat(descriptor)
        content = os.read(descriptor, 128).decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise InstanceIdentityError("invalid instance identity file") from exc
    finally:
        os.close(descriptor)
    try:
        instance_id = UUID(content.rstrip("\n"))
    except (AttributeError, ValueError) as exc:
        raise InstanceIdentityError("invalid instance identity") from exc
    if content != f"{instance_id}\n":
        raise InstanceIdentityError("invalid instance identity")
    if not stat.S_ISREG(metadata.st_mode):
        raise InstanceIdentityError("invalid instance identity file")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise InstanceIdentityError("instance identity file must be owner-only")
    return instance_id


def load_or_create_instance_id(
    path: Path,
    *,
    allow_create: bool,
    initialization: SQLiteIdentityInitialization | None = None,
) -> UUID:
    with _storage_locks(path):
        return _load_or_create_instance_id_locked(
            path,
            allow_create=allow_create,
            initialization=initialization,
        )


def initialize_instance_id(
    path: Path,
    database: Path,
    migrate: Callable[[Path], None],
) -> UUID:
    canonical_database = database.resolve()
    canonical_identity = path.resolve()
    with _storage_locks(canonical_database, canonical_identity):
        intent_path = bootstrap_intent_path(canonical_database)
        expected_intent = _bootstrap_intent_content(
            canonical_database, canonical_identity
        )
        has_intent = _read_bootstrap_intent(intent_path, expected_intent)
        bootstrap_allowed = has_intent or identity_bootstrap_allowed(database)
        if bootstrap_allowed and not has_intent:
            _create_bootstrap_intent(intent_path, expected_intent)
        migrate(database)
        instance_id = _load_or_create_instance_id_locked(
            path,
            allow_create=True,
            initialization=SQLiteIdentityInitialization(
                database,
                identity_path=canonical_identity,
                bootstrap_allowed=bootstrap_allowed,
            ),
        )
        if bootstrap_allowed:
            _remove_bootstrap_intent(intent_path, expected_intent)
        return instance_id


def bootstrap_intent_path(database: Path) -> Path:
    canonical = database.resolve()
    digest = hashlib.sha256(os.fsencode(canonical)).hexdigest()
    return canonical.with_name(f".instance-identity-bootstrap.{digest}")


def _bootstrap_intent_content(database: Path, identity_path: Path) -> bytes:
    return (
        json.dumps(
            {
                "database": str(database.resolve()),
                "identity": str(identity_path.resolve()),
                "version": 1,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _read_bootstrap_intent(path: Path, expected: bytes) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstanceIdentityError("unsafe instance identity bootstrap intent") from exc
    try:
        metadata = os.fstat(descriptor)
        content = os.read(descriptor, len(expected) + 1)
    except OSError as exc:
        raise InstanceIdentityError("invalid instance identity bootstrap intent") from exc
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise InstanceIdentityError("invalid instance identity bootstrap intent")
    if content != expected:
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstanceIdentityError(
                "invalid instance identity bootstrap intent"
            ) from exc
        if not isinstance(decoded, dict) or decoded.get("version") != 1:
            raise InstanceIdentityError("invalid instance identity bootstrap intent")
        raise InstanceIdentityError(
            "instance identity bootstrap intent does not match database and identity path"
        )
    return True


def _create_bootstrap_intent(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            written = os.write(descriptor, content)
            if written != len(content):
                raise OSError("short bootstrap intent write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _read_bootstrap_intent(path, content)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise InstanceIdentityError("unable to create identity bootstrap intent") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _remove_bootstrap_intent(path: Path, expected: bytes) -> None:
    if not _read_bootstrap_intent(path, expected):
        return
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise InstanceIdentityError("unable to remove identity bootstrap intent") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_or_create_instance_id_locked(
    path: Path,
    *,
    allow_create: bool,
    initialization: SQLiteIdentityInitialization | None,
) -> UUID:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        try:
            instance_id = _read_instance_id(path)
        except InstanceIdentityError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
        else:
            if initialization is not None:
                initialization.mark_initialized()
            return instance_id

        if not allow_create:
            raise InstanceIdentityError("instance identity is missing")
        if initialization is not None and (
            initialization.is_initialized() or not initialization.bootstrap_allowed
        ):
            raise InstanceIdentityError(
                "instance identity is missing; restore instance identity from backup"
            )
        instance_id = uuid4()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{instance_id}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
            if initialization is not None:
                initialization.mark_initialized()
        finally:
            temporary.unlink(missing_ok=True)
        return instance_id
    except OSError as exc:
        raise InstanceIdentityError("unable to create instance identity") from exc

import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class ServiceKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceKeyPolicy:
    identity_file: Path
    known_hosts_file: Path
    snapshots: tuple[tuple[str, int, int, int, int], ...]


def validate_service_key_files(
    identity_file: Path,
    known_hosts_file: Path,
    *,
    inspect_key: Callable[[Path], str] | None = None,
) -> ServiceKeyPolicy:
    try:
        _validate_private_file(identity_file, exact_mode=0o600, require_content=True)
        _validate_private_file(known_hosts_file, exact_mode=None, require_content=True)
        first_line = identity_file.read_text(encoding="ascii").splitlines()[0]
        if first_line != "-----BEGIN OPENSSH PRIVATE KEY-----":
            raise OSError
        public = (inspect_key or _inspect_key)(identity_file)
        if not public.startswith("ssh-ed25519 "):
            raise OSError
    except (OSError, UnicodeError, IndexError, subprocess.SubprocessError):
        raise ServiceKeyError("service_key_unavailable") from None
    return ServiceKeyPolicy(
        identity_file=identity_file,
        known_hosts_file=known_hosts_file,
        snapshots=(*_snapshot_chain(identity_file), *_snapshot_chain(known_hosts_file)),
    )


def validate_service_key_snapshot(policy: ServiceKeyPolicy) -> None:
    try:
        current = (
            *_snapshot_chain(policy.identity_file),
            *_snapshot_chain(policy.known_hosts_file),
        )
        if current != policy.snapshots:
            raise OSError
        _validate_private_file(policy.identity_file, exact_mode=0o600, require_content=True)
        _validate_private_file(
            policy.known_hosts_file, exact_mode=None, require_content=True
        )
    except OSError:
        raise ServiceKeyError("service_key_unavailable") from None


def authorized_key_options() -> str:
    return (
        'command="ic-env-guard agent enroll-manager",restrict,no-pty,'
        "no-agent-forwarding,no-X11-forwarding,no-port-forwarding,no-user-rc"
    )


def _validate_private_file(
    path: Path, *, exact_mode: int | None, require_content: bool
) -> None:
    if not path.is_absolute():
        raise OSError
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
    parent = path.parent.lstat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
        raise OSError
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o077
        or (exact_mode is not None and mode != exact_mode)
        or (require_content and metadata.st_size == 0)
    ):
        raise OSError


def _inspect_key(path: Path) -> str:
    result = subprocess.run(
        ("/usr/bin/ssh-keygen", "-y", "-P", "", "-f", str(path)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=3,
        text=True,
    )
    if result.returncode != 0 or len(result.stdout.encode()) > 4096:
        raise OSError
    return result.stdout.strip()


def _snapshot_chain(path: Path) -> tuple[tuple[str, int, int, int, int], ...]:
    snapshots = []
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        metadata = current.lstat()
        snapshots.append(
            (
                str(current),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_uid,
                stat.S_IMODE(metadata.st_mode),
            )
        )
    return tuple(snapshots)

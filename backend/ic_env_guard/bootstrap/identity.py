import fcntl
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4


class InstanceIdentityError(RuntimeError):
    pass


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


def load_or_create_instance_id(path: Path, *, allow_create: bool) -> UUID:
    try:
        return _read_instance_id(path)
    except FileNotFoundError:
        pass
    except InstanceIdentityError as exc:
        if not isinstance(exc.__cause__, FileNotFoundError):
            raise

    if not allow_create:
        raise InstanceIdentityError("instance identity is missing")

    parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        if path.exists():
            return _read_instance_id(path)
        instance_id = uuid4()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{instance_id}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.fsync(parent_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return instance_id
    except OSError as exc:
        raise InstanceIdentityError("unable to create instance identity") from exc
    finally:
        fcntl.flock(parent_fd, fcntl.LOCK_UN)
        os.close(parent_fd)

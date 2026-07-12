import os
import re
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


class CredentialStoreError(Exception):
    pass


class RegistryCredentialReferences(Protocol):
    def credential_references(self) -> set[str]: ...


class JournalCredentialReferences(Protocol):
    def recovery_credential_references(self) -> set[str]: ...


_REFERENCE = re.compile(r"^[0-9a-f]{48}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class CredentialLifecycleCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()

    @contextmanager
    def exclusive_lease(self) -> Iterator[None]:
        with self._lock:
            yield


class CredentialStore:
    def __init__(
        self,
        directory: Path,
        *,
        coordinator: CredentialLifecycleCoordinator | None = None,
    ) -> None:
        self.directory = Path(directory)
        self._coordinator = coordinator or CredentialLifecycleCoordinator()
        created = False
        try:
            self.directory.lstat()
        except FileNotFoundError:
            try:
                self.directory.mkdir(mode=0o700, parents=True)
                self.directory.chmod(0o700)
                created = True
            except OSError as exc:
                raise CredentialStoreError("credential directory is unavailable") from exc
        self._validate_directory()
        if created:
            self._fsync_parent_entry()

    def lifecycle_lease(self) -> Iterator[None]:
        return self._coordinator.exclusive_lease()

    def put(self, secret: bytes) -> str:
        self._validate_secret(secret)
        with self._coordinator.exclusive_lease():
            for _ in range(8):
                reference = secrets.token_hex(24)
                if self._publish_new(self.directory / reference, secret):
                    return reference
        raise CredentialStoreError("could not allocate a credential reference")

    def read(self, reference: str) -> bytes:
        with self._coordinator.exclusive_lease():
            path = self._path(reference)
            fd = self._open_validated(path)
            try:
                chunks = []
                while chunk := os.read(fd, 65536):
                    chunks.append(chunk)
                return b"".join(chunks)
            except OSError as exc:
                raise CredentialStoreError("credential could not be read") from exc
            finally:
                os.close(fd)

    def replace(self, reference: str, secret: bytes) -> None:
        self._validate_secret(secret)
        with self._coordinator.exclusive_lease():
            path = self._path(reference)
            fd = self._open_validated(path)
            os.close(fd)
            self._publish_replace(path, secret)

    def delete(self, reference: str) -> None:
        with self._coordinator.exclusive_lease():
            path = self._path(reference)
            fd = self._open_validated(path)
            os.close(fd)
            try:
                path.unlink()
                self._fsync_directory()
            except OSError as exc:
                raise CredentialStoreError("credential could not be deleted") from exc

    def cleanup_orphans(
        self,
        registry: RegistryCredentialReferences,
        journal: JournalCredentialReferences,
    ) -> tuple[dict[str, str], ...]:
        with self._coordinator.exclusive_lease():
            retained = registry.credential_references()
            retained.update(journal.recovery_credential_references())
            findings: list[dict[str, str]] = []
            for entry in sorted(self.directory.iterdir(), key=lambda path: path.name):
                if not _REFERENCE.fullmatch(entry.name):
                    findings.append({"entry": "unrecognized", "action": "retained"})
                    continue
                if entry.name in retained:
                    continue
                self.delete(entry.name)
                findings.append({"entry": "credential", "action": "deleted"})
            return tuple(findings)

    def resolve_for_test(self, reference: str) -> Path:
        return self._path(reference)

    def _validate_directory(self) -> None:
        try:
            metadata = self.directory.lstat()
        except OSError as exc:
            raise CredentialStoreError("credential directory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or self.directory.is_symlink():
            raise CredentialStoreError("credential directory must be a real directory")
        if metadata.st_uid != os.geteuid():
            raise CredentialStoreError("credential directory has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise CredentialStoreError("credential directory permissions are too broad")

    @staticmethod
    def _validate_secret(secret: bytes) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise CredentialStoreError("credential must be non-empty bytes")

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise CredentialStoreError("invalid credential reference")
        return self.directory / reference

    def _open_validated(self, path: Path) -> int:
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise CredentialStoreError("credential must be a regular file")
            fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        except FileNotFoundError as exc:
            raise CredentialStoreError("credential not found") from exc
        except CredentialStoreError:
            raise
        except OSError as exc:
            raise CredentialStoreError("credential file is unsafe") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise CredentialStoreError("credential must be a regular file")
            if (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino):
                raise CredentialStoreError("credential file changed during access")
            if metadata.st_uid != os.geteuid():
                raise CredentialStoreError("credential file has the wrong owner")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise CredentialStoreError("credential file permissions must be 0600")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _write_temporary(self, secret: bytes) -> Path:
        temporary = self.directory / f".tmp-{secrets.token_hex(24)}"
        fd = -1
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
            )
            os.fchmod(fd, 0o600)
            view = memoryview(secret)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short credential write")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            return temporary
        except OSError as exc:
            if fd >= 0:
                os.close(fd)
                fd = -1
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise CredentialStoreError("credential could not be published") from exc
        finally:
            if fd >= 0:
                os.close(fd)

    def _publish_new(self, target: Path, secret: bytes) -> bool:
        temporary = self._write_temporary(secret)
        try:
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                return False
            temporary.unlink()
            self._fsync_directory()
            return True
        except OSError as exc:
            raise CredentialStoreError("credential could not be published") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _publish_replace(self, target: Path, secret: bytes) -> None:
        temporary = self._write_temporary(secret)
        try:
            os.replace(temporary, target)
            self._fsync_directory()
        except OSError as exc:
            raise CredentialStoreError("credential could not be published") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _fsync_directory(self) -> None:
        try:
            fd = os.open(self.directory, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise CredentialStoreError("credential directory could not be synchronized") from exc

    def _fsync_parent_entry(self) -> None:
        parent = self.directory.parent
        try:
            before = parent.lstat()
            if not stat.S_ISDIR(before.st_mode):
                raise OSError("parent is not a real directory")
            fd = os.open(parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
            try:
                metadata = os.fstat(fd)
                if (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino):
                    raise OSError("parent changed during access")
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise CredentialStoreError(
                "credential directory entry could not be synchronized"
            ) from exc

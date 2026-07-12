import os
import stat
from pathlib import Path

import pytest

from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError


class References:
    def __init__(self, values=()):
        self.values = set(values)

    def credential_references(self):
        return set(self.values)


class JournalReferences:
    def __init__(self, values=()):
        self.values = set(values)

    def non_terminal_credential_references(self):
        return set(self.values)


@pytest.mark.unit
def test_credential_store_creates_owner_only_atomic_file(tmp_path, monkeypatch):
    directory = tmp_path / "credentials"
    fsync_calls = []
    real_fsync = os.fsync

    def record_fsync(fd):
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", record_fsync)
    store = CredentialStore(directory)
    reference = store.put(b"manager-token")
    path = store.resolve_for_test(reference)

    assert len(reference) == 48
    assert "/" not in reference
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.read(reference) == b"manager-token"
    assert len(fsync_calls) >= 2
    assert not tuple(directory.glob(".tmp-*"))


@pytest.mark.unit
def test_credential_store_replace_and_delete_are_durable(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"first")

    store.replace(reference, b"second")
    assert store.read(reference) == b"second"
    store.delete(reference)

    with pytest.raises(CredentialStoreError, match="not found"):
        store.read(reference)


@pytest.mark.unit
def test_credential_store_fails_closed_for_unsafe_directory(tmp_path, monkeypatch):
    broad = tmp_path / "broad"
    broad.mkdir(mode=0o755)
    with pytest.raises(CredentialStoreError, match="permissions"):
        CredentialStore(broad)

    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(CredentialStoreError, match="directory"):
        CredentialStore(link)

    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(CredentialStoreError, match="directory"):
        CredentialStore(broken)

    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    monkeypatch.setattr(os, "geteuid", lambda: owned.stat().st_uid + 1)
    with pytest.raises(CredentialStoreError, match="owner"):
        CredentialStore(owned)


@pytest.mark.unit
def test_credential_store_rejects_paths_symlinks_and_unsafe_files(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"safe")

    for invalid in ("../token", "nested/token", ".", "..", "not-hex"):
        with pytest.raises(CredentialStoreError, match="reference"):
            store.read(invalid)

    path = store.resolve_for_test(reference)
    path.chmod(0o640)
    with pytest.raises(CredentialStoreError, match="permissions"):
        store.read(reference)

    path.unlink()
    target = tmp_path / "outside"
    target.write_bytes(b"outside")
    path.symlink_to(target)
    with pytest.raises(CredentialStoreError, match="regular file"):
        store.read(reference)
    with pytest.raises(CredentialStoreError, match="regular file"):
        store.replace(reference, b"attack")
    assert target.read_bytes() == b"outside"


@pytest.mark.unit
def test_credential_store_detects_inode_swap_during_open(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"original")
    path = store.resolve_for_test(reference)
    displaced = tmp_path / "displaced"
    real_open = os.open
    swapped = False

    def swap_then_open(candidate, flags, mode=0o777):
        nonlocal swapped
        if Path(candidate) == path and not swapped:
            swapped = True
            path.rename(displaced)
            path.write_bytes(b"replacement")
            path.chmod(0o600)
        return real_open(candidate, flags, mode)

    monkeypatch.setattr(os, "open", swap_then_open)
    with pytest.raises(CredentialStoreError, match="changed during access"):
        store.read(reference)

    assert displaced.read_bytes() == b"original"
    assert path.read_bytes() == b"replacement"


@pytest.mark.unit
def test_credential_replace_failure_preserves_original_and_removes_temp(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials")
    reference = store.put(b"original")

    def fail_publication(_source, _target):
        raise OSError("publication failed")

    monkeypatch.setattr(os, "replace", fail_publication)
    with pytest.raises(CredentialStoreError, match="could not be published"):
        store.replace(reference, b"new")

    assert store.read(reference) == b"original"
    assert not tuple(store.directory.glob(".tmp-*"))


@pytest.mark.unit
def test_orphan_cleanup_preserves_registry_and_nonterminal_journal_references(tmp_path):
    store = CredentialStore(tmp_path / "credentials")
    registered = store.put(b"registered")
    recovering = store.put(b"recovering")
    orphan = store.put(b"orphan")

    findings = store.cleanup_orphans(
        References({registered}), JournalReferences({recovering})
    )

    assert findings == ({"reference": orphan, "action": "deleted"},)
    assert store.read(registered) == b"registered"
    assert store.read(recovering) == b"recovering"
    with pytest.raises(CredentialStoreError, match="not found"):
        store.read(orphan)

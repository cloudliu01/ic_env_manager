import os
import stat
import threading
from pathlib import Path

import pytest

from ic_env_guard.enrollment.credential_store import (
    CredentialLifecycleCoordinator,
    CredentialStore,
    CredentialStoreError,
)


class References:
    def __init__(self, values=()):
        self.values = set(values)

    def credential_references(self):
        return set(self.values)


class JournalReferences:
    def __init__(self, values=()):
        self.values = set(values)

    def recovery_credential_references(self):
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
def test_put_never_replaces_same_reference_under_concurrency(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials")
    local = threading.local()

    def deterministic_token(_size):
        count = getattr(local, "count", 0)
        local.count = count + 1
        return "a" * 48 if count % 2 == 0 else f"{threading.get_ident():048x}"[-48:]

    monkeypatch.setattr(
        "ic_env_guard.enrollment.credential_store.secrets.token_hex", deterministic_token
    )
    barrier = threading.Barrier(2)
    results = []

    def publish(value):
        barrier.wait()
        try:
            results.append(("ok", store.put(value)))
        except CredentialStoreError:
            results.append(("error", None))

    threads = [
        threading.Thread(target=publish, args=(b"first",)),
        threading.Thread(target=publish, args=(b"second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert store.read("a" * 48) in {b"first", b"second"}


@pytest.mark.unit
def test_put_retries_destination_collision_without_overwrite(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials")
    existing = store.put(b"existing")
    replacement = "b" * 48
    values = iter((existing, "c" * 48, replacement, "d" * 48))
    monkeypatch.setattr(
        "ic_env_guard.enrollment.credential_store.secrets.token_hex", lambda _size: next(values)
    )

    created = store.put(b"new")

    assert created == replacement
    assert store.read(existing) == b"existing"
    assert store.read(replacement) == b"new"


@pytest.mark.unit
def test_new_credential_directory_parent_fsync_failure_is_mapped(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync")))

    with pytest.raises(CredentialStoreError, match="directory entry"):
        CredentialStore(tmp_path / "credentials")


@pytest.mark.unit
def test_put_fsync_failure_removes_unpublished_temporary_file(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials")
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync")))

    with pytest.raises(CredentialStoreError, match="could not be published"):
        store.put(b"secret")

    assert tuple(store.directory.iterdir()) == ()


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

    assert findings == ({"entry": "credential", "action": "deleted"},)
    assert orphan not in repr(findings)
    assert store.read(registered) == b"registered"
    assert store.read(recovering) == b"recovering"
    with pytest.raises(CredentialStoreError, match="not found"):
        store.read(orphan)


@pytest.mark.unit
def test_cleanup_cannot_interleave_between_publish_and_journal_commit(tmp_path):
    coordinator = CredentialLifecycleCoordinator()
    store = CredentialStore(tmp_path / "credentials", coordinator=coordinator)
    registry = References()
    journal = JournalReferences()
    published = threading.Event()
    release = threading.Event()
    cleanup_finished = threading.Event()
    result = {}

    def mutation():
        with store.lifecycle_lease():
            reference = store.put(b"recoverable")
            result["reference"] = reference
            published.set()
            release.wait(timeout=2)
            journal.values.add(reference)

    cleanup_result = []

    def cleanup():
        cleanup_result.extend(store.cleanup_orphans(registry, journal))
        cleanup_finished.set()

    mutation_thread = threading.Thread(target=mutation)
    mutation_thread.start()
    assert published.wait(timeout=2)
    cleanup_thread = threading.Thread(target=cleanup)
    cleanup_thread.start()
    assert not cleanup_finished.wait(timeout=0.1)
    release.set()
    mutation_thread.join()
    cleanup_thread.join()

    assert cleanup_result == []
    assert store.read(result["reference"]) == b"recoverable"


@pytest.mark.unit
def test_startup_removes_valid_unpublished_temporary_file(tmp_path):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    temporary = directory / (".tmp-" + "a" * 48)
    temporary.write_bytes(b"never-published-secret")
    temporary.chmod(0o600)

    store = CredentialStore(directory)

    assert not temporary.exists()
    assert store.startup_findings == ({"entry": "temporary", "action": "deleted"},)
    assert "never-published-secret" not in repr(store.startup_findings)
    assert "a" * 48 not in repr(store.startup_findings)


@pytest.mark.unit
def test_startup_removes_linked_temporary_but_preserves_published_target(tmp_path):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    temporary = directory / (".tmp-" + "b" * 48)
    target = directory / ("c" * 48)
    temporary.write_bytes(b"published-secret")
    temporary.chmod(0o600)
    os.link(temporary, target)

    store = CredentialStore(directory)

    assert not temporary.exists()
    assert store.read("c" * 48) == b"published-secret"
    assert store.startup_findings == ({"entry": "temporary", "action": "deleted"},)


@pytest.mark.unit
def test_startup_retains_only_nonmatching_temporary_name(tmp_path):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    invalid = directory / ".tmp-not-a-reference"
    invalid.write_bytes(b"invalid")

    store = CredentialStore(directory)

    assert invalid.exists()
    assert store.startup_findings == (
        {"entry": "temporary", "action": "retained", "reason": "invalid_name"},
    )


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_kind", ("symlink", "wrong_mode", "directory"))
def test_startup_fails_closed_for_unsafe_strict_temporary_entry(tmp_path, unsafe_kind):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    temporary = directory / (".tmp-" + "d" * 48)
    if unsafe_kind == "symlink":
        temporary.symlink_to(outside)
    elif unsafe_kind == "wrong_mode":
        temporary.write_bytes(b"wrong-mode")
        temporary.chmod(0o640)
    else:
        temporary.mkdir(mode=0o700)

    with pytest.raises(CredentialStoreError, match="unsafe temporary"):
        CredentialStore(directory)

    assert temporary.exists() or temporary.is_symlink()
    assert outside.read_bytes() == b"outside"


@pytest.mark.unit
def test_startup_retains_wrong_owner_temporary_entry(tmp_path, monkeypatch):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    temporary = directory / (".tmp-" + "f" * 48)
    temporary.write_bytes(b"wrong-owner")
    temporary.chmod(0o600)
    real_uid = os.geteuid()
    calls = iter((real_uid, real_uid + 1))
    monkeypatch.setattr(os, "geteuid", lambda: next(calls, real_uid + 1))

    with pytest.raises(CredentialStoreError, match="unsafe temporary"):
        CredentialStore(directory)
    assert temporary.exists()


@pytest.mark.unit
def test_startup_rejects_temporary_with_unexpected_extra_hardlink(tmp_path):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    temporary = directory / (".tmp-" + "f" * 48)
    target = directory / ("1" * 48)
    unexpected = tmp_path / "unexpected-link"
    temporary.write_bytes(b"secret")
    temporary.chmod(0o600)
    os.link(temporary, target)
    os.link(temporary, unexpected)

    with pytest.raises(CredentialStoreError, match="hard links"):
        CredentialStore(directory)

    assert temporary.exists()
    assert target.read_bytes() == b"secret"

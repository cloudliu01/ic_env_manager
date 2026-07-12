from pathlib import Path

import pytest

from ic_env_guard.enrollment.service_key import (
    ServiceKeyError,
    authorized_key_options,
    validate_service_key_files,
    validate_service_key_snapshot,
)


def _safe_files(tmp_path: Path) -> tuple[Path, Path]:
    directory = tmp_path / "manager"
    directory.mkdir(mode=0o700)
    identity = directory / "id_ed25519"
    identity.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    identity.chmod(0o600)
    known_hosts = directory / "known_hosts"
    known_hosts.write_text("agent.example ssh-ed25519 AAAAC3NzaFixture\n")
    known_hosts.chmod(0o600)
    return identity, known_hosts


def test_service_key_requires_safe_ed25519_identity_and_known_hosts(tmp_path):
    identity, known_hosts = _safe_files(tmp_path)
    calls = []

    policy = validate_service_key_files(
        identity,
        known_hosts,
        inspect_key=lambda path: calls.append(path) or "ssh-ed25519 AAAAC3NzaFixture",
    )

    assert policy.identity_file == identity
    assert policy.known_hosts_file == known_hosts
    assert calls == [identity]


@pytest.mark.parametrize("problem", ("identity_mode", "identity_symlink", "known_hosts_mode"))
def test_service_key_rejects_writable_or_redirectable_files(tmp_path, problem):
    identity, known_hosts = _safe_files(tmp_path)
    if problem == "identity_mode":
        identity.chmod(0o640)
    elif problem == "identity_symlink":
        target = identity.with_name("real-key")
        identity.rename(target)
        identity.symlink_to(target)
    else:
        known_hosts.chmod(0o660)

    with pytest.raises(ServiceKeyError, match="service_key_unavailable"):
        validate_service_key_files(
            identity,
            known_hosts,
            inspect_key=lambda _path: "ssh-ed25519 AAAAC3NzaFixture",
        )


@pytest.mark.parametrize("public", ("ssh-rsa AAAA", "encrypted", ""))
def test_service_key_rejects_non_ed25519_encrypted_or_invalid_key(tmp_path, public):
    identity, known_hosts = _safe_files(tmp_path)

    with pytest.raises(ServiceKeyError, match="service_key_unavailable"):
        validate_service_key_files(
            identity, known_hosts, inspect_key=lambda _path: public
        )


def test_authorized_key_options_are_forced_and_shell_incapable():
    value = authorized_key_options()

    assert value.startswith('command="ic-env-guard agent enroll-manager",')
    for option in (
        "restrict",
        "no-pty",
        "no-agent-forwarding",
        "no-X11-forwarding",
        "no-port-forwarding",
        "no-user-rc",
    ):
        assert option in value.split(",")
    assert "shell" not in value.lower()


def test_service_key_snapshot_rejects_same_uid_file_replacement_before_dispatch(tmp_path):
    identity, known_hosts = _safe_files(tmp_path)
    policy = validate_service_key_files(
        identity,
        known_hosts,
        inspect_key=lambda _path: "ssh-ed25519 AAAAC3NzaFixture",
    )
    replacement = identity.with_name("replacement")
    replacement.write_text(identity.read_text())
    replacement.chmod(0o600)
    replacement.replace(identity)

    with pytest.raises(ServiceKeyError, match="service_key_unavailable"):
        validate_service_key_snapshot(policy)

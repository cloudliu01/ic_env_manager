import json
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.enrollment.ssh import (
    SshEnrollmentAdapter,
    SshEnrollmentError,
    SshEnrollmentRequest,
    _validate_user_known_hosts_file,
)
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import TrustedLanHttpProfile


@pytest.mark.security
@pytest.mark.parametrize(
    ("user", "host"),
    (
        ("-oProxyCommand=touch", "agent.example"),
        ("edaops", "-F/tmp/attacker"),
        ("edaops;touch", "agent.example"),
        ("edaops", "agent.example\nProxyJump attacker"),
    ),
)
async def test_browser_destination_injection_fails_before_dns_or_subprocess(
    tmp_path, user, host
):
    resolved = False

    def resolver(_host, _port):
        nonlocal resolved
        resolved = True
        return ("10.20.30.40",)

    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=resolver,
        ),
        executable=tmp_path / "must-not-run",
        connect_timeout_seconds=1,
        total_timeout_seconds=1,
    )
    request = SshEnrollmentRequest(
        manager_id="11111111-1111-4111-8111-111111111111",
        enrollment_id="22222222-2222-4222-8222-222222222222",
        base_url="http://agent.example:8765",
        ssh_user=user,
        ssh_host=host,
        ssh_port=22,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    with pytest.raises(SshEnrollmentError) as caught:
        await adapter.issue(
            request,
            TrustedLanHttpProfile(id="lan", allowed_cidrs=["10.0.0.0/8"]),
        )

    assert caught.value.code == "ssh_unavailable"
    assert resolved is False
    assert json.dumps(caught.value.args) == '["ssh_unavailable"]'


@pytest.mark.security
def test_known_hosts_allows_safe_first_use_creation_and_then_regular_file(tmp_path):
    home = tmp_path / "manager-home"
    ssh_directory = home / ".ssh"
    ssh_directory.mkdir(parents=True)
    home.chmod(0o700)
    ssh_directory.chmod(0o700)
    known_hosts = ssh_directory / "known_hosts"

    assert _validate_user_known_hosts_file(known_hosts) == known_hosts
    known_hosts.write_text("host key\n")
    known_hosts.chmod(0o600)
    assert _validate_user_known_hosts_file(known_hosts) == known_hosts


@pytest.mark.security
@pytest.mark.parametrize("unsafe", ("writable_directory", "symlink_leaf", "non_regular"))
def test_known_hosts_rejects_writable_or_redirectable_paths(tmp_path, unsafe):
    home = tmp_path / "manager-home"
    ssh_directory = home / ".ssh"
    ssh_directory.mkdir(parents=True)
    home.chmod(0o700)
    ssh_directory.chmod(0o700)
    known_hosts = ssh_directory / "known_hosts"
    if unsafe == "writable_directory":
        ssh_directory.chmod(0o770)
    elif unsafe == "symlink_leaf":
        target = tmp_path / "attacker-known-hosts"
        target.write_text("")
        known_hosts.symlink_to(target)
    else:
        known_hosts.mkdir()

    with pytest.raises(OSError):
        _validate_user_known_hosts_file(known_hosts)


@pytest.mark.security
def test_known_hosts_rejects_symlink_in_home_chain(tmp_path):
    real_home = tmp_path / "real-home"
    (real_home / ".ssh").mkdir(parents=True)
    real_home.chmod(0o700)
    (real_home / ".ssh").chmod(0o700)
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(real_home, target_is_directory=True)

    with pytest.raises(OSError):
        _validate_user_known_hosts_file(linked_home / ".ssh" / "known_hosts")

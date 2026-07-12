import json
from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.enrollment.ssh import (
    SshEnrollmentAdapter,
    SshEnrollmentError,
    SshEnrollmentRequest,
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

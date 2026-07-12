import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ic_env_guard.enrollment.service_key import validate_service_key_files
from ic_env_guard.enrollment.ssh import (
    EnrollmentHelperResult,
    ExecutableIdentity,
    SshEnrollmentAdapter,
    SshEnrollmentError,
    SshEnrollmentRequest,
)
from ic_env_guard.fleet.target_policy import AgentTargetPolicy
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
MANAGER_ID = "11111111-1111-4111-8111-111111111111"
ENROLLMENT_ID = "22222222-2222-4222-8222-222222222222"
INSTANCE_ID = "33333333-3333-4333-8333-333333333333"
CREDENTIAL_ID = "44444444-4444-4444-8444-444444444444"
TOKEN = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
TRUSTED = TrustedLanHttpProfile(id="lab-http", allowed_cidrs=["10.0.0.0/8"])
VERIFIED = VerifiedTlsProfile(id="lab-tls")


def _script(tmp_path: Path, actual: str, *, preflight_rewrite: str = "") -> Path:
    path = tmp_path / "fixed-ssh"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, sys, time\n"
        "opts = {}\n"
        "for i, arg in enumerate(sys.argv):\n"
        "    if arg == '-o' and i + 1 < len(sys.argv):\n"
        "        key, value = sys.argv[i + 1].split('=', 1)\n"
        "        opts[key.lower()] = value\n"
        "if '-G' in sys.argv:\n"
        f"    rewrite = {preflight_rewrite!r}\n"
        "    keys = ('hostname','user','port','hostkeyalias','userknownhostsfile',"
        "'stricthostkeychecking',"
        "'batchmode','preferredauthentications','passwordauthentication',"
        "'kbdinteractiveauthentication','proxycommand','proxyjump',"
        "'clearallforwardings','requesttty','permitlocalcommand',"
        "'canonicalizehostname','localcommand','remotecommand','knownhostscommand',"
        "'controlmaster','controlpath','controlpersist','forwardagent','forwardx11',"
        "'forwardx11trusted','proxyusefdpass','connectionattempts',"
        "'numberofpasswordprompts','connecttimeout','loglevel')\n"
        "    if rewrite:\n"
        "        opts['hostname'] = rewrite\n"
        "    for key in keys:\n"
        "        print(key, opts[key])\n"
        "    for key in ('identityfile','identityagent','identitiesonly'):\n"
        "        if key in opts: print(key, opts[key])\n"
        "    raise SystemExit(0)\n"
        "payload = sys.stdin.buffer.read()\n"
        "with open(sys.argv[0] + '.stdin', 'wb') as stream:\n"
        "    stream.write(payload)\n"
        "with open(sys.argv[0] + '.argv', 'w') as stream:\n"
        "    json.dump(sys.argv, stream)\n"
        f"{actual}\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _result_json(**changes) -> str:
    value = {
        "protocol": "manager-enrollment.v1",
        "instance_id": INSTANCE_ID,
        "credential_id": CREDENTIAL_ID,
        "token": TOKEN,
        "expires_at": "2026-07-12T12:05:00Z",
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":"))


def _request() -> SshEnrollmentRequest:
    return SshEnrollmentRequest(
        manager_id=MANAGER_ID,
        enrollment_id=ENROLLMENT_ID,
        base_url="http://agent.lab.example:8765",
        ssh_user="edaops",
        ssh_host="agent.lab.example",
        ssh_port=2222,
        expires_at=NOW + timedelta(minutes=10),
    )


def _test_executable_identity(path: Path) -> ExecutableIdentity:
    metadata = path.stat()
    return ExecutableIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode & 0o7777,
        uid=metadata.st_uid,
    )


def _adapter(
    executable: Path, *, resolver=None, timeout=1.0, executable_validator=None
):
    ssh_directory = executable.parent / "manager-home" / ".ssh"
    ssh_directory.mkdir(parents=True, exist_ok=True)
    ssh_directory.chmod(0o700)
    return SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=resolver or (lambda _host, _port: ("10.20.30.40",)),
        ),
        executable=executable,
        connect_timeout_seconds=1,
        total_timeout_seconds=timeout,
        clock=lambda: NOW,
        termination_grace_seconds=0.1,
        user_known_hosts_file=ssh_directory / "known_hosts",
        executable_validator=executable_validator or _test_executable_identity,
    )


@pytest.mark.integration
async def test_issue_runs_preflight_then_fixed_helper_and_returns_secret_safe_result(tmp_path):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r} + '\\n'); sys.stdout.flush()",
    )
    resolutions = 0

    def resolver(_host, _port):
        nonlocal resolutions
        resolutions += 1
        return ("10.20.30.40",)

    result = await _adapter(executable, resolver=resolver).issue(_request(), TRUSTED)

    assert result == EnrollmentHelperResult(
        instance_id=INSTANCE_ID,
        credential_id=CREDENTIAL_ID,
        token=TOKEN.encode(),
        expires_at=NOW + timedelta(minutes=5),
        validation_target=result.validation_target,
    )
    assert TOKEN not in repr(result)
    assert resolutions == 2
    stdin = json.loads(Path(f"{executable}.stdin").read_bytes())
    assert stdin == {
        "protocol": "manager-enrollment.v1",
        "manager_id": MANAGER_ID,
        "enrollment_id": ENROLLMENT_ID,
    }
    argv = json.loads(Path(f"{executable}.argv").read_text())
    assert "Hostname=10.20.30.40" in argv
    assert argv[-1] == "ic-env-guard agent enroll-manager"


@pytest.mark.integration
async def test_real_ssh_g_with_null_config_is_safe_and_has_no_sendenv(tmp_path):
    executable = Path("/usr/bin/ssh")
    if not executable.exists():
        pytest.skip("platform SSH executable is unavailable")
    ssh_directory = tmp_path / "manager-home" / ".ssh"
    ssh_directory.mkdir(parents=True)
    ssh_directory.chmod(0o700)
    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
        ),
        executable=executable,
        user_known_hosts_file=ssh_directory / "known_hosts",
        connect_timeout_seconds=1,
        total_timeout_seconds=2,
    )

    assert await adapter.check_available() is True


@pytest.mark.integration
async def test_issue_binds_http_validation_and_ssh_to_deterministic_shared_address(tmp_path):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r} + '\\n'); sys.stdout.flush()",
    )
    calls = []

    def resolver(host, port):
        calls.append((host, port))
        if host == "api.agent.example":
            return ("10.20.30.42", "10.20.30.40", "2001:db8::40")
        assert host == "ssh.agent.example"
        return ("2001:db8::40", "10.20.30.40", "10.20.30.41")

    ssh_directory = executable.parent / "manager-home" / ".ssh"
    ssh_directory.mkdir(parents=True)
    ssh_directory.chmod(0o700)
    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8", "2001:db8::/32"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=resolver,
        ),
        executable=executable,
        connect_timeout_seconds=1,
        total_timeout_seconds=1,
        clock=lambda: NOW,
        user_known_hosts_file=ssh_directory / "known_hosts",
        executable_validator=_test_executable_identity,
    )
    result = await adapter.issue(
        replace(
            _request(),
            base_url="http://api.agent.example:8765",
            ssh_host="ssh.agent.example",
        ),
        TrustedLanHttpProfile(
            id="dual-stack", allowed_cidrs=["10.0.0.0/8", "2001:db8::/32"]
        ),
    )

    assert calls == [
        ("api.agent.example", 8765),
        ("ssh.agent.example", 2222),
    ]
    assert result.validation_target is not None
    assert str(result.validation_target.pinned_address) == "10.20.30.40"
    assert result.validation_target.hostname == "api.agent.example"
    assert result.validation_target.port == 8765
    assert result.validation_target.host_header == "api.agent.example:8765"
    assert "Hostname=10.20.30.40" in json.loads(
        Path(f"{executable}.argv").read_text()
    )


@pytest.mark.integration
async def test_service_key_uses_validated_non_dot_ssh_known_hosts_for_health_and_issue(
    tmp_path,
):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r} + '\\n'); sys.stdout.flush()",
    )
    service_dir = tmp_path / "var/lib/ic-env-guard/ssh"
    service_dir.mkdir(parents=True, mode=0o700)
    service_dir.chmod(0o700)
    identity = service_dir / "id_ed25519"
    identity.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    identity.chmod(0o600)
    known_hosts = service_dir / "known_hosts"
    known_hosts.write_text("agent.example ssh-ed25519 AAAAFixture\n")
    known_hosts.chmod(0o600)
    policy = validate_service_key_files(
        identity,
        known_hosts,
        inspect_key=lambda _path: "ssh-ed25519 AAAAFixture",
    )
    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=lambda _host, _port: ("10.20.30.40",),
        ),
        executable=executable,
        connect_timeout_seconds=1,
        total_timeout_seconds=2,
        clock=lambda: NOW,
        user_known_hosts_file=known_hosts,
        identity_file=identity,
        service_key_policy=policy,
        executable_validator=_test_executable_identity,
    )

    assert await adapter.check_available() is True
    await adapter.issue(_request(), TRUSTED)

    argv = json.loads(Path(f"{executable}.argv").read_text())
    assert f"UserKnownHostsFile={known_hosts}" in argv
    assert f"IdentityFile={identity}" in argv


@pytest.mark.integration
async def test_service_known_hosts_replacement_marks_unhealthy_before_actual_spawn(tmp_path):
    executable = _script(tmp_path, "raise AssertionError('must not run')")
    service_dir = tmp_path / "var/lib/ic-env-guard/ssh"
    service_dir.mkdir(parents=True)
    service_dir.chmod(0o700)
    identity = service_dir / "id_ed25519"
    identity.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    identity.chmod(0o600)
    known_hosts = service_dir / "known_hosts"
    known_hosts.write_text("agent.example ssh-ed25519 AAAAFixture\n")
    known_hosts.chmod(0o600)
    policy = validate_service_key_files(
        identity,
        known_hosts,
        inspect_key=lambda _path: "ssh-ed25519 AAAAFixture",
    )
    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=lambda _host, _port: ("10.20.30.40",),
        ),
        executable=executable,
        connect_timeout_seconds=1,
        total_timeout_seconds=2,
        user_known_hosts_file=known_hosts,
        identity_file=identity,
        service_key_policy=policy,
        executable_validator=_test_executable_identity,
    )
    assert await adapter.check_available() is True
    target = service_dir / "replacement-known-hosts"
    target.write_text("replacement\n")
    target.chmod(0o600)
    known_hosts.unlink()
    known_hosts.symlink_to(target)

    with pytest.raises(SshEnrollmentError, match="ssh_unavailable"):
        await adapter.issue(_request(), TRUSTED)

    assert adapter.healthy is False
    assert not Path(f"{executable}.argv").exists()


@pytest.mark.integration
async def test_service_policy_is_revalidated_immediately_before_each_ssh_spawn(
    tmp_path, monkeypatch
):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r} + '\\n'); sys.stdout.flush()",
    )
    service_dir = tmp_path / "service-ssh"
    service_dir.mkdir(mode=0o700)
    identity = service_dir / "id_ed25519"
    identity.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    identity.chmod(0o600)
    known_hosts = service_dir / "known_hosts"
    known_hosts.write_text("agent.example ssh-ed25519 AAAAFixture\n")
    known_hosts.chmod(0o600)
    policy = validate_service_key_files(
        identity,
        known_hosts,
        inspect_key=lambda _path: "ssh-ed25519 AAAAFixture",
    )
    from ic_env_guard.enrollment import ssh as ssh_module

    actual_validate = ssh_module.validate_service_key_snapshot
    calls = []

    def counted_validate(value):
        calls.append(value)
        actual_validate(value)

    monkeypatch.setattr(ssh_module, "validate_service_key_snapshot", counted_validate)
    adapter = SshEnrollmentAdapter(
        target_policy=AgentTargetPolicy(
            allowed_agent_cidrs=["10.0.0.0/8"],
            self_targets=[("10.0.0.1", 8765)],
            resolver=lambda _host, _port: ("10.20.30.40",),
        ),
        executable=executable,
        connect_timeout_seconds=1,
        total_timeout_seconds=2,
        clock=lambda: NOW,
        user_known_hosts_file=known_hosts,
        identity_file=identity,
        service_key_policy=policy,
        executable_validator=_test_executable_identity,
    )

    await adapter.issue(_request(), TRUSTED)

    assert calls == [policy, policy, policy, policy]


@pytest.mark.integration
async def test_issue_rejects_disjoint_http_and_ssh_addresses_before_spawn(tmp_path):
    executable = _script(tmp_path, "raise AssertionError('must not run')")

    def resolver(host, _port):
        return ("10.20.30.40",) if host == "api.agent.example" else ("10.20.30.41",)

    request = replace(
        _request(),
        base_url="http://api.agent.example:8765",
        ssh_host="ssh.agent.example",
    )
    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable, resolver=resolver).issue(request, TRUSTED)

    assert caught.value.code == "agent_identity_mismatch"
    assert caught.value.dispatch_state == "not_dispatched"
    assert not Path(f"{executable}.stdin").exists()


@pytest.mark.integration
async def test_issue_detects_same_hostname_dns_rebinding_between_http_and_ssh(tmp_path):
    executable = _script(tmp_path, "raise AssertionError('must not run')")
    answers = iter((("10.20.30.40",), ("10.20.30.41",)))

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable, resolver=lambda _host, _port: next(answers)).issue(
            _request(), TRUSTED
        )

    assert caught.value.code == "agent_identity_mismatch"
    assert caught.value.dispatch_state == "not_dispatched"
    assert not Path(f"{executable}.stdin").exists()


@pytest.mark.integration
async def test_issue_rejects_effective_config_target_rewrite_before_actual_dispatch(tmp_path):
    executable = _script(
        tmp_path,
        "raise AssertionError('actual SSH must not run')",
        preflight_rewrite="attacker.example",
    )

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_unavailable"
    assert not Path(f"{executable}.stdin").exists()


@pytest.mark.integration
async def test_issue_fails_closed_if_executable_identity_changes_between_spawns(tmp_path):
    executable = _script(tmp_path, "raise AssertionError('actual SSH must not run')")
    original = _test_executable_identity(executable)
    replaced_identity = replace(original, inode=original.inode + 1)
    identities = iter((original, original, replaced_identity))
    adapter = _adapter(
        executable, executable_validator=lambda _path: next(identities)
    )

    with pytest.raises(SshEnrollmentError) as caught:
        await adapter.issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_unavailable"
    assert caught.value.dispatch_state == "not_dispatched"
    assert adapter.healthy is False
    assert not Path(f"{executable}.stdin").exists()


@pytest.mark.integration
@pytest.mark.parametrize("failing_spawn", (1, 2))
async def test_spawn_oserror_is_never_reported_as_dispatched(
    tmp_path, monkeypatch, failing_spawn
):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r} + '\\n'); sys.stdout.flush()",
    )
    real_spawn = asyncio.create_subprocess_exec
    calls = 0

    async def fail_selected_spawn(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failing_spawn:
            raise OSError("spawn failed")
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.asyncio.create_subprocess_exec",
        fail_selected_spawn,
    )
    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_unavailable"
    assert caught.value.dispatch_state == "not_dispatched"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stderr", "code"),
    (
        ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", "ssh_host_key_changed"),
        ("No ED25519 host key is known and strict checking was requested", "ssh_host_key_unknown"),
        ("Enter passphrase for key '/secret/path'", "ssh_interaction_required"),
        ("sudo: a password is required", "ssh_interaction_required"),
        ("Permission denied (publickey).", "ssh_auth_failed"),
        ("remote helper exited", "ssh_remote_command_failed"),
    ),
)
async def test_issue_maps_ssh_failure_without_exposing_stderr(tmp_path, stderr, code):
    executable = _script(
        tmp_path,
        f"sys.stderr.write({stderr!r}); sys.stderr.flush(); raise SystemExit(255)",
    )

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable).issue(
            replace(_request(), base_url="https://agent.lab.example:8765"), VERIFIED
        )

    assert caught.value.code == code
    assert stderr not in str(caught.value)
    assert "/secret/path" not in repr(caught.value)


@pytest.mark.integration
async def test_issue_kills_child_that_ignores_term_after_timeout(tmp_path):
    executable = _script(
        tmp_path,
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
    )
    started = time.monotonic()

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable, timeout=1.0).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_remote_command_failed"
    assert time.monotonic() - started < 2.0


@pytest.mark.integration
async def test_cancellation_kills_and_reaps_real_adapter_child_that_ignores_term(
    tmp_path, monkeypatch
):
    executable = _script(
        tmp_path,
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
    )
    processes = []
    real_spawn = asyncio.create_subprocess_exec

    async def capture_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.asyncio.create_subprocess_exec", capture_spawn
    )
    task = asyncio.create_task(_adapter(executable, timeout=10).issue(_request(), TRUSTED))
    while len(processes) < 2:
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert processes[1].returncode is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    "actual",
    (
        "sys.stdout.write('x' * 9000); sys.stdout.flush(); time.sleep(30)",
        "sys.stderr.write('x' * 9000); sys.stderr.flush(); time.sleep(30)",
    ),
)
async def test_issue_enforces_streaming_hard_caps_before_child_exit(tmp_path, actual):
    executable = _script(tmp_path, actual)
    started = time.monotonic()

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable, timeout=5).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_remote_command_failed"
    assert time.monotonic() - started < 1.5


@pytest.mark.integration
async def test_overflow_stops_child_while_both_pipes_are_active(tmp_path):
    executable = _script(
        tmp_path,
        "sys.stdout.write('x' * 9000); sys.stdout.flush(); "
        "sys.stderr.write('y' * 9000); sys.stderr.flush(); time.sleep(30)",
    )
    started = time.monotonic()

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable, timeout=5).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_remote_command_failed"
    assert time.monotonic() - started < 1.5


@pytest.mark.integration
async def test_child_receives_only_bounded_ssh_identity_environment(tmp_path, monkeypatch):
    captured_environments = []
    real_create = asyncio.create_subprocess_exec

    async def capture_create(*args, **kwargs):
        captured_environments.append(dict(kwargs["env"]))
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.asyncio.create_subprocess_exec", capture_create
    )
    executable = _script(
        tmp_path,
        f"sys.stdout.write({_result_json()!r})",
    )
    monkeypatch.setenv("TOP_SECRET", "must-not-reach-child")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("SSH_ASKPASS", "/tmp/askpass")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("SSH_SK_PROVIDER", "provider\nwith-control")

    await _adapter(executable).issue(_request(), TRUSTED)

    assert len(captured_environments) == 2
    expected_environment = {
        key: os.environ[key]
        for key in ("HOME", "USER", "LOGNAME")
        if key in os.environ
    }
    expected_environment["SSH_AUTH_SOCK"] = "/tmp/agent.sock"
    for child_env in captured_environments:
        assert child_env == expected_environment
    for forbidden in (
        "TOP_SECRET",
        "LANG",
        "LC_ALL",
        "SSH_ASKPASS",
        "DISPLAY",
        "SSH_SK_PROVIDER",
        "PATH",
    ):
        assert forbidden not in child_env


@pytest.mark.integration
@pytest.mark.parametrize("failure_position", (1, 2, 3, 4))
async def test_partial_stream_task_creation_failure_reaps_process_and_tasks(
    tmp_path, monkeypatch, failure_position
):
    executable = _script(
        tmp_path,
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
    )
    adapter = _adapter(executable)
    adapter._executable_identity = _test_executable_identity(executable)
    loop = asyncio.get_running_loop()
    real_create_task = loop.create_task
    calls = 0
    captured_processes = []
    real_create_process = asyncio.create_subprocess_exec

    def injected_create_task(coroutine, *args, **kwargs):
        nonlocal calls
        if not captured_processes:
            return real_create_task(coroutine, *args, **kwargs)
        calls += 1
        if calls == failure_position:
            raise RuntimeError("injected task creation failure")
        return real_create_task(coroutine, *args, **kwargs)

    async def capture_process(*args, **kwargs):
        process = await real_create_process(*args, **kwargs)
        captured_processes.append(process)
        return process

    monkeypatch.setattr(loop, "create_task", injected_create_task)
    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.asyncio.create_subprocess_exec", capture_process
    )

    with pytest.raises(RuntimeError, match="injected task creation failure"):
        await adapter._execute(
            (str(executable),),
            stdin_payload=b"{}",
            stdout_limit=8192,
            stderr_limit=8192,
            timeout=5,
        )

    assert len(captured_processes) == 1
    assert captured_processes[0].returncode is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    "output",
    (
        "banner\\n" + _result_json(),
        _result_json(extra="field"),
        _result_json(protocol="manager-enrollment.v2"),
        _result_json(instance_id="not-a-uuid"),
        _result_json(credential_id="NOT-CANONICAL"),
        _result_json(token="short"),
        _result_json(token=TOKEN[:-1] + "B"),
        _result_json().replace(
            f'"token":"{TOKEN}"', f'"token":"{TOKEN}","token":"{TOKEN}"'
        ),
        _result_json(expires_at="2027-01-01T00:00:00Z"),
        _result_json() + "\\n{}",
    ),
)
async def test_issue_rejects_malformed_unbound_or_oversized_lifetime_helper_output(
    tmp_path, output
):
    executable = _script(
        tmp_path,
        f"sys.stdout.write({output!r}); sys.stdout.flush()",
    )

    with pytest.raises(SshEnrollmentError) as caught:
        await _adapter(executable).issue(_request(), TRUSTED)

    assert caught.value.code == "ssh_remote_command_failed"
    assert TOKEN not in str(caught.value)

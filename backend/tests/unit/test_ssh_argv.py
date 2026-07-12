from ipaddress import ip_address
from pathlib import Path

import pytest

from ic_env_guard.enrollment.ssh_config import (
    SshConfigError,
    SshEffectiveTarget,
    build_ssh_argv,
    build_ssh_preflight_argv,
    validate_ssh_destination,
    verify_effective_config,
)
from ic_env_guard.fleet.transport import TrustedLanHttpProfile, VerifiedTlsProfile

TRUSTED = TrustedLanHttpProfile(id="lab-http", allowed_cidrs=["10.0.0.0/8"])
VERIFIED = VerifiedTlsProfile(id="lab-tls")


def _actual(profile=TRUSTED):
    return build_ssh_argv(
        executable=Path("/usr/bin/ssh"),
        pinned_address=ip_address("10.20.30.40"),
        user="edaops",
        host="agent.lab.example",
        port=2222,
        profile=profile,
        connect_timeout_seconds=10,
        batch_mode=True,
    )


def test_auto_argv_is_target_pinned_and_contains_only_fixed_remote_command():
    argv = _actual()

    assert argv[0] == "/usr/bin/ssh"
    assert argv[-2:] == (
        "agent.lab.example",
        "ic-env-guard agent enroll-manager",
    )
    assert argv[-4:-2] == ("-o", "SendEnv=-*")
    for option in (
        "Hostname=10.20.30.40",
        "User=edaops",
        "Port=2222",
        "HostKeyAlias=[agent.lab.example]:2222",
        "StrictHostKeyChecking=accept-new",
        "BatchMode=yes",
        "PreferredAuthentications=publickey",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ClearAllForwardings=yes",
        "RequestTTY=no",
        "PermitLocalCommand=no",
        "CanonicalizeHostname=no",
        "ControlMaster=no",
        "ControlPath=none",
        "ControlPersist=no",
        "ForwardAgent=no",
        "ForwardX11=no",
    ):
        assert option in argv
    assert all("\n" not in item and ";" not in item for item in argv)


def test_verified_tls_auto_never_accepts_an_unknown_host_key():
    assert "StrictHostKeyChecking=yes" in _actual(VERIFIED)


def test_preflight_uses_the_exact_actual_overrides_and_fixed_command():
    actual = _actual()
    preflight = build_ssh_preflight_argv(actual)

    assert preflight[0:2] == ("/usr/bin/ssh", "-G")
    assert preflight[2:] == actual[1:]


@pytest.mark.parametrize(
    ("user", "host", "port"),
    (
        ("-oProxyCommand=evil", "agent.example", 22),
        ("user\nname", "agent.example", 22),
        ("user;touch", "agent.example", 22),
        ("edaops", "-Fattacker", 22),
        ("edaops", "agent.example\nProxyJump evil", 22),
        ("edaops", "agent.example;touch", 22),
        ("edaops", "user@agent.example", 22),
        ("edaops", "agent.example", 0),
        ("edaops", "agent.example", 65536),
        ("edaops", "agent.example", True),
    ),
)
def test_destination_rejects_option_control_metachar_and_invalid_port(user, host, port):
    with pytest.raises(SshConfigError, match="ssh_target_invalid"):
        validate_ssh_destination(user=user, host=host, port=port)


def _effective(**changes):
    values = {
        "hostname": "10.20.30.40",
        "user": "edaops",
        "port": "2222",
        "hostkeyalias": "[agent.lab.example]:2222",
        "stricthostkeychecking": "accept-new",
        "batchmode": "yes",
        "preferredauthentications": "publickey",
        "passwordauthentication": "no",
        "kbdinteractiveauthentication": "no",
        "proxycommand": "none",
        "proxyjump": "none",
        "clearallforwardings": "yes",
        "requesttty": "no",
        "permitlocalcommand": "no",
        "canonicalizehostname": "no",
        "localcommand": "none",
        "remotecommand": "none",
        "knownhostscommand": "none",
        "controlmaster": "no",
        "controlpath": "none",
        "controlpersist": "no",
        "forwardagent": "no",
        "forwardx11": "no",
        "forwardx11trusted": "no",
        "proxyusefdpass": "no",
        "connectionattempts": "1",
        "numberofpasswordprompts": "0",
        "connecttimeout": "10",
        "loglevel": "ERROR",
    }
    values.update(changes)
    return "".join(f"{key} {value}\n" for key, value in values.items()).encode()


def _expected():
    return SshEffectiveTarget(
        pinned_address="10.20.30.40",
        user="edaops",
        port=2222,
        host_key_alias="[agent.lab.example]:2222",
        strict_host_key_checking="accept-new",
        batch_mode=True,
        connect_timeout_seconds=10,
    )


def test_effective_config_accepts_safe_key_and_agent_contributions():
    output = _effective() + (
        b"identityfile ~/.ssh/id_ed25519\nidentityagent SSH_AUTH_SOCK\n"
        b"sendenv LANG\nsendenv LC_*\n"
    )

    verify_effective_config(output, _expected())


def test_effective_config_rejects_non_locale_sendenv_pattern():
    with pytest.raises(SshConfigError, match="ssh_effective_config_unsafe"):
        verify_effective_config(_effective() + b"sendenv TOP_SECRET\n", _expected())


@pytest.mark.parametrize(
    "changes",
    (
        {"hostname": "attacker.example"},
        {"user": "root"},
        {"port": "22"},
        {"hostkeyalias": "attacker"},
        {"proxycommand": "nc attacker 22"},
        {"proxyjump": "bastion"},
        {"localcommand": "touch /tmp/pwned"},
        {"remotecommand": "arbitrary command"},
        {"knownhostscommand": "attacker-command"},
        {"clearallforwardings": "no"},
        {"requesttty": "yes"},
        {"permitlocalcommand": "yes"},
        {"canonicalizehostname": "yes"},
        {"batchmode": "no"},
        {"passwordauthentication": "yes"},
        {"kbdinteractiveauthentication": "yes"},
        {"preferredauthentications": "password,publickey"},
        {"controlmaster": "auto"},
        {"controlpath": "~/.ssh/control"},
        {"forwardagent": "yes"},
        {"forwardx11": "yes"},
        {"forwardx11trusted": "yes"},
        {"proxyusefdpass": "yes"},
        {"connectionattempts": "2"},
        {"numberofpasswordprompts": "1"},
        {"connecttimeout": "30"},
        {"loglevel": "DEBUG3"},
    ),
)
def test_effective_config_rejects_target_command_auth_and_forwarding_rewrites(changes):
    with pytest.raises(SshConfigError, match="ssh_effective_config_unsafe"):
        verify_effective_config(_effective(**changes), _expected())


def test_effective_config_rejects_duplicate_conflict_control_and_oversize():
    with pytest.raises(SshConfigError, match="ssh_effective_config_unsafe"):
        verify_effective_config(_effective() + b"hostname attacker.example\n", _expected())
    with pytest.raises(SshConfigError, match="ssh_effective_config_unsafe"):
        verify_effective_config(_effective() + b"hostname value\x00suffix\n", _expected())
    with pytest.raises(SshConfigError, match="ssh_effective_config_unsafe"):
        verify_effective_config(b"x" * 32769, _expected())


def test_argv_builder_rejects_relative_or_non_ssh_executable():
    with pytest.raises(SshConfigError, match="ssh_unavailable"):
        build_ssh_argv(
            executable=Path("ssh"),
            pinned_address=ip_address("10.20.30.40"),
            user="edaops",
            host="agent.example",
            port=22,
            profile=TRUSTED,
            connect_timeout_seconds=10,
            batch_mode=True,
        )

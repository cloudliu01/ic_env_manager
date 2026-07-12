import re
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile

MAX_EFFECTIVE_CONFIG_BYTES = 32 * 1024
REMOTE_ENROLLMENT_COMMAND = "ic-env-guard agent enroll-manager"
_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_KEY = re.compile(r"^[a-z][a-z0-9]*$")


class SshConfigError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SshEffectiveTarget:
    pinned_address: str
    user: str
    port: int
    host_key_alias: str
    strict_host_key_checking: str
    batch_mode: bool
    connect_timeout_seconds: int


def validate_ssh_destination(*, user: str, host: str, port: int) -> tuple[str, str, int]:
    if (
        not isinstance(user, str)
        or not _USER.fullmatch(user)
        or not isinstance(host, str)
        or not _HOST.fullmatch(host)
        or host.startswith("-")
        or ".." in host
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        raise SshConfigError("ssh_target_invalid")
    return user, host.lower(), port


def host_key_alias(host: str, port: int) -> str:
    return f"[{host.lower()}]:{port}"


def build_ssh_argv(
    *,
    executable: Path,
    pinned_address: IPv4Address | IPv6Address,
    user: str,
    host: str,
    port: int,
    profile: TransportProfile,
    connect_timeout_seconds: int,
    batch_mode: bool,
) -> tuple[str, ...]:
    user, host, port = validate_ssh_destination(user=user, host=host, port=port)
    if (
        not isinstance(executable, Path)
        or not executable.is_absolute()
        or not 1 <= connect_timeout_seconds <= 60
    ):
        raise SshConfigError("ssh_unavailable")
    strict = "accept-new" if isinstance(profile, TrustedLanHttpProfile) else "yes"
    options = (
        f"Hostname={pinned_address}",
        f"User={user}",
        f"Port={port}",
        f"HostKeyAlias={host_key_alias(host, port)}",
        f"StrictHostKeyChecking={strict}",
        f"BatchMode={'yes' if batch_mode else 'no'}",
        "PreferredAuthentications=publickey",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ChallengeResponseAuthentication=no",
        "NumberOfPasswordPrompts=0",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ProxyUseFdpass=no",
        "ClearAllForwardings=yes",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ForwardX11Trusted=no",
        "RequestTTY=no",
        "PermitLocalCommand=no",
        "LocalCommand=none",
        "RemoteCommand=none",
        "KnownHostsCommand=none",
        "CanonicalizeHostname=no",
        "ControlMaster=no",
        "ControlPath=none",
        "ControlPersist=no",
        "ConnectionAttempts=1",
        f"ConnectTimeout={connect_timeout_seconds}",
        "LogLevel=ERROR",
        "SendEnv=-*",
    )
    argv: list[str] = [str(executable)]
    for option in options:
        argv.extend(("-o", option))
    argv.extend((host, REMOTE_ENROLLMENT_COMMAND))
    return tuple(argv)


def build_ssh_preflight_argv(actual: tuple[str, ...]) -> tuple[str, ...]:
    if len(actual) < 3 or not Path(actual[0]).is_absolute():
        raise SshConfigError("ssh_unavailable")
    return (actual[0], "-G", *actual[1:])


def verify_effective_config(output: bytes, expected: SshEffectiveTarget) -> None:
    values = _parse_effective_config(output)
    exact = {
        "hostname": expected.pinned_address,
        "user": expected.user,
        "port": str(expected.port),
        "hostkeyalias": expected.host_key_alias,
        "stricthostkeychecking": expected.strict_host_key_checking,
        "preferredauthentications": "publickey",
        "connectionattempts": "1",
        "numberofpasswordprompts": "0",
        "connecttimeout": str(expected.connect_timeout_seconds),
        "loglevel": "ERROR",
    }
    absent_or_none = (
        "proxycommand",
        "proxyjump",
        "localcommand",
        "remotecommand",
        "knownhostscommand",
        "controlpath",
    )
    booleans = {
        "batchmode": expected.batch_mode,
        "passwordauthentication": False,
        "kbdinteractiveauthentication": False,
        "clearallforwardings": True,
        "requesttty": False,
        "permitlocalcommand": False,
        "canonicalizehostname": False,
        "controlmaster": False,
        "controlpersist": False,
        "forwardagent": False,
        "forwardx11": False,
        "forwardx11trusted": False,
        "proxyusefdpass": False,
    }
    for key, wanted in exact.items():
        actual = _single(values, key)
        if key == "stricthostkeychecking" and wanted == "yes":
            if actual not in {"yes", "true"}:
                raise SshConfigError("ssh_effective_config_unsafe")
        elif actual != wanted:
            raise SshConfigError("ssh_effective_config_unsafe")
    for key in absent_or_none:
        found = values.get(key)
        if found is not None and found != ["none"]:
            raise SshConfigError("ssh_effective_config_unsafe")
    for key, wanted in booleans.items():
        if _as_bool(_single(values, key)) is not wanted:
            raise SshConfigError("ssh_effective_config_unsafe")
    challenge_response = values.get("challengeresponseauthentication")
    if challenge_response is not None and (
        len(challenge_response) != 1 or _as_bool(challenge_response[0])
    ):
        raise SshConfigError("ssh_effective_config_unsafe")
    if any(value not in {"LANG", "LC_*"} for value in values.get("sendenv", [])):
        raise SshConfigError("ssh_effective_config_unsafe")
    if values.get("setenv"):
        raise SshConfigError("ssh_effective_config_unsafe")


def _parse_effective_config(output: bytes) -> dict[str, list[str]]:
    if len(output) > MAX_EFFECTIVE_CONFIG_BYTES:
        raise SshConfigError("ssh_effective_config_unsafe")
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        raise SshConfigError("ssh_effective_config_unsafe") from None
    if any(character not in "\n\r\t" and ord(character) < 0x20 for character in text):
        raise SshConfigError("ssh_effective_config_unsafe")
    parsed: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or len(line) > 4096:
            raise SshConfigError("ssh_effective_config_unsafe")
        parts = line.split(None, 1)
        if len(parts) != 2 or not _KEY.fullmatch(parts[0].lower()):
            raise SshConfigError("ssh_effective_config_unsafe")
        parsed.setdefault(parts[0].lower(), []).append(parts[1])
    return parsed


def _single(values: dict[str, list[str]], key: str) -> str:
    found = values.get(key)
    if found is None or len(found) != 1:
        raise SshConfigError("ssh_effective_config_unsafe")
    return found[0]


def _as_bool(value: str) -> bool:
    if value in {"yes", "true"}:
        return True
    if value in {"no", "false"}:
        return False
    raise SshConfigError("ssh_effective_config_unsafe")

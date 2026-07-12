import re
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path

from ic_env_guard.fleet.transport import TransportProfile, TrustedLanHttpProfile

MAX_EFFECTIVE_CONFIG_BYTES = 32 * 1024
REMOTE_ENROLLMENT_COMMAND = "ic-env-guard agent enroll-manager"
_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
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
    user_known_hosts_file: str
    identity_file: str = ""


def validate_ssh_destination(*, user: str, host: str, port: int) -> tuple[str, str, int]:
    if not isinstance(user, str) or not _USER.fullmatch(user):
        raise SshConfigError("ssh_target_invalid")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise SshConfigError("ssh_target_invalid")
    return user, _canonical_host(host), port


def host_key_alias(host: str, port: int) -> str:
    _user, canonical, validated_port = validate_ssh_destination(
        user="host-key-alias", host=host, port=port
    )
    return f"[{canonical}]:{validated_port}"


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
    user_known_hosts_file: Path,
    identity_file: Path | None = None,
) -> tuple[str, ...]:
    user, host, port = validate_ssh_destination(user=user, host=host, port=port)
    if (
        not isinstance(executable, Path)
        or not executable.is_absolute()
        or not isinstance(user_known_hosts_file, Path)
        or not user_known_hosts_file.is_absolute()
        or any(ord(character) < 0x20 for character in str(user_known_hosts_file))
        or str(user_known_hosts_file).lower() in {"none", "/dev/null"}
        or (
            identity_file is not None
            and (
                not identity_file.is_absolute()
                or any(ord(character) < 0x20 for character in str(identity_file))
            )
        )
        or not 1 <= connect_timeout_seconds <= 60
    ):
        raise SshConfigError("ssh_unavailable")
    strict = (
        "yes"
        if identity_file is not None
        else "accept-new" if isinstance(profile, TrustedLanHttpProfile) else "yes"
    )
    options = (
        f"Hostname={pinned_address}",
        f"User={user}",
        f"Port={port}",
        f"HostKeyAlias={host_key_alias(host, port)}",
        f"UserKnownHostsFile={user_known_hosts_file}",
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
        *(
            (
                f"IdentityFile={identity_file}",
                "IdentityAgent=none",
                "IdentitiesOnly=yes",
            )
            if identity_file is not None
            else ()
        ),
    )
    argv: list[str] = [str(executable), "-F", "/dev/null"]
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
    if expected.user_known_hosts_file:
        exact["userknownhostsfile"] = expected.user_known_hosts_file
    if expected.identity_file:
        exact["identityfile"] = expected.identity_file
        exact["identityagent"] = "none"
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
    if expected.identity_file:
        booleans["identitiesonly"] = True
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
    if values.get("sendenv"):
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


def _canonical_host(host: object) -> str:
    if not isinstance(host, str) or not host or host.startswith("-"):
        raise SshConfigError("ssh_target_invalid")
    if any(ord(character) < 0x21 for character in host) or any(
        character in "@/\\[]%" for character in host
    ):
        raise SshConfigError("ssh_target_invalid")
    candidate = host.rstrip(".")
    if not candidate:
        raise SshConfigError("ssh_target_invalid")
    try:
        return ip_address(candidate).compressed.lower()
    except ValueError:
        pass
    try:
        canonical = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise SshConfigError("ssh_target_invalid") from None
    labels = canonical.split(".")
    if (
        len(canonical) > 253
        or (len(labels) == 4 and all(label.isdigit() for label in labels))
        or any(not _DNS_LABEL.fullmatch(label) for label in labels)
    ):
        raise SshConfigError("ssh_target_invalid")
    return canonical

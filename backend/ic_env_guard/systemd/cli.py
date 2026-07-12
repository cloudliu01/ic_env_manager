import argparse
import os
import pwd
import sys
from pathlib import Path

from ic_env_guard.config.loader import ConfigLoadError, load_config
from ic_env_guard.enrollment.cli import CliSshRunner, run_cli_enrollment
from ic_env_guard.enrollment.helper import run_helper

DEFAULT_CONFIG = Path("/etc/ic-env-guard/config.yaml")
USER_CONFIG_DIR = Path("/etc/ic-env-guard")


def resolve_helper_config_path() -> Path:
    configured = os.environ.get("IC_ENV_GUARD_CONFIG")
    if configured:
        return Path(configured)
    username = pwd.getpwuid(os.geteuid()).pw_name
    user_config = USER_CONFIG_DIR / f"{username}.yaml"
    return user_config if user_config.is_file() else DEFAULT_CONFIG


def build_runtime_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ic-env-guard")
    parser.add_argument("--config", type=Path)
    subparsers = parser.add_subparsers(dest="command")
    agent = subparsers.add_parser("agent")
    agent_subcommands = agent.add_subparsers(dest="agent_command", required=True)
    agent_subcommands.add_parser("enroll-manager")
    return parser


def runtime_main(argv: list[str] | None = None) -> int:
    args = build_runtime_parser().parse_args(argv)
    if args.command == "agent":
        config_path = resolve_helper_config_path()
        try:
            config = load_config(config_path)
        except ConfigLoadError:
            print("ic-env-guard: enrollment helper configuration unavailable", file=sys.stderr)
            return 1
        if config.mode != "agent":
            print("ic-env-guard: enrollment helper unavailable", file=sys.stderr)
            return 1
        return run_helper(
            config.enrollment.socket_path,
            sys.stdin.buffer,
            sys.stdout.buffer,
            sys.stderr,
        )

    import asyncio

    from ic_env_guard.main import serve_config

    config_path = args.config or Path(os.environ.get("IC_ENV_GUARD_CONFIG", DEFAULT_CONFIG))
    config = load_config(config_path)
    asyncio.run(serve_config(config, config_path=config_path))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ic-env-guard-config")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("config", type=Path)
    args = parser.parse_args(argv)

    if args.command == "validate":
        try:
            load_config(args.config)
        except ConfigLoadError as exc:
            print(f"configuration invalid: {exc}")
            return 1
        print("configuration valid")
        return 0
    return 1


def build_ctl_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ic-env-guardctl")
    commands = parser.add_subparsers(dest="command", required=True)
    agent = commands.add_parser("agent")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    enroll = agent_commands.add_parser("enroll")
    enroll.add_argument("--manager-socket", type=Path, required=True)
    enroll.add_argument("--enrollment-id", required=True)
    enroll.add_argument("--ssh", required=True)
    return parser


def ctl_main(
    argv: list[str] | None = None, *, runner: CliSshRunner | None = None
) -> int:
    args = build_ctl_parser().parse_args(argv)
    return run_cli_enrollment(
        manager_socket=args.manager_socket,
        enrollment_id=args.enrollment_id,
        ssh=args.ssh,
        stdout=sys.stdout,
        stderr=sys.stderr,
        runner=runner,
    )

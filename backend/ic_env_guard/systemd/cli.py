import argparse
import os
import sys
from pathlib import Path

from ic_env_guard.config.loader import ConfigLoadError, load_config
from ic_env_guard.enrollment.helper import run_helper

DEFAULT_CONFIG = Path("/etc/ic-env-guard/config.yaml")


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
        config_path = Path(os.environ.get("IC_ENV_GUARD_CONFIG", DEFAULT_CONFIG))
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
    asyncio.run(serve_config(config))
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

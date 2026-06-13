import argparse
from pathlib import Path

from ic_env_guard.config.loader import ConfigLoadError, load_config


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

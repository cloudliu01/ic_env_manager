from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ic_env_guard.config.models import AppConfig


class ConfigLoadError(Exception):
    pass


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"cannot read config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"cannot parse config file: {path}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigLoadError("config root must be a mapping")

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigLoadError(str(exc)) from exc


def sanitize_config_for_audit(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key.lower() in {"token", "password", "secret", "private_key"} else sanitize_config_for_audit(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_config_for_audit(item) for item in value]
    return value

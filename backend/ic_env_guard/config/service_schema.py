import json
from pathlib import Path

from jsonschema import Draft202012Validator


class ServiceSchemaValidationError(Exception):
    pass


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "001-linux-host-agent"
    / "contracts"
    / "service-config.schema.json"
)


def validate_service_config_document(config: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(config), key=lambda error: list(error.path)
    )
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "root"
        raise ServiceSchemaValidationError(f"{path}: {first.message}")

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "development"
    / "specs"
    / "001-linux-host-agent"
    / "contracts"
    / "service-config.schema.json"
)


def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def valid_config(tmp_path):
    return {
        "server": {"bind": "127.0.0.1", "port": 8765},
        "auth": {"mode": "bearer_token", "token_file": str(tmp_path / "token")},
        "metrics": {"enabled": True, "collect_interval_seconds": 10},
        "services": [
            {
                "id": "demo",
                "name": "Demo",
                "command": "python -m http.server 9000",
                "allowed_operations": ["start", "stop", "restart", "status", "healthcheck"],
                "restart": "never",
                "start_timeout_seconds": 5,
                "stop_timeout_seconds": 5,
                "logs": {"capture": True, "max_tail_lines": 100},
            }
        ],
    }


@pytest.mark.contract
def test_valid_service_config_schema(tmp_path):
    errors = list(validator().iter_errors(valid_config(tmp_path)))
    assert errors == []


@pytest.mark.contract
def test_invalid_service_config_rejects_missing_mapping(tmp_path):
    config = valid_config(tmp_path)
    del config["services"][0]["command"]

    assert list(validator().iter_errors(config))


@pytest.mark.contract
def test_invalid_service_config_rejects_unsafe_unknown_fields(tmp_path):
    config = valid_config(tmp_path)
    config["services"][0]["arbitrary_api_command"] = "rm -rf /"

    assert list(validator().iter_errors(config))


@pytest.mark.contract
@pytest.mark.parametrize(("retention", "valid"), [(0, True), (-1, False)])
def test_terminal_retention_schema_allows_immediate_cleanup(tmp_path, retention, valid):
    config = valid_config(tmp_path)
    config["terminal"] = {"exited_retention_minutes": retention}

    assert (list(validator().iter_errors(config)) == []) is valid

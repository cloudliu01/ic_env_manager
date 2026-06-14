import importlib
import tomllib
from pathlib import Path

import pytest

from ic_env_guard.auth.token import generate_bearer_token, validate_token_file_permissions


@pytest.mark.integration
def test_generated_token_file_permissions_are_owner_only(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(generate_bearer_token(), encoding="utf-8")
    token_file.chmod(0o600)

    validate_token_file_permissions(token_file)


@pytest.mark.integration
def test_runtime_documentation_declares_controlled_runtime():
    readme = Path(__file__).resolve().parents[3] / "packaging" / "runtime" / "README.md"
    if not readme.exists():
        pytest.skip("runtime README not created yet")
    text = readme.read_text(encoding="utf-8")
    assert "controlled Python runtime" in text
    assert "system Python" in text


@pytest.mark.integration
def test_agent_migrations_are_packaged_with_runtime_code():
    migrations_package = importlib.import_module("ic_env_guard.migrations")
    migrations_path = Path(migrations_package.__file__).parent

    assert migrations_path.is_dir()
    assert (migrations_path / "0001_initial.py").is_file()


@pytest.mark.integration
def test_control_plane_runtime_dependencies_are_declared():
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = pyproject["project"]["dependencies"]
    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]

    assert any(dependency.startswith("httpx>=") for dependency in dependencies)
    assert any(dependency.startswith("websockets>=") for dependency in dependencies)
    assert any(dependency.startswith("build>=1.2") for dependency in test_dependencies)

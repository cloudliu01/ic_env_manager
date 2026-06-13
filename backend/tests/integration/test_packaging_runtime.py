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

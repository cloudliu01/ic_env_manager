
import pytest

from ic_env_guard.auth.token import (
    BearerTokenValidator,
    generate_bearer_token,
    load_bearer_token,
    redact_token,
    validate_token_file_permissions,
)


@pytest.mark.unit
def test_load_bearer_token_strips_newline(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")

    assert load_bearer_token(token_file) == "secret-token"


@pytest.mark.unit
def test_validate_token_file_permissions_rejects_group_readable(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")
    token_file.chmod(0o640)

    with pytest.raises(ValueError, match="group/other"):
        validate_token_file_permissions(token_file)


@pytest.mark.unit
def test_validate_token_file_permissions_accepts_owner_only(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")
    token_file.chmod(0o600)

    validate_token_file_permissions(token_file)


@pytest.mark.unit
def test_token_redaction_never_returns_original_value():
    assert redact_token("secret-token") == "<redacted>"
    assert redact_token(None) is None


@pytest.mark.unit
def test_bearer_token_validator_uses_exact_secret():
    validator = BearerTokenValidator("secret-token")

    assert validator.validate("secret-token")
    assert not validator.validate("wrong-token")


@pytest.mark.unit
def test_generate_bearer_token_returns_non_empty_secret():
    assert generate_bearer_token()

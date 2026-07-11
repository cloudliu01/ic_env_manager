import stat

import pytest

from ic_env_guard.bootstrap.identity import InstanceIdentityError, load_or_create_instance_id


def test_instance_id_is_created_once_and_reused(tmp_path):
    path = tmp_path / "instance-id"

    first = load_or_create_instance_id(path, allow_create=True)
    second = load_or_create_instance_id(path, allow_create=False)

    assert first == second
    assert path.read_text(encoding="utf-8") == f"{first}\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_malformed_instance_id_fails_closed(tmp_path):
    path = tmp_path / "instance-id"
    path.write_text("not-a-uuid\n", encoding="utf-8")

    with pytest.raises(InstanceIdentityError, match="invalid instance identity"):
        load_or_create_instance_id(path, allow_create=False)


def test_missing_instance_id_fails_when_creation_is_disabled(tmp_path):
    with pytest.raises(InstanceIdentityError, match="instance identity is missing"):
        load_or_create_instance_id(tmp_path / "instance-id", allow_create=False)

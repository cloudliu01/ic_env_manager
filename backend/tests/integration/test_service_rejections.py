import pytest

from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.services.manager import ServiceManager


@pytest.mark.integration
@pytest.mark.security
def test_unknown_service_rejected_without_command_execution():
    manager = ServiceManager([])

    with pytest.raises(KeyError):
        manager.start("missing")


@pytest.mark.integration
@pytest.mark.security
def test_unsupported_operation_rejected_without_command_execution():
    manager = ServiceManager(
        [
            ServiceRuntime(
                id="demo",
                name="Demo",
                command="python -c 'print(1)'",
                allowed_operations=["status"],
            )
        ]
    )

    with pytest.raises(PermissionError):
        manager.start("demo")
    assert manager.services["demo"].pid is None

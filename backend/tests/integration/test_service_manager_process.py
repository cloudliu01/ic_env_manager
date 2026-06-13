import pytest

from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.services.manager import ServiceManager


@pytest.mark.integration
def test_configured_command_service_start_stop_restart_idempotency():
    manager = ServiceManager(
        [ServiceRuntime(id="demo", name="Demo", command="python -c 'import time; time.sleep(5)'")]
    )

    first = manager.start("demo")
    assert first.result == "success"
    assert manager.services["demo"].status == "running"

    second = manager.start("demo")
    assert second.result == "already_in_state"

    restarted = manager.restart("demo")
    assert restarted.operation == "restart"
    assert manager.services["demo"].status == "running"

    stopped = manager.stop("demo")
    assert stopped.result == "success"
    assert manager.services["demo"].status == "exited"

    stopped_again = manager.stop("demo")
    assert stopped_again.result == "already_in_state"

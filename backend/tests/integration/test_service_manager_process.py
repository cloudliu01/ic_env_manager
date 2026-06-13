import os
from dataclasses import dataclass

import pytest

from ic_env_guard.db.services import ServiceRuntime
from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.services.process_runner import ProcessHandle


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


@dataclass
class FakeProcess:
    returncode: int | None = None


class FakeRunner:
    def __init__(self):
        self.started: dict[str, object] | None = None
        self.stopped: dict[str, object] | None = None

    def start(
        self, command: str, cwd: str | None = None, env: dict[str, str] | None = None
    ) -> ProcessHandle:
        self.started = {"command": command, "cwd": cwd, "env": env}
        return ProcessHandle(pid=1234, process=FakeProcess())  # type: ignore[arg-type]

    def stop(self, handle: ProcessHandle, timeout_seconds: int = 30) -> int | None:
        self.stopped = {"handle": handle, "timeout_seconds": timeout_seconds}
        return 0


@pytest.mark.integration
def test_configured_process_launch_uses_cwd_env_and_stop_timeout(tmp_path):
    manager = ServiceManager(
        [
            ServiceRuntime(
                id="demo",
                name="Demo",
                command="python demo.py",
                cwd=str(tmp_path),
                env={"DEMO_FLAG": "enabled"},
                stop_timeout_seconds=4,
            )
        ]
    )
    runner = FakeRunner()
    manager.runner = runner  # type: ignore[assignment]

    started = manager.start("demo")
    assert started.result == "success"
    assert runner.started == {
        "command": "python demo.py",
        "cwd": str(tmp_path),
        "env": {**os.environ, "DEMO_FLAG": "enabled"},
    }

    stopped = manager.stop("demo")
    assert stopped.result == "success"
    assert runner.stopped is not None
    assert runner.stopped["timeout_seconds"] == 4


import pytest

from ic_env_guard.services.healthchecks import HealthCheckRunner
from ic_env_guard.services.logs import create_service_logger, tail_lines


@pytest.mark.integration
def test_tcp_healthcheck_failure_is_reported():
    runner = HealthCheckRunner()
    outcome = runner.run_tcp("127.0.0.1", 1, timeout_seconds=1)
    assert outcome.success is False
    assert outcome.error


@pytest.mark.integration
def test_rotated_service_log_tail(tmp_path):
    logger = create_service_logger("demo", tmp_path, max_bytes=100, backup_count=1)
    logger.info("hello")
    for handler in logger.handlers:
        handler.flush()

    lines = tail_lines(tmp_path / "demo.log", max_lines=10)
    assert any("hello" in line for line in lines)

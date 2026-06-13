import time

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app
from ic_env_guard.metrics.collector import MetricsCollector


@pytest.mark.integration
def test_metrics_refresh_loop_runs_until_shutdown(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
server:
  bind: 127.0.0.1
  port: 8765
auth:
  mode: bearer_token
  token_file: {token_file}
metrics:
  enabled: true
  collect_interval_seconds: 1
services: []
""",
        encoding="utf-8",
    )
    refresh_calls = 0
    original_refresh = MetricsCollector.refresh

    def counted_refresh(self):
        nonlocal refresh_calls
        refresh_calls += 1
        original_refresh(self)

    monkeypatch.setattr(MetricsCollector, "refresh", counted_refresh)

    app = create_app(config_path=config_path)
    with TestClient(app):
        deadline = time.monotonic() + 2.5
        while refresh_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

    assert refresh_calls >= 2

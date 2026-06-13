import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from ic_env_guard.main import create_app

FORBIDDEN_LABELS = {"terminal_id", "command", "request_id", "source_ip", "token"}


@pytest.mark.integration
@pytest.mark.security
def test_metrics_do_not_use_forbidden_high_cardinality_labels(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    client = TestClient(create_app(token_file=token_file))

    response = client.get("/metrics")
    assert response.status_code == 200

    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            assert FORBIDDEN_LABELS.isdisjoint(sample.labels.keys())

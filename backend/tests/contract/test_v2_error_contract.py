import pytest
from fastapi import Query
from fastapi.testclient import TestClient

from ic_env_guard.api.runtime import get_runtime_metadata
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.main import create_app


def _app(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(token_file=token_file)

    return app


@pytest.mark.contract
def test_v2_api_error_uses_nested_envelope(tmp_path):
    app = _app(tmp_path)

    def expected_error():
        raise V2ApiError(409, "expected_error", "safe message")

    app.dependency_overrides[get_runtime_metadata] = expected_error
    response = TestClient(app).get(
        "/api/v2/runtime", headers={"X-Correlation-ID": "client-request_42"}
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "expected_error",
            "message": "safe message",
            "correlation_id": "client-request_42",
        }
    }
    assert response.headers["X-Correlation-ID"] == "client-request_42"


@pytest.mark.contract
@pytest.mark.parametrize(
    "invalid_id", ["x" * 65, "line-break\nvalue", "space separated", "bad/value"]
)
def test_invalid_v2_correlation_id_is_replaced(tmp_path, invalid_id):
    response = TestClient(_app(tmp_path)).get(
        "/api/v2/runtime", headers={"X-Correlation-ID": invalid_id}
    )

    reflected = response.headers["X-Correlation-ID"]
    assert reflected != invalid_id
    assert len(reflected) <= 64


@pytest.mark.contract
def test_unexpected_v2_exception_uses_safe_internal_error(tmp_path):
    app = _app(tmp_path)

    def unexpected_error():
        raise RuntimeError("internal path /private/secret")

    app.dependency_overrides[get_runtime_metadata] = unexpected_error
    response = TestClient(app, raise_server_exceptions=False).get("/api/v2/runtime")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "an unexpected error occurred"
    assert "/private/secret" not in response.text
    assert response.headers["X-Correlation-ID"] == response.json()["error"]["correlation_id"]


@pytest.mark.contract
def test_exact_v2_prefix_uses_envelope_and_replaces_invalid_correlation_id(tmp_path):
    invalid_id = "x" * 65

    response = TestClient(_app(tmp_path)).get(
        "/api/v2", headers={"X-Correlation-ID": invalid_id}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    correlation_id = response.json()["error"]["correlation_id"]
    assert correlation_id != invalid_id
    assert response.headers["X-Correlation-ID"] == correlation_id


@pytest.mark.contract
def test_v2_wrong_method_uses_nested_error_envelope(tmp_path):
    response = TestClient(_app(tmp_path)).post("/api/v2/runtime")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["X-Correlation-ID"] == response.json()["error"]["correlation_id"]


@pytest.mark.contract
def test_v2_request_validation_uses_nested_error_envelope(tmp_path):
    app = _app(tmp_path)

    def validated(limit: int = Query(gt=0)):
        return {"limit": limit}

    app.add_api_route("/api/v2/validated", validated, methods=["GET"])
    route = app.router.routes.pop()
    app.router.routes.insert(0, route)

    response = TestClient(app).get("/api/v2/validated", params={"limit": 0})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "request validation failed"
    assert response.headers["X-Correlation-ID"] == response.json()["error"]["correlation_id"]


@pytest.mark.contract
def test_legacy_framework_error_body_remains_unchanged(tmp_path):
    response = TestClient(_app(tmp_path)).post("/api/capabilities")

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}

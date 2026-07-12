from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ic_env_guard.api.static import mount_static_ui


@pytest.fixture
def static_client(tmp_path: Path) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<main>agent shell</main>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    app = FastAPI()
    mount_static_ui(app, tmp_path)
    return TestClient(app)


@pytest.mark.contract
@pytest.mark.parametrize("path", ["/terminal", "/observations", "/fleet"])
def test_spa_deep_links_return_index_only_for_html_requests(static_client, path):
    browser = static_client.get(path, headers={"Accept": "text/html"})
    api_client = static_client.get(path, headers={"Accept": "application/json"})

    assert browser.status_code == 200
    assert browser.text == "<main>agent shell</main>"
    assert api_client.status_code == 404


@pytest.mark.contract
@pytest.mark.parametrize(
    "path",
    [
        "/api/not-real",
        "/ws/not-real",
        "/metrics/not-real",
        "/healthz/not-real",
        "/readyz/not-real",
        "/assets/not-real.js",
    ],
)
def test_spa_never_rewrites_reserved_routes(static_client, path):
    response = static_client.get(path, headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert "agent shell" not in response.text

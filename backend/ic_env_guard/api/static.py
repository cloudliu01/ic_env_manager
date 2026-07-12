from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

_RESERVED_PREFIXES = ("/api", "/ws", "/metrics", "/healthz", "/readyz", "/assets")


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not self._is_spa_request(scope):
                raise
            return FileResponse(Path(self.directory) / "index.html")
        if response.status_code == 404 and self._is_spa_request(scope):
            return FileResponse(Path(self.directory) / "index.html")
        return response

    @staticmethod
    def _is_spa_request(scope: Scope) -> bool:
        request_path = scope.get("path", "")
        if any(
            request_path == prefix or request_path.startswith(f"{prefix}/")
            for prefix in _RESERVED_PREFIXES
        ):
            return False
        if scope.get("method") not in {"GET", "HEAD"}:
            return False
        return "text/html" in Headers(scope=scope).get("accept", "")


def mount_static_ui(app: FastAPI, static_dir: Path | None = None) -> None:
    if static_dir is None:
        static_dir = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="ui")

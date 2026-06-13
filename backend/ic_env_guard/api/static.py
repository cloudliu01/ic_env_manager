from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_static_ui(app: FastAPI, static_dir: Path | None = None) -> None:
    if static_dir is None:
        static_dir = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

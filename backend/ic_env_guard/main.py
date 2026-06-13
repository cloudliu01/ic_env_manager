from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI

from ic_env_guard.api.auth import router as auth_router
from ic_env_guard.api.errors import register_error_handlers
from ic_env_guard.auth.dependencies import AuthContext, AuthState, get_auth_state, require_auth


def create_app(token_file: Path | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="IC Design Environment Guard", version="0.1.0")
    register_error_handlers(app)

    auth_state = AuthState(token_file=token_file, token=token)

    def configured_auth_state() -> AuthState:
        return auth_state

    app.dependency_overrides[get_auth_state] = configured_auth_state
    app.include_router(auth_router)

    protected_router = APIRouter()

    @protected_router.get("/api/terminals")
    def list_terminals(_: AuthContext = Depends(require_auth)) -> dict[str, list[object]]:
        return {"terminals": []}

    @protected_router.post("/api/terminals", status_code=501)
    def create_terminal(_: AuthContext = Depends(require_auth)) -> dict[str, str]:
        return {"status": "not_implemented"}

    @protected_router.post("/api/services/{service_id}/start", status_code=501)
    def start_service(service_id: str, _: AuthContext = Depends(require_auth)) -> dict[str, str]:
        return {"service_id": service_id, "status": "not_implemented"}

    @protected_router.post("/api/services/{service_id}/stop", status_code=501)
    def stop_service(service_id: str, _: AuthContext = Depends(require_auth)) -> dict[str, str]:
        return {"service_id": service_id, "status": "not_implemented"}

    app.include_router(protected_router)
    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ic_env_guard.main:create_app", factory=True, host="127.0.0.1", port=8765)

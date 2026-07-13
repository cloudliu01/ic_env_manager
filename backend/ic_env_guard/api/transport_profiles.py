from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.fleet.transport import TrustedLanHttpProfile

router = APIRouter(prefix="/api/v2/transport-profiles", tags=["transport-profiles"])


@router.get("")
def list_transport_profiles(
    request: Request,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
) -> dict[str, list[dict[str, str | None]]]:
    config = request.app.state.config
    profiles = config.control_plane.transport_profiles
    return {
        "profiles": [
            {
                "id": profile.id,
                "type": profile.type,
                "security_label": (
                    "Trusted-LAN HTTP"
                    if isinstance(profile, TrustedLanHttpProfile)
                    else "Verified TLS"
                ),
                "warning": (
                    "trusted_lan_http_unencrypted"
                    if isinstance(profile, TrustedLanHttpProfile)
                    else None
                ),
            }
            for profile in profiles
        ]
    }

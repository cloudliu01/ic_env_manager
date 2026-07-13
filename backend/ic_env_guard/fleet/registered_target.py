from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod
from ic_env_guard.fleet.target_policy import (
    AgentTargetPolicy,
    TargetPolicyError,
    ValidatedTarget,
)
from ic_env_guard.fleet.transport import TransportProfile

LOCAL_BOOTSTRAP_PROFILE_ID = "local-loopback-http"
LOCAL_BOOTSTRAP_SOURCE = "local_dev_bootstrap"


def resolve_registered_target(
    policy: AgentTargetPolicy,
    record: AgentRecord,
    profile: TransportProfile,
    *,
    local_bootstrap_enabled: bool,
) -> ValidatedTarget:
    if record.enrollment_method is not EnrollmentMethod.LOCAL_SOCKET:
        return policy.resolve(record.normalized_endpoint, profile)
    if (
        not local_bootstrap_enabled
        or record.source != LOCAL_BOOTSTRAP_SOURCE
        or record.transport_profile_id != LOCAL_BOOTSTRAP_PROFILE_ID
        or profile.id != LOCAL_BOOTSTRAP_PROFILE_ID
    ):
        raise TargetPolicyError(
            "target_address_forbidden", "local Agent bootstrap target is forbidden"
        )
    return policy.resolve_local_socket(record.normalized_endpoint, profile)

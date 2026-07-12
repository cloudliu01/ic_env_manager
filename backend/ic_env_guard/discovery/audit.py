from sqlalchemy.orm import sessionmaker

from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.discovery.models import DiscoveryJob, DiscoveryState


class DiscoveryAuditOutcomeRecorder:
    def __init__(self, session_factory: sessionmaker, health=None) -> None:
        self._session_factory = session_factory
        self._health = health

    def __call__(self, job: DiscoveryJob) -> None:
        success = job.state in {DiscoveryState.COMPLETED, DiscoveryState.CANCELLED}
        try:
            with self._session_factory() as session:
                ControlPlaneAuditRepository(session).finalize(
                    job.start_audit_event_id,
                    result="success" if success else "failed",
                    dispatch_state=(
                        "dispatched" if job.checked_targets else "not_dispatched"
                    ),
                    failure_category=(
                        job.safe_error_code
                        if not success
                        else (
                            "discovery_cancelled"
                            if job.state is DiscoveryState.CANCELLED
                            else None
                        )
                    ),
                )
                session.commit()
        except Exception:
            if self._health is not None:
                self._health.mark_unhealthy()
            raise

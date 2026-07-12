from sqlalchemy.orm import sessionmaker

from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.discovery.models import DiscoveryJob, DiscoveryState


class DiscoveryAuditOutcomeRecorder:
    def __init__(self, session_factory: sessionmaker, health=None) -> None:
        self._session_factory = session_factory
        self._health = health

    def __call__(self, job: DiscoveryJob) -> None:
        success = job.state is DiscoveryState.COMPLETED
        if job.checked_targets:
            dispatch_state = "dispatched"
        elif job.state is DiscoveryState.CANCELLED:
            dispatch_state = "not_dispatched"
        else:
            dispatch_state = "unknown"
        failure_category = None
        if job.state is DiscoveryState.CANCELLED:
            failure_category = "discovery_cancelled"
        elif job.state is DiscoveryState.FAILED:
            failure_category = job.safe_error_code or "discovery_failed"
        try:
            with self._session_factory() as session:
                ControlPlaneAuditRepository(session).finalize_pending(
                    job.start_audit_event_id,
                    expected_operation="discovery.start",
                    expected_target=f"discovery:{job.scope_id}",
                    result="success" if success else "failed",
                    dispatch_state=dispatch_state,
                    failure_category=failure_category,
                )
                session.commit()
            if self._health is not None:
                self._health.mark_healthy()
        except Exception:
            if self._health is not None:
                self._health.mark_unhealthy()
            raise

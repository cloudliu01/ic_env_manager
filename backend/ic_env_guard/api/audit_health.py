from dataclasses import dataclass

from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository


class AuditStorageUnavailable(Exception):
    pass


@dataclass
class AuditStorageHealth:
    healthy: bool = True

    def mark_healthy(self) -> None:
        self.healthy = True

    def mark_unhealthy(self) -> None:
        self.healthy = False


def get_audit_storage_health() -> AuditStorageHealth:
    raise RuntimeError("AuditStorageHealth dependency was not configured")


def commit_audit_intent(
    audit_repo: ControlPlaneAuditRepository, audit_health: AuditStorageHealth
) -> None:
    try:
        audit_repo.session.commit()
    except Exception as exc:
        audit_repo.session.rollback()
        audit_health.mark_unhealthy()
        raise AuditStorageUnavailable("audit storage is unavailable") from exc
    audit_health.mark_healthy()


def commit_audit_outcome(
    audit_repo: ControlPlaneAuditRepository, audit_health: AuditStorageHealth
) -> bool:
    try:
        audit_repo.session.commit()
    except Exception:
        audit_repo.session.rollback()
        audit_health.mark_unhealthy()
        return False
    audit_health.mark_healthy()
    return True

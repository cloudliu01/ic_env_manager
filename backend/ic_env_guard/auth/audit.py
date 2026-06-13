from ic_env_guard.db.audit import AuditEventCreate, AuditRepository


def audit_auth_success(
    repo: AuditRepository, actor_id: str, source_addr: str | None = None
) -> None:
    repo.add(
        AuditEventCreate(
            actor_id=actor_id,
            source_addr=source_addr,
            operation="auth.login",
            target_type="auth",
            result="success",
        )
    )


def audit_auth_failure(repo: AuditRepository, source_addr: str | None = None) -> None:
    repo.add(
        AuditEventCreate(
            source_addr=source_addr,
            operation="auth.login",
            target_type="auth",
            result="denied",
            failure_reason="invalid bearer token",
        )
    )

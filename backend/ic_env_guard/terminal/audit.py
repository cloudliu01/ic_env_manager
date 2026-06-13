from ic_env_guard.db.audit import AuditEventCreate, AuditRepository


def add_terminal_lifecycle_event(
    repo: AuditRepository,
    operation: str,
    terminal_id: str,
    result: str = "success",
    actor_id: str = "local-admin",
    failure_reason: str | None = None,
) -> None:
    repo.add(
        AuditEventCreate(
            actor_id=actor_id,
            operation=operation,
            target_type="terminal",
            target_id=terminal_id,
            result=result,  # type: ignore[arg-type]
            failure_reason=failure_reason,
        )
    )

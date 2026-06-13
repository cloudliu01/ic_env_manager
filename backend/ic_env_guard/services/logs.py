import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def create_service_logger(
    service_id: str, log_dir: Path, max_bytes: int = 1_000_000, backup_count: int = 3
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"ic_env_guard.service.{service_id}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_dir / f"{service_id}.log", maxBytes=max_bytes, backupCount=backup_count
        )
        logger.addHandler(handler)
    return logger


def tail_lines(path: Path, max_lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-max_lines:]

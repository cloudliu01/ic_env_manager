import json
import os
import stat
from pathlib import Path

from ic_env_guard.logs.models import (
    LogFileUnavailable,
    LogPathForbidden,
    TailResult,
)

_MAX_TAIL_LINES = 1000
_MAX_TAIL_BYTES = 983040
_READ_CHUNK_BYTES = 64 * 1024
_SERIALIZED_LINES_BUDGET = (1024 * 1024) - (16 * 1024)


def _serialized_lines_size(lines: tuple[str, ...]) -> int:
    return len(
        json.dumps(
            lines,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _fit_utf8_suffix(value: str, max_bytes: int) -> tuple[str, bool]:
    if len(value.encode("utf-8")) <= max_bytes:
        return value, False
    low = 0
    high = len(value)
    while low < high:
        midpoint = (low + high) // 2
        if len(value[midpoint:].encode("utf-8")) <= max_bytes:
            high = midpoint
        else:
            low = midpoint + 1
    return value[low:], True


def _fit_json_budget(lines: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    if not lines or _serialized_lines_size(lines) <= _SERIALIZED_LINES_BUDGET:
        return lines, False

    line = lines[-1]
    if _serialized_lines_size((line,)) <= _SERIALIZED_LINES_BUDGET:
        low = 1
        high = len(lines) - 1
        while low < high:
            midpoint = (low + high) // 2
            if _serialized_lines_size(lines[midpoint:]) <= _SERIALIZED_LINES_BUDGET:
                high = midpoint
            else:
                low = midpoint + 1
        return lines[low:], True

    low = 0
    high = len(line)
    while low < high:
        midpoint = (low + high) // 2
        if _serialized_lines_size((line[midpoint:],)) <= _SERIALIZED_LINES_BUDGET:
            high = midpoint
        else:
            low = midpoint + 1
    return (line[low:],), True


class LogPathPolicy:
    def __init__(self, allowed_roots: list[Path]) -> None:
        roots: list[Path] = []
        for root in allowed_roots:
            try:
                resolved = Path(root).resolve(strict=True)
            except OSError as exc:
                raise ValueError("allowed root must be an existing directory") from exc
            if not resolved.is_dir():
                raise ValueError("allowed root must be an existing directory")
            roots.append(resolved)
        self.roots = tuple(roots)

    def contains(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.roots)

    def resolve_regular_file(self, path: str | Path) -> Path:
        candidate = Path(path)
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise LogFileUnavailable("log target is not an existing regular file") from exc
        if not self.contains(resolved):
            raise LogPathForbidden("log path is outside allowed roots")
        try:
            mode = resolved.stat().st_mode
        except OSError as exc:
            raise LogFileUnavailable("log target is not an existing regular file") from exc
        if not stat.S_ISREG(mode):
            raise LogFileUnavailable("log target is not a regular file")
        return resolved


class LogTailReader:
    def __init__(
        self,
        policy: LogPathPolicy,
        *,
        max_bytes: int = _MAX_TAIL_BYTES,
    ) -> None:
        if not 1 <= max_bytes <= _MAX_TAIL_BYTES:
            raise ValueError("max_bytes must be between 1 and 983040")
        self.policy = policy
        self.max_bytes = max_bytes

    def _open_verified(self, path: str | Path) -> int:
        resolved = self.policy.resolve_regular_file(path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(resolved, flags)
        except OSError as exc:
            try:
                self.policy.resolve_regular_file(path)
            except (LogFileUnavailable, LogPathForbidden) as policy_error:
                raise policy_error from exc
            raise LogFileUnavailable("log file could not be opened safely") from exc

        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise LogFileUnavailable("opened log target is not a regular file")

            verified_path = self.policy.resolve_regular_file(path)
            verified = verified_path.stat()
            if (opened.st_dev, opened.st_ino) != (verified.st_dev, verified.st_ino):
                raise LogFileUnavailable("log file changed while opening")

            proc_path = Path(f"/proc/self/fd/{descriptor}")
            if proc_path.exists():
                opened_path = proc_path.resolve(strict=True)
                if not self.policy.contains(opened_path):
                    raise LogPathForbidden("opened log path is outside allowed roots")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def read(
        self,
        path: str | Path,
        *,
        lines: int,
        max_bytes: int | None = None,
    ) -> TailResult:
        if not 1 <= lines <= _MAX_TAIL_LINES:
            raise ValueError("lines must be between 1 and 1000")
        byte_limit = self.max_bytes if max_bytes is None else max_bytes
        if not 1 <= byte_limit <= self.max_bytes:
            raise ValueError(f"max_bytes must be between 1 and {self.max_bytes}")

        descriptor = self._open_verified(path)
        try:
            size = os.fstat(descriptor).st_size
            position = size
            chunks: list[bytes] = []
            bytes_read = 0
            newline_count = 0
            while position > 0 and bytes_read < byte_limit and newline_count <= lines:
                amount = min(_READ_CHUNK_BYTES, position, byte_limit - bytes_read)
                position -= amount
                chunk = os.pread(descriptor, amount, position)
                if not chunk:
                    break
                chunks.append(chunk)
                bytes_read += len(chunk)
                newline_count += chunk.count(b"\n")
            raw = b"".join(reversed(chunks))
        finally:
            os.close(descriptor)

        decoded = raw.decode("utf-8", errors="replace")
        decoded, byte_trimmed = _fit_utf8_suffix(decoded, byte_limit)
        available_lines = decoded.splitlines()
        selected = tuple(available_lines[-lines:])
        content_trimmed = False
        if len("\n".join(selected).encode("utf-8")) > byte_limit:
            low = 1
            high = len(selected)
            while low < high:
                midpoint = (low + high) // 2
                content = "\n".join(selected[midpoint:])
                if len(content.encode("utf-8")) <= byte_limit:
                    high = midpoint
                else:
                    low = midpoint + 1
            selected = selected[low:]
            content_trimmed = True
        selected, wire_trimmed = _fit_json_budget(selected)
        truncated = (
            position > 0
            or len(available_lines) > lines
            or size > bytes_read
            or byte_trimmed
            or content_trimmed
            or wire_trimmed
        )
        return TailResult(lines=selected, truncated=truncated)

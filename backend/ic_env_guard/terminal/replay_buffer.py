from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReplayHistory:
    terminal_id: str
    from_cursor: int
    to_cursor: int
    buffer_start_cursor: int
    truncated: bool
    status: str
    output: str


class ReplayBuffer:
    def __init__(self, max_bytes: int = 2 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._chunks: list[tuple[int, str]] = []
        self.start_cursor = 0
        self.cursor = 0

    def append(self, text: str) -> None:
        if not text:
            return
        start = self.cursor
        self.cursor += len(text.encode("utf-8", errors="replace"))
        self._chunks.append((start, text))
        self._trim()

    def _trim(self) -> None:
        while self.size_bytes > self.max_bytes and len(self._chunks) > 1:
            start, text = self._chunks.pop(0)
            self.start_cursor = start + len(text.encode("utf-8", errors="replace"))
        if self.size_bytes > self.max_bytes and self._chunks:
            start, text = self._chunks[0]
            encoded = text.encode("utf-8", errors="replace")
            tail = encoded[-self.max_bytes :]
            self.start_cursor = start + len(encoded) - len(tail)
            self._chunks[0] = (self.start_cursor, tail.decode("utf-8", errors="ignore"))

    @property
    def size_bytes(self) -> int:
        return sum(len(text.encode("utf-8", errors="replace")) for _, text in self._chunks)

    def read_from(self, cursor: int) -> tuple[str, int, bool]:
        if cursor > self.cursor:
            return "", self.cursor, False
        truncated = cursor < self.start_cursor
        effective_cursor = max(cursor, self.start_cursor)
        output_parts: list[str] = []
        for start, text in self._chunks:
            end = start + len(text.encode("utf-8", errors="replace"))
            if end <= effective_cursor:
                continue
            if start >= effective_cursor:
                output_parts.append(text)
            else:
                # Cursor landed inside this chunk. Use character slicing as a safe approximation.
                byte_offset = effective_cursor - start
                encoded = text.encode("utf-8", errors="replace")
                output_parts.append(encoded[byte_offset:].decode("utf-8", errors="ignore"))
        return "".join(output_parts), effective_cursor, truncated

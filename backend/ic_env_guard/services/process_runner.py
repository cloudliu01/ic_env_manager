from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class ProcessHandle:
    pid: int
    process: subprocess.Popen


class ConfiguredProcessRunner:
    def start(
        self, command: str, cwd: str | None = None, env: dict[str, str] | None = None
    ) -> ProcessHandle:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return ProcessHandle(pid=process.pid, process=process)

    def stop(self, handle: ProcessHandle, timeout_seconds: int = 30) -> int | None:
        handle.process.terminate()
        try:
            return handle.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            return handle.process.wait(timeout=5)

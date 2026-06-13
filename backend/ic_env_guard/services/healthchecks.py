import socket
import time
from dataclasses import dataclass
from urllib.request import urlopen


@dataclass
class HealthCheckOutcome:
    success: bool
    latency_ms: int | None = None
    status_code: int | None = None
    error: str | None = None


class HealthCheckRunner:
    def run_none(self) -> HealthCheckOutcome:
        return HealthCheckOutcome(success=True)

    def run_tcp(self, host: str, port: int, timeout_seconds: int = 2) -> HealthCheckOutcome:
        start = time.time()
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return HealthCheckOutcome(
                    success=True, latency_ms=int((time.time() - start) * 1000)
                )
        except OSError as exc:
            return HealthCheckOutcome(success=False, error=str(exc))

    def run_http(self, url: str, timeout_seconds: int = 2) -> HealthCheckOutcome:
        start = time.time()
        try:
            response = urlopen(url, timeout=timeout_seconds)
            return HealthCheckOutcome(
                success=200 <= response.status < 400,
                latency_ms=int((time.time() - start) * 1000),
                status_code=response.status,
            )
        except Exception as exc:
            return HealthCheckOutcome(success=False, error=str(exc))

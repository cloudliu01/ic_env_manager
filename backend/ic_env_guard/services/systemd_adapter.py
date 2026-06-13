import subprocess


class SystemdServiceAdapter:
    def __init__(self, allowed_units: set[str]) -> None:
        self.allowed_units = allowed_units

    def _check_unit(self, unit: str) -> None:
        if unit not in self.allowed_units:
            raise ValueError("systemd unit is not explicitly configured")

    def start(self, unit: str) -> None:
        self._check_unit(unit)
        subprocess.run(["systemctl", "start", unit], check=True)

    def stop(self, unit: str) -> None:
        self._check_unit(unit)
        subprocess.run(["systemctl", "stop", unit], check=True)

    def status(self, unit: str) -> str:
        self._check_unit(unit)
        result = subprocess.run(
            ["systemctl", "is-active", unit], check=False, capture_output=True, text=True
        )
        return result.stdout.strip() or "unknown"

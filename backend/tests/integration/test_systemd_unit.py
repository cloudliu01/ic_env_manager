from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.systemd
def test_systemd_unit_declares_required_operational_settings():
    unit = Path(__file__).resolve().parents[3] / "packaging" / "systemd" / "ic-env-guard.service"
    if not unit.exists():
        pytest.skip("systemd unit not created yet")
    text = unit.read_text(encoding="utf-8")
    assert "Restart=on-failure" in text
    assert "User=ic-env-guard" in text
    assert "WorkingDirectory=/var/lib/ic-env-guard" in text
    assert "StandardOutput=journal" in text
    assert "After=network-online.target" in text

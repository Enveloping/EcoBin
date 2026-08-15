from pathlib import Path


HARDWARE = Path(__file__).resolve().parents[1]


def test_hardware_waits_for_verified_enrollment_and_always_restarts():
    unit = (HARDWARE / "ecobin-hardware.service").read_text(encoding="utf-8")

    assert "Requires=ecobin-enrollment.service" in unit
    assert "After=network-online.target ecobin-enrollment.service" in unit
    assert "ConditionPathExists=/etc/ecobin/device-credentials.json" in unit
    assert "Restart=always" in unit
    assert "RuntimeDirectory=ecobin/remote-support" in unit


def test_enrollment_is_oneshot_retriable_and_deletes_generation_module():
    unit = (HARDWARE / "ecobin-enrollment.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "Restart=on-failure" in unit
    assert "PYTHONDONTWRITEBYTECODE=1" in unit
    assert "--cleanup-file /opt/ecobin-enrollment/device_enrollment.py" in unit
    assert "--cleanup-file /root/EcoBin/hardware/device_enrollment.py" in unit
    assert "ExecStartPost=" in unit
    assert "maintenance_ssh_setup.py" in unit
    assert "Before=ecobin-hardware.service" in unit
    assert "After=network-online.target time-sync.target" in unit

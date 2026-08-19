from pathlib import Path


HARDWARE = Path(__file__).resolve().parents[1]


def test_hardware_waits_for_verified_enrollment_and_always_restarts():
    unit = (HARDWARE / "ecobin-hardware.service").read_text(encoding="utf-8")

    assert "Requires=ecobin-enrollment.service" in unit
    assert "Wants=network-online.target ecobin-remote-support.service" in unit
    assert (
        "After=network-online.target ecobin-enrollment.service "
        "ecobin-remote-support.service"
    ) in unit
    assert "ConditionPathExists=/etc/ecobin/device-credentials.json" in unit
    assert "Restart=always" in unit
    assert "RuntimeDirectory=ecobin/remote-support" not in unit


def test_remote_support_service_has_an_independent_lifecycle():
    unit = (HARDWARE / "ecobin-remote-support.service").read_text(
        encoding="utf-8"
    )

    assert "User=ecobin-remote" in unit
    assert "Type=notify" in unit
    assert "NotifyAccess=main" in unit
    assert "Requires=ecobin-enrollment.service" in unit
    assert "After=network-online.target ecobin-enrollment.service" in unit
    assert "PartOf=ecobin-hardware.service" not in unit
    assert "BindsTo=ecobin-hardware.service" not in unit
    assert "RuntimeDirectory=ecobin/remote-support" in unit
    assert "StateDirectory=ecobin/remote-support" in unit
    assert "Restart=always" in unit
    assert "TimeoutStartSec=30" in unit
    assert "TimeoutStopSec=20" in unit
    assert "/opt/ecobin-remote-support/remote_support_agent.py" in unit
    assert (
        "LoadCredential=remote-support.json:"
        "/etc/ecobin/remote-support-credentials.json"
    ) in unit
    assert "--credentials %d/remote-support.json" in unit


def test_enrollment_is_oneshot_retriable_and_deletes_generation_module():
    unit = (HARDWARE / "ecobin-enrollment.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "Restart=on-failure" in unit
    assert "PYTHONDONTWRITEBYTECODE=1" in unit
    assert "--cleanup-file /opt/ecobin-enrollment/device_enrollment.py" in unit
    assert "--cleanup-file /root/EcoBin/hardware/device_enrollment.py" in unit
    assert "ExecStartPost=" in unit
    assert "maintenance_ssh_setup.py" in unit
    assert (
        "Before=ecobin-remote-support.service ecobin-hardware.service"
        in unit
    )
    assert "remote_support_credentials.py" in unit
    assert "After=network-online.target time-sync.target" in unit

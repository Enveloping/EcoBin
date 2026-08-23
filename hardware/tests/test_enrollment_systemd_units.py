from pathlib import Path


HARDWARE = Path(__file__).resolve().parents[1]


def test_hardware_waits_for_verified_enrollment_and_always_restarts():
    unit = (HARDWARE / "ecobin-hardware.service").read_text(encoding="utf-8")

    assert (
        "Requires=ecobin-mcu-safe-gpio.service ecobin-enrollment.service"
        in unit
    )
    assert "Wants=network-online.target ecobin-remote-support.service" in unit
    assert (
        "After=network-online.target ecobin-mcu-safe-gpio.service "
        "ecobin-enrollment.service ecobin-remote-support.service"
    ) in unit
    assert "ConditionPathExists=/etc/ecobin/device-credentials.json" in unit
    assert "Restart=always" in unit
    assert "Type=notify" in unit
    assert "NotifyAccess=main" in unit
    assert "TimeoutStartSec=180" in unit
    assert "RuntimeDirectory=ecobin/remote-support" not in unit
    assert "WorkingDirectory=/opt/ecobin/hardware/current/app" in unit
    assert (
        "ExecStart=/opt/ecobin/hardware/current/.venv/bin/python "
        "/opt/ecobin/hardware/current/app/main.py"
    ) in unit
    assert (
        "ExecStartPre=/usr/bin/python3 /usr/lib/ecobin/mcu_safe_gpio.py "
        "--gpio-path /usr/bin/gpio"
    ) in unit
    assert "EnvironmentFile=/etc/ecobin/hardware.env" in unit
    assert "LimitCORE=0" in unit
    assert (
        "EnvironmentFile=/opt/ecobin/hardware/current/release.env" in unit
    )
    assert "/root/EcoBin" not in unit


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
    assert (
        "/opt/ecobin/remote-support/current/app/remote_support_agent.py"
        in unit
    )
    assert (
        "LoadCredential=remote-support.json:"
        "/etc/ecobin/remote-support-credentials.json"
    ) in unit
    assert "--credentials %d/remote-support.json" in unit
    assert "LimitCORE=0" in unit


def test_enrollment_is_oneshot_retriable_and_deletes_generation_module():
    unit = (HARDWARE / "ecobin-enrollment.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "Restart=on-failure" in unit
    assert "PYTHONDONTWRITEBYTECODE=1" in unit
    assert "EnvironmentFile=/etc/ecobin/enrollment.env" in unit
    assert "EnvironmentFile=-/etc/ecobin/enrollment.env" not in unit
    assert "--cleanup-file /opt/ecobin/enrollment/device_enrollment.py" in unit
    assert "/root/EcoBin" not in unit
    assert "/opt/ecobin-enrollment" not in unit
    assert "ExecStartPost=" in unit
    assert "maintenance_ssh_setup.py" in unit
    assert (
        "Before=ecobin-remote-support.service ecobin-hardware.service"
        in unit
    )
    assert "remote_support_credentials.py" in unit
    assert "LimitCORE=0" in unit
    assert "MemorySwapMax=0" in unit
    assert (
        "ExecStartPre=/opt/ecobin/enrollment/.venv/bin/python "
        "/opt/ecobin/enrollment/secret_memory_guard.py "
        "--proc-swaps /proc/swaps"
    ) in unit
    assert "Requires=ecobin-mcu-safe-gpio.service" in unit
    assert (
        "After=network-online.target time-sync.target "
        "ecobin-mcu-safe-gpio.service"
    ) in unit

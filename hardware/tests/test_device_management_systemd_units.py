from __future__ import annotations

from pathlib import Path


HARDWARE = Path(__file__).resolve().parents[1]
SYSUSERS = (
    HARDWARE
    / "device_management/config/sysusers.d/ecobin-device-runtime.conf"
).read_text(encoding="utf-8")
TMPFILES = (
    HARDWARE
    / "device_management/config/tmpfiles.d/ecobin-device-runtime.conf"
).read_text(encoding="utf-8")


def _unit(name: str) -> str:
    return (HARDWARE / name).read_text(encoding="utf-8")


def test_permanent_accounts_and_socket_groups_are_exact() -> None:
    for user in ("ecobin-communication", "ecobin-business", "ecobin-updater"):
        assert f"u {user} " in SYSUSERS
        assert f"m {user} ecobin-communication-ipc" in SYSUSERS
        assert f"m {user} ecobin-business-ipc" in SYSUSERS
        assert f"m {user} ecobin-updater-ipc" in SYSUSERS
    for group in (
        "ecobin-communication-ipc",
        "ecobin-business-ipc",
        "ecobin-updater-ipc",
        "ecobin-privileged-helper-ipc",
    ):
        assert f"g {group} - -" in SYSUSERS
    assert "m ecobin-business dialout" in SYSUSERS
    assert "m ecobin-business video" in SYSUSERS
    assert "m ecobin-updater ecobin-privileged-helper-ipc" in SYSUSERS
    assert "m ecobin-business ecobin-privileged-helper-ipc" not in SYSUSERS
    assert "m ecobin-communication ecobin-privileged-helper-ipc" not in SYSUSERS
    assert SYSUSERS.count("/usr/sbin/nologin") == 3


def test_permanent_state_and_socket_directories_are_least_privilege() -> None:
    for owner in ("communication", "business"):
        assert (
            f"d /var/lib/ecobin/{owner} 0700 ecobin-{owner} ecobin-{owner} -"
            in TMPFILES
        )
    assert (
        "d /var/lib/ecobin/updater 0700 "
        "ecobin-updater ecobin-updater-ipc -"
    ) in TMPFILES
    assert (
        "d /var/lib/ecobin/updater 0700 "
        "ecobin-updater ecobin-updater -"
    ) not in TMPFILES
    for owner in ("communication", "business", "updater"):
        assert (
            f"d /run/ecobin/{owner} 0750 ecobin-{owner} ecobin-{owner}-ipc -"
            in TMPFILES
        )
    assert (
        "d /var/lib/ecobin/business/photos 0700 "
        "ecobin-business ecobin-business -"
    ) in TMPFILES
    assert (
        "d /run/ecobin/privileged 0750 root ecobin-privileged-helper-ipc -"
        in TMPFILES
    )
    assert "f /run/ecobin/privileged/mutation.lock 0600 root root -" in TMPFILES
    for directory in ("staging", "mcu-firmware"):
        assert (
            f"d /var/lib/ecobin/updater/{directory} 0700 "
            "ecobin-updater ecobin-updater -"
            in TMPFILES
        )
    assert "d /var/lib/ecobin/privileged 0700 root root -" in TMPFILES
    assert (
        "d /var/lib/ecobin/privileged/business-snapshots 0700 root root -"
        in TMPFILES
    )
    assert "/var/lib/ecobin/updater/snapshots" not in TMPFILES
    assert "d /opt/ecobin/business 0750 root ecobin-business -" in TMPFILES
    assert (
        "d /opt/ecobin/business/releases 0750 root ecobin-business -"
        in TMPFILES
    )


def test_silent_permanent_units_are_local_only_and_independently_sandboxed() -> None:
    communication = _unit("ecobin-communication.service")
    updater = _unit("ecobin-updater.service")

    assert "User=ecobin-communication" in communication
    assert "Group=ecobin-communication-ipc" in communication
    assert "User=ecobin-updater" in updater
    assert "Group=ecobin-updater-ipc" in updater
    assert "communication_agent.py --state /var/lib/ecobin/communication/communication.db" in communication
    assert "updater_agent.py --state /var/lib/ecobin/updater/updater.db" in updater
    assert "--allowed-uid 0" in communication
    assert "--allowed-user ecobin-business" in communication
    assert "--allowed-user ecobin-updater" in communication
    assert "--allowed-uid 0" in updater
    assert "--allowed-user ecobin-communication" in updater
    assert "--allowed-user ecobin-business" in updater
    assert "--socket-group ecobin-communication-ipc" in communication
    assert "--socket-group ecobin-updater-ipc" in updater
    assert "--enable-stage4-candidate" not in updater
    assert "ECOBIN_STAGE4_JOB_GATE_MODE" not in updater
    assert "ecobin-privileged-helper-ipc" not in communication
    assert (
        "SupplementaryGroups=ecobin-communication-ipc "
        "ecobin-business-ipc ecobin-privileged-helper-ipc"
        in updater
    )

    for unit in (communication, updater):
        assert "Type=notify" in unit
        assert "NoNewPrivileges=yes" in unit
        assert "PrivateNetwork=yes" in unit
        assert "PrivateDevices=yes" in unit
        assert "ProtectSystem=strict" in unit
        assert "RestrictAddressFamilies=AF_UNIX" in unit
        assert "IPAddressDeny=any" in unit
        assert "CapabilityBoundingSet=\n" in unit
        assert "DevicePolicy=closed" in unit
        assert "InaccessiblePaths=-/etc/ecobin" in unit
        assert "network-online.target" not in unit
        assert "EnvironmentFile=/etc/ecobin" not in unit
        assert "device-credentials.json" not in unit
        assert "[Install]" not in unit

    assert "StateDirectory=ecobin/communication" in communication
    assert "RuntimeDirectory=ecobin/communication" in communication
    assert "StateDirectory=ecobin/updater" in updater
    assert "RuntimeDirectory=ecobin/updater" in updater
    assert "ReadOnlyPaths=/usr/share/ecobin/runtime-release-keys" in updater
    assert "Requires=ecobin-mcu-safe-gpio.service" in updater
    assert "After=local-fs.target ecobin-mcu-safe-gpio.service" in updater
    assert "ecobin-hardware.service" not in communication + updater


def test_business_permission_preflight_is_a_manual_non_root_hardware_gate() -> None:
    preflight = _unit("ecobin-business-permission-preflight.service")

    assert "User=ecobin-business" in preflight
    assert "Group=ecobin-business" in preflight
    assert "SupplementaryGroups=dialout video" in preflight
    assert "DeviceAllow=/dev/ttyS5 rw" in preflight
    assert "DeviceAllow=char-video4linux rw" in preflight
    assert "PrivateNetwork=yes" in preflight
    assert "RestrictAddressFamilies=AF_UNIX" in preflight
    assert "business_runtime_preflight.py" in preflight
    assert "[Install]" not in preflight


def test_legacy_business_service_exposes_only_read_only_local_status() -> None:
    hardware = _unit("ecobin-hardware.service")

    assert "Environment=ECOBIN_BUSINESS_CONTROL_MODE=status" in hardware
    assert (
        "Environment=ECOBIN_BUSINESS_CONTROL_SOCKET="
        "/run/ecobin/business/control.sock"
    ) in hardware
    assert "ECOBIN_BUSINESS_CONTROL_MODE=candidate" not in hardware
    assert "ECOBIN_STAGE4_JOB_GATE_MODE=candidate" not in hardware


def test_cutover_candidate_units_are_static_mutually_exclusive_and_non_root() -> None:
    communication = _unit("ecobin-communication-proxy.service")
    updater = _unit("ecobin-updater-candidate.service")
    business = _unit("ecobin-business.service")

    assert "--mode proxy-candidate" in communication
    assert "--enable-stage4-candidate" in updater
    assert "--enable-mcu-update-candidate" in updater
    assert "/opt/ecobin/updater/current/.venv/bin/python" in updater
    assert "--mcu-update-state /var/lib/ecobin/updater/mcu-updates.db" in updater
    assert "--mcu-firmware-root /var/lib/ecobin/updater/mcu-firmware" in updater
    assert "--mcu-signing-keys /usr/share/ecobin/mcu-release-keys" in updater
    assert "User=ecobin-communication" in communication
    assert "User=ecobin-updater" in updater
    assert "User=ecobin-business" in business
    assert "User=root" not in communication + updater + business
    assert "Conflicts=ecobin-communication.service ecobin-hardware.service" in communication
    assert "Conflicts=ecobin-updater.service" in updater
    assert "Conflicts=ecobin-hardware.service ecobin-factory-test.service" in business
    assert "ECOBIN_CLOUD_TRANSPORT_MODE=local-proxy" in business
    assert "ECOBIN_STAGE4_JOB_GATE_MODE=candidate" in business
    assert "--posture proxy-candidate" in business
    assert "ECOBIN_DATA_DIR=/var/lib/ecobin/business" in business
    assert "InaccessiblePaths=-/etc/ecobin/device-credentials.json" in business
    assert "InaccessiblePaths=-/var/lib/ecobin/communication" in business
    assert "InaccessiblePaths=-/var/lib/ecobin/updater" in business
    assert "DeviceAllow=/dev/ttyS5 rw" in business
    assert "DeviceAllow=char-video4linux rw" in business
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in communication
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in business
    assert "[Install]" not in communication + updater + business

    runtime_target = (
        HARDWARE / "first_boot/systemd/ecobin-runtime.target"
    ).read_text(encoding="utf-8")
    assert "ecobin-communication-proxy.service" not in runtime_target
    assert "ecobin-updater-candidate.service" not in runtime_target
    assert "ecobin-business.service" not in runtime_target


def test_candidate_root_helpers_are_static_updater_authorized_and_offline() -> None:
    pairs = (
        (
            "ecobin-business-activation-candidate-helper.socket",
            "ecobin-business-activation-candidate-helper@.service",
            "business-activation-candidate.sock",
        ),
        (
            "ecobin-mcu-flash-candidate-helper.socket",
            "ecobin-mcu-flash-candidate-helper@.service",
            "mcu-flash-candidate.sock",
        ),
    )
    for socket_name, service_name, socket_path in pairs:
        socket_unit = (
            HARDWARE / "device_management/helpers/systemd" / socket_name
        ).read_text(encoding="utf-8")
        service_unit = (
            HARDWARE / "device_management/helpers/systemd" / service_name
        ).read_text(encoding="utf-8")

        assert f"ListenStream=/run/ecobin/privileged/{socket_path}" in socket_unit
        assert "Accept=yes" in socket_unit
        assert "SocketGroup=ecobin-privileged-helper-ipc" in socket_unit
        assert "ecobin-updater-candidate.service" in next(
            line for line in socket_unit.splitlines() if line.startswith("Requires=")
        )
        assert "ecobin-updater-candidate.service" in next(
            line for line in socket_unit.splitlines() if line.startswith("After=")
        )
        assert "[Install]" not in socket_unit + service_unit
        assert "User=root" in service_unit
        assert "PrivateNetwork=yes" in service_unit
        assert "RestrictAddressFamilies=AF_UNIX" in service_unit
        assert "IPAddressDeny=any" in service_unit
        assert "ReadOnlyPaths=/run/ecobin/updater" in service_unit
        assert "ReadWritePaths=/run/ecobin/privileged/mutation.lock" in service_unit

    updater = _unit("ecobin-updater-candidate.service")
    assert "Wants=ecobin-business-activation-candidate-helper.socket " in updater
    assert "ecobin-mcu-flash-candidate-helper.socket" in updater


def test_candidate_helper_timeouts_enclose_the_operations_they_supervise() -> None:
    business_runtime = _unit("ecobin-business.service")
    business_helper = (
        HARDWARE
        / "device_management/helpers/systemd/"
        "ecobin-business-activation-candidate-helper@.service"
    ).read_text(encoding="utf-8")
    mcu_helper = (
        HARDWARE
        / "device_management/helpers/systemd/"
        "ecobin-mcu-flash-candidate-helper@.service"
    ).read_text(encoding="utf-8")

    assert "TimeoutStartSec=180" in business_runtime
    assert "TimeoutStartSec=210" in business_helper
    assert "RuntimeMaxSec=210" in business_helper
    assert "TimeoutStartSec=360" in mcu_helper
    assert "RuntimeMaxSec=360" in mcu_helper


def test_device_management_preflight_runs_as_real_updater_and_is_boot_scoped() -> None:
    preflight = _unit("ecobin-device-management-preflight.service")

    assert "Type=oneshot" in preflight
    assert "RemainAfterExit=yes" in preflight
    assert "User=ecobin-updater" in preflight
    assert "Group=ecobin-updater-ipc" in preflight
    assert "SupplementaryGroups=ecobin-privileged-helper-ipc" in preflight
    assert (
        "Requires=ecobin-business-activation-helper.socket "
        "ecobin-mcu-flash-helper.socket"
        in preflight
    )
    assert "device_management_preflight.py" in preflight
    assert "PrivateNetwork=yes" in preflight
    assert "RestrictAddressFamilies=AF_UNIX" in preflight
    assert "CapabilityBoundingSet=\n" in preflight
    assert "[Install]" not in preflight

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


def _unit_directives(unit: str, name: str) -> set[str]:
    prefix = f"{name}="
    return {
        value
        for line in unit.splitlines()
        if line.startswith(prefix)
        for value in line.removeprefix(prefix).split()
    }


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
    assert (
        "d /var/lib/ecobin/privileged/business-runtime-cutover 0700 root root -"
        in TMPFILES
    )
    assert (
        "f /run/ecobin/privileged/business-runtime-cutover.lock 0600 root root -"
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
    assert "--enable-software-state-reporting" not in updater
    assert "--enable-remote-business-update" not in updater
    assert "--enable-updater-event-reporting" not in communication
    assert "--enable-remote-business-update" not in communication
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
    assert "RuntimeDirectoryPreserve=yes" in communication
    assert "StateDirectory=ecobin/updater" in updater
    assert "RuntimeDirectory=ecobin/updater" in updater
    assert "RuntimeDirectoryPreserve=yes" in updater
    assert "ReadOnlyPaths=/usr/share/ecobin/runtime-release-keys" in updater
    assert "Requires=ecobin-mcu-safe-gpio.service" in updater
    assert "After=local-fs.target ecobin-mcu-safe-gpio.service" in updater
    assert "ecobin-hardware.service" not in communication + updater


def test_business_permission_preflight_is_a_manual_non_root_hardware_gate() -> None:
    preflight = _unit("ecobin-business-permission-preflight.service")

    assert "User=ecobin-business" in preflight
    assert "Group=ecobin-business" in preflight
    assert "SupplementaryGroups=dialout video ecobin-factory-web" in preflight
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
    assert "--enable-business-update-candidate" in updater
    assert "--enable-software-state-reporting" not in updater
    assert "--enable-remote-business-update" not in updater
    assert "--enable-updater-event-reporting" not in communication
    assert "--enable-remote-business-update" not in communication
    assert "/opt/ecobin/updater/current/.venv/bin/python" in updater
    assert "--mcu-update-state /var/lib/ecobin/updater/mcu-updates.db" in updater
    assert "--mcu-firmware-root /var/lib/ecobin/updater/mcu-firmware" in updater
    assert "--mcu-signing-keys /usr/share/ecobin/mcu-release-keys" in updater
    assert "--business-signing-keys /usr/share/ecobin/business-release-keys" in updater
    assert "ReadOnlyPaths=/usr/share/ecobin/business-release-keys" in updater
    assert "User=ecobin-communication" in communication
    assert "User=ecobin-updater" in updater
    assert "User=ecobin-business" in business
    assert "SupplementaryGroups=dialout video ecobin-factory-web" in business
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
    assert "ExecStart=/usr/bin/env" in business
    assert "Requires=ecobin-business-runtime-cutover-gate.service" in business
    assert "After=" in business
    assert "ecobin-business-runtime-cutover-gate.service" in business.split(
        "After=", 1
    )[1].splitlines()[0]
    for assignment in (
        "ECOBIN_CONFIG_MODE=production",
        "ECOBIN_CLOUD_TRANSPORT_MODE=local-proxy",
        "ECOBIN_BUSINESS_IDENTITY_PATH=/var/lib/ecobin/business/device-identity.json",
        "ECOBIN_DEVICE_CAPABILITIES_PATH=/var/lib/ecobin/device-capabilities.json",
        "ECOBIN_STAGE4_JOB_GATE_MODE=candidate",
        "ECOBIN_BUSINESS_CONTROL_MODE=candidate",
        "ECOBIN_BUSINESS_CONTROL_SOCKET=/run/ecobin/business/control.sock",
        "ECOBIN_COMMUNICATION_SOCKET=/run/ecobin/communication/control.sock",
        "ECOBIN_UPDATER_CONTROL_SOCKET=/run/ecobin/updater/control.sock",
        "ECOBIN_DATA_DIR=/var/lib/ecobin/business",
        "ECOBIN_EDGE_STORE_PATH=/var/lib/ecobin/business/edge.db",
        "ECOBIN_EDGE_BOOT_ID_PATH=/var/lib/ecobin/business/edge-boot-id",
        "ECOBIN_DEVICE_CONFIG_PATH=/var/lib/ecobin/business/device-config.json",
        "ECOBIN_MCU_FIRMWARE_CACHE_DIR=/var/lib/ecobin/business/mcu-firmware",
    ):
        assert assignment in business
        assert f"Environment={assignment}" not in business
    for unit in (communication, updater, business):
        assert "business-runtime-cutover/active.json" in unit
        assert "business-runtime-cutover/pending.json" in unit
        assert "ecobin-business-runtime.target" in unit
    assert "RuntimeDirectoryPreserve=yes" in communication
    assert "RuntimeDirectoryPreserve=yes" in updater

    runtime_target = (
        HARDWARE / "first_boot/systemd/ecobin-runtime.target"
    ).read_text(encoding="utf-8")
    assert "ecobin-communication-proxy.service" not in runtime_target
    assert "ecobin-updater-candidate.service" not in runtime_target
    assert "ecobin-business.service" not in runtime_target
    assert "ecobin-business-updatable-candidate.service" not in runtime_target
    assert "ecobin-hardware.service" not in runtime_target
    assert "ecobin-communication.service" not in runtime_target
    assert "ecobin-updater.service" not in runtime_target


def test_managed_runtime_target_is_selected_only_by_valid_cutover_gate() -> None:
    target = _unit("ecobin-business-runtime.target")
    gate = _unit("ecobin-business-runtime-cutover-gate.service")
    bridge = _unit("ecobin-business.service")
    replaceable = _unit("ecobin-business-updatable-candidate.service")

    assert "business-runtime-cutover/active.json" in target
    assert "business-runtime-cutover/pending.json" in target
    assert "Requires=ecobin-business-runtime-cutover-gate.service" in target
    assert "ecobin-updater-candidate.service" in target
    assert "ecobin-communication-proxy.service" in target
    assert "Wants=ecobin-business.service" in target
    assert "ecobin-business-updatable-candidate.service" in target
    assert "[Install]" not in target

    # Both candidates must be submitted in the same boot transaction so their
    # inverse release-pointer conditions can select exactly one.  A direct
    # Conflicts= edge makes systemd discard one job before it evaluates those
    # conditions, which can leave neither business runtime running.
    assert "ConditionPathExists=!/opt/ecobin/business/current/release.env" in bridge
    assert "ConditionPathExists=/opt/ecobin/business/current/release.env" in replaceable
    assert "ecobin-business-updatable-candidate.service" not in _unit_directives(
        bridge, "Conflicts"
    )
    assert "ecobin-business.service" not in _unit_directives(
        replaceable, "Conflicts"
    )

    assert "Type=oneshot" in gate
    assert "RemainAfterExit=yes" in gate
    assert "User=root" in gate
    assert "business_runtime_cutover.py verify-active" in gate
    assert "PrivateNetwork=yes" in gate
    assert "ProtectSystem=strict" in gate
    assert "CapabilityBoundingSet=CAP_DAC_READ_SEARCH" in gate
    assert "CAP_DAC_OVERRIDE" not in gate
    assert "CAP_FOWNER" not in gate
    assert "InaccessiblePaths=-/etc/ecobin" in gate
    assert "ReadOnlyPaths=/var/lib/ecobin/business" in gate
    assert "[Install]" not in gate


def test_legacy_runtime_units_are_fenced_after_cutover() -> None:
    for name in (
        "ecobin-hardware.service",
        "ecobin-communication.service",
        "ecobin-updater.service",
    ):
        unit = _unit(name)
        assert "ConditionPathExists=!/var/lib/ecobin/privileged/business-runtime-cutover/pending.json" in unit
        assert "ConditionPathExists=!/var/lib/ecobin/privileged/business-runtime-cutover/active.json" in unit
    helper_root = HARDWARE / "device_management/helpers/systemd"
    for name in (
        "ecobin-business-activation-helper.socket",
        "ecobin-business-activation-helper@.service",
        "ecobin-mcu-flash-helper.socket",
        "ecobin-mcu-flash-helper@.service",
    ):
        unit = (helper_root / name).read_text(encoding="utf-8")
        assert "business-runtime-cutover/pending.json" in unit
        assert "business-runtime-cutover/active.json" in unit


def test_replaceable_business_service_is_static_and_power_loss_fenced() -> None:
    business = _unit("ecobin-business-updatable-candidate.service")

    assert "User=ecobin-business" in business
    assert "SupplementaryGroups=dialout video ecobin-factory-web" in business
    assert "WorkingDirectory=/opt/ecobin/business/current/app" in business
    assert "EnvironmentFile=/opt/ecobin/business/current/release.env" in business
    assert "ExecStart=/usr/bin/env" in business
    assert "/opt/ecobin/business/current/.venv/bin/python" in business
    assert "Requires=ecobin-business-runtime-cutover-gate.service" in business
    assert "ecobin-business-runtime-cutover-gate.service" in business.split(
        "After=", 1
    )[1].splitlines()[0]
    assert "business-snapshots/.restore-in-progress.json" in business
    assert "Conflicts=ecobin-hardware.service ecobin-factory-test.service" in business
    assert "ecobin-business.service" not in _unit_directives(business, "Conflicts")
    assert "ECOBIN_CLOUD_TRANSPORT_MODE=local-proxy" in business
    assert "[Install]" not in business


def test_candidate_root_helpers_are_static_updater_authorized_and_offline() -> None:
    pairs = (
        (
            "ecobin-business-activation-candidate-helper.socket",
            "ecobin-business-activation-candidate-helper@.service",
            "business-activation-candidate.sock",
        ),
        (
            "ecobin-business-release-activation-candidate-helper.socket",
            "ecobin-business-release-activation-candidate-helper@.service",
            "business-release-activation-candidate.sock",
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
        for unit in (socket_unit, service_unit):
            assert "business-runtime-cutover/active.json" in unit
            assert "business-runtime-cutover/pending.json" in unit
            assert "PartOf=ecobin-business-runtime.target" in unit

    updater = _unit("ecobin-updater-candidate.service")
    assert "Wants=ecobin-business-activation-candidate-helper.socket " in updater
    assert "ecobin-business-release-activation-candidate-helper.socket" in updater
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

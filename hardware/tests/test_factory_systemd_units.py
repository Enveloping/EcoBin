from __future__ import annotations

from pathlib import Path


SYSTEMD_DIRECTORY = Path(__file__).parents[1] / "factory" / "systemd"
FACTORY_DIRECTORY = SYSTEMD_DIRECTORY.parent


def _unit(name: str) -> str:
    return (SYSTEMD_DIRECTORY / name).read_text(encoding="utf-8")


def test_egress_lock_is_unconditional_and_the_only_early_enabled_unit() -> None:
    egress = _unit("ecobin-factory-egress-lock.service")

    assert "DefaultDependencies=no" in egress
    assert "Before=network-pre.target network.target" in egress
    assert "NetworkManager.service" in egress
    assert "-m factory.firewall" in egress
    assert "WantedBy=sysinit.target" in egress
    assert "RequiredBy=network-pre.target" in egress
    assert "ConditionPathExists" not in egress
    assert "InaccessiblePaths=-/etc/ecobin" in egress
    assert "InaccessiblePaths=-/var/lib/ecobin" in egress

    for path in SYSTEMD_DIRECTORY.iterdir():
        if path.name != "ecobin-factory-egress-lock.service":
            assert "[Install]" not in path.read_text(encoding="utf-8")


def test_no_factory_unit_turns_a_sealed_start_into_a_successful_skip() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in SYSTEMD_DIRECTORY.iterdir()
    )

    assert "ConditionPathExists" not in combined
    assert "sealed.json" not in combined


def test_ap_daemons_are_split_from_short_lived_root_preparation() -> None:
    prepare = _unit("ecobin-factory-ap-prepare.service")
    hostapd = _unit("ecobin-factory-hostapd.service")
    dnsmasq = _unit("ecobin-factory-dnsmasq.service")
    coordinator = _unit("ecobin-factory-ap.service")

    assert "Type=oneshot" in prepare
    assert "User=root" in prepare
    assert "CapabilityBoundingSet=CAP_NET_ADMIN" in prepare
    assert "RemainAfterExit=yes" in prepare
    assert "RuntimeDirectory=ecobin/factory-network" in prepare
    assert "-m factory.ap_supervisor prepare" in prepare
    assert "-m factory.ap_supervisor cleanup" in prepare
    assert "hostapd.service" in prepare
    assert "dnsmasq.service" in prepare
    assert "wpa_supplicant@wlan0.service" in prepare

    assert "User=ecobin-factory-ap" in hostapd
    assert "ExecStart=/usr/sbin/hostapd " in hostapd
    assert "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW" in hostapd
    assert "CAP_SETUID" not in hostapd
    assert "CAP_SETGID" not in hostapd

    assert "User=ecobin-factory-dns" in dnsmasq
    assert "--keep-in-foreground" in dnsmasq
    assert "--pid-file=" in dnsmasq
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n" in dnsmasq
    assert "CAP_NET_ADMIN" not in dnsmasq
    assert "CAP_NET_RAW" not in dnsmasq
    assert "CAP_SETUID" not in dnsmasq
    assert "CAP_SETGID" not in dnsmasq
    assert "SocketBindAllow=ipv4:tcp:53" in dnsmasq
    assert "SocketBindAllow=ipv4:udp:53" in dnsmasq
    assert "SocketBindAllow=ipv4:udp:67" in dnsmasq
    assert "ReadOnlyPaths=/run/ecobin/factory-network/dnsmasq" in dnsmasq
    assert "ReadWritePaths=/run/ecobin/factory-network/dnsmasq-state" in dnsmasq

    assert "Type=notify" in coordinator
    assert "User=ecobin-factory-web" in coordinator
    assert "Requires=ecobin-factory-hostapd.service ecobin-factory-dnsmasq.service" in coordinator
    assert "-m factory.ap_supervisor monitor" in coordinator
    assert "WatchdogSec=10" in coordinator
    assert "CapabilityBoundingSet=\n" in coordinator
    assert "InaccessiblePaths=-/run/ecobin/factory-network/dnsmasq-state" in coordinator


def test_factory_accounts_and_runtime_directories_are_declared() -> None:
    sysusers = (
        FACTORY_DIRECTORY / "config" / "sysusers.d" / "ecobin-factory.conf"
    ).read_text(encoding="utf-8")
    tmpfiles = (
        FACTORY_DIRECTORY / "config" / "tmpfiles.d" / "ecobin-factory.conf"
    ).read_text(encoding="utf-8")

    assert "u ecobin-factory-web" in sysusers
    assert "u ecobin-factory-ap" in sysusers
    assert "u ecobin-factory-dns" in sysusers
    assert "/usr/sbin/nologin" in sysusers
    assert "d /run/ecobin/factory-portal 0750 root ecobin-factory-web" in tmpfiles
    assert "f /run/ecobin/factory-portal/status.json 0640 root ecobin-factory-web" in tmpfiles
    assert "d /run/ecobin/factory-test 0750 root ecobin-factory-web" in tmpfiles
    assert "d /run/ecobin/factory-test/photos 0750 root ecobin-factory-web" in tmpfiles
    assert "f /run/ecobin/factory-portal/acceptance.json 0640 root ecobin-factory-web" in tmpfiles
    assert "d /var/lib/ecobin/factory-test 0700 root root" in tmpfiles
    assert "d /run/lock/ecobin 0700 root root" in tmpfiles
    assert "d /run/ecobin/factory-network 0755 root root" in tmpfiles
    assert "f /run/ecobin/factory-network/ap-allowed.json 0640 root ecobin-factory-web" in tmpfiles


def test_ap_stages_require_lock_and_safe_gpio_without_shell_or_secret_args() -> None:
    prepare = _unit("ecobin-factory-ap-prepare.service")
    coordinator = _unit("ecobin-factory-ap.service")

    assert "Requires=ecobin-factory-egress-lock.service ecobin-mcu-safe-gpio.service" in prepare
    assert "Requires=ecobin-factory-egress-lock.service ecobin-mcu-safe-gpio.service" in coordinator
    assert "PartOf=ecobin-factory.target" in prepare
    assert "PartOf=ecobin-factory.target" in coordinator
    assert "Conflicts=ecobin-hardware.service" not in coordinator
    assert "/bin/sh" not in prepare + coordinator
    assert "--password" not in prepare + coordinator
    assert "InaccessiblePaths=-/etc/ecobin/enrollment.key" in prepare
    assert "InaccessiblePaths=-/etc/ecobin/device-credentials.json" in prepare
    assert "InaccessiblePaths=-/var/lib/ecobin/hardware" in prepare
    assert (
        "BindReadOnlyPaths=/var/lib/ecobin/hardware:"
        "/run/ecobin/factory-network/edge-store"
    ) in prepare
    assert "InaccessiblePaths=-/var/lib/ecobin" in coordinator
    assert (
        "ReadOnlyPaths=/run/ecobin/factory-network/ap-allowed.json"
        in coordinator
    )
    assert "InaccessiblePaths=-/run/ecobin/factory-network/edge-store" in coordinator


def test_portal_is_low_privilege_local_only_and_cannot_read_secrets() -> None:
    portal = _unit("ecobin-factory-portal.service")

    assert "User=ecobin-factory-web" in portal
    assert "PartOf=ecobin-factory.target" in portal
    assert "Requires=ecobin-factory-ap.service" in portal
    assert "NoNewPrivileges=yes" in portal
    assert "PrivateDevices=yes" in portal
    assert "ProtectSystem=strict" in portal
    assert "IPAddressDeny=any" in portal
    assert "IPAddressAllow=10.42.0.0/24" in portal
    assert "RestrictAddressFamilies=AF_INET" in portal
    assert "SocketBindDeny=any" in portal
    assert "SocketBindAllow=ipv4:tcp:80" in portal
    assert "InaccessiblePaths=-/etc/ecobin/enrollment.key" in portal
    assert "InaccessiblePaths=-/etc/ecobin/setup-ap.key" in portal
    assert "InaccessiblePaths=-/etc/ecobin/device-credentials.json" in portal
    assert "InaccessiblePaths=-/var/lib/ecobin" in portal
    assert "InaccessiblePaths=-/run/ecobin/factory-network" in portal
    assert "EnvironmentFile=" not in portal
    assert "0.0.0.0" not in portal


def test_factory_target_keeps_ap_and_portal_while_only_executor_conflicts_runtime() -> None:
    target = _unit("ecobin-factory.target")

    assert "Requires=ecobin-factory-egress-lock.service" in target
    assert "Requires=ecobin-factory-ap.service" in target
    assert "Requires=ecobin-factory-portal.service" in target
    assert "Wants=ecobin-factory-test.service" in target
    assert "Conflicts=" not in target


def test_portal_can_reach_only_the_local_executor_socket_and_public_projection() -> None:
    portal = _unit("ecobin-factory-portal.service")

    assert "RestrictAddressFamilies=AF_INET AF_UNIX" in portal
    assert "ReadOnlyPaths=-/run/ecobin/factory-test" in portal
    assert "ReadWritePaths=/run/ecobin/factory-test" not in portal
    assert "edge.db" not in portal


def test_factory_hardware_executor_has_narrow_devices_and_no_network() -> None:
    executor = (
        FACTORY_DIRECTORY.parent
        / "first_boot"
        / "systemd"
        / "ecobin-factory-test.service"
    ).read_text(encoding="utf-8")

    assert "User=root" in executor
    assert "Group=ecobin-factory-web" in executor
    assert "-m factory.acceptance_service" in executor
    assert "PartOf=ecobin-factory.target" in executor
    assert "Conflicts=ecobin-cellular-uplink.service" in executor
    assert "DevicePolicy=closed" in executor
    assert "DeviceAllow=/dev/ttyS5 rw" in executor
    assert "DeviceAllow=/dev/gpiomem rw" in executor
    assert "DeviceAllow=/dev/mem rw" in executor
    assert "DeviceAllow=char-video4linux rw" in executor
    assert "TasksMax=16" in executor
    assert "MemoryMax=192M" in executor
    assert "RestrictAddressFamilies=AF_UNIX" in executor
    assert "IPAddressDeny=any" in executor


def test_network_manager_never_claims_factory_wlan_as_a_station() -> None:
    network_manager = (
        FACTORY_DIRECTORY
        / "config"
        / "NetworkManager"
        / "conf.d"
        / "90-ecobin-factory-wlan.conf"
    ).read_text(encoding="utf-8")

    assert "[main]" in network_manager
    assert "no-auto-default=*" in network_manager
    assert "[keyfile]" in network_manager
    assert "unmanaged-devices=interface-name:wlan0" in network_manager
    assert "ssid=" not in network_manager.lower()

from __future__ import annotations

from pathlib import Path


SYSTEMD = Path(__file__).parents[1] / "first_boot" / "systemd"


def _read(relative: str) -> str:
    return (SYSTEMD / relative).read_text(encoding="utf-8")


def test_only_first_boot_coordinator_is_installable_in_p5_units() -> None:
    installable = []
    for path in SYSTEMD.rglob("*"):
        if path.is_file() and "[Install]" in path.read_text(encoding="utf-8"):
            installable.append(path.relative_to(SYSTEMD).as_posix())

    assert installable == ["ecobin-first-boot.service"]


def test_first_boot_requires_immutable_gpio_and_early_egress_lock() -> None:
    unit = _read("ecobin-first-boot.service")

    assert "Requires=ecobin-expand-rootfs.service ecobin-mcu-safe-gpio.service ecobin-edge-store-prepare.service ecobin-factory-egress-lock.service" in unit
    assert "After=local-fs.target ecobin-expand-rootfs.service ecobin-mcu-safe-gpio.service ecobin-edge-store-prepare.service ecobin-factory-egress-lock.service" in unit
    assert "Before=network-pre.target ecobin-enrollment.service ecobin-remote-support.service ecobin-hardware.service" in unit
    assert "RequiredBy=network-pre.target" in unit
    assert "-m first_boot.orchestrator" in unit
    assert "ReadWritePaths=/var/lib/ecobin/first-boot" in unit
    assert "ReadWritePaths=/run/ecobin/factory-portal" in unit
    assert "ReadWritePaths=/run/ecobin/factory-network" in unit
    assert "LimitCORE=0" in unit
    assert "/bin/sh" not in unit


def test_edge_store_is_migrated_offline_before_seal_facts_are_read() -> None:
    unit = _read("ecobin-edge-store-prepare.service")

    assert "Before=ecobin-first-boot.service network-pre.target" in unit
    assert "PrivateNetwork=yes" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "edge_store_prepare.py" in unit
    assert "EnvironmentFile=/etc/ecobin/hardware.env" in unit
    assert "ReadWritePaths=/var/lib/ecobin/hardware" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=2" in unit
    assert "StartLimitIntervalSec=0" in unit
    assert "[Install]" not in unit


def test_every_phase_unit_is_static_and_rechecks_a_fact_gate() -> None:
    expected = {
        "ecobin-cellular-uplink.service": "--require factory-test-passed",
        "ecobin-factory-test.service": "--require factory-test",
        "ecobin-factory-handoff.service": "--require handoff",
        "ecobin-runtime-gate.service": "--require runtime",
    }
    for name, gate in expected.items():
        unit = _read(name)
        assert "[Install]" not in unit
        assert gate in unit
    assert "[Install]" not in _read("ecobin-runtime.target")


def test_cellular_uplink_can_update_the_seal_aware_firewall_lock() -> None:
    unit_lines = _read("ecobin-cellular-uplink.service").splitlines()

    assert "ReadWritePaths=/run/lock/ecobin" in unit_lines


def test_first_boot_can_finish_all_seal_firewall_and_artifact_cleanup() -> None:
    unit_lines = _read("ecobin-first-boot.service").splitlines()

    assert "ReadWritePaths=/run/lock/ecobin" in unit_lines
    assert "ReadWritePaths=/etc/ecobin" in unit_lines
    assert "ReadWritePaths=/var/lib/ecobin" in unit_lines
    assert "ReadWritePaths=/opt/ecobin/enrollment" in unit_lines
    assert "ReadWritePaths=-/etc/ecobin/setup-ap.key" not in unit_lines


def test_factory_and_runtime_owners_are_mutually_exclusive() -> None:
    factory_test = _read("ecobin-factory-test.service")
    runtime = _read("ecobin-runtime.target")
    factory_dropin = _read("ecobin-factory.target.d/20-first-boot-conflicts.conf")

    assert "Conflicts=ecobin-cellular-uplink.service ecobin-enrollment.service ecobin-remote-support.service ecobin-hardware.service ecobin-runtime.target" in factory_test
    assert "Conflicts=ecobin-factory-test.service" in runtime
    assert "ecobin-factory.target" not in runtime
    assert "Conflicts=" not in factory_dropin
    assert "intentionally coexist" in factory_dropin


def test_existing_production_units_receive_non_persistent_fact_gates() -> None:
    enrollment = _read("ecobin-enrollment.service.d/20-first-boot-gate.conf")
    hardware = _read("ecobin-hardware.service.d/20-first-boot-gate.conf")
    support = _read("ecobin-remote-support.service.d/20-first-boot-gate.conf")

    assert "--require enrollment" in enrollment
    assert "Conflicts=ecobin-factory-test.service" in enrollment
    assert "--require runtime" in hardware
    assert "--require runtime" in support
    assert "Requires=ecobin-runtime-gate.service" in hardware
    assert "Requires=ecobin-runtime-gate.service" in support


def test_p7_real_executor_and_handoff_are_static_fail_closed_units() -> None:
    executor = _read("ecobin-factory-test.service")
    handoff = _read("ecobin-factory-handoff.service")

    assert "ExecStart=/usr/bin/false" not in executor + handoff
    assert "-m factory.acceptance_service" in executor
    assert "-m factory.acceptance_handoff" in handoff
    assert "Restart=no" in executor
    assert "RestrictAddressFamilies=AF_UNIX" in executor
    assert "RestrictAddressFamilies=AF_UNIX" in handoff
    for unit in (executor, handoff):
        assert "DevicePolicy=closed" in unit
        assert "DeviceAllow=/dev/ttyS5 rw" in unit
        assert "DeviceAllow=/dev/gpiomem rw" in unit
        assert "DeviceAllow=/dev/mem rw" in unit
    assert "DeviceAllow=char-video4linux rw" in executor
    assert "DeviceAllow=char-video4linux rw" not in handoff


def test_factory_acceptance_gate_can_read_the_current_boot_id() -> None:
    executor = _read("ecobin-factory-test.service")

    assert "ProtectProc=invisible" in executor
    # first_boot.gate validates the current-boot GPIO safety fact against
    # /proc/sys/kernel/random/boot_id. ProcSubset=pid hides that kernel path
    # and makes systemd skip the executor even though the same gate succeeds
    # outside the service sandbox.
    assert "ProcSubset=all" in executor
    assert "ProcSubset=pid" not in executor

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from first_boot.model import FactoryTestStatus, FirstBootFacts
from first_boot.time_sync import TimeSyncOutcome, TimeSyncResult
import first_boot.cellular_service as cellular_service


def _accepted(**overrides: object) -> FirstBootFacts:
    values: dict[str, object] = {
        "system_prepared": True,
        "factory_portal_ready": True,
        "factory_test_status": FactoryTestStatus.PASSED,
        "factory_report_valid": True,
        "factory_recovery_required": False,
    }
    values.update(overrides)
    return FirstBootFacts(**values)


def _install_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    facts: FirstBootFacts,
    firewall_modes: list[str],
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "SystemFactsProvider",
        lambda: SimpleNamespace(collect=lambda: facts),
    )
    config = SimpleNamespace(connection_id="ecobin-air780e-rndis")
    device = SimpleNamespace(interface="enxcell0")
    inventory = SimpleNamespace(devices=lambda: (device,))
    monkeypatch.setattr(cellular_service, "load_cellular_config", lambda: config)
    monkeypatch.setattr(
        cellular_service, "SysfsUsbNetworkInventory", lambda: inventory
    )
    monkeypatch.setattr(
        cellular_service,
        "select_rndis_device",
        lambda _config, _devices: (device, "NONE"),
    )
    monkeypatch.setattr(
        cellular_service,
        "render_network_manager_profile",
        lambda _config, _device: "[connection]\n",
    )
    monkeypatch.setattr(
        cellular_service, "install_network_manager_profile", lambda *_args: None
    )
    monkeypatch.setattr(
        cellular_service,
        "NetworkManagerActivator",
        lambda: SimpleNamespace(activate=lambda **_kwargs: True),
    )
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=True,
                error_code="NONE",
                dns_ready=True,
            )
        ),
    )
    monkeypatch.setattr(
        cellular_service,
        "ChronyTimeSynchronizer",
        lambda: SimpleNamespace(
            synchronize=lambda: TimeSyncOutcome(
                TimeSyncResult.SYNCED,
                "NONE",
            )
        ),
        raising=False,
    )

    def apply(interface: str, inspector) -> str:
        fact = inspector()
        mode = "PRODUCTION" if fact.valid else "FACTORY"
        firewall_modes.append(f"{interface}:{mode}")
        return mode

    monkeypatch.setattr(
        cellular_service, "apply_seal_aware_uplink_gate", apply
    )


def test_invalid_seal_immediately_restores_emergency_before_any_fact_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=True, valid=False),
    )
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )
    monkeypatch.setattr(
        cellular_service,
        "SystemFactsProvider",
        lambda: pytest.fail("facts/network must not run for an invalid seal"),
    )

    result = cellular_service.run_once()

    assert result == "SEALED_FACT_INVALID"
    assert emergency == [True]


def test_seal_written_during_loop_is_rechecked_under_firewall_lock_and_uses_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = iter(
        (
            SimpleNamespace(exists=False, valid=False),
            SimpleNamespace(exists=True, valid=True),
        )
    )
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: next(observations),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(), modes)
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    result = cellular_service.run_once()

    assert result == "NONE"
    assert modes == ["enxcell0:PRODUCTION"]
    assert emergency == []


def test_unsealed_loop_keeps_local_factory_profile_until_p8_seals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(), modes)
    monkeypatch.setattr(
        cellular_service, "apply_emergency_uplink_lock", lambda: True
    )

    assert cellular_service.run_once() == "NONE"

    assert modes == ["enxcell0:FACTORY"]


def test_lost_p7_gate_replaces_any_old_uplink_profile_with_emergency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    monkeypatch.setattr(
        cellular_service,
        "SystemFactsProvider",
        lambda: SimpleNamespace(collect=lambda: FirstBootFacts()),
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    result = cellular_service.run_once()

    assert result == "FACTORY_TEST_GATE_CLOSED"
    assert emergency == [True]


def test_dns_ready_untrusted_clock_is_synchronised_before_https_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=False), modes)
    health = iter(
        (
            SimpleNamespace(
                ready=False,
                error_code="CELLULAR_HTTPS_UNAVAILABLE",
                dns_ready=True,
            ),
            SimpleNamespace(ready=True, error_code="NONE", dns_ready=True),
        )
    )
    probe_calls: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: probe_calls.append(True) or next(health)
        ),
    )
    sync_calls: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "ChronyTimeSynchronizer",
        lambda: SimpleNamespace(
            synchronize=lambda: sync_calls.append(True)
            or TimeSyncOutcome(TimeSyncResult.SYNCED, "NONE")
        ),
        raising=False,
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert cellular_service.run_once() == "NONE"
    assert sync_calls == [True]
    assert probe_calls == [True, True]
    assert emergency == []


def test_failed_clock_synchronisation_restores_emergency_uplink_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=False), modes)
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=False,
                error_code="CELLULAR_HTTPS_UNAVAILABLE",
                dns_ready=True,
            )
        ),
    )
    monkeypatch.setattr(
        cellular_service,
        "ChronyTimeSynchronizer",
        lambda: SimpleNamespace(
            synchronize=lambda: TimeSyncOutcome(
                TimeSyncResult.FAILED,
                "CHRONY_ONLINE_FAILED",
            )
        ),
        raising=False,
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert cellular_service.run_once() == "CHRONY_ONLINE_FAILED"
    assert emergency == [True]


def test_pending_clock_sync_keeps_restricted_uplink_open_for_chronyd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An asynchronous chrony burst must retain its NTP egress window."""

    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=False), modes)
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=False,
                error_code="CELLULAR_HTTPS_UNAVAILABLE",
                dns_ready=True,
            )
        ),
    )
    monkeypatch.setattr(
        cellular_service,
        "ChronyTimeSynchronizer",
        lambda: SimpleNamespace(
            synchronize=lambda: TimeSyncOutcome(
                TimeSyncResult.PENDING,
                "TIME_SYNC_PENDING",
            )
        ),
        raising=False,
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert cellular_service.run_once() == "TIME_SYNC_PENDING"
    assert modes == ["enxcell0:FACTORY"]
    assert emergency == []


def test_dns_failure_does_not_attempt_clock_synchronisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=False), modes)
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=False,
                error_code="CELLULAR_DNS_UNAVAILABLE",
                dns_ready=False,
            )
        ),
    )
    monkeypatch.setattr(
        cellular_service,
        "ChronyTimeSynchronizer",
        lambda: pytest.fail("chrony must not run before bound DNS passes"),
        raising=False,
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert cellular_service.run_once() == "CELLULAR_DNS_UNAVAILABLE"
    assert emergency == [True]


def test_result_reporter_persists_every_cycle_but_logs_only_transitions(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    reporter = cellular_service.CellularResultReporter(
        path=tmp_path / "cellular-uplink" / "status.json",
        emit=messages.append,
    )

    reporter.report("CHRONY_ONLINE_FAILED")
    reporter.report("CHRONY_ONLINE_FAILED")
    reporter.report("NONE")

    assert reporter.current_result() == "NONE"
    assert messages == [
        "ecobin-cellular-uplink result=CHRONY_ONLINE_FAILED statusProjection=OK",
        "ecobin-cellular-uplink result=NONE statusProjection=OK",
    ]

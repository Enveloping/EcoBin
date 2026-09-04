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


@pytest.mark.parametrize(
    ("error_code", "dns_ready", "diagnostic_code", "elapsed_ms"),
    (
        (
            "CELLULAR_DNS_UNAVAILABLE",
            False,
            "RESOLVECTL_EXIT_1",
            37,
        ),
        (
            "CELLULAR_HTTPS_UNAVAILABLE",
            True,
            "CURL_EXIT_28",
            10_004,
        ),
    ),
)
def test_live_connectivity_failure_keeps_verified_production_firewall(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    dns_ready: bool,
    diagnostic_code: str,
    elapsed_ms: int,
) -> None:
    """A liveness miss must not replace an already-verified narrow policy."""

    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=True, valid=True),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=True), modes)
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=False,
                error_code=error_code,
                dns_ready=dns_ready,
                diagnostic_code=diagnostic_code,
                diagnostic_elapsed_ms=elapsed_ms,
            )
        ),
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )
    diagnostics: list[tuple[str, str | None, int | None]] = []

    assert [
        cellular_service.run_once(
            probe_diagnostic=lambda *values: diagnostics.append(values)
        )
        for _ in range(3)
    ] == [error_code] * 3
    assert modes == ["enxcell0:PRODUCTION"] * 3
    assert emergency == []
    assert diagnostics == [(error_code, diagnostic_code, elapsed_ms)] * 3


def test_https_failure_still_fails_closed_before_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=False, valid=False),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=True), modes)
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
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert cellular_service.run_once() == "CELLULAR_HTTPS_UNAVAILABLE"
    assert modes == ["enxcell0:FACTORY"]
    assert emergency == [True]


def test_non_liveness_probe_failure_still_fails_closed_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cellular_service,
        "inspect_sealed_authorization",
        lambda _paths: SimpleNamespace(exists=True, valid=True),
    )
    modes: list[str] = []
    _install_happy_path(monkeypatch, _accepted(time_trusted=True), modes)
    monkeypatch.setattr(
        cellular_service,
        "CellularProbe",
        lambda _config, _inventory: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                ready=False,
                error_code="CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE",
                dns_ready=False,
            )
        ),
    )
    emergency: list[bool] = []
    monkeypatch.setattr(
        cellular_service,
        "apply_emergency_uplink_lock",
        lambda: emergency.append(True) or True,
    )

    assert (
        cellular_service.run_once()
        == "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE"
    )
    assert modes == ["enxcell0:PRODUCTION"]
    assert emergency == [True]


def test_result_reporter_persists_every_cycle_but_logs_only_transitions(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    reporter = cellular_service.CellularResultReporter(
        path=tmp_path / "cellular-uplink" / "status.json",
        emit=messages.append,
        monotonic=iter((100.0, 101.0, 102.0)).__next__,
    )

    reporter.report("CHRONY_ONLINE_FAILED", retry_after_seconds=15.0)
    reporter.report("CHRONY_ONLINE_FAILED", retry_after_seconds=15.0)
    retrying = reporter.current_status()
    assert retrying is not None
    assert retrying.consecutive_failure_count == 2
    assert retrying.next_retry_at_monotonic_ms == 116_000
    reporter.report("NONE")

    assert reporter.current_result() == "NONE"
    completed = reporter.current_status()
    assert completed is not None
    assert completed.consecutive_failure_count == 0
    assert completed.next_retry_at_monotonic_ms is None
    assert messages == [
        "ecobin-cellular-uplink result=CHRONY_ONLINE_FAILED statusProjection=OK",
        "ecobin-cellular-uplink result=NONE statusProjection=OK",
    ]


def test_probe_diagnostic_reporter_logs_safe_details_only_on_transitions() -> None:
    messages: list[str] = []
    reporter = cellular_service.CellularProbeDiagnosticReporter(
        emit=messages.append
    )

    reporter.report("CELLULAR_HTTPS_UNAVAILABLE", "CURL_EXIT_28", 10_004)
    reporter.report("CELLULAR_HTTPS_UNAVAILABLE", "CURL_EXIT_28", 9_500)
    reporter.report("NONE", None, None)
    reporter.report("CELLULAR_HTTPS_UNAVAILABLE", "CURL_EXIT_28", 8_000)

    assert messages == [
        (
            "ecobin-cellular-probe result=CELLULAR_HTTPS_UNAVAILABLE "
            "diagnostic=CURL_EXIT_28 elapsedMs=10004"
        ),
        (
            "ecobin-cellular-probe result=CELLULAR_HTTPS_UNAVAILABLE "
            "diagnostic=CURL_EXIT_28 elapsedMs=8000"
        ),
    ]


def test_main_once_wires_probe_diagnostic_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics: list[tuple[str, str | None, int | None]] = []
    results: list[str] = []

    class DiagnosticReporter:
        def report(
            self,
            result_code: str,
            diagnostic_code: str | None,
            elapsed_ms: int | None,
        ) -> None:
            diagnostics.append((result_code, diagnostic_code, elapsed_ms))

    class ResultReporter:
        def report(self, result_code: str) -> None:
            results.append(result_code)

    def run_once(
        *,
        probe_diagnostic: Callable[[str, str | None, int | None], None] | None,
    ) -> str:
        assert probe_diagnostic is not None
        probe_diagnostic("CELLULAR_HTTPS_UNAVAILABLE", "CURL_TIMEOUT", 12_000)
        return "CELLULAR_HTTPS_UNAVAILABLE"

    monkeypatch.setattr(
        cellular_service,
        "CellularProbeDiagnosticReporter",
        DiagnosticReporter,
    )
    monkeypatch.setattr(cellular_service, "CellularResultReporter", ResultReporter)
    monkeypatch.setattr(cellular_service, "run_once", run_once)

    assert cellular_service.main(["--once"]) == 1
    assert diagnostics == [
        ("CELLULAR_HTTPS_UNAVAILABLE", "CURL_TIMEOUT", 12_000)
    ]
    assert results == ["CELLULAR_HTTPS_UNAVAILABLE"]

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import time
from typing import Callable, Sequence

from .cellular_config import CellularConfigurationError, load_cellular_config
from .cellular_firewall import (
    apply_emergency_uplink_lock,
    apply_seal_aware_uplink_gate,
)
from .cellular_modem import ModemRegistrationProbe
from .cellular_probe import (
    CellularProbe,
    SysfsUsbNetworkInventory,
    select_rndis_device,
)
from .cellular_status import CellularStatus, CellularStatusStore
from .facts import SystemFactsProvider
from .network_manager import (
    NetworkManagerActivator,
    install_network_manager_profile,
    render_network_manager_profile,
)
from .state_machine import gate_allows
from .time_sync import ChronyTimeSynchronizer, TimeSyncOutcome, TimeSyncResult
from factory_seal.validation import FactorySealPaths, inspect_sealed_authorization


PROFILE_PATH = Path("/etc/NetworkManager/system-connections/ecobin-air780e.nmconnection")
_PRODUCTION_LIVENESS_FAILURES = frozenset(
    {
        "CELLULAR_MODEM_CONTROL_UNAVAILABLE",
        "CELLULAR_MODEM_STATUS_UNAVAILABLE",
        "CELLULAR_NETWORK_REGISTRATION_PENDING",
        "CELLULAR_NETWORK_REGISTRATION_DENIED",
        "CELLULAR_PACKET_SERVICE_PENDING",
        "CELLULAR_SIM_ABSENT",
        "CELLULAR_SIM_LOCKED",
        "CELLULAR_DNS_UNAVAILABLE",
        "CELLULAR_HTTPS_UNAVAILABLE",
    }
)


def _emit_status_transition(message: str) -> None:
    print(message, flush=True)


class CellularResultReporter:
    """Publish boot-scoped status and journal only observable transitions."""

    def __init__(
        self,
        *,
        path: Path = Path("/run/ecobin/cellular-uplink/status.json"),
        emit: Callable[[str], None] = _emit_status_transition,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = CellularStatusStore(path)
        self._emit = emit
        self._monotonic = monotonic
        self._last_observation: tuple[str, str] | None = None
        self._last_result_code: str | None = None
        self._consecutive_failure_count = 0

    def report(
        self,
        result_code: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        if result_code == "NONE":
            self._consecutive_failure_count = 0
        elif result_code == self._last_result_code:
            self._consecutive_failure_count += 1
        else:
            self._consecutive_failure_count = 1
        next_retry_at_monotonic_ms = None
        if result_code != "NONE" and retry_after_seconds is not None:
            next_retry_at_monotonic_ms = round(
                (self._monotonic() + retry_after_seconds) * 1000
            )
        projection = "OK"
        try:
            self._store.publish(
                result_code,
                consecutive_failure_count=self._consecutive_failure_count,
                next_retry_at_monotonic_ms=next_retry_at_monotonic_ms,
            )
        except Exception:
            projection = "CELLULAR_STATUS_WRITE_FAILED"
        self._last_result_code = result_code
        observation = (result_code, projection)
        if observation == self._last_observation:
            return
        self._emit(
            f"ecobin-cellular-uplink result={result_code} "
            f"statusProjection={projection}"
        )
        self._last_observation = observation

    def current_result(self) -> str | None:
        return self._store.read()

    def current_status(self) -> CellularStatus | None:
        return self._store.read_status()


class CellularProbeDiagnosticReporter:
    """Log bounded probe diagnostics only when their stable identity changes."""

    def __init__(
        self,
        *,
        emit: Callable[[str], None] = _emit_status_transition,
    ) -> None:
        self._emit = emit
        self._last_observation: tuple[str, str | None] | None = None

    def report(
        self,
        result_code: str,
        diagnostic_code: str | None,
        elapsed_ms: int | None,
    ) -> None:
        observation = (result_code, diagnostic_code)
        if observation == self._last_observation:
            return
        self._last_observation = observation
        if diagnostic_code is None:
            return
        elapsed = elapsed_ms if elapsed_ms is not None else "UNKNOWN"
        self._emit(
            f"ecobin-cellular-probe result={result_code} "
            f"diagnostic={diagnostic_code} elapsedMs={elapsed}"
        )


def run_once(
    *,
    probe_diagnostic: Callable[[str, str | None, int | None], None] | None = None,
) -> str:
    seal_paths = FactorySealPaths()
    seal_fact = inspect_sealed_authorization(seal_paths)
    if seal_fact.exists and not seal_fact.valid:
        apply_emergency_uplink_lock()
        return "SEALED_FACT_INVALID"
    try:
        facts = SystemFactsProvider().collect()
    except Exception:
        apply_emergency_uplink_lock()
        return "SYSTEM_FACTS_INVALID"
    if not gate_allows("factory-test-passed", facts):
        apply_emergency_uplink_lock()
        return "FACTORY_TEST_GATE_CLOSED"
    try:
        config = load_cellular_config()
    except CellularConfigurationError as error:
        apply_emergency_uplink_lock()
        return error.code
    inventory = SysfsUsbNetworkInventory()
    device, selection_error = select_rndis_device(config, inventory.devices())
    if device is None:
        apply_emergency_uplink_lock()
        return selection_error
    firewall_mode = apply_seal_aware_uplink_gate(
        device.interface,
        lambda: inspect_sealed_authorization(seal_paths),
    )
    if firewall_mode not in {"FACTORY", "PRODUCTION"}:
        return firewall_mode
    modem = ModemRegistrationProbe().probe(device)
    if not modem.ready:
        if not (
            firewall_mode == "PRODUCTION"
            and modem.error_code in _PRODUCTION_LIVENESS_FAILURES
        ):
            apply_emergency_uplink_lock()
        return modem.error_code
    profile = render_network_manager_profile(config, device)
    try:
        install_network_manager_profile(PROFILE_PATH, profile)
    except OSError:
        apply_emergency_uplink_lock()
        return "CELLULAR_PROFILE_INSTALL_FAILED"
    activated = NetworkManagerActivator().activate(
        profile_path=PROFILE_PATH,
        connection_id=config.connection_id,
        interface=device.interface,
        factory_test_passed=True,
        factory_recovery_required=False,
    )
    if not activated:
        apply_emergency_uplink_lock()
        return "CELLULAR_ACTIVATION_FAILED"
    probe = CellularProbe(config, inventory)
    health = probe.probe()
    if not facts.time_trusted and health.dns_ready:
        try:
            time_sync = ChronyTimeSynchronizer().synchronize()
        except Exception:
            time_sync = TimeSyncOutcome(
                TimeSyncResult.FAILED,
                "TIME_SYNC_INTERNAL_ERROR",
            )
        if time_sync.state is not TimeSyncResult.SYNCED:
            if time_sync.state is TimeSyncResult.FAILED:
                apply_emergency_uplink_lock()
            # PENDING means chronyd accepted a bounded asynchronous sync but
            # has not yet supplied the operating-system trust fact.  Keep the
            # already-restricted RNDIS gate in place so its NTP packets can
            # finish between coordinator loops.  Enrollment and runtime stay
            # blocked until the canonical fact becomes trusted.
            return time_sync.reason_code
        if not health.ready:
            health = probe.probe()
    if not health.ready:
        if probe_diagnostic is not None:
            probe_diagnostic(
                health.error_code,
                health.diagnostic_code,
                health.diagnostic_elapsed_ms,
            )
        # A valid sealed device already has the narrow, verified PRODUCTION
        # policy installed.  DNS/HTTPS liveness misses are availability facts,
        # not evidence that this policy or the selected RNDIS identity became
        # unsafe.  Keep the policy so an established MQTT/maintenance session
        # can recover; the returned error still blocks first-boot progression.
        # Factory mode and every structural/policy failure remain fail-closed.
        if not (
            firewall_mode == "PRODUCTION"
            and health.error_code in _PRODUCTION_LIVENESS_FAILURES
        ):
            apply_emergency_uplink_lock()
        return health.error_code
    if probe_diagnostic is not None:
        probe_diagnostic("NONE", None, None)
    return "NONE"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EcoBin locked Air780E RNDIS uplink")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)
    if not 5 <= args.interval_seconds <= 300:
        parser.error("--interval-seconds must be between 5 and 300")
    reporter = CellularResultReporter()
    diagnostic_reporter = CellularProbeDiagnosticReporter()
    if args.once:
        result = run_once(probe_diagnostic=diagnostic_reporter.report)
        reporter.report(result)
        return 0 if result == "NONE" else 1
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        reporter.report(
            run_once(probe_diagnostic=diagnostic_reporter.report),
            retry_after_seconds=args.interval_seconds,
        )
        end = time.monotonic() + args.interval_seconds
        while not stopping and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

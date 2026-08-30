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
from .cellular_probe import (
    CellularProbe,
    SysfsUsbNetworkInventory,
    select_rndis_device,
)
from .cellular_status import CellularStatusStore
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


def _emit_status_transition(message: str) -> None:
    print(message, flush=True)


class CellularResultReporter:
    """Publish boot-scoped status and journal only observable transitions."""

    def __init__(
        self,
        *,
        path: Path = Path("/run/ecobin/cellular-uplink/status.json"),
        emit: Callable[[str], None] = _emit_status_transition,
    ) -> None:
        self._store = CellularStatusStore(path)
        self._emit = emit
        self._last_observation: tuple[str, str] | None = None

    def report(self, result_code: str) -> None:
        projection = "OK"
        try:
            self._store.publish(result_code)
        except Exception:
            projection = "CELLULAR_STATUS_WRITE_FAILED"
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


def run_once() -> str:
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
    profile = render_network_manager_profile(config, device)
    try:
        install_network_manager_profile(PROFILE_PATH, profile)
    except OSError:
        apply_emergency_uplink_lock()
        return "CELLULAR_PROFILE_INSTALL_FAILED"
    firewall_mode = apply_seal_aware_uplink_gate(
        device.interface,
        lambda: inspect_sealed_authorization(seal_paths),
    )
    if firewall_mode not in {"FACTORY", "PRODUCTION"}:
        return firewall_mode
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
        apply_emergency_uplink_lock()
        return health.error_code
    return "NONE"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EcoBin locked Air780E RNDIS uplink")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)
    if not 5 <= args.interval_seconds <= 300:
        parser.error("--interval-seconds must be between 5 and 300")
    reporter = CellularResultReporter()
    if args.once:
        result = run_once()
        reporter.report(result)
        return 0 if result == "NONE" else 1
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        reporter.report(run_once())
        end = time.monotonic() + args.interval_seconds
        while not stopping and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

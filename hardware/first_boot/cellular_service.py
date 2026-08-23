from __future__ import annotations

import argparse
from pathlib import Path
import signal
import time
from typing import Sequence

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
from .facts import SystemFactsProvider
from .network_manager import (
    NetworkManagerActivator,
    install_network_manager_profile,
    render_network_manager_profile,
)
from .state_machine import gate_allows
from factory_seal.validation import FactorySealPaths, inspect_sealed_authorization


PROFILE_PATH = Path("/etc/NetworkManager/system-connections/ecobin-air780e.nmconnection")


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
    health = CellularProbe(config, inventory).probe()
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
    if args.once:
        return 0 if run_once() == "NONE" else 1
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        run_once()
        end = time.monotonic() + args.interval_seconds
        while not stopping and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

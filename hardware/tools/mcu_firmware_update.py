"""Queue and inspect signed STM32 firmware updates from a maintenance SSH shell.

This tool never opens UART or toggles GPIO. It verifies/caches the package and
atomically writes the edge SQLite journal; the already-running ``main.py``
process remains the sole UART/GPIO owner and notices the maintenance lock on
its next loop iteration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HARDWARE_DIR = Path(__file__).resolve().parents[1]
if str(HARDWARE_DIR) not in sys.path:
    sys.path.insert(0, str(HARDWARE_DIR))

from config import (  # noqa: E402
    EDGE_STORE_PATH,
    MCU_FIRMWARE_CACHE_DIR,
    MCU_HARDWARE_COMPATIBILITY,
    MCU_SIGNING_PUBLIC_KEYS_DIR,
    MCU_UPDATE_ENABLED,
)
from edge_store import EdgeStore  # noqa: E402
from mcu_firmware_updater import (  # noqa: E402
    FirmwarePackageCache,
    McuFirmwareUpdater,
    McuUpdateError,
    load_release_public_keys,
)


class LocalFirmwareUpdateError(RuntimeError):
    pass


def _default_db_path() -> Path:
    path = Path(EDGE_STORE_PATH)
    return path if path.is_absolute() else HARDWARE_DIR / path


def _open_existing_store(value: str) -> EdgeStore:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise LocalFirmwareUpdateError(f"edge database does not exist: {path}")
    store = EdgeStore(str(path))
    store.initialize()
    return store


def _queue_only_updater(store: EdgeStore) -> McuFirmwareUpdater:
    if not MCU_UPDATE_ENABLED:
        raise LocalFirmwareUpdateError(
            "ECOBIN_MCU_UPDATE_ENABLED is false; wire and configure BOOT0/NRST first"
        )
    keys = load_release_public_keys(Path(MCU_SIGNING_PUBLIC_KEYS_DIR))
    cache = FirmwarePackageCache(
        Path(MCU_FIRMWARE_CACHE_DIR),
        keys,
        MCU_HARDWARE_COMPATIBILITY,
    )
    return McuFirmwareUpdater(
        store=store,
        uart_link=None,
        package_cache=cache,
        boot_control=None,
        flash_runner=None,
        enabled=True,
    )


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=str(_default_db_path()),
        help="running gateway SQLite path",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    install = subparsers.add_parser(
        "install",
        help="verify/cache a signed package and queue it for the running gateway",
    )
    install.add_argument("package", type=Path)
    install.add_argument("--deployment-uid")
    install.add_argument(
        "--legacy-preflight",
        action="store_true",
        help="one-time revision-1 migration; relies only on the edge maintenance lock",
    )
    install.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="local break-glass only; the package must still have a trusted signature",
    )
    install.add_argument(
        "--reason",
        help="required for legacy preflight or downgrade and retained for audit",
    )
    install.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help="optionally wait for a terminal update state",
    )

    status = subparsers.add_parser("status", help="show update and stable firmware state")
    status.add_argument("--update-uid")
    status.add_argument("--deployment-uid")

    retry = subparsers.add_parser(
        "retry-failed",
        help="explicitly re-arm the active FAILED_LOCKED journal",
    )
    retry.add_argument("--update-uid", required=True)
    retry.add_argument("--reason", required=True)
    retry.add_argument(
        "--rollback-only",
        action="store_true",
        help="retry only the previously verified stable package",
    )
    return parser.parse_args(argv)


def _wait_for_terminal(
    store: EdgeStore,
    update_uid: str,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    update = store.get_mcu_firmware_update(update_uid)
    while (
        update is not None
        and update["state"] not in {
            "SUCCEEDED",
            "ROLLED_BACK",
            "REJECTED",
            "FAILED_LOCKED",
        }
        and time.monotonic() < deadline
    ):
        time.sleep(0.2)
        update = store.get_mcu_firmware_update(update_uid)
    return update


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = _open_existing_store(args.db_path)
    try:
        if args.action == "status":
            if args.update_uid and args.deployment_uid:
                raise LocalFirmwareUpdateError(
                    "choose either --update-uid or --deployment-uid"
                )
            if args.update_uid:
                update = store.get_mcu_firmware_update(args.update_uid)
            elif args.deployment_uid:
                update = store.get_mcu_firmware_update_by_deployment(
                    args.deployment_uid
                )
            else:
                update = store.get_active_mcu_firmware_update()
            _print_json(
                {
                    "update": update,
                    "maintenanceLock": store.get_maintenance_lock(),
                    "stableFirmware": store.get_mcu_firmware_state(),
                }
            )
            return 0

        if args.action == "retry-failed":
            retried = store.retry_failed_mcu_firmware_update(
                args.update_uid,
                rollback_only=args.rollback_only,
                reason=args.reason,
            )
            if not retried:
                raise LocalFirmwareUpdateError(
                    "the update is not the active FAILED_LOCKED journal"
                )
            _print_json(store.get_mcu_firmware_update(args.update_uid))
            return 0

        if (args.legacy_preflight or args.allow_downgrade) and not args.reason:
            raise LocalFirmwareUpdateError(
                "--reason is required for legacy preflight or downgrade"
            )
        if args.wait_seconds < 0:
            raise LocalFirmwareUpdateError("--wait-seconds must be non-negative")
        updater = _queue_only_updater(store)
        result = updater.queue_local(
            args.package.expanduser().resolve(),
            deployment_uid=args.deployment_uid,
            legacy_preflight=args.legacy_preflight,
            allow_downgrade=args.allow_downgrade,
            requested_reason=args.reason,
        )
        update = store.get_mcu_firmware_update(result["updateUid"])
        if args.wait_seconds:
            update = _wait_for_terminal(
                store,
                result["updateUid"],
                args.wait_seconds,
            )
        _print_json({"queue": result, "update": update})
        return (
            3
            if update and update["state"] in {"REJECTED", "FAILED_LOCKED"}
            else 0
        )
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (LocalFirmwareUpdateError, McuUpdateError, OSError, ValueError) as error:
        code = getattr(error, "code", "LOCAL_MCU_UPDATE_ERROR")
        print(f"ERROR [{code}]: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

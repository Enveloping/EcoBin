"""Inject diagnostic commands into the running EcoBin edge gateway.

The tool writes a normal OneNet command envelope to the gateway's SQLite
command inbox.  It deliberately does not open the serial port or call the MCU
directly, so the running gateway remains the only UART owner and executes the
usual CommandProcessor -> WorkManager -> MCU path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HARDWARE_DIR = Path(__file__).resolve().parents[1]
if str(HARDWARE_DIR) not in sys.path:
    sys.path.insert(0, str(HARDWARE_DIR))

from config import DEPLOYMENT_CODE, EDGE_STORE_PATH  # noqa: E402
from edge_store import EdgeStore  # noqa: E402
from onenet_wire import canonical_payload_sha256, validate_command_envelope  # noqa: E402
from uart_link import compute_mcu_payload_sha256  # noqa: E402

DEFAULT_COMMAND_TTL_SECONDS = 30
DEFAULT_WAIT_SECONDS = 3.0


class LocalCommandError(RuntimeError):
    """A local command cannot be built or safely queued."""


def _new_uid() -> str:
    return str(uuid.uuid4())


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _configured_port(applied: dict[str, Any], port_no: int) -> dict[str, Any]:
    ports = applied["payload"].get("ports", [])
    port = next((item for item in ports if item.get("portNo") == port_no), None)
    if port is None:
        available = ", ".join(str(item.get("portNo")) for item in ports) or "none"
        raise LocalCommandError(
            f"port {port_no} is absent from the applied configuration "
            f"(available: {available})"
        )
    if not port.get("enabled", False):
        raise LocalCommandError(f"port {port_no} is disabled")
    return port


def build_start_delivery_command(
    applied: dict[str, Any],
    *,
    port_no: int,
    command_uid: str | None = None,
    session_uid: str | None = None,
    bag_uid: str | None = None,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a START_DELIVERY_SESSION command from the applied configuration."""

    if ttl_seconds <= 0:
        raise LocalCommandError("ttl_seconds must be greater than zero")

    deployment_code = applied.get("deployment_code")
    if not deployment_code:
        raise LocalCommandError("the applied configuration has no deployment code")

    applied_payload = applied["payload"]
    device_config = applied_payload.get("deviceConfig")
    if not isinstance(device_config, dict):
        raise LocalCommandError("the applied configuration has no deviceConfig")
    port = _configured_port(applied, port_no)

    command_uid = command_uid or _new_uid()
    session_uid = session_uid or _new_uid()
    bag_uid = bag_uid or _new_uid()
    now = now or datetime.now(timezone.utc)
    payload = {
        "bagUid": bag_uid,
        "config": {
            "contentSha256": applied["content_sha256"],
            "mcuPayloadSha256": applied["mcu_payload_sha256"],
            "version": applied["config_version"],
        },
        "continueDeliveryWaitMs": device_config["continueDeliveryWaitMs"],
        "deliveryAutoCloseMs": device_config["deliveryAutoCloseMs"],
        "negativeWeightThresholdGrams": device_config[
            "negativeWeightThresholdGrams"
        ],
        "portNo": port_no,
        "sessionUid": session_uid,
        "unitPriceTenThousandths": port["unitPriceTenThousandths"],
    }
    command = {
        "commandType": "START_DELIVERY_SESSION",
        "commandUid": command_uid,
        "cosGrant": None,
        "deploymentCode": deployment_code,
        "expiresAt": _rfc3339(now + timedelta(seconds=ttl_seconds)),
        "issuedAt": _rfc3339(now),
        "payload": payload,
        "payloadSchemaVersion": 1,
        "payloadSha256": canonical_payload_sha256(payload),
        "schemaVersion": 1,
        "target": {
            "type": "DELIVERY_SESSION",
            "uid": session_uid,
        },
    }
    validate_command_envelope(command)
    return command


def build_sample_configuration_command(
    *,
    deployment_code: str,
    config_version: int,
    door_travel_wait_ms: int = 30000,
    command_uid: str | None = None,
    application_uid: str | None = None,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the one-port configuration used by the UART HIL probe."""

    if not deployment_code:
        raise LocalCommandError("deployment_code is required")
    if not 1 <= config_version <= 9_007_199_254_740_991:
        raise LocalCommandError(
            "config_version must be in 1..9007199254740991"
        )
    if not 30000 <= door_travel_wait_ms <= 45000:
        raise LocalCommandError(
            "door_travel_wait_ms must be in 30000..45000"
        )
    if ttl_seconds <= 0:
        raise LocalCommandError("ttl_seconds must be greater than zero")

    config_fingerprint = json.dumps(
        {
            "version": config_version,
            "doorTravelWaitMs": door_travel_wait_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    application_uid = application_uid or _new_uid()
    payload = {
        "applicationUid": application_uid,
        "config": {
            "version": config_version,
            "contentSha256": hashlib.sha256(
                config_fingerprint.encode()
            ).hexdigest(),
            "mcuPayloadSha256": "0" * 64,
        },
        "deviceConfig": {
            "continueDeliveryWaitMs": 30000,
            "negativeWeightThresholdGrams": 500,
            "deliveryAutoCloseMs": 120000,
            "weightMeasurementTimeoutMs": 6000,
            "deliveryDoorTravelWaitMs": door_travel_wait_ms,
            "cleanSolenoidPulseMs": 1000,
            "smokeMonitoringEnabled": True,
        },
        "ports": [
            {
                "portNo": 1,
                "enabled": True,
                "unitPriceTenThousandths": 4500,
                "fullnessMode": 3,
                "configuredFullWeightGrams": 50000,
                "fullnessSettleWaitMs": 5000,
                "fullnessSensorKind": 1,
                "fullnessDistanceThresholdMm": 600,
                "fullnessSampleCount": 5,
                "fullnessMinimumValidSampleCount": 3,
                "fullnessEchoTimeoutUs": 30000,
                "weightStableWindowMs": 1500,
                "weightMaximumFluctuationGrams": 20,
                "weightRequiredSampleCount": 10,
                "weightMeasurementTimeoutMs": 6000,
                "weightMinimumGrams": -5000,
                "weightMaximumGrams": 100000,
                "calibrationVersion": 4,
            }
        ],
    }
    payload["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        payload
    )
    command_uid = command_uid or _new_uid()
    now = now or datetime.now(timezone.utc)
    command = {
        "commandType": "APPLY_CONFIGURATION",
        "commandUid": command_uid,
        "cosGrant": None,
        "deploymentCode": deployment_code,
        "expiresAt": _rfc3339(now + timedelta(seconds=ttl_seconds)),
        "issuedAt": _rfc3339(now),
        "payload": payload,
        "payloadSchemaVersion": 1,
        "payloadSha256": canonical_payload_sha256(payload),
        "schemaVersion": 1,
        "target": {
            "type": "CONFIGURATION_APPLICATION",
            "uid": application_uid,
        },
    }
    validate_command_envelope(command)
    return command


def queue_sample_configuration(
    store: EdgeStore,
    *,
    deployment_code: str,
    config_version: int,
    door_travel_wait_ms: int = 30000,
    command_uid: str | None = None,
    application_uid: str | None = None,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    dry_run: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Build and optionally queue one sample APPLY_CONFIGURATION command."""

    active = store.get_work_slot()
    if active:
        raise LocalCommandError(
            "device is busy: "
            f"{active['work_type']} {active['work_uid']} "
            f"(state={active.get('work_state')})"
        )
    command = build_sample_configuration_command(
        deployment_code=deployment_code,
        config_version=config_version,
        door_travel_wait_ms=door_travel_wait_ms,
        command_uid=command_uid,
        application_uid=application_uid,
        ttl_seconds=ttl_seconds,
    )
    if dry_run:
        return "DRY_RUN", command
    disposition = store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    if disposition not in ("ACCEPTED", "DUPLICATE"):
        raise LocalCommandError(f"command inbox rejected the command: {disposition}")
    return disposition, command


def queue_start_delivery(
    store: EdgeStore,
    *,
    port_no: int,
    command_uid: str | None = None,
    session_uid: str | None = None,
    bag_uid: str | None = None,
    ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    dry_run: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Build and optionally queue one local diagnostic delivery command."""

    active = store.get_work_slot()
    if active:
        raise LocalCommandError(
            "device is busy: "
            f"{active['work_type']} {active['work_uid']} "
            f"(state={active.get('work_state')})"
        )

    applied = store.get_latest_applied_configuration()
    if applied is None:
        raise LocalCommandError("no APPLIED configuration exists in the edge database")

    command = build_start_delivery_command(
        applied,
        port_no=port_no,
        command_uid=command_uid,
        session_uid=session_uid,
        bag_uid=bag_uid,
        ttl_seconds=ttl_seconds,
    )
    if dry_run:
        return "DRY_RUN", command

    disposition = store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    if disposition not in ("ACCEPTED", "DUPLICATE"):
        raise LocalCommandError(f"command inbox rejected the command: {disposition}")
    return disposition, command


def wait_for_command_claim(
    store: EdgeStore,
    command_uid: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """Wait briefly until the gateway consumes a newly queued command."""

    deadline = time.monotonic() + max(timeout_seconds, 0)
    row = store.get_command(command_uid)
    while row and row["state"] == "PENDING" and time.monotonic() < deadline:
        time.sleep(0.1)
        row = store.get_command(command_uid)
    return row


def _wait_for_configuration_result(
    store: EdgeStore,
    command_uid: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    transient_states = {
        "PENDING",
        "PROCESSING",
        "WAITING_MCU_RESULT",
    }
    row = store.get_command(command_uid)
    while (
        row
        and row["state"] in transient_states
        and time.monotonic() < deadline
    ):
        time.sleep(0.1)
        row = store.get_command(command_uid)
    return row


def _default_db_path() -> Path:
    path = Path(EDGE_STORE_PATH)
    return path if path.is_absolute() else HARDWARE_DIR / path


def _open_existing_store(db_path: str | Path) -> EdgeStore:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise LocalCommandError(
            f"edge database does not exist: {path}; use --db-path if the "
            "gateway uses a different ECOBIN_EDGE_STORE_PATH"
        )
    store = EdgeStore(str(path))
    store.initialize()
    return store


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path",
        default=str(_default_db_path()),
        help="gateway SQLite path (default: configured ECOBIN_EDGE_STORE_PATH)",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser(
        "start-delivery",
        help="PHYSICAL FLOW: queue START_DELIVERY_SESSION for the running gateway",
    )
    _add_db_argument(start)
    start.add_argument("--port-no", type=int, default=1)
    start.add_argument("--command-uid")
    start.add_argument("--session-uid")
    start.add_argument("--bag-uid")
    start.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_COMMAND_TTL_SECONDS,
        help="short expiry prevents a stale command executing after a late restart",
    )
    start.add_argument(
        "--wait-seconds",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help="wait for the running gateway to claim the inbox row",
    )
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="print the command without adding it to the inbox",
    )

    apply_config = subparsers.add_parser(
        "apply-sample-configuration",
        help="queue the one-port UART HIL configuration through the gateway",
    )
    _add_db_argument(apply_config)
    apply_config.add_argument(
        "--deployment-code",
        default=DEPLOYMENT_CODE,
        help="defaults to ECOBIN_DEPLOYMENT_CODE",
    )
    apply_config.add_argument("--config-version", type=int, required=True)
    apply_config.add_argument(
        "--door-travel-wait-ms",
        type=int,
        default=30000,
    )
    apply_config.add_argument("--command-uid")
    apply_config.add_argument("--application-uid")
    apply_config.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_COMMAND_TTL_SECONDS,
    )
    apply_config.add_argument(
        "--wait-seconds",
        type=float,
        default=10.0,
        help="wait for the MCU configuration result",
    )
    apply_config.add_argument("--dry-run", action="store_true")

    status = subparsers.add_parser("status", help="show one command inbox row")
    _add_db_argument(status)
    status.add_argument("--command-uid", required=True)

    active = subparsers.add_parser("active-work", help="show the active work slot")
    _add_db_argument(active)

    return parser.parse_args(argv)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = _open_existing_store(args.db_path)
    try:
        if args.action == "active-work":
            _print_json(store.get_work_slot() or {"work_type": "NONE"})
            return 0

        if args.action == "status":
            row = store.get_command(args.command_uid)
            if row is None:
                raise LocalCommandError(f"command not found: {args.command_uid}")
            _print_json(row)
            return 0

        if args.action == "apply-sample-configuration":
            disposition, command = queue_sample_configuration(
                store,
                deployment_code=args.deployment_code,
                config_version=args.config_version,
                door_travel_wait_ms=args.door_travel_wait_ms,
                command_uid=args.command_uid,
                application_uid=args.application_uid,
                ttl_seconds=args.ttl_seconds,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                _print_json({"disposition": disposition, "command": command})
                return 0
            row = _wait_for_configuration_result(
                store,
                command["commandUid"],
                args.wait_seconds,
            )
            _print_json(
                {
                    "disposition": disposition,
                    "commandUid": command["commandUid"],
                    "applicationUid": command["payload"]["applicationUid"],
                    "configVersion": command["payload"]["config"]["version"],
                    "inboxState": row["state"] if row else "NOT_FOUND",
                    "lastError": row.get("last_error") if row else None,
                }
            )
            return 0 if row and row["state"] == "COMPLETED" else 3

        disposition, command = queue_start_delivery(
            store,
            port_no=args.port_no,
            command_uid=args.command_uid,
            session_uid=args.session_uid,
            bag_uid=args.bag_uid,
            ttl_seconds=args.ttl_seconds,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            _print_json({"disposition": disposition, "command": command})
            return 0

        row = wait_for_command_claim(
            store,
            command["commandUid"],
            args.wait_seconds,
        )
        _print_json(
            {
                "disposition": disposition,
                "commandUid": command["commandUid"],
                "sessionUid": command["payload"]["sessionUid"],
                "bagUid": command["payload"]["bagUid"],
                "portNo": command["payload"]["portNo"],
                "inboxState": row["state"] if row else "NOT_FOUND",
                "lastError": row.get("last_error") if row else None,
            }
        )
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (KeyError, LocalCommandError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

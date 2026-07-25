"""Minimal UART 1.0 hardware-in-the-loop probe.

This tool deliberately bypasses MQTT, SQLite, cameras, and COS so a failed
result identifies the Edge <-> MCU serial/protocol boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import uuid
from pathlib import Path

HARDWARE_DIR = Path(__file__).resolve().parents[1]
if str(HARDWARE_DIR) not in sys.path:
    sys.path.insert(0, str(HARDWARE_DIR))

from uart_link import (  # noqa: E402
    EDGE_CAPABILITY_BITMAP,
    UartError,
    UartLink,
    compute_mcu_payload_sha256,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyS5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--edge-boot-id", type=int, default=1)
    parser.add_argument("--port-count", type=int, default=1)
    parser.add_argument(
        "--required-capabilities",
        type=lambda value: int(value, 0),
        default=EDGE_CAPABILITY_BITMAP,
        help="required MCU capability bitmap; defaults to strict P0 mask 0x1fff",
    )
    parser.add_argument(
        "--query-state",
        action="store_true",
        help="run QUERY_STATE after a successful HELLO handshake",
    )
    parser.add_argument(
        "--apply-sample-configuration",
        action="store_true",
        help="apply a generated valid one-port configuration and await MCU result",
    )
    parser.add_argument(
        "--config-version",
        type=int,
        default=23,
        help="sample configuration version (default: 23)",
    )
    parser.add_argument(
        "--repeat-sample-configuration",
        action="store_true",
        help="replay the exact same configuration parts and verify idempotency",
    )
    return parser.parse_args()


def _sample_configuration(version: int) -> dict:
    if not 1 <= version <= 9007199254740991:
        raise ValueError("config version must be in 1..9007199254740991")
    payload = {
        "applicationUid": str(uuid.uuid4()),
        "config": {
            "version": version,
            "contentSha256": hashlib.sha256(
                f"ecobin-uart-hil-config-v{version}".encode()
            ).hexdigest(),
            "mcuPayloadSha256": "0" * 64,
        },
        "deviceConfig": {
            "continueDeliveryWaitMs": 30000,
            "negativeWeightThresholdGrams": 500,
            "deliveryAutoCloseMs": 60000,
            "weightMeasurementTimeoutMs": 10000,
            "cleanSolenoidPulseMs": 1500,
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
                "fullnessConfirmationWaitMs": 10000,
                "weightStableWindowMs": 1000,
                "weightMaximumFluctuationGrams": 20,
                "weightRequiredSampleCount": 10,
                "weightMeasurementTimeoutMs": 10000,
                "weightMinimumGrams": -5000,
                "weightMaximumGrams": 100000,
                "calibrationVersion": 4,
                "infraredSampleTimeoutMs": 2000,
                "deliveryDoorOperationTimeoutMs": 5000,
            }
        ],
    }
    payload["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        payload
    )
    return {"payload": payload}


def _ack_hil_frame(link: UartLink, frame: dict) -> None:
    payload = frame.get("payload") or {}
    link.send_ack(
        payload["mcuBootId"],
        frame["tx_sequence"],
        frame["message_type"],
    )


def _apply_sample_configuration(
    link: UartLink,
    version: int,
    repeat: bool = False,
) -> dict:
    command = _sample_configuration(version)
    part_uids = [
        str(uuid.uuid4())
        for _ in range(len(command["payload"]["ports"]) + 3)
    ]

    def deliver_once() -> dict:
        delivery = link.apply_configuration(command, part_uids)
        if not delivery["acked"]:
            raise UartError(
                "sample configuration delivery failed: "
                f"{delivery.get('error', 'unknown')}"
            )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            remaining_ms = max(
                1,
                int((deadline - time.monotonic()) * 1000),
            )
            frame = link.read_mcu_event(timeout_ms=remaining_ms)
            if frame is None:
                break
            if frame.get("message_name") != "CONFIG_APPLY_RESULT":
                raise UartError(
                    "expected CONFIG_APPLY_RESULT, got "
                    f"{frame.get('message_name')}"
                )
            _ack_hil_frame(link, frame)
            return {"delivery": delivery, "result": frame}
        raise UartError("CONFIG_APPLY_RESULT timeout")

    first = deliver_once()
    outcome = {
        **first,
        "applicationUid": command["payload"]["applicationUid"],
        "configVersion": version,
        "contentSha256": command["payload"]["config"]["contentSha256"],
        "mcuPayloadSha256": command["payload"]["config"][
            "mcuPayloadSha256"
        ],
    }
    if repeat:
        duplicate = deliver_once()
        commit_part = duplicate["delivery"]["parts"][-1]
        if commit_part.get("disposition") != "DUPLICATE_ACCEPTED":
            raise UartError(
                "duplicate CONFIG_COMMIT was not duplicate-accepted"
            )
        first_sequence = first["result"]["payload"]["mcuEventSequence"]
        duplicate_sequence = duplicate["result"]["payload"][
            "mcuEventSequence"
        ]
        if duplicate_sequence != first_sequence:
            raise UartError(
                "duplicate CONFIG_APPLY_RESULT changed event sequence"
            )
        outcome["duplicateReplay"] = duplicate
    return outcome


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    link = UartLink(
        port=args.port,
        edge_boot_id=args.edge_boot_id,
        port_count=args.port_count,
        baudrate=args.baudrate,
        required_capability_bitmap=args.required_capabilities,
    )
    if not link.open():
        print(json.dumps({"stage": "OPEN", "ok": False}, sort_keys=True))
        return 2

    try:
        mcu_info = link.handshake()
        result: dict[str, object] = {
            "stage": "HELLO",
            "ok": True,
            "mcu": mcu_info,
        }
        if args.apply_sample_configuration:
            result["configuration"] = _apply_sample_configuration(
                link,
                args.config_version,
                args.repeat_sample_configuration,
            )
            result["stage"] = "APPLY_CONFIGURATION"
        if args.query_state:
            result["snapshots"] = link.query_state(
                on_segment=lambda frame: _ack_hil_frame(link, frame)
            )
            result["stage"] = "QUERY_STATE"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "stage": "UART",
                    "ok": False,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())

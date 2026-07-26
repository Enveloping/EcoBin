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
    REQUIRED_MCU_CAPABILITY_BITMAP,
    UartError,
    UartLink,
    compute_mcu_payload_sha256,
)

logger = logging.getLogger("uart-hil-probe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyS5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--edge-boot-id", type=int, default=1)
    parser.add_argument("--port-count", type=int, default=1)
    parser.add_argument(
        "--required-capabilities",
        type=lambda value: int(value, 0),
        default=REQUIRED_MCU_CAPABILITY_BITMAP,
        help="required MCU baseline capability bitmap; defaults to Registry mask 0x300",
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
    parser.add_argument(
        "--door-travel-wait-ms",
        type=int,
        default=30000,
        help="fixed mechanical travel wait, not door feedback (default: 30000)",
    )
    parser.add_argument(
        "--run-door-cycle",
        action="store_true",
        help=(
            "PHYSICAL ACTION: reconcile boot, weigh, issue one OPEN, then "
            "SAFE_CLOSE; implies --apply-sample-configuration"
        ),
    )
    parser.add_argument(
        "--safe-close-only",
        action="store_true",
        help=(
            "PHYSICAL ACTION: issue SAFE_CLOSE without applying configuration "
            "or starting a delivery"
        ),
    )
    parser.add_argument(
        "--boot-recovery-timeout-s",
        type=float,
        default=45.0,
        help="maximum wait for startup SAFE_CLOSE travel recovery",
    )
    return parser.parse_args()


def _sample_configuration(
    version: int,
    door_travel_wait_ms: int = 30000,
) -> dict:
    if not 1 <= version <= 9007199254740991:
        raise ValueError("config version must be in 1..9007199254740991")
    if not 30000 <= door_travel_wait_ms <= 45000:
        raise ValueError("door travel wait must be in 30000..45000 ms")
    config_fingerprint = json.dumps(
        {
            "version": version,
            "doorTravelWaitMs": door_travel_wait_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    payload = {
        "applicationUid": str(uuid.uuid4()),
        "config": {
            "version": version,
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
    command: dict,
    repeat: bool = False,
) -> dict:
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

        expected = command["payload"]
        frame = _wait_for_event(
            link,
            "CONFIG_APPLY_RESULT",
            timeout_s=5.0,
            predicate=lambda candidate: (
                candidate["payload"].get("applicationUid")
                == expected["applicationUid"]
                and candidate["payload"].get("configVersion")
                == expected["config"]["version"]
            ),
        )
        return {"delivery": delivery, "result": frame}

    first = deliver_once()
    outcome = {
        **first,
        "applicationUid": command["payload"]["applicationUid"],
        "configVersion": command["payload"]["config"]["version"],
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


def _wait_for_event(
    link: UartLink,
    expected_name: str,
    timeout_s: float,
    predicate=None,
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        frame = link.read_mcu_event(timeout_ms=remaining_ms)
        if frame is None:
            break
        payload = frame.get("payload") or {}
        if "mcuBootId" in payload and "mcuEventSequence" in payload:
            _ack_hil_frame(link, frame)
        if (
            frame.get("message_name") == expected_name
            and (predicate is None or predicate(frame))
        ):
            return frame
    raise UartError(f"{expected_name} timeout")


def _confirm_no_active_work(
    link: UartLink,
    command: dict,
    timeout_s: float,
) -> dict:
    payload = command["payload"]
    deadline = time.monotonic() + timeout_s
    while True:
        result = link.send_confirm_no_active_work(
            payload["config"]["version"],
            payload["config"]["contentSha256"],
        )
        if result["acked"]:
            event = _wait_for_event(
                link,
                "BOOT_RECONCILIATION_RESULT",
                timeout_s=5.0,
            )
            if event["payload"].get("status") != "ACCEPTED":
                raise UartError(
                    "boot reconciliation result was not ACCEPTED"
                )
            return {"delivery": result, "result": event}
        if result.get("error") == "STATE_CONFLICT":
            snapshots = link.query_state(
                on_segment=lambda frame: _ack_hil_frame(link, frame)
            )
            begin = next(
                (
                    frame["payload"]
                    for frame in snapshots
                    if frame.get("message_name")
                    == "STATE_SNAPSHOT_BEGIN"
                ),
                None,
            )
            if (
                begin is not None
                and begin.get("activeWorkType") == "NONE"
                and begin.get("activeWorkPhase") == "IDLE"
                and begin.get("appliedConfigVersion")
                == payload["config"]["version"]
                and begin.get("appliedContentSha256")
                == payload["config"]["contentSha256"]
            ):
                return {
                    "delivery": result,
                    "alreadyIdle": True,
                    "snapshots": snapshots,
                }
            raise UartError(
                "CONFIRM_NO_ACTIVE_WORK state conflict was not an "
                "already-reconciled idle MCU"
            )
        if (
            result.get("error") != "BUSY"
            or time.monotonic() >= deadline
        ):
            raise UartError(
                "CONFIRM_NO_ACTIVE_WORK failed: "
                f"{result.get('error', 'unknown')}"
            )
        time.sleep(0.5)


def _run_delivery_door_cycle(
    link: UartLink,
    command: dict,
    boot_recovery_timeout_s: float,
) -> dict:
    payload = command["payload"]
    device = payload["deviceConfig"]
    port = payload["ports"][0]
    reconciliation = _confirm_no_active_work(
        link,
        command,
        boot_recovery_timeout_s,
    )

    session_uid = str(uuid.uuid4())
    start = link.send_start_delivery_session(
        session_uid=session_uid,
        port_no=port["portNo"],
        config_version=payload["config"]["version"],
        config_content_sha256=payload["config"]["contentSha256"],
        unit_price_ten_thousandths=port["unitPriceTenThousandths"],
        continue_delivery_wait_ms=device["continueDeliveryWaitMs"],
        negative_weight_threshold_grams=device[
            "negativeWeightThresholdGrams"
        ],
        start_execution_window_ms=45000,
        delivery_auto_close_ms=device["deliveryAutoCloseMs"],
    )
    if not start["acked"]:
        raise UartError(
            f"START_DELIVERY_SESSION failed: {start.get('error', 'unknown')}"
        )
    preopen = _wait_for_event(
        link,
        "WORK_PREOPEN_WEIGHT_READY",
        timeout_s=max(
            30.0,
            device["weightMeasurementTimeoutMs"] / 1000.0 + 5.0,
        ),
        predicate=lambda frame: (
            frame["payload"].get("sessionUid") == session_uid
        ),
    )
    preopen_payload = preopen["payload"]
    if (
        preopen_payload.get("weightSensorHealth") != "OK"
        or preopen_payload.get("measurementStatus")
        not in ("STABLE", "UNSTABLE")
        or not preopen_payload.get("weightValuePresent")
    ):
        raise UartError(
            "pre-open weight does not permit opening: "
            f"health={preopen_payload.get('weightSensorHealth')} "
            f"status={preopen_payload.get('measurementStatus')} "
            f"valuePresent={preopen_payload.get('weightValuePresent')}"
        )

    authorize = link.send_authorize_delivery_first_open(
        session_uid=session_uid,
        port_no=port["portNo"],
        preopen_measurement_uid=preopen_payload["measurementUid"],
        parent_start_command_uid=start["mcu_command_uid"],
        remaining_ms=45000,
    )
    if not authorize["acked"]:
        raise UartError(
            "AUTHORIZE_DELIVERY_FIRST_OPEN failed: "
            f"{authorize.get('error', 'unknown')}"
        )
    open_result = _wait_for_event(
        link,
        "DELIVERY_DOOR_COMMAND_RESULT",
        timeout_s=15.0,
        predicate=lambda frame: (
            frame["payload"].get("sessionUid") == session_uid
            and frame["payload"].get("command") == "OPEN"
        ),
    )
    if (
        open_result["payload"].get("outputStatus")
        != "COMMAND_DISPATCHED"
    ):
        raise UartError(
            "OPEN output was not dispatched: "
            f"{open_result['payload'].get('outputStatus')}"
        )

    travel_wait_s = device["deliveryDoorTravelWaitMs"] / 1000.0
    logger.info(
        "OPEN 方向已锁存，等待 %.1f 秒机械行程/现场观察后再 SAFE_CLOSE",
        travel_wait_s,
    )
    time.sleep(travel_wait_s)

    close = link.send_safe_close_all()
    if not close["acked"]:
        raise UartError(
            f"SAFE_CLOSE failed: {close.get('error', 'unknown')}"
        )
    close_result = _wait_for_event(
        link,
        "SAFE_CLOSE_RESULT",
        timeout_s=15.0,
    )
    if close_result["payload"].get("outputStatus") not in (
        "COMMAND_DISPATCHED",
        "COALESCED_WITH_EXISTING_CLOSE",
    ):
        raise UartError(
            "CLOSE output was not dispatched: "
            f"{close_result['payload'].get('outputStatus')}"
        )
    return {
        "reconciliation": reconciliation,
        "sessionUid": session_uid,
        "preopenWeight": preopen,
        "openCommand": authorize,
        "openResult": open_result,
        "openObservationWaitMs": device["deliveryDoorTravelWaitMs"],
        "safeCloseCommand": close,
        "safeCloseResult": close_result,
    }


def _run_safe_close_only(link: UartLink) -> dict:
    command = link.send_safe_close_all()
    if not command["acked"]:
        raise UartError(
            f"SAFE_CLOSE failed: {command.get('error', 'unknown')}"
        )
    result = _wait_for_event(
        link,
        "SAFE_CLOSE_RESULT",
        timeout_s=50.0,
    )
    if result["payload"].get("outputStatus") not in (
        "COMMAND_DISPATCHED",
        "COALESCED_WITH_EXISTING_CLOSE",
    ):
        raise UartError(
            "SAFE_CLOSE output did not converge: "
            f"{result['payload'].get('outputStatus')}"
        )
    return {"command": command, "result": result}


def main() -> int:
    args = parse_args()
    if args.run_door_cycle and args.safe_close_only:
        raise ValueError(
            "--run-door-cycle and --safe-close-only are mutually exclusive"
        )
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
        command = _sample_configuration(
            args.config_version,
            args.door_travel_wait_ms,
        )
        if args.apply_sample_configuration or args.run_door_cycle:
            result["configuration"] = _apply_sample_configuration(
                link,
                command,
                args.repeat_sample_configuration,
            )
            result["stage"] = "APPLY_CONFIGURATION"
        if args.run_door_cycle:
            result["doorHil"] = _run_delivery_door_cycle(
                link,
                command,
                args.boot_recovery_timeout_s,
            )
            result["stage"] = "DOOR_HIL"
        if args.safe_close_only:
            result["safeClose"] = _run_safe_close_only(link)
            result["stage"] = "SAFE_CLOSE"
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

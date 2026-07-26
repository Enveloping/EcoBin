"""Persistent OneNet command consumer and MCU execution coordinator."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from onenet_wire import validate_command_envelope
from uart_link import compute_mcu_payload_sha256

logger = logging.getLogger("command-processor")


class CommandProcessor:
    """Consume command_inbox rows without doing physical work in MQTT callbacks."""

    def __init__(self, store, uart_link, work_manager=None):
        self._store = store
        self._uart = uart_link
        self._work = work_manager
        self._wake_event = threading.Event()

    def wake(self) -> None:
        self._wake_event.set()

    def wait(self, timeout_s: float = 0.5) -> None:
        self._wake_event.wait(timeout_s)
        self._wake_event.clear()

    def process_next(self) -> bool:
        row = self._store.claim_next_command()
        if not row:
            return False
        command = row["payload"]
        command_uid = row["command_uid"]
        try:
            validate_command_envelope(command)
            if command["commandType"] == "APPLY_CONFIGURATION":
                self._apply_configuration(command)
            elif command["commandType"] == "START_DELIVERY_SESSION":
                self._start_delivery_session(command)
            elif command["commandType"] == "START_CLEAN_OPERATION":
                self._start_clean_operation(command)
            elif command["commandType"] == "SAMPLE_FULLNESS":
                self._sample_fullness(command)
            elif command["commandType"] == "MEASURE_EMPTY_BAG_BASELINE":
                self._measure_empty_bag_baseline(command)
            elif command["commandType"] == "END_CLEAN_BEFORE_UNLOCK":
                self._end_clean_before_unlock(command)
            elif command["commandType"] == "RESUME_CLEAN_OPERATION":
                self._resume_clean_operation(command)
            else:
                self._store.fail_command(command_uid, "COMMAND_NOT_IMPLEMENTED")
                logger.warning(
                    "command type not implemented yet: %s", command["commandType"]
                )
        except Exception as error:
            error_code = _error_code(error)
            self._store.fail_command(command_uid, error_code)
            logger.error("command %s failed: %s", command_uid, error)
        return True

    def _start_delivery_session(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_delivery_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _start_clean_operation(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_clean_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _sample_fullness(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_fullness_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _measure_empty_bag_baseline(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.start_baseline_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        self._store.mark_command_waiting_mcu(
            command["commandUid"],
            result["mcu_command_uid"],
            result,
        )

    def _end_clean_before_unlock(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.end_clean_before_unlock_command(command)
        self._accept_dispatch_result(command, result)

    def _resume_clean_operation(self, command: dict) -> None:
        if self._work is None:
            raise RuntimeError("work manager is required")
        result = self._work.resume_clean_command(command)
        if not self._accept_dispatch_result(command, result):
            return
        if not result.get("already_recovered"):
            self._store.mark_command_waiting_mcu(
                command["commandUid"],
                result["mcu_command_uid"],
                result,
            )

    def _accept_dispatch_result(self, command: dict, result: dict) -> bool:
        if result.get("acked"):
            return True
        error = _symbol(str(result.get("error") or "UART_FAILURE"))
        if error == "TIMEOUT":
            self._store.mark_command_recovery_required(
                command["commandUid"],
                "UART_ACK_RESULT_UNKNOWN",
                result.get("mcu_command_uid"),
                result,
            )
        else:
            self._store.fail_command(command["commandUid"], error)
        return False

    def _apply_configuration(self, command: dict) -> None:
        payload = command["payload"]
        config = payload["config"]
        application_uid = payload["applicationUid"]
        actual_mcu_sha = compute_mcu_payload_sha256(payload)
        if actual_mcu_sha != config["mcuPayloadSha256"]:
            raise ValueError("mcuPayloadSha256 mismatch")

        existing = self._store.get_configuration(application_uid)
        if existing:
            part_uids = existing["part_command_uids"]
        else:
            part_uids = [
                str(uuid.uuid4()) for _ in range(len(payload["ports"]) + 3)
            ]
        saved = self._store.save_configuration_edge(command, part_uids)
        if saved == "OUTDATED":
            raise ValueError("configuration version is older than local version")
        if saved == "CONFLICT":
            raise ValueError("configuration identity conflict")

        result = self._uart.apply_configuration(command, part_uids)
        if not result["acked"]:
            error_code = str(result.get("error") or "UART_FAILURE")
            failed_part = _last_part_uid(result)
            if error_code == "TIMEOUT":
                self._store.mark_configuration_recovery_required(
                    application_uid, "UART_RESULT_UNKNOWN"
                )
            else:
                self._store.fail_configuration_edge(
                    application_uid,
                    _symbol(error_code),
                    failed_part,
                )
            return

        commit_uid = result["commit_mcu_command_uid"]
        self._store.mark_configuration_waiting_mcu(application_uid, commit_uid)
        self._store.mark_command_waiting_mcu(
            command["commandUid"], commit_uid, result
        )
        logger.info(
            "configuration staged at MCU, waiting result: app=%s version=%d",
            application_uid,
            config["version"],
        )

    def process_mcu_event(self, event: dict) -> None:
        message_name = event["message_name"]
        payload = event["payload"]
        if message_name == "CONFIG_APPLY_RESULT":
            result = self._store.apply_configuration_result(payload)
            if result not in ("ACCEPTED", "DUPLICATE"):
                raise ValueError(f"configuration result {result.lower()}")
        elif self._work is not None:
            self._work.handle_mcu_event({
                "message_name": message_name,
                "message_type": event["message_type"],
                "tx_sequence": event["source_tx_sequence"],
                "payload": payload,
            })


def _last_part_uid(result: dict) -> Optional[str]:
    parts = result.get("parts") or []
    if not parts:
        return None
    return parts[-1].get("mcu_command_uid")


def _error_code(error: Exception) -> str:
    message = str(error).upper()
    if "EXPIRED" in message:
        return "COMMAND_EXPIRED"
    if "MCUPAYLOADSHA256" in message:
        return "MCU_PAYLOAD_DIGEST_MISMATCH"
    if "PAYLOADSHA256" in message:
        return "PAYLOAD_DIGEST_MISMATCH"
    if "UUID" in message:
        return "INVALID_COMMAND_IDENTITY"
    if "OLDER" in message:
        return "CONFIG_VERSION_OUTDATED"
    if "CONFLICT" in message:
        return "IDEMPOTENCY_CONFLICT"
    return "INVALID_COMMAND"


def _symbol(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_" for character in value.upper()
    ).strip("_")
    return (normalized or "UART_FAILURE")[:64]

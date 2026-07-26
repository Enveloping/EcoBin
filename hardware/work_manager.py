"""work_manager.py -- delivery session and clean operation state machines."""
from __future__ import annotations
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from edge_store import (
    EdgeStore,
    WORK_TYPE_BASELINE,
    WORK_TYPE_CLEAN,
    WORK_TYPE_DELIVERY,
    WORK_TYPE_FULLNESS,
    WORK_TYPE_NONE,
)
from uart_protocol import REGISTRY
logger = logging.getLogger("work-manager")
def _new_uid() -> str:
    return str(_uuid.uuid4())


def _reported_weight(payload: dict[str, Any]) -> Optional[int]:
    if not payload.get("weightValuePresent"):
        return None
    value = payload.get("reportedWeightGrams")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _delivery_usable_weight(payload: dict[str, Any]) -> Optional[int]:
    if (
        payload.get("weightSensorHealth") != "OK"
        or payload.get("measurementStatus") not in ("STABLE", "UNSTABLE")
    ):
        return None
    return _reported_weight(payload)


def _measurement_fact(payload: Optional[dict[str, Any]]) -> Optional[dict]:
    if not payload:
        return None
    value_present = bool(payload.get("weightValuePresent"))
    fault_code = payload.get("faultCode")
    return {
        "measurementUid": payload.get("measurementUid"),
        "status": payload.get("measurementStatus"),
        "weightValueAvailable": value_present,
        "reportedWeightGrams": (
            _reported_weight(payload) if value_present else None
        ),
        "weightValueKind": payload.get("weightValueKind", "NONE"),
        "measurementElapsedMs": payload.get("measurementElapsedMs", 0),
        "sampleCount": payload.get("sampleCount", 0),
        "calibrationVersion": payload.get("calibrationVersion", 0),
        "sensorHealth": payload.get("weightSensorHealth", "UNKNOWN"),
        "faultCode": None if fault_code in (None, "NONE") else fault_code,
        "mcuBootId": payload.get("mcuBootId"),
        "mcuEventSequence": payload.get("mcuEventSequence"),
    }


def _frozen_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": config["version"],
        "contentSha256": config["contentSha256"],
        "mcuPayloadSha256": config["mcuPayloadSha256"],
    }


def _pending_photo_facts(slots: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "status": "UPLOAD_PENDING",
            "photoUid": None,
            "url": None,
            "sha256": None,
            "sizeBytes": None,
            "capturedAt": None,
            "missingReason": "PHOTO_METADATA_PENDING",
        }
        for slot in slots
    ]


def _remaining_execution_ms(command: dict[str, Any]) -> int:
    return _remaining_until(command["expiresAt"])


def _remaining_until(expires_at_value: str) -> int:
    expires_at = datetime.fromisoformat(
        str(expires_at_value).replace("Z", "+00:00")
    )
    remaining = int(
        (expires_at - datetime.now(timezone.utc)).total_seconds() * 1000
    )
    if remaining <= 0:
        raise ValueError("command expired")
    return min(remaining, 4294967295)


def _deadline_after_ms(duration_ms: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(milliseconds=duration_ms)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fault_code_number(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(
        REGISTRY["enums"]["FaultCode"]["values"].get(
            str(value),
            REGISTRY["enums"]["FaultCode"]["values"]["MCU_INTERNAL"],
        )
    )


class WorkManager:

    def __init__(self, store: EdgeStore, uart_link, mqtt_client, photo_manager):
        self._store = store
        self._uart = uart_link
        self._mqtt = mqtt_client
        self._photo = photo_manager

    @property
    def active_delivery_session(self) -> Optional[dict]:
        slot = self._store.get_work_slot()
        if slot and slot["work_type"] == WORK_TYPE_DELIVERY:
            return slot
        return None

    def start_delivery_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Persist and dispatch one cloud-authorized delivery session."""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        start_window_ms = _remaining_execution_ms(command)
        session_uid = payload["sessionUid"]
        mcu_command_uid = _new_uid()
        ctx = {
            "session_uid": session_uid,
            "port_no": payload["portNo"],
            "bag_uid": payload["bagUid"],
            "unit_price_ten_thousandths": payload["unitPriceTenThousandths"],
            "continue_delivery_wait_ms": payload["continueDeliveryWaitMs"],
            "negative_weight_threshold_grams": payload[
                "negativeWeightThresholdGrams"
            ],
            "delivery_auto_close_ms": payload["deliveryAutoCloseMs"],
            "config": config,
            "start_command_uid": command["commandUid"],
            "deployment_code": command["deploymentCode"],
            "start_mcu_command_uid": mcu_command_uid,
            "expires_at": command["expiresAt"],
            "phase": "STARTING",
            "round_index": 0,
            "negative_weight_anomaly": False,
            "first_weight_grams": None,
            "first_measurement_uid": None,
            "final_weight_grams": None,
            "final_measurement_uid": None,
        }
        if not self._store.acquire_work_slot(
            WORK_TYPE_DELIVERY,
            session_uid,
            payload["portNo"],
            ctx,
        ):
            return {"acked": False, "error": "DEVICE_BUSY"}
        result = self._uart.send_command(
            "START_DELIVERY_SESSION",
            {
                "sessionUid": session_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "unitPriceTenThousandths": payload[
                    "unitPriceTenThousandths"
                ],
                "continueDeliveryWaitMs": payload["continueDeliveryWaitMs"],
                "negativeWeightThresholdGrams": payload[
                    "negativeWeightThresholdGrams"
                ],
                "startExecutionWindowMs": start_window_ms,
                "deliveryAutoCloseMs": payload["deliveryAutoCloseMs"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        ctx["phase"] = (
            "WAITING_PREOPEN_WEIGHT" if result["acked"] else "START_RESULT_UNKNOWN"
        )
        self._store.update_work_context(session_uid, ctx)
        return result

    def start_clean_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Persist and dispatch one cloud-authorized clean operation."""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        operation_uid = payload["operationUid"]
        mcu_command_uid = _new_uid()
        ctx = {
            "operation_uid": operation_uid,
            "port_no": payload["portNo"],
            "old_bag_uid": payload["oldBagUid"],
            "old_baseline_weight_grams": payload[
                "oldBaselineWeightGrams"
            ],
            "new_bag_uid": payload["newBagUid"],
            "config": config,
            "start_command_uid": command["commandUid"],
            "deployment_code": command["deploymentCode"],
            "start_mcu_command_uid": mcu_command_uid,
            "recovery_generation": 0,
            "action_sequence": 0,
            "operation_deadline": _deadline_after_ms(
                payload["operationWindowMs"]
            ),
            "phase": "STARTING",
            "preunlock_weight_grams": None,
            "preunlock_measurement_uid": None,
            "final_weight_grams": None,
            "final_measurement_uid": None,
            "completion_confirmed": False,
        }
        if not self._store.acquire_work_slot(
            WORK_TYPE_CLEAN,
            operation_uid,
            payload["portNo"],
            ctx,
        ):
            return {"acked": False, "error": "DEVICE_BUSY"}
        result = self._uart.send_command(
            "START_CLEAN_OPERATION",
            {
                "operationUid": operation_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": _remaining_execution_ms(command),
                "operationWindowMs": payload["operationWindowMs"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        ctx["phase"] = (
            "WAITING_PREUNLOCK_WEIGHT"
            if result["acked"]
            else "START_RESULT_UNKNOWN"
        )
        self._store.update_work_context(operation_uid, ctx)
        return result

    def start_fullness_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        detection_uid = payload["detectionUid"]
        mcu_command_uid = _new_uid()
        ctx = {
            "detection_uid": detection_uid,
            "port_no": payload["portNo"],
            "command_uid": command["commandUid"],
            "deployment_code": command["deploymentCode"],
            "mcu_command_uid": mcu_command_uid,
            "payload": payload,
            "phase": "SAMPLING",
        }
        if not self._store.acquire_work_slot(
            WORK_TYPE_FULLNESS,
            detection_uid,
            payload["portNo"],
            ctx,
        ):
            return {"acked": False, "error": "DEVICE_BUSY"}
        result = self._uart.send_command(
            "SAMPLE_FULLNESS",
            {
                "detectionUid": detection_uid,
                "portNo": payload["portNo"],
                "sampleRole": payload["sampleRole"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": _remaining_execution_ms(command),
                "settleWaitMs": payload["settleWaitMs"],
                "measurementTimeoutMs": payload["measurementTimeoutMs"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        ctx["phase"] = (
            "WAITING_RESULT" if result["acked"] else "RESULT_UNKNOWN"
        )
        self._store.update_work_context(detection_uid, ctx)
        return result

    def start_baseline_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        measurement_uid = payload["measurementUid"]
        mcu_command_uid = _new_uid()
        ctx = {
            "measurement_uid": measurement_uid,
            "port_no": payload["portNo"],
            "bag_uid": payload["bagUid"],
            "empty_bag_confirmed": payload["emptyBagConfirmed"],
            "command_uid": command["commandUid"],
            "deployment_code": command["deploymentCode"],
            "mcu_command_uid": mcu_command_uid,
            "config": config,
            "phase": "MEASURING",
        }
        if not self._store.acquire_work_slot(
            WORK_TYPE_BASELINE,
            measurement_uid,
            payload["portNo"],
            ctx,
        ):
            return {"acked": False, "error": "DEVICE_BUSY"}
        result = self._uart.send_command(
            "MEASURE_BASELINE",
            {
                "measurementUid": measurement_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": _remaining_execution_ms(command),
                "measurementTimeoutMs": payload["measurementTimeoutMs"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        ctx["phase"] = (
            "WAITING_RESULT" if result["acked"] else "RESULT_UNKNOWN"
        )
        self._store.update_work_context(measurement_uid, ctx)
        return result

    def end_clean_before_unlock_command(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        payload = command["payload"]
        slot = self._store.get_work_slot()
        if (
            not slot
            or slot["work_type"] != WORK_TYPE_CLEAN
            or slot["work_uid"] != payload["operationUid"]
            or slot["port_no"] != payload["portNo"]
        ):
            return {"acked": False, "error": "UNKNOWN_WORK"}
        ctx = slot["context"]
        if ctx.get("phase") not in {
            "STARTING",
            "WAITING_PREUNLOCK_WEIGHT",
            "PREUNLOCK_MEASURED",
        }:
            return {"acked": False, "error": "STATE_CONFLICT"}
        mcu_command_uid = _new_uid()
        result = self._uart.send_command(
            "END_CLEAN_BEFORE_UNLOCK",
            {
                "operationUid": slot["work_uid"],
                "portNo": slot["port_no"],
                "parentCommandUid": ctx["start_mcu_command_uid"],
                "recoveryGeneration": ctx["recovery_generation"],
                "executionDeadlineMs": _remaining_execution_ms(command),
                "reason": payload["reason"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        if result["acked"]:
            start_command_uid = ctx.get("start_command_uid")
            if start_command_uid:
                start_row = self._store.get_command(start_command_uid)
                if start_row and start_row["state"] not in {
                    "COMPLETED",
                    "FAILED",
                }:
                    self._store.fail_command(
                        start_command_uid,
                        payload["reason"],
                    )
            self._store.complete_command(
                command["commandUid"],
                {"reason": payload["reason"]},
            )
            self._store.release_work_slot(slot["work_uid"])
        return result

    def resume_clean_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        self._require_applied_config(payload["config"])
        slot = self._store.get_work_slot()
        if (
            not slot
            or slot["work_type"] != WORK_TYPE_CLEAN
            or slot["work_uid"] != payload["operationUid"]
            or slot["port_no"] != payload["portNo"]
        ):
            return {"acked": False, "error": "UNKNOWN_WORK"}
        ctx = slot["context"]
        if ctx.get("new_bag_uid") not in (None, payload["newBagUid"]):
            return {"acked": False, "error": "IDEMPOTENCY_CONFLICT"}
        current_generation = int(ctx.get("recovery_generation", 0))
        requested_generation = payload["recoveryGeneration"]
        if current_generation > requested_generation:
            return {
                "acked": False,
                "error": "RECOVERY_GENERATION_OUTDATED",
            }
        if (
            current_generation == requested_generation
            and ctx.get("phase") == "CLEAN_RECOVERY_REQUIRED"
        ):
            self._store.complete_command(
                command["commandUid"],
                {
                    "recoveryGeneration": current_generation,
                    "state": "CLEAN_RECOVERY_REQUIRED",
                },
            )
            return {
                "acked": True,
                "already_recovered": True,
                "mcu_command_uid": ctx.get("resume_mcu_command_uid"),
            }
        next_action_sequence = int(ctx.get("action_sequence", 0)) + 1
        resume_uid = _new_uid()
        ctx["recovery_generation"] = requested_generation
        ctx["resume_mcu_command_uid"] = resume_uid
        ctx["resume_command_uid"] = command["commandUid"]
        ctx["phase"] = "RESUMING_CLEAN"
        self._store.update_work_context(slot["work_uid"], ctx)
        result = self._uart.send_command(
            "RESUME_CLEAN_OPERATION",
            {
                "operationUid": slot["work_uid"],
                "portNo": slot["port_no"],
                "recoveryGeneration": requested_generation,
                "nextCleanActionSequence": next_action_sequence,
                "configVersion": payload["config"]["version"],
                "configContentSha256": payload["config"][
                    "contentSha256"
                ],
            },
            mcu_command_uid=resume_uid,
        )
        return result

    def _require_applied_config(self, config: dict[str, Any]) -> None:
        version = self._store.get_state("applied_config_version")
        content_sha256 = self._store.get_state(
            "applied_config_content_sha256"
        )
        if (
            version != str(config.get("version"))
            or content_sha256 != config.get("contentSha256")
        ):
            raise ValueError("command config is not applied locally")

    def _create_reliable_event(
        self,
        *,
        event_type: str,
        target_type: str,
        work_uid: str,
        command_uid: Optional[str],
        deployment_code: Optional[str],
        payload: dict[str, Any],
        work_state_update: Optional[dict] = None,
        event_uid: Optional[str] = None,
    ) -> str:
        event_uid = event_uid or _new_uid()
        return self._store.create_edge_event(
            event_uid=event_uid,
            event_type=event_type,
            payload=payload,
            work_uid=work_uid,
            work_state_update=work_state_update,
            deployment_code=deployment_code or "Dp_unknown",
            target_type=target_type,
            command_uid=command_uid,
        )

    def start_delivery_session(self, session_uid, port_no, unit_price_ten_thousandths,
                               bag_qr_code, negative_weight_threshold_grams=500):
        ctx = {"session_uid": session_uid, "port_no": port_no,
               "unit_price_ten_thousandths": unit_price_ten_thousandths,
               "bag_qr_code": bag_qr_code,
               "negative_weight_threshold_grams": negative_weight_threshold_grams,
               "phase": "STARTED", "round_index": 0,
               "negative_weight_anomaly": False, "first_weight_grams": None,
               "first_measurement_uid": None, "final_weight_grams": None,
               "final_measurement_uid": None}
        ok = self._store.acquire_work_slot(WORK_TYPE_DELIVERY, session_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("delivery session started: %s port=%d", session_uid, port_no)
        return {"success": True, "session_uid": session_uid}

    def authorize_first_open(self, session_uid):
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != session_uid:
            return {"success": False, "reason": "SESSION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        preopen_uid = ctx.get("first_measurement_uid") or _new_uid()
        if not ctx.get("first_measurement_uid"):
            ctx["first_measurement_uid"] = preopen_uid
            self._store.update_work_context(session_uid, ctx)
        parent_cmd_uid = _new_uid()
        result = self._uart.send_authorize_delivery_first_open(
            session_uid=session_uid, port_no=port_no,
            preopen_measurement_uid=preopen_uid,
            parent_start_command_uid=parent_cmd_uid, remaining_ms=45000)
        if result["acked"]:
            ctx["phase"] = "WAITING_PREOPEN_WEIGHT"
            self._store.update_work_context(session_uid, ctx)
            return {"success": True, "session_uid": session_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def handle_mcu_event(self, frame):
        msg_name = frame.get("message_name", "")
        payload = frame.get("payload", {})
        if msg_name == "SAFETY_SENSOR_EVENT":
            self._on_safety_sensor_event(payload)
            return
        if msg_name == "FAULT_OBSERVED":
            self._on_fault_observed(payload)
            return
        if msg_name == "SAFE_CLOSE_RESULT":
            self._on_safe_close_result(payload)
            return
        slot = self._store.get_work_slot()
        if not slot:
            return
        ctx = slot.get("context", {})
        work_uid = slot["work_uid"]
        work_type = slot["work_type"]
        if msg_name == "WORK_PREOPEN_WEIGHT_READY":
            self._on_preopen_weight(ctx, payload, work_uid)
        elif msg_name == "DELIVERY_DOOR_COMMAND_RESULT" and work_type == WORK_TYPE_DELIVERY:
            self._on_delivery_door_command_result(ctx, payload, work_uid)
        elif msg_name == "WORK_POSTCLOSE_WEIGHT_READY":
            self._on_postclose_weight(ctx, payload, work_uid)
        elif msg_name == "DELIVERY_SELECTION":
            self._on_delivery_selection(ctx, payload, work_uid)
        elif msg_name == "WORK_PREUNLOCK_WEIGHT_READY" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_preunlock_weight(ctx, payload, work_uid)
        elif msg_name == "CLEAN_UNLOCK_REQUESTED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_unlock_requested(ctx, payload, work_uid)
        elif msg_name == "CLEAN_FINISH_REQUESTED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_finish_requested(ctx, payload, work_uid)
        elif msg_name == "CLEAN_LOCK_POWER_CHANGED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_lock_power_changed(ctx, payload, work_uid)
        elif msg_name == "CLEAN_FINAL_WEIGHT_READY" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_final_weight(ctx, payload, work_uid)
        elif msg_name == "CLEAN_COMPLETION_CONFIRMED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_completion(ctx, payload, work_uid)
        elif msg_name == "FULLNESS_SAMPLE_RESULT" and work_type == WORK_TYPE_FULLNESS:
            self._on_fullness_sample_result(ctx, payload, work_uid)
        elif msg_name == "BASELINE_MEASUREMENT_RESULT" and work_type == WORK_TYPE_BASELINE:
            self._on_baseline_measurement_result(ctx, payload, work_uid)
        elif msg_name == "BOOT_RECONCILIATION_RESULT" and work_type == WORK_TYPE_CLEAN:
            self._on_boot_reconciliation_result(ctx, payload, work_uid)

    def _on_safety_sensor_event(self, payload):
        smoke_state = str(payload.get("smokeState") or "UNKNOWN")
        smoke_health = str(
            payload.get("smokeSensorHealth") or "UNKNOWN"
        )
        self._store.set_state("smoke_state", smoke_state)
        self._store.set_state("smoke_sensor_health", smoke_health)
        port_no = payload.get("portNo")
        if isinstance(port_no, int) and port_no > 0:
            self._store.set_state(
                f"port_{port_no}_smoke_state",
                smoke_state,
            )
            self._store.set_state(
                f"port_{port_no}_smoke_sensor_health",
                smoke_health,
            )
        deployment_code = (
            getattr(self._mqtt, "deployment_code", None) or "Dp_unknown"
        )
        work_type = payload.get("workType", "NONE")
        work_uid = payload.get("workUid")
        if work_type == "NONE":
            work_uid = None
        fault_code = payload.get("faultCode")
        event_payload = {
            "portNo": port_no if isinstance(port_no, int) and port_no > 0 else None,
            "smokeState": smoke_state,
            "smokeSensorHealth": smoke_health,
            "faultCode": (
                None if fault_code in (None, "NONE") else fault_code
            ),
            "workType": work_type,
            "workUid": work_uid,
            "mcuBootId": payload.get("mcuBootId"),
            "mcuEventSequence": payload.get("mcuEventSequence"),
        }
        event_uid = _new_uid()
        self._store.create_edge_event(
            event_uid=event_uid,
            event_type="SAFETY_SENSOR_STATE_CHANGED",
            payload=event_payload,
            work_uid=work_uid,
            deployment_code=deployment_code,
            target_type="DEVICE_DEPLOYMENT",
            target_uid=deployment_code,
        )

    def _on_fault_observed(self, payload):
        severity = str(payload.get("severity") or "WARNING")
        lifecycle = str(payload.get("lifecycle") or "OBSERVED")
        fault_uid = str(payload.get("faultUid") or _new_uid())
        self._store.record_fault(
            str(payload.get("component") or "MCU_INTERNAL"),
            _fault_code_number(payload.get("faultCode")),
            severity,
            dict(payload),
            fault_uid=fault_uid,
            lifecycle=lifecycle,
        )
        deployment_code = (
            getattr(self._mqtt, "deployment_code", None) or "Dp_unknown"
        )
        event_type = (
            "DEVICE_FAULT_RECOVERED"
            if lifecycle == "RECOVERED"
            else "DEVICE_FAULT_OBSERVED"
        )
        event_uid = _new_uid()
        self._store.create_edge_event(
            event_uid=event_uid,
            event_type=event_type,
            payload={
                "faultUid": fault_uid,
                "portNo": (
                    payload.get("portNo")
                    if isinstance(payload.get("portNo"), int)
                    and payload.get("portNo") > 0
                    else None
                ),
                "component": payload.get("component", "MCU_INTERNAL"),
                "severity": severity,
                "faultCode": payload.get("faultCode", "MCU_INTERNAL"),
                "mcuBootId": payload.get("mcuBootId"),
                "mcuEventSequence": payload.get("mcuEventSequence"),
            },
            work_uid=(
                payload.get("workUid")
                if payload.get("workType") != "NONE"
                else None
            ),
            deployment_code=deployment_code,
            target_type="DEVICE_DEPLOYMENT",
            target_uid=deployment_code,
        )

    def _on_boot_reconciliation_result(self, ctx, payload, work_uid):
        if (
            payload.get("decision") != "RESUME_CLEAN_OPERATION"
            or payload.get("mcuCommandUid")
            != ctx.get("resume_mcu_command_uid")
            or payload.get("activeWorkUid") != work_uid
            or payload.get("recoveryGeneration")
            != ctx.get("recovery_generation")
        ):
            raise ValueError("boot reconciliation result does not match clean")
        if payload.get("status") != "ACCEPTED":
            ctx["phase"] = "CLEAN_RECOVERY_FAILED"
            self._store.update_work_context(work_uid, ctx)
            raise ValueError(
                "clean resume rejected: "
                + str(payload.get("faultCode") or "MCU_INTERNAL")
            )
        ctx["phase"] = "CLEAN_RECOVERY_REQUIRED"
        self._store.update_work_context(work_uid, ctx)
        command_uid = ctx.get("resume_command_uid")
        if command_uid:
            self._store.complete_command(
                command_uid,
                {
                    "recoveryGeneration": ctx["recovery_generation"],
                    "state": "CLEAN_RECOVERY_REQUIRED",
                },
            )

    def _on_safe_close_result(self, payload):
        self._store.set_state(
            "last_safe_close_output_status",
            str(payload.get("outputStatus") or "UNKNOWN"),
        )
        self._store.set_state(
            "last_safe_close_physical_state_basis",
            str(
                payload.get("physicalDoorStateBasis")
                or "NOT_OBSERVABLE"
            ),
        )

    def _on_delivery_door_command_result(self, ctx, payload, work_uid):
        if payload.get("sessionUid") != work_uid:
            raise ValueError("door command result does not match delivery")
        ctx["last_delivery_door_command"] = payload.get("command")
        ctx["last_delivery_door_output_status"] = payload.get(
            "outputStatus"
        )
        ctx["delivery_door_physical_state_basis"] = payload.get(
            "physicalDoorStateBasis"
        )
        if payload.get("command") == "OPEN":
            ctx["phase"] = "DELIVERY_WINDOW"
        elif payload.get("command") == "CLOSE":
            ctx["phase"] = "WAITING_POSTCLOSE_WEIGHT"
        self._store.update_work_context(work_uid, ctx)

    def _on_preopen_weight(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("start_mcu_command_uid")
        ):
            raise ValueError("preopen measurement does not match active delivery")
        measurement_uid = payload.get("measurementUid", "")
        reported_weight = _reported_weight(payload)
        weight_grams = _delivery_usable_weight(payload)
        status = payload.get("measurementStatus", "STABLE")
        ctx["phase"] = "PREOPEN_MEASURED"
        ctx["first_reported_weight_grams"] = reported_weight
        ctx["first_measurement_status"] = status
        ctx["first_measurement_uid"] = measurement_uid
        ctx["first_measurement"] = dict(payload)
        if weight_grams is not None:
            ctx["first_weight_grams"] = weight_grams
        self._store.update_work_context(work_uid, ctx)
        start_command_uid = ctx.get("start_command_uid")
        if start_command_uid:
            self._store.complete_command(
                start_command_uid,
                {
                    "measurementUid": measurement_uid,
                    "measurementStatus": status,
                },
            )
        if weight_grams is None:
            ctx["phase"] = "PREOPEN_WEIGHT_BLOCKED"
            self._store.update_work_context(work_uid, ctx)
            return
        photo_results = self._photo.capture_open_photos(work_uid)
        if not photo_results or any(
            result.get("status") != "OK"
            for result in photo_results.values()
        ):
            ctx["phase"] = "PREOPEN_PHOTOS_FAILED"
            self._store.update_work_context(work_uid, ctx)
            return
        authorize_uid = ctx.get("authorize_mcu_command_uid") or _new_uid()
        ctx["authorize_mcu_command_uid"] = authorize_uid
        ctx["phase"] = "AUTHORIZING_FIRST_OPEN"
        self._store.update_work_context(work_uid, ctx)
        result = self._uart.send_command(
            "AUTHORIZE_DELIVERY_FIRST_OPEN",
            {
                "sessionUid": work_uid,
                "portNo": ctx["port_no"],
                "firstPreOpenMeasurementUid": measurement_uid,
                "parentStartCommandUid": ctx["start_mcu_command_uid"],
                "remainingStartAuthorizationMs": _remaining_until(
                    ctx["expires_at"]
                ),
            },
            mcu_command_uid=authorize_uid,
        )
        ctx["phase"] = (
            "WAITING_OPEN_COMMAND_RESULT"
            if result["acked"]
            else "FIRST_OPEN_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)
        logger.info("preopen weight: %d g", weight_grams or 0)

    def _on_postclose_weight(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
        ):
            raise ValueError("postclose measurement does not match delivery")
        reported_weight = _reported_weight(payload)
        weight_grams = _delivery_usable_weight(payload)
        status = payload.get("measurementStatus", "STABLE")
        round_idx = payload.get("roundIndex", 0)
        measurement_uid = payload.get("measurementUid", "")
        ctx["round_index"] = round_idx
        ctx[f"round_{round_idx}_reported_weight_grams"] = reported_weight
        ctx[f"round_{round_idx}_measurement_status"] = status
        ctx[f"round_{round_idx}_measurement_uid"] = measurement_uid
        ctx["final_measurement_uid"] = measurement_uid
        if weight_grams is not None:
            prev_key = "first_weight_grams" if round_idx == 1 else f"round_{round_idx-1}_open_weight"
            prev_weight = ctx.get(prev_key)
            threshold = ctx.get("negative_weight_threshold_grams", 500)
            if prev_weight is not None and (weight_grams - prev_weight) < -threshold:
                ctx["negative_weight_anomaly"] = True
            ctx[f"round_{round_idx}_close_weight"] = weight_grams
            ctx["final_weight_grams"] = weight_grams
        else:
            ctx["final_weight_grams"] = None
        ctx["final_measurement"] = dict(payload)
        ctx["phase"] = "WAITING_SELECTION"
        self._store.update_work_context(work_uid, ctx)
        logger.info("postclose weight round=%d wt=%d", round_idx, weight_grams or 0)

    def _on_delivery_selection(self, ctx, payload, work_uid):
        round_index = payload.get("roundIndex")
        measurement_uid = payload.get("postCloseMeasurementUid")
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or round_index != ctx.get("round_index")
            or measurement_uid
            != ctx.get(f"round_{round_index}_measurement_uid")
        ):
            raise ValueError("selection does not bind persisted postclose weight")
        selection = payload.get("selection", "END")
        session_uid = ctx.get("session_uid", work_uid)
        if selection in ("END", "WINDOW_EXPIRED"):
            ctx["phase"] = "FINALIZING"
            self._store.update_work_context(work_uid, ctx)
            self._photo.capture_close_photos(work_uid)
            first_wt = ctx.get("first_weight_grams")
            final_wt = ctx.get("final_weight_grams")
            net = None
            if first_wt is not None and final_wt is not None:
                net = final_wt - first_wt
            event_payload = {
                "sessionUid": session_uid,
                "portNo": ctx["port_no"],
                "firstPreOpenMeasurement": _measurement_fact(
                    ctx.get("first_measurement")
                ),
                "finalPostCloseMeasurement": _measurement_fact(
                    ctx.get("final_measurement")
                ),
                "deliveryNetWeightGrams": net,
                "finalDoorCommand": {
                    "command": ctx.get(
                        "last_delivery_door_command",
                        "CLOSE",
                    ),
                    "outputStatus": ctx.get(
                        "last_delivery_door_output_status",
                        "COMMAND_DISPATCHED",
                    ),
                    "physicalStateBasis": ctx.get(
                        "delivery_door_physical_state_basis",
                        "NOT_OBSERVABLE",
                    ),
                },
                "completionReason": (
                    "USER_ENDED"
                    if selection == "END"
                    else "SELECTION_WINDOW_EXPIRED"
                ),
                "manualReviewRequired": False,
                "negativeWeightAnomaly": ctx.get(
                    "negative_weight_anomaly",
                    False,
                ),
                "frozenConfig": _frozen_config(ctx["config"]),
                "unitPriceTenThousandths": ctx[
                    "unit_price_ten_thousandths"
                ],
                "photos": _pending_photo_facts(
                    (
                        "BEFORE_INNER",
                        "BEFORE_OUTER",
                        "AFTER_INNER",
                        "AFTER_OUTER",
                    )
                ),
            }
            self._create_reliable_event(
                event_type="DELIVERY_COMPLETE",
                target_type="DELIVERY_SESSION",
                work_uid=work_uid,
                command_uid=ctx.get("start_command_uid"),
                deployment_code=ctx.get("deployment_code"),
                payload=event_payload,
                work_state_update={
                    "state": "COMPLETING",
                    "context": ctx,
                },
            )
            logger.info("delivery complete: %s net=%d", session_uid, net or 0)
        else:
            round_idx = ctx.get("round_index", 0) + 1
            ctx["round_index"] = round_idx
            ctx[f"round_{round_idx}_open_weight"] = ctx.get(f"round_{round_idx-1}_close_weight")
            ctx["phase"] = "CONTINUING"
            self._store.update_work_context(work_uid, ctx)
            logger.info("continue delivery round=%d", round_idx)

    def finalize_delivery(self, work_uid):
        self._store.release_work_slot(work_uid)
        logger.info("delivery session ended: %s", work_uid)

    def start_clean_operation(self, operation_uid, port_no, old_bag_qr, new_bag_qr):
        ctx = {"operation_uid": operation_uid, "port_no": port_no,
               "old_bag_qr": old_bag_qr, "new_bag_qr": new_bag_qr,
               "phase": "STARTED", "action_sequence": 0,
               "preunlock_weight_grams": None, "preunlock_measurement_uid": None,
               "final_weight_grams": None, "final_measurement_uid": None,
               "completion_confirmed": False}
        ok = self._store.acquire_work_slot(WORK_TYPE_CLEAN, operation_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("clean operation started: %s port=%d", operation_uid, port_no)
        return {"success": True, "operation_uid": operation_uid}

    def _on_clean_preunlock_weight(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("start_mcu_command_uid")
        ):
            raise ValueError("preunlock measurement does not match active clean")
        measurement_uid = payload.get("measurementUid", "")
        ctx["preunlock_measurement_uid"] = measurement_uid
        ctx["preunlock_weight_grams"] = _reported_weight(payload)
        ctx["preunlock_measurement"] = dict(payload)
        ctx["phase"] = "PREUNLOCK_MEASURED"
        self._store.update_work_context(work_uid, ctx)
        start_command_uid = ctx.get("start_command_uid")
        if start_command_uid:
            self._store.complete_command(
                start_command_uid,
                {
                    "measurementUid": measurement_uid,
                    "measurementStatus": payload.get("measurementStatus"),
                },
            )
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            raise ValueError("no applied configuration for clean unlock")
        unlock_uid = ctx.get("unlock_mcu_command_uid") or _new_uid()
        ctx["unlock_mcu_command_uid"] = unlock_uid
        ctx["phase"] = "UNLOCKING"
        self._store.update_work_context(work_uid, ctx)
        result = self._uart.send_command(
            "UNLOCK_CLEAN_DOOR",
            {
                "operationUid": work_uid,
                "portNo": ctx["port_no"],
                "cleanActionSequence": ctx["action_sequence"],
                "recoveryGeneration": ctx["recovery_generation"],
                "unlockPulseMs": applied["payload"]["deviceConfig"][
                    "cleanSolenoidPulseMs"
                ],
                "remainingOperationWindowMs": _remaining_until(
                    ctx["operation_deadline"]
                ),
                "parentCommandUid": ctx["start_mcu_command_uid"],
            },
            mcu_command_uid=unlock_uid,
        )
        ctx["phase"] = (
            "WAITING_LOCK_OUTPUT"
            if result["acked"]
            else "UNLOCK_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_unlock_requested(self, ctx, payload, work_uid):
        if payload.get("operationUid") != work_uid:
            raise ValueError("unlock request does not match active clean")
        action_sequence = payload.get("cleanActionSequence")
        if (
            not isinstance(action_sequence, int)
            or action_sequence <= ctx.get("action_sequence", 0)
        ):
            raise ValueError("clean action sequence did not advance")
        ctx["action_sequence"] = action_sequence
        ctx["final_weight_grams"] = None
        ctx["final_measurement_uid"] = None
        ctx["final_measurement"] = None
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            raise ValueError("no applied configuration for clean unlock")
        unlock_uid = _new_uid()
        ctx["unlock_mcu_command_uid"] = unlock_uid
        ctx["phase"] = "REUNLOCKING"
        self._store.update_work_context(work_uid, ctx)
        parent_uid = (
            ctx.get("resume_mcu_command_uid")
            or ctx["start_mcu_command_uid"]
        )
        result = self._uart.send_command(
            "UNLOCK_CLEAN_DOOR",
            {
                "operationUid": work_uid,
                "portNo": ctx["port_no"],
                "cleanActionSequence": action_sequence,
                "recoveryGeneration": ctx["recovery_generation"],
                "unlockPulseMs": applied["payload"]["deviceConfig"][
                    "cleanSolenoidPulseMs"
                ],
                "remainingOperationWindowMs": _remaining_until(
                    ctx["operation_deadline"]
                ),
                "parentCommandUid": parent_uid,
            },
            mcu_command_uid=unlock_uid,
        )
        ctx["phase"] = (
            "WAITING_LOCK_OUTPUT"
            if result["acked"]
            else "UNLOCK_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_finish_requested(self, ctx, payload, work_uid):
        if payload.get("operationUid") != work_uid:
            raise ValueError("finish request does not match active clean")
        action_sequence = payload.get("cleanActionSequence")
        if (
            not isinstance(action_sequence, int)
            or action_sequence <= ctx.get("action_sequence", 0)
        ):
            raise ValueError("clean action sequence did not advance")
        ctx["action_sequence"] = action_sequence
        ctx["final_weight_grams"] = None
        ctx["final_measurement_uid"] = None
        ctx["final_measurement"] = None
        ctx["phase"] = "WAITING_FINAL_WEIGHT"
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_lock_power_changed(self, ctx, payload, work_uid):
        if payload.get("operationUid") != work_uid:
            raise ValueError("lock event does not match active clean")
        ctx["clean_lock_power_state"] = payload.get("lockPowerState")
        ctx["clean_solenoid_health"] = payload.get("solenoidHealth")
        if payload.get("lockPowerState") == "DEENERGIZED":
            ctx["phase"] = "ACTIVE"
        self._store.update_work_context(work_uid, ctx)

    def authorize_clean_unlock(self, operation_uid):
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != operation_uid:
            return {"success": False, "reason": "OPERATION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        if not ctx.get("preunlock_measurement_uid"):
            ctx["preunlock_measurement_uid"] = _new_uid()
            self._store.update_work_context(operation_uid, ctx)
        ctx["action_sequence"] = ctx.get("action_sequence", 0) + 1
        action_seq = ctx["action_sequence"]
        ctx["phase"] = "UNLOCKING"
        self._store.update_work_context(operation_uid, ctx)
        result = self._uart.send_unlock_clean_door(
            operation_uid=operation_uid, port_no=port_no,
            action_sequence=action_seq,
            preunlock_measurement_uid=ctx["preunlock_measurement_uid"])
        if result["acked"]:
            ctx["phase"] = "ACTIVE"
            self._store.update_work_context(operation_uid, ctx)
            return {"success": True, "operation_uid": operation_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def _on_clean_final_weight(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("cleanActionSequence")
            != ctx.get("action_sequence")
        ):
            raise ValueError("final measurement does not match active clean")
        weight_grams = _reported_weight(payload)
        ctx["final_weight_grams"] = weight_grams
        ctx["final_measurement_status"] = payload.get("measurementStatus")
        ctx["final_weight_value_kind"] = payload.get("weightValueKind")
        ctx["final_measurement_uid"] = payload.get("measurementUid", "")
        ctx["final_measurement"] = dict(payload)
        ctx["phase"] = "FINAL_WEIGHT_READY"
        self._store.update_work_context(work_uid, ctx)
        logger.info("clean final weight: %d g", weight_grams or 0)

    def _on_clean_completion(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("cleanActionSequence")
            != ctx.get("action_sequence")
            or payload.get("finalMeasurementUid")
            != ctx.get("final_measurement_uid")
            or payload.get("lockPowerState") != "DEENERGIZED"
            or payload.get("cleanDoorStateBasis")
            != "CLEANER_CONFIRMATION"
            or payload.get("cleanerPhysicalCloseConfirmed") is not True
        ):
            raise ValueError("clean completion lacks matching manual confirmation")
        ctx["phase"] = "COMPLETION_CONFIRMED"
        ctx["completion_confirmed"] = True
        ctx["clean_lock_power_state"] = payload.get("lockPowerState")
        ctx["clean_solenoid_health"] = payload.get("solenoidHealth")
        ctx["clean_door_state_basis"] = payload.get(
            "cleanDoorStateBasis"
        )
        ctx["cleaner_physical_close_confirmed"] = True
        self._store.update_work_context(work_uid, ctx)
        self._photo.capture_clean_photos(work_uid)
        final_usable = _delivery_usable_weight(
            ctx.get("final_measurement") or {}
        )
        preunlock_weight = ctx.get("preunlock_weight_grams")
        removed_weight = (
            preunlock_weight - final_usable
            if preunlock_weight is not None and final_usable is not None
            else None
        )
        event_payload = {
            "operationUid": ctx["operation_uid"],
            "portNo": ctx["port_no"],
            "oldBagUid": ctx.get("old_bag_uid"),
            "newBagUid": ctx.get("new_bag_uid"),
            "preUnlockMeasurement": _measurement_fact(
                ctx.get("preunlock_measurement")
            ),
            "cleanerConfirmedFinalMeasurement": _measurement_fact(
                ctx.get("final_measurement")
            ),
            "removedNetWeightGrams": removed_weight,
            "newBaselineWeightGrams": final_usable,
            "cleanerCompletionConfirmed": True,
            "cleanActionSequence": ctx["action_sequence"],
            "cleanLockAndManualDoorConfirmation": {
                "lockPowerState": payload.get("lockPowerState"),
                "solenoidHealth": payload.get("solenoidHealth"),
                "physicalDoorStateBasis": payload.get(
                    "cleanDoorStateBasis"
                ),
                "cleanerPhysicalCloseConfirmed": True,
            },
            "frozenConfig": _frozen_config(ctx["config"]),
            "photos": _pending_photo_facts(
                (
                    "FIRST_OPEN_INNER",
                    "FIRST_OPEN_OUTER",
                    "FINAL_CLOSE_INNER",
                    "FINAL_CLOSE_OUTER",
                )
            ),
        }
        self._create_reliable_event(
            event_type="CLEAN_COMPLETE",
            target_type="CLEAN_OPERATION",
            work_uid=work_uid,
            command_uid=ctx.get("start_command_uid"),
            deployment_code=ctx.get("deployment_code"),
            payload=event_payload,
            work_state_update={
                "state": "COMPLETING",
                "context": ctx,
            },
        )
        logger.info("clean complete: %s", ctx["operation_uid"])

    def finalize_clean(self, work_uid):
        self._store.release_work_slot(work_uid)
        logger.info("clean operation ended: %s", work_uid)

    def _on_fullness_sample_result(self, ctx, payload, work_uid):
        if (
            payload.get("detectionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
        ):
            raise ValueError("fullness result does not match active detection")
        distance = (
            payload.get("representativeDistanceMm")
            if payload.get("representativeDistancePresent")
            else None
        )
        event_payload = {
            "detectionUid": work_uid,
            "portNo": ctx["port_no"],
            "sampleRole": payload.get("sampleRole"),
            "triggerType": ctx["payload"].get("triggerType"),
            "fullnessMode": ctx["payload"].get("fullnessMode"),
            "fullnessSensorKind": payload.get("fullnessSensorKind"),
            "fullnessSensorValue": payload.get("fullnessSensorValue"),
            "fullnessSampleBasis": payload.get("fullnessSampleBasis"),
            "representativeDistanceMm": distance,
            "requestedSampleCount": payload.get("requestedSampleCount"),
            "validSampleCount": payload.get("validSampleCount"),
            "totalWeightMeasurement": _measurement_fact(payload),
            "frozenConfig": _frozen_config(ctx["payload"]["config"]),
        }
        self._store.complete_command(
            ctx["command_uid"],
            {
                "fullnessSensorValue": payload.get(
                    "fullnessSensorValue"
                ),
                "fullnessSampleBasis": payload.get(
                    "fullnessSampleBasis"
                ),
            },
        )
        self._create_reliable_event(
            event_type="FULLNESS_SAMPLE_COMPLETE",
            target_type="FULLNESS_DETECTION",
            command_uid=ctx["command_uid"],
            deployment_code=ctx.get("deployment_code"),
            payload=event_payload,
            work_uid=work_uid,
            work_state_update={"state": "COMPLETED", "context": ctx},
        )
        self._store.release_work_slot(work_uid)

    def _on_baseline_measurement_result(self, ctx, payload, work_uid):
        if (
            payload.get("measurementUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
        ):
            raise ValueError("baseline result does not match active measurement")
        event_payload = {
            "measurementUid": work_uid,
            "portNo": ctx["port_no"],
            "bagUid": ctx["bag_uid"],
            "emptyBagConfirmed": ctx["empty_bag_confirmed"],
            "totalWeightMeasurement": _measurement_fact(payload),
            "frozenConfig": _frozen_config(ctx["config"]),
        }
        self._store.complete_command(
            ctx["command_uid"],
            {
                "measurementStatus": payload.get("measurementStatus"),
                "weightValuePresent": payload.get("weightValuePresent"),
            },
        )
        self._create_reliable_event(
            event_type="BASELINE_MEASUREMENT_COMPLETE",
            target_type="BASELINE_MEASUREMENT",
            command_uid=ctx["command_uid"],
            deployment_code=ctx.get("deployment_code"),
            payload=event_payload,
            work_uid=work_uid,
            work_state_update={"state": "COMPLETED", "context": ctx},
        )
        self._store.release_work_slot(work_uid)

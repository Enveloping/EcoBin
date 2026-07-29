"""work_manager.py -- delivery session and clean operation state machines."""
from __future__ import annotations
import json
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
logger = logging.getLogger("work-manager")
def _new_uid() -> str:
    return str(_uuid.uuid4())


def _compat_uid(work_uid: str, label: str) -> str:
    return _new_uid()


def _compat_measurement(
    work_uid: str,
    label: str,
    weight_grams: int,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "measurementUid": _compat_uid(work_uid, label),
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": weight_grams,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 0,
        "sampleCount": 1,
        "calibrationVersion": 0,
        "weightSensorHealth": "OK",
        "faultCode": "NONE",
        "mcuBootId": source["mcuBootId"],
        "mcuEventSequence": source["mcuEventSequence"],
    }


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


class WorkManager:

    def __init__(self, store: EdgeStore, uart_link, mqtt_client, photo_manager):
        self._store = store
        self._uart = uart_link
        self._mqtt = mqtt_client
        self._photo = photo_manager

    def _capture_photos(self, method_name: str, work_uid: str) -> bool:
        if self._photo is None:
            logger.warning(
                "photo manager unavailable: work=%s",
                work_uid,
            )
            return False
        method = getattr(self._photo, method_name, None)
        if method is None:
            logger.warning(
                "photo capture method unavailable: %s work=%s",
                method_name,
                work_uid,
            )
            return False
        try:
            captured = method(work_uid)
        except Exception as error:
            logger.warning(
                "photo capture persistence failed: work=%s error=%s",
                work_uid,
                error,
            )
            return False
        if captured is False:
            logger.warning(
                "photo capture was not persisted: work=%s",
                work_uid,
            )
            return False
        return True

    def _offer_initial_photo_grant(
        self,
        command: dict[str, Any],
        work_type: str,
        work_uid: str,
    ) -> None:
        grant = command.get("cosGrant")
        if grant is None or self._photo is None:
            return
        method = getattr(self._photo, "offer_initial_grant", None)
        if method is None:
            return
        method(work_type, work_uid, grant)

    def _completion_photo_facts(
        self,
        work_uid: str,
        work_type: str,
        slots: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if self._photo is not None:
            method = getattr(
                self._photo,
                "get_completion_photo_facts",
                None,
            )
            if method is not None:
                try:
                    return method(work_uid, work_type)
                except Exception:
                    logger.exception(
                        "completion photo snapshot failed: work=%s",
                        work_uid,
                    )
        return _pending_photo_facts(slots)

    def _reject_command(
        self,
        command: dict[str, Any],
        error_code: str,
    ) -> dict[str, Any]:
        self._store.record_command_observation(
            command,
            "REJECTED",
            error_code=error_code,
        )
        return {
            "acked": False,
            "error": error_code,
        }

    def _safety_rejection(self, port_no: int) -> Optional[str]:
        smoke_state = self._store.get_state(
            f"port_{port_no}_smoke_state",
            self._store.get_state("smoke_state", ""),
        )
        smoke_health = self._store.get_state(
            f"port_{port_no}_smoke_sensor_health",
            self._store.get_state("smoke_sensor_health", ""),
        )
        if smoke_state == "ALARM":
            return "SAFETY_SMOKE_ALARM"
        if smoke_health and smoke_health != "OK":
            return "SAFETY_SENSOR_UNHEALTHY"
        return None

    def accept_photo_upload_grant(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        if self._photo is None:
            raise RuntimeError("photo manager is required")
        method = getattr(self._photo, "offer_upload_grant", None)
        if method is None:
            raise RuntimeError("photo upload is not supported")
        return method(command)

    @property
    def active_delivery_session(self) -> Optional[dict]:
        slot = self._store.get_work_slot()
        if slot and slot["work_type"] == WORK_TYPE_DELIVERY:
            return slot
        return None

    def expire_fixed_frame_work(self) -> bool:
        """Fail an expired DD/EF wait without replaying its physical command."""
        if not getattr(self._uart, "compatibility_mode", False):
            return False
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] not in {
            WORK_TYPE_DELIVERY,
            WORK_TYPE_CLEAN,
        }:
            return False
        context = slot["context"]
        deadline = (
            context.get("expires_at")
            if slot["work_type"] == WORK_TYPE_DELIVERY
            else context.get("operation_deadline")
        )
        if not deadline:
            return False
        try:
            if _remaining_until(deadline) > 0:
                return False
        except ValueError:
            pass
        command_uid = context.get("start_command_uid")
        command_row = (
            self._store.get_command(command_uid)
            if command_uid
            else None
        )
        command = (
            command_row.get("payload")
            if command_row
            else None
        )
        if command is None:
            self._store.release_work_slot(slot["work_uid"])
            return True
        return self._store.fail_fixed_frame_work(
            work_uid=slot["work_uid"],
            command=command,
            error_code="MCU_RESULT_TIMEOUT",
            mcu_command_uid=context.get("start_mcu_command_uid"),
            stage="FAILED",
        )

    def start_delivery_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Persist and dispatch one cloud-authorized delivery session."""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        safety_error = self._safety_rejection(payload["portNo"])
        if safety_error:
            return self._reject_command(command, safety_error)
        if (
            getattr(self._uart, "compatibility_mode", False)
            and payload["portNo"] != 1
        ):
            return self._reject_command(
                command,
                "MCU_FEATURE_NOT_SUPPORTED",
            )
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
            observed_command=command,
        ):
            return self._reject_command(command, "DEVICE_BUSY")
        self._offer_initial_photo_grant(
            command,
            "DELIVERY_SESSION",
            session_uid,
        )
        compatibility_mode = getattr(
            self._uart,
            "compatibility_mode",
            False,
        )
        if compatibility_mode:
            self._capture_photos(
                "capture_open_photos",
                session_uid,
            )
            try:
                start_window_ms = _remaining_execution_ms(command)
            except ValueError:
                self._store.fail_fixed_frame_work(
                    work_uid=session_uid,
                    command=command,
                    error_code="COMMAND_EXPIRED",
                    mcu_command_uid=mcu_command_uid,
                    stage="PRE_START_FAILED",
                )
                return {
                    "acked": False,
                    "error": "COMMAND_EXPIRED",
                }
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
        if compatibility_mode:
            ctx["phase"] = (
                "WAITING_COMPAT_DELIVERY_RESULT"
                if result["acked"]
                else "START_FAILED"
            )
        else:
            ctx["phase"] = (
                "WAITING_PREOPEN_WEIGHT"
                if result["acked"]
                else "START_RESULT_UNKNOWN"
            )
        self._store.update_work_context(session_uid, ctx)
        if result["acked"]:
            self._store.record_command_observation(
                command,
                "MCU_ACCEPTED",
                mcu_command_uid=mcu_command_uid,
            )
        elif compatibility_mode:
            error_code = str(
                result.get("error") or "UART_WRITE_FAILED"
            )
            stage = (
                "PRE_START_FAILED"
                if error_code in {
                    "UART_CLOSED",
                    "MCU_FEATURE_NOT_SUPPORTED",
                }
                else "FAILED"
            )
            self._store.fail_fixed_frame_work(
                work_uid=session_uid,
                command=command,
                error_code=error_code,
                mcu_command_uid=mcu_command_uid,
                stage=stage,
            )
        return result

    def start_clean_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Persist and dispatch one cloud-authorized clean operation."""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        safety_error = self._safety_rejection(payload["portNo"])
        if safety_error:
            return self._reject_command(command, safety_error)
        if (
            getattr(self._uart, "compatibility_mode", False)
            and payload["portNo"] != 1
        ):
            return self._reject_command(
                command,
                "MCU_FEATURE_NOT_SUPPORTED",
            )
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
            observed_command=command,
        ):
            return self._reject_command(command, "DEVICE_BUSY")
        self._offer_initial_photo_grant(
            command,
            "CLEAN_OPERATION",
            operation_uid,
        )
        compatibility_mode = getattr(
            self._uart,
            "compatibility_mode",
            False,
        )
        if compatibility_mode:
            self._capture_photos(
                "capture_clean_open_photos",
                operation_uid,
            )
            try:
                start_window_ms = _remaining_execution_ms(command)
            except ValueError:
                self._store.fail_fixed_frame_work(
                    work_uid=operation_uid,
                    command=command,
                    error_code="COMMAND_EXPIRED",
                    mcu_command_uid=mcu_command_uid,
                    stage="PRE_START_FAILED",
                )
                return {
                    "acked": False,
                    "error": "COMMAND_EXPIRED",
                }
        else:
            start_window_ms = _remaining_execution_ms(command)
        result = self._uart.send_command(
            "START_CLEAN_OPERATION",
            {
                "operationUid": operation_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": start_window_ms,
                "operationWindowMs": payload["operationWindowMs"],
            },
            mcu_command_uid=mcu_command_uid,
        )
        if compatibility_mode:
            ctx["phase"] = (
                "WAITING_COMPAT_CLEAN_RESULT"
                if result["acked"]
                else "START_FAILED"
            )
        else:
            ctx["phase"] = (
                "WAITING_PREUNLOCK_WEIGHT"
                if result["acked"]
                else "START_RESULT_UNKNOWN"
            )
        self._store.update_work_context(operation_uid, ctx)
        if result["acked"]:
            self._store.record_command_observation(
                command,
                "MCU_ACCEPTED",
                mcu_command_uid=mcu_command_uid,
            )
        elif compatibility_mode:
            error_code = str(
                result.get("error") or "UART_WRITE_FAILED"
            )
            stage = (
                "PRE_START_FAILED"
                if error_code in {
                    "UART_CLOSED",
                    "MCU_FEATURE_NOT_SUPPORTED",
                }
                else "FAILED"
            )
            self._store.fail_fixed_frame_work(
                work_uid=operation_uid,
                command=command,
                error_code=error_code,
                mcu_command_uid=mcu_command_uid,
                stage=stage,
            )
        return result

    def start_fullness_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        if getattr(self._uart, "compatibility_mode", False):
            return self._start_compat_fullness_command(command)
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

    def _start_compat_fullness_command(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        payload = command["payload"]
        try:
            observation = json.loads(
                self._store.get_state(
                    "fixed_frame_latest_observation_json",
                    "",
                )
            )
        except (TypeError, ValueError):
            observation = None
        has_history = (
            isinstance(observation, dict)
            and observation.get("portNo") == payload["portNo"]
            and self._valid_compat_fullness_observation(observation)
        )
        if not has_history:
            observation = {
                "postWeightGrams": 0,
                "infraredBlocked": False,
                "mcuBootId": max(
                    1,
                    int(self._store.get_edge_boot_id() or 1),
                ),
                "mcuEventSequence": (
                    self._store.reserve_compat_mcu_event_sequence()
                ),
            }
        detection_uid = payload["detectionUid"]
        measurement = _compat_measurement(
            detection_uid,
            "total-weight",
            observation["postWeightGrams"],
            observation,
        )
        event_payload = {
            "detectionUid": detection_uid,
            "portNo": payload["portNo"],
            "sampleRole": payload["sampleRole"],
            "triggerType": payload["triggerType"],
            "fullnessMode": payload["fullnessMode"],
            "fullnessSensorKind": "DIGITAL_INFRARED",
            "fullnessSensorValue": (
                "BLOCKED"
                if observation["infraredBlocked"]
                else "CLEAR"
            ),
            "fullnessSampleBasis": "NOT_SAMPLED",
            "representativeDistanceMm": None,
            "requestedSampleCount": 1 if has_history else 0,
            "validSampleCount": 1 if has_history else 0,
            "totalWeightMeasurement": _measurement_fact(measurement),
            "frozenConfig": _frozen_config(payload["config"]),
        }
        full_weight = payload["configuredFullWeightGrams"]
        fullness_percent = (
            observation["postWeightGrams"] * 100.0 / full_weight
        )
        local_result = {
            "fullnessSensorValue": event_payload[
                "fullnessSensorValue"
            ],
            "fullnessSampleBasis": "NOT_SAMPLED",
            "fullnessPercent": fullness_percent,
            "fullnessState": (
                "FULL"
                if fullness_percent >= 100.0
                else "NOT_FULL"
            ),
            "resultSource": (
                "LATEST_FLOW_POST"
                if has_history
                else "NO_HISTORY_ZERO"
            ),
        }
        completed = self._store.complete_fixed_frame_local_result(
            result_type="FULLNESS",
            result_key=(
                f"{detection_uid}:{payload['sampleRole']}"
            ),
            command=command,
            event_uid=_new_uid(),
            event_type="FULLNESS_SAMPLE_COMPLETE",
            target_type="FULLNESS_DETECTION",
            target_uid=detection_uid,
            event_payload=event_payload,
            result=local_result,
        )
        if completed not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame fullness persistence {completed.lower()}"
            )
        return {
            "acked": True,
            "completed_locally": True,
            "mcu_command_uid": None,
            "disposition": local_result["resultSource"],
        }

    @staticmethod
    def _valid_compat_fullness_observation(
        observation: dict[str, Any],
    ) -> bool:
        post_weight = observation.get("postWeightGrams")
        mcu_boot_id = observation.get("mcuBootId")
        event_sequence = observation.get("mcuEventSequence")
        return (
            isinstance(post_weight, int)
            and not isinstance(post_weight, bool)
            and 0 <= post_weight <= 350_000
            and isinstance(observation.get("infraredBlocked"), bool)
            and isinstance(mcu_boot_id, int)
            and not isinstance(mcu_boot_id, bool)
            and mcu_boot_id > 0
            and isinstance(event_sequence, int)
            and not isinstance(event_sequence, bool)
            and event_sequence > 0
        )

    def start_baseline_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        if getattr(self._uart, "compatibility_mode", False):
            return self._start_compat_baseline_command(command)
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

    def _start_compat_baseline_command(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        payload = command["payload"]
        if payload.get("emptyBagConfirmed") is not True:
            return self._reject_command(
                command,
                "EMPTY_BAG_NOT_CONFIRMED",
            )
        measurement_uid = payload["measurementUid"]
        bag_uid = payload["bagUid"]
        saved = self._store.get_bag_baseline(bag_uid)
        if saved:
            source_kind = (
                saved.get("source_kind")
                or "SAME_BAG_CLEAN_POST"
            )
            weight_grams = saved["weight_grams"]
            source = {
                "mcuBootId": (
                    saved.get("source_mcu_boot_id")
                    or max(
                        1,
                        int(self._store.get_edge_boot_id() or 1),
                    )
                ),
                "mcuEventSequence": (
                    saved.get("source_mcu_event_sequence")
                    or self._store.reserve_compat_mcu_event_sequence()
                ),
            }
            source_work_type = saved.get("source_work_type")
            source_work_uid = saved.get("source_work_uid")
        else:
            try:
                observation = json.loads(
                    self._store.get_state(
                        "fixed_frame_latest_observation_json",
                        "",
                    )
                )
            except (TypeError, ValueError):
                observation = None
            if (
                isinstance(observation, dict)
                and self._valid_compat_fullness_observation(
                    observation
                )
            ):
                source_kind = "LATEST_FLOW_POST"
                weight_grams = observation["postWeightGrams"]
                source = observation
                source_work_type = observation.get("sourceWorkType")
                source_work_uid = observation.get("sourceWorkUid")
            else:
                source_kind = "NO_HISTORY_ZERO"
                weight_grams = 0
                source = {
                    "mcuBootId": max(
                        1,
                        int(self._store.get_edge_boot_id() or 1),
                    ),
                    "mcuEventSequence": (
                        self._store.reserve_compat_mcu_event_sequence()
                    ),
                }
                source_work_type = None
                source_work_uid = None
        measurement = _compat_measurement(
            measurement_uid,
            "baseline",
            weight_grams,
            source,
        )
        event_payload = {
            "measurementUid": measurement_uid,
            "portNo": payload["portNo"],
            "bagUid": bag_uid,
            "emptyBagConfirmed": True,
            "totalWeightMeasurement": _measurement_fact(measurement),
            "frozenConfig": _frozen_config(payload["config"]),
        }
        result = {
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": weight_grams,
            "compatibilitySource": source_kind,
        }
        completed = self._store.complete_fixed_frame_local_result(
            result_type="BASELINE",
            result_key=measurement_uid,
            command=command,
            event_uid=_new_uid(),
            event_type="BASELINE_MEASUREMENT_COMPLETE",
            target_type="BASELINE_MEASUREMENT",
            target_uid=measurement_uid,
            event_payload=event_payload,
            result=result,
            bag_baseline={
                "bag_uid": bag_uid,
                "weight_grams": weight_grams,
                "source_kind": source_kind,
                "source_work_type": source_work_type,
                "source_work_uid": source_work_uid,
                "source_mcu_boot_id": source.get("mcuBootId"),
                "source_mcu_event_sequence": source.get(
                    "mcuEventSequence"
                ),
                "source_observed_at": None,
                "measurement_uid": measurement[
                    "measurementUid"
                ],
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(
                    timespec="milliseconds"
                ).replace("+00:00", "Z"),
            },
        )
        if completed not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame baseline persistence {completed.lower()}"
            )
        return {
            "acked": True,
            "completed_locally": True,
            "mcu_command_uid": None,
            "disposition": source_kind,
        }

    def end_clean_before_unlock_command(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        if getattr(self._uart, "compatibility_mode", False):
            return {
                "acked": False,
                "error": "MCU_FEATURE_NOT_SUPPORTED",
            }
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
        if getattr(self._uart, "compatibility_mode", False):
            return {
                "acked": False,
                "error": "MCU_FEATURE_NOT_SUPPORTED",
            }
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
        self._offer_initial_photo_grant(
            command,
            "CLEAN_OPERATION",
            payload["operationUid"],
        )
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
        if msg_name == "COMPAT_DELIVERY_RESULT":
            self._on_compat_delivery_result(payload)
            return
        if msg_name == "COMPAT_CLEAN_RESULT":
            self._on_compat_clean_result(payload)
            return
        if msg_name == "SAFETY_SENSOR_EVENT":
            self._on_safety_sensor_event(
                payload,
                int(frame.get("mcu_receive_generation") or 0),
            )
            return
        if msg_name == "FAULT_OBSERVED":
            self._on_fault_observed(
                payload,
                int(frame.get("mcu_receive_generation") or 0),
            )
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

    def _on_compat_delivery_result(self, payload: dict[str, Any]) -> None:
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] != WORK_TYPE_DELIVERY:
            logger.warning(
                "ignoring DD result without active delivery work"
            )
            return
        pre_weight, post_weight = self._compat_result_weights(payload)
        work_uid = slot["work_uid"]
        ctx = slot["context"]
        first_measurement = _compat_measurement(
            work_uid,
            "pre",
            pre_weight,
            payload,
        )
        final_measurement = _compat_measurement(
            work_uid,
            "post",
            post_weight,
            payload,
        )
        ctx.update({
            "phase": "COMPLETING",
            "round_index": 1,
            "first_weight_grams": pre_weight,
            "first_measurement_uid": first_measurement["measurementUid"],
            "first_measurement": first_measurement,
            "final_weight_grams": post_weight,
            "final_measurement_uid": final_measurement["measurementUid"],
            "final_measurement": final_measurement,
            "negative_weight_anomaly": False,
            "last_delivery_door_command": "CLOSE",
            "last_delivery_door_output_status": "COMMAND_DISPATCHED",
            "delivery_door_physical_state_basis": "NOT_OBSERVABLE",
        })
        self._capture_photos(
            "capture_close_photos",
            work_uid,
        )
        event_payload = {
            "sessionUid": ctx.get("session_uid", work_uid),
            "portNo": ctx["port_no"],
            "firstPreOpenMeasurement": _measurement_fact(first_measurement),
            "finalPostCloseMeasurement": _measurement_fact(final_measurement),
            "deliveryNetWeightGrams": post_weight - pre_weight,
            "finalDoorCommand": {
                "command": "CLOSE",
                "outputStatus": "COMMAND_DISPATCHED",
                "physicalStateBasis": "NOT_OBSERVABLE",
            },
            "completionReason": "USER_ENDED",
            "manualReviewRequired": False,
            "negativeWeightAnomaly": False,
            "frozenConfig": _frozen_config(ctx["config"]),
            "unitPriceTenThousandths": ctx[
                "unit_price_ten_thousandths"
            ],
            "photos": self._completion_photo_facts(
                work_uid,
                "DELIVERY_SESSION",
                (
                    "BEFORE_INNER",
                    "BEFORE_OUTER",
                    "AFTER_INNER",
                    "AFTER_OUTER",
                ),
            ),
        }
        observation = {
            "sourceWorkType": WORK_TYPE_DELIVERY,
            "sourceWorkUid": work_uid,
            "portNo": ctx["port_no"],
            "postWeightGrams": post_weight,
            "infraredBlocked": payload["infraredBlocked"],
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
        }
        created = self._store.complete_fixed_frame_work(
            work_type=WORK_TYPE_DELIVERY,
            work_uid=work_uid,
            command_uid=ctx["start_command_uid"],
            command_result={
                "preWeightGrams": pre_weight,
                "postWeightGrams": post_weight,
                "resultSource": "FIXED_FRAME_DD",
            },
            context=ctx,
            observation=observation,
            event_uid=_new_uid(),
            event_type="DELIVERY_COMPLETE",
            event_payload=event_payload,
            deployment_code=ctx.get("deployment_code") or "Dp_unknown",
            target_type="DELIVERY_SESSION",
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame delivery persistence {created.lower()}"
            )
        logger.info(
            "fixed-frame delivery complete: %s net=%d",
            work_uid,
            post_weight - pre_weight,
        )

    def _on_compat_clean_result(self, payload: dict[str, Any]) -> None:
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] != WORK_TYPE_CLEAN:
            logger.warning("ignoring EF result without active clean work")
            return
        pre_weight, post_weight = self._compat_result_weights(payload)
        work_uid = slot["work_uid"]
        ctx = slot["context"]
        pre_measurement = _compat_measurement(
            work_uid,
            "pre",
            pre_weight,
            payload,
        )
        final_measurement = _compat_measurement(
            work_uid,
            "post",
            post_weight,
            payload,
        )
        ctx.update({
            "phase": "COMPLETING",
            "action_sequence": 1,
            "preunlock_weight_grams": pre_weight,
            "preunlock_measurement_uid": pre_measurement["measurementUid"],
            "preunlock_measurement": pre_measurement,
            "final_weight_grams": post_weight,
            "final_measurement_uid": final_measurement["measurementUid"],
            "final_measurement": final_measurement,
            "completion_confirmed": True,
            "clean_lock_power_state": "DEENERGIZED",
            "clean_solenoid_health": "UNKNOWN",
            "clean_door_state_basis": "CLEANER_CONFIRMATION",
            "cleaner_physical_close_confirmed": True,
        })
        self._capture_photos(
            "capture_clean_close_photos",
            work_uid,
        )
        old_baseline = ctx.get("old_baseline_weight_grams")
        removed_weight = (
            pre_weight - old_baseline
            if isinstance(old_baseline, int)
            and not isinstance(old_baseline, bool)
            else None
        )
        event_payload = {
            "operationUid": ctx.get("operation_uid", work_uid),
            "portNo": ctx["port_no"],
            "oldBagUid": ctx.get("old_bag_uid"),
            "newBagUid": ctx.get("new_bag_uid"),
            "preUnlockMeasurement": _measurement_fact(pre_measurement),
            "cleanerConfirmedFinalMeasurement": _measurement_fact(
                final_measurement
            ),
            "removedNetWeightGrams": removed_weight,
            "newBaselineWeightGrams": post_weight,
            "cleanerCompletionConfirmed": True,
            "cleanActionSequence": 1,
            "cleanLockAndManualDoorConfirmation": {
                "lockPowerState": "DEENERGIZED",
                "solenoidHealth": "UNKNOWN",
                "physicalDoorStateBasis": "CLEANER_CONFIRMATION",
                "cleanerPhysicalCloseConfirmed": True,
            },
            "frozenConfig": _frozen_config(ctx["config"]),
            "photos": self._completion_photo_facts(
                work_uid,
                "CLEAN_OPERATION",
                (
                    "FIRST_OPEN_INNER",
                    "FIRST_OPEN_OUTER",
                    "FINAL_CLOSE_INNER",
                    "FINAL_CLOSE_OUTER",
                ),
            ),
        }
        observation = {
            "sourceWorkType": WORK_TYPE_CLEAN,
            "sourceWorkUid": work_uid,
            "portNo": ctx["port_no"],
            "postWeightGrams": post_weight,
            "infraredBlocked": payload["infraredBlocked"],
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
        }
        now = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        created = self._store.complete_fixed_frame_work(
            work_type=WORK_TYPE_CLEAN,
            work_uid=work_uid,
            command_uid=ctx["start_command_uid"],
            command_result={
                "preWeightGrams": pre_weight,
                "postWeightGrams": post_weight,
                "resultSource": "FIXED_FRAME_EF",
            },
            context=ctx,
            observation=observation,
            event_uid=_new_uid(),
            event_type="CLEAN_COMPLETE",
            event_payload=event_payload,
            deployment_code=ctx.get("deployment_code") or "Dp_unknown",
            target_type="CLEAN_OPERATION",
            bag_baseline={
                "bag_uid": ctx["new_bag_uid"],
                "weight_grams": post_weight,
                "source_kind": "SAME_BAG_CLEAN_POST",
                "source_work_type": WORK_TYPE_CLEAN,
                "source_work_uid": work_uid,
                "source_mcu_boot_id": payload["mcuBootId"],
                "source_mcu_event_sequence": payload[
                    "mcuEventSequence"
                ],
                "source_observed_at": now,
                "measurement_uid": final_measurement[
                    "measurementUid"
                ],
                "updated_at": now,
            },
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame clean persistence {created.lower()}"
            )
        logger.info(
            "fixed-frame clean complete: %s removed=%d",
            work_uid,
            removed_weight or 0,
        )

    @staticmethod
    def _compat_result_weights(
        payload: dict[str, Any],
    ) -> tuple[int, int]:
        values = (
            payload.get("preWeightGrams"),
            payload.get("postWeightGrams"),
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 350_000
            for value in values
        ):
            raise ValueError("invalid fixed-frame weight result")
        if (
            not isinstance(payload.get("mcuBootId"), int)
            or isinstance(payload.get("mcuBootId"), bool)
            or payload["mcuBootId"] <= 0
            or not isinstance(payload.get("mcuEventSequence"), int)
            or isinstance(payload.get("mcuEventSequence"), bool)
            or payload["mcuEventSequence"] <= 0
            or not isinstance(payload.get("infraredBlocked"), bool)
        ):
            raise ValueError("invalid fixed-frame result identity")
        return values

    def _on_safety_sensor_event(
        self,
        payload,
        mcu_receive_generation: int,
    ):
        deployment_code = (
            getattr(self._mqtt, "deployment_code", None) or "Dp_unknown"
        )
        result = self._store.record_safety_state_and_event(
            deployment_code=deployment_code,
            mcu_receive_generation=mcu_receive_generation,
            payload=payload,
        )
        if result not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"safety sensor event {result.lower()}"
            )

    def _on_fault_observed(
        self,
        payload,
        mcu_receive_generation: int,
    ):
        severity = str(payload.get("severity") or "WARNING")
        lifecycle = str(payload.get("lifecycle") or "OBSERVED")
        fault_uid = str(payload.get("faultUid") or _new_uid())
        deployment_code = (
            getattr(self._mqtt, "deployment_code", None) or "Dp_unknown"
        )
        component = str(
            payload.get("component") or "MCU_INTERNAL"
        )
        fault_code = str(
            payload.get("faultCode") or "MCU_INTERNAL"
        )
        port_no = (
            payload.get("portNo")
            if isinstance(payload.get("portNo"), int)
            and payload.get("portNo") > 0
            else None
        )
        if lifecycle == "RECOVERED":
            result = self._store.recover_fault_and_create_event(
                deployment_code=deployment_code,
                fault_uid=fault_uid,
                component=component,
                fault_code=fault_code,
                port_no=port_no,
                recovery_evidence="MCU_RECOVERY_EVENT",
                mcu_boot_id=payload.get("mcuBootId"),
                mcu_event_sequence=payload.get(
                    "mcuEventSequence"
                ),
                mcu_receive_generation=mcu_receive_generation,
            )
        else:
            result = self._store.observe_fault_and_create_event(
                deployment_code=deployment_code,
                component=component,
                fault_code=fault_code,
                severity=severity,
                port_no=port_no,
                fault_uid=fault_uid,
                mcu_boot_id=payload.get("mcuBootId"),
                mcu_event_sequence=payload.get(
                    "mcuEventSequence"
                ),
                mcu_receive_generation=mcu_receive_generation,
                detail=dict(payload),
            )
        if result not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(f"fault lifecycle result {result.lower()}")

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
        if not self._capture_photos(
            "capture_open_photos",
            work_uid,
        ):
            ctx["phase"] = "PREOPEN_PHOTO_BLOCKED"
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
            self._capture_photos(
                "capture_close_photos",
                work_uid,
            )
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
                "photos": self._completion_photo_facts(
                    work_uid,
                    "DELIVERY_SESSION",
                    (
                        "BEFORE_INNER",
                        "BEFORE_OUTER",
                        "AFTER_INNER",
                        "AFTER_OUTER",
                    ),
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
        if not self._capture_photos(
            "capture_clean_open_photos",
            work_uid,
        ):
            ctx["phase"] = "PREUNLOCK_PHOTO_BLOCKED"
            self._store.update_work_context(work_uid, ctx)
            return
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
        self._capture_photos(
            "capture_clean_close_photos",
            work_uid,
        )
        final_usable = _delivery_usable_weight(
            ctx.get("final_measurement") or {}
        )
        preunlock_weight = ctx.get("preunlock_weight_grams")
        old_baseline = ctx.get("old_baseline_weight_grams")
        removed_weight = (
            preunlock_weight - old_baseline
            if preunlock_weight is not None
            and old_baseline is not None
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
            "photos": self._completion_photo_facts(
                work_uid,
                "CLEAN_OPERATION",
                (
                    "FIRST_OPEN_INNER",
                    "FIRST_OPEN_OUTER",
                    "FINAL_CLOSE_INNER",
                    "FINAL_CLOSE_OUTER",
                ),
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

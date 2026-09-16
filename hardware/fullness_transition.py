"""Build one current-bag fullness transition from immutable observations.

This module is shared by the legacy ``WorkManager`` completion paths and the
native UART-v2 result reporter.  It only builds a candidate transition; the
``EdgeStore`` remains responsible for applying it atomically with the owning
business event.
"""
from __future__ import annotations

from typing import Any, Optional
import uuid


_SENSOR_KINDS = {"DIGITAL_INFRARED", "ULTRASONIC"}
_CONFIRMATION_BASES = {
    "FIXED_FRAME_CACHED_FINAL_OBSERVATION",
    "MCU_INDEPENDENT_RECHECK",
}


def _new_uid() -> str:
    return str(uuid.uuid4())


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _measurement_value(measurement: dict[str, Any]) -> Optional[int]:
    status = measurement.get("measurementStatus", measurement.get("status"))
    health = measurement.get(
        "weightSensorHealth",
        measurement.get("sensorHealth"),
    )
    available = measurement.get(
        "weightValuePresent",
        measurement.get("weightValueAvailable"),
    )
    value = measurement.get("reportedWeightGrams")
    if (
        status not in {"STABLE", "UNSTABLE"}
        or health != "OK"
        or available is not True
        or not _integer(value)
    ):
        return None
    return value


def _measurement_fact(measurement: dict[str, Any]) -> dict[str, Any]:
    available = measurement.get(
        "weightValuePresent",
        measurement.get("weightValueAvailable"),
    ) is True
    fault_code = measurement.get(
        "faultCode",
        measurement.get("weightFaultCode"),
    )
    return {
        "measurementUid": measurement.get(
            "measurementUid",
            measurement.get("weightMeasurementUid"),
        ),
        "status": measurement.get(
            "measurementStatus",
            measurement.get("status"),
        ),
        "weightValueAvailable": available,
        "reportedWeightGrams": (
            measurement.get("reportedWeightGrams") if available else None
        ),
        "weightValueKind": measurement.get("weightValueKind", "NONE"),
        "measurementElapsedMs": measurement.get("measurementElapsedMs", 0),
        "sampleCount": measurement.get(
            "sampleCount",
            measurement.get("weightSampleCount", 0),
        ),
        "calibrationVersion": measurement.get("calibrationVersion", 0),
        "sensorHealth": measurement.get(
            "weightSensorHealth",
            measurement.get("sensorHealth", "UNKNOWN"),
        ),
        "faultCode": None if fault_code in {None, "NONE"} else fault_code,
        "mcuBootId": measurement.get(
            "mcuBootId",
            measurement.get("weightMcuBootId"),
        ),
        "mcuEventSequence": measurement.get(
            "mcuEventSequence",
            measurement.get("weightMcuEventSequence"),
        ),
    }


def _sensor_value(
    port_config: dict[str, Any],
    observation: Optional[dict[str, Any]],
) -> tuple[Optional[bool], str]:
    """Return the interpreted full bit and its public observation value.

    ``fullnessCapturedUptimeMs`` is deliberately not consulted.  The caller
    supplies one already-accepted DEVICE_FACTS snapshot, and the auxiliary
    fullness observation may be older than the snapshot itself.
    """
    configured_kind = port_config.get("fullnessSensorKind")
    if (
        not isinstance(observation, dict)
        or observation.get("fullnessReadStatus") != "VALID"
        or observation.get("fullnessObservationKind") != configured_kind
    ):
        return None, "NOT_SAMPLED"
    if configured_kind == "DIGITAL_INFRARED":
        blocked = observation.get("fullnessInfraredBlocked")
        if not isinstance(blocked, bool):
            return None, "NOT_SAMPLED"
        return blocked, "BLOCKED" if blocked else "CLEAR"
    if configured_kind == "ULTRASONIC":
        distance = observation.get("fullnessDistanceMm")
        threshold = port_config.get("fullnessDistanceThresholdMm")
        if not _integer(distance) or not _integer(threshold) or threshold <= 0:
            return None, "NOT_SAMPLED"
        blocked = distance <= threshold
        return blocked, "BLOCKED" if blocked else "CLEAR"
    return None, "NOT_SAMPLED"


def new_bag_default_transition(
    port_no: int,
    bag_uid: str,
    device_name: str,
) -> dict[str, Any]:
    """Associate a newly installed bag with the deliberate NOT_FULL default."""
    return {
        "port_no": port_no,
        "bag_uid": bag_uid,
        "state": "NOT_FULL",
        "state_change_uid": _new_uid(),
        "event_uid": _new_uid(),
        "device_name": device_name,
        "payload": {},
    }


def build_fullness_transition(
    *,
    store,
    port_no: int,
    bag_uid: Optional[str],
    source_work_type: str,
    source_work_uid: str,
    device_name: str,
    measurement: dict[str, Any],
    sensor_observation: Optional[dict[str, Any]],
    confirmation_basis: Optional[str],
    baseline_weight_grams: Optional[int] = None,
    reset_for_new_bag: bool = False,
) -> Optional[dict[str, Any]]:
    """Build a reliable event only when the available facts decide a state.

    Failed, missing, malformed, or differently configured sensor observations
    stay ``NOT_SAMPLED``.  They never manufacture ``CLEAR`` and never block a
    later business operation.
    """
    if not bag_uid:
        return None
    applied = store.get_latest_applied_configuration()
    if not applied:
        return (
            new_bag_default_transition(port_no, bag_uid, device_name)
            if reset_for_new_bag
            else None
        )
    payload = applied.get("payload")
    ports = payload.get("ports", []) if isinstance(payload, dict) else []
    port_config = next(
        (
            port
            for port in ports
            if isinstance(port, dict) and port.get("portNo") == port_no
        ),
        None,
    )
    if (
        not isinstance(port_config, dict)
        or port_config.get("enabled") is not True
        or port_config.get("fullnessSensorKind") not in _SENSOR_KINDS
    ):
        return (
            new_bag_default_transition(port_no, bag_uid, device_name)
            if reset_for_new_bag
            else None
        )

    total_weight = _measurement_value(measurement)
    if baseline_weight_grams is None:
        baseline = store.get_bag_baseline(bag_uid)
        if baseline is not None:
            baseline_weight_grams = baseline.get("weight_grams")
    configured_full_weight = port_config.get("configuredFullWeightGrams")
    weight_available = bool(
        _integer(total_weight)
        and _integer(baseline_weight_grams)
        and _integer(configured_full_weight)
        and configured_full_weight > 0
    )
    weight_full: Optional[bool] = None
    fullness_percent_hundredths: Optional[int] = None
    if weight_available:
        net_weight = max(0, total_weight - baseline_weight_grams)
        weight_full = net_weight >= configured_full_weight
        fullness_percent_hundredths = (
            net_weight * 10_000 // configured_full_weight
        )

    sensor_full, sensor_value = _sensor_value(
        port_config,
        sensor_observation,
    )
    sensor_available = sensor_full is not None
    mode = port_config.get("fullnessMode")
    decided_state: Optional[str] = None
    if mode == "SENSOR_ONLY" and sensor_available:
        decided_state = "FULL" if sensor_full else "NOT_FULL"
    elif mode == "WEIGHT_ONLY" and weight_available:
        decided_state = "FULL" if weight_full else "NOT_FULL"
    elif mode == "SENSOR_OR_WEIGHT":
        if sensor_full is True or weight_full is True:
            decided_state = "FULL"
        elif sensor_available and weight_available:
            decided_state = "NOT_FULL"

    if decided_state is None:
        return (
            new_bag_default_transition(port_no, bag_uid, device_name)
            if reset_for_new_bag
            else None
        )
    if (
        confirmation_basis not in _CONFIRMATION_BASES
        or not isinstance(sensor_observation, dict)
    ):
        # The public contract has no "guessed" basis.  Without an actual
        # source observation, do not create a misleading state transition.
        return (
            new_bag_default_transition(port_no, bag_uid, device_name)
            if reset_for_new_bag
            else None
        )

    config = payload.get("config") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        return None
    state_change_uid = _new_uid()
    event_uid = _new_uid()
    event_payload = {
        "stateChangeUid": state_change_uid,
        "portNo": port_no,
        "bagUid": bag_uid,
        "state": decided_state,
        "sourceWorkType": source_work_type,
        "sourceWorkUid": source_work_uid,
        "fullnessMode": mode,
        "fullnessSensorKind": port_config["fullnessSensorKind"],
        "fullnessSensorValue": sensor_value,
        "confirmationBasis": confirmation_basis,
        "totalWeightMeasurement": _measurement_fact(measurement),
        "baselineWeightGrams": baseline_weight_grams,
        "configuredFullWeightGrams": configured_full_weight,
        "fullnessPercentHundredths": fullness_percent_hundredths,
        "weightFull": weight_full,
        "frozenConfig": {
            "version": config["version"],
            "contentSha256": config["contentSha256"],
            "mcuPayloadSha256": config["mcuPayloadSha256"],
        },
    }
    return {
        "port_no": port_no,
        "bag_uid": bag_uid,
        "state": decided_state,
        "state_change_uid": state_change_uid,
        "event_uid": event_uid,
        "device_name": device_name,
        "payload": event_payload,
    }

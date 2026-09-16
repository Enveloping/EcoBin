"""Shared fullness policy for native and compatibility completion paths."""
from __future__ import annotations

import uuid

import pytest

from fullness_transition import build_fullness_transition


class FullnessStore:
    def __init__(self, *, mode, kind, baseline=0, threshold=600):
        self.applied = {
            "payload": {
                "config": {
                    "version": 7,
                    "contentSha256": "a" * 64,
                    "mcuPayloadSha256": "b" * 64,
                },
                "ports": [
                    {
                        "portNo": 1,
                        "enabled": True,
                        "fullnessMode": mode,
                        "fullnessSensorKind": kind,
                        "fullnessDistanceThresholdMm": threshold,
                        "configuredFullWeightGrams": 100,
                    }
                ],
            }
        }
        self.baseline = baseline

    def get_latest_applied_configuration(self):
        return self.applied

    def get_bag_baseline(self, bag_uid):
        return {"bag_uid": bag_uid, "weight_grams": self.baseline}


def measurement(weight):
    return {
        "measurementUid": str(uuid.uuid4()),
        "status": "STABLE",
        "weightValueAvailable": True,
        "reportedWeightGrams": weight,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 1_000,
        "sampleCount": 5,
        "calibrationVersion": 4,
        "sensorHealth": "OK",
        "faultCode": None,
        "mcuBootId": 42,
        "mcuEventSequence": 9,
    }


def observation(kind, *, status="VALID", blocked=False, distance=1_000):
    return {
        "fullnessObservationKind": kind,
        "fullnessReadStatus": status,
        # Deliberately ancient relative to the enclosing DEVICE_FACTS capture.
        # Fullness policy accepts this auxiliary observation without an age gate.
        "fullnessCapturedUptimeMs": 1,
        "capturedUptimeMs": 4_000_000,
        "fullnessInfraredBlocked": blocked,
        "fullnessDistanceMm": distance,
    }


def build(*, mode, kind, weight, observed, baseline=0):
    return build_fullness_transition(
        store=FullnessStore(
            mode=mode,
            kind=kind,
            baseline=baseline,
        ),
        port_no=1,
        bag_uid="10000000-0000-4000-8000-000000000001",
        source_work_type="DELIVERY_SESSION",
        source_work_uid="20000000-0000-4000-8000-000000000001",
        device_name="device-1",
        measurement=measurement(weight),
        sensor_observation=observed,
        confirmation_basis="MCU_INDEPENDENT_RECHECK",
    )


@pytest.mark.parametrize(
    "mode,kind,weight,observed,expected_state,expected_sensor",
    [
        (
            "SENSOR_ONLY",
            "DIGITAL_INFRARED",
            50,
            observation("DIGITAL_INFRARED", blocked=True),
            "FULL",
            "BLOCKED",
        ),
        (
            "SENSOR_ONLY",
            "DIGITAL_INFRARED",
            150,
            observation("DIGITAL_INFRARED", blocked=False),
            "NOT_FULL",
            "CLEAR",
        ),
        (
            "SENSOR_ONLY",
            "ULTRASONIC",
            50,
            observation("ULTRASONIC", distance=600),
            "FULL",
            "BLOCKED",
        ),
        (
            "SENSOR_ONLY",
            "ULTRASONIC",
            150,
            observation("ULTRASONIC", distance=601),
            "NOT_FULL",
            "CLEAR",
        ),
        (
            "WEIGHT_ONLY",
            "DIGITAL_INFRARED",
            150,
            observation("DIGITAL_INFRARED", status="UNAVAILABLE"),
            "FULL",
            "NOT_SAMPLED",
        ),
        (
            "WEIGHT_ONLY",
            "ULTRASONIC",
            50,
            observation("DIGITAL_INFRARED", blocked=True),
            "NOT_FULL",
            "NOT_SAMPLED",
        ),
        (
            "SENSOR_OR_WEIGHT",
            "DIGITAL_INFRARED",
            150,
            observation("DIGITAL_INFRARED", blocked=False),
            "FULL",
            "CLEAR",
        ),
        (
            "SENSOR_OR_WEIGHT",
            "ULTRASONIC",
            50,
            observation("ULTRASONIC", distance=100),
            "FULL",
            "BLOCKED",
        ),
        (
            "SENSOR_OR_WEIGHT",
            "ULTRASONIC",
            50,
            observation("ULTRASONIC", distance=1_000),
            "NOT_FULL",
            "CLEAR",
        ),
        (
            "SENSOR_OR_WEIGHT",
            "ULTRASONIC",
            150,
            observation("DIGITAL_INFRARED", blocked=False),
            "FULL",
            "NOT_SAMPLED",
        ),
    ],
)
def test_fullness_mode_matrix_uses_complete_observation_without_age_gate(
    mode,
    kind,
    weight,
    observed,
    expected_state,
    expected_sensor,
):
    transition = build(
        mode=mode,
        kind=kind,
        weight=weight,
        observed=observed,
    )

    assert transition["state"] == expected_state
    assert transition["payload"]["fullnessSensorValue"] == expected_sensor
    assert transition["payload"]["confirmationBasis"] == (
        "MCU_INDEPENDENT_RECHECK"
    )


@pytest.mark.parametrize("status", ["NOT_OBSERVED", "UNAVAILABLE"])
def test_missing_sensor_never_manufactures_clear_or_decides_sensor_only(status):
    assert build(
        mode="SENSOR_ONLY",
        kind="DIGITAL_INFRARED",
        weight=150,
        observed=observation("DIGITAL_INFRARED", status=status),
    ) is None
    assert build(
        mode="SENSOR_OR_WEIGHT",
        kind="DIGITAL_INFRARED",
        weight=50,
        observed=observation("DIGITAL_INFRARED", status=status),
    ) is None


def test_malformed_or_mismatched_sensor_is_not_interpreted_as_clear():
    malformed = observation("ULTRASONIC", distance=None)
    assert build(
        mode="SENSOR_ONLY",
        kind="ULTRASONIC",
        weight=50,
        observed=malformed,
    ) is None
    mismatch = build(
        mode="WEIGHT_ONLY",
        kind="DIGITAL_INFRARED",
        weight=150,
        observed=observation("ULTRASONIC", distance=100),
    )
    assert mismatch["state"] == "FULL"
    assert mismatch["payload"]["fullnessSensorValue"] == "NOT_SAMPLED"


def test_independent_recheck_label_requires_an_actual_snapshot_source():
    assert build(
        mode="WEIGHT_ONLY",
        kind="DIGITAL_INFRARED",
        weight=150,
        observed=None,
    ) is None
    assert build_fullness_transition(
        store=FullnessStore(
            mode="WEIGHT_ONLY",
            kind="DIGITAL_INFRARED",
        ),
        port_no=1,
        bag_uid="10000000-0000-4000-8000-000000000001",
        source_work_type="DELIVERY_SESSION",
        source_work_uid="20000000-0000-4000-8000-000000000001",
        device_name="device-1",
        measurement=measurement(150),
        sensor_observation=None,
        confirmation_basis=None,
    ) is None

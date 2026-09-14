from __future__ import annotations

from pathlib import Path

import pytest

from factory.acceptance_measurements import valid_reference_weight, valid_sampling
from factory.acceptance_service import _check_summary
from first_boot.factory_flow import _validate_acceptance_projection
from .test_factory_flow_projection import _p7_projection


@pytest.mark.parametrize("reference", [11, 400, 500, 1000, 350000])
def test_reference_protocol_bounds(reference: int) -> None:
    assert valid_reference_weight(reference)


@pytest.mark.parametrize("override", [
    {"samplesGrams": [True]}, {"samplesGrams": [-1]}, {"samplesGrams": [350001]},
    {"samplesGrams": [1.1]}, {"samplesGrams": ["secret"]}, {"samplesGrams": [0] * 33},
    {"readCount": False}, {"readCount": -1}, {"resultCode": "not a safe code"}, {"secret": "hidden"},
])
def test_untrusted_sampling_is_not_projected_or_accepted(override: dict) -> None:
    trace = {"samplesGrams": [0], "readCount": 1, "resultCode": "STABLE_WEIGHT_CAPTURED", **override}
    sampling = {"empty": trace}
    assert not valid_sampling(sampling)
    assert "sampling" not in _check_summary({"sampling": sampling})
    projection = _p7_projection()
    projection["checks"]["weight"]["sampling"] = sampling
    with pytest.raises(ValueError, match="weight samples"):
        _validate_acceptance_projection(projection)


def test_native_fullness_and_control_facts_reach_the_public_projection() -> None:
    action = {
        "preWeightGrams": 100,
        "postWeightGrams": 580,
        "weightDeltaGrams": 480,
        "infraredBlocked": True,
        "fullnessSensorKind": "ULTRASONIC",
        "fullnessReadStatus": "VALID",
        "fullnessDistanceMm": 321,
        "fullnessDistanceThresholdMm": 600,
        "fullnessBlocked": True,
        "finishReason": "DELIVERY_END",
        "deliveryDoorCommand": "CLOSE",
        "deliveryDoorOutputStatus": "COMMAND_DISPATCHED",
        "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
    }
    summary = _check_summary({"status": "PASSED", "result": action})
    projection = _p7_projection()
    projection["checks"]["delivery"] = summary

    _validate_acceptance_projection(projection)

    assert summary["result"] == action | {
        "cleanLockPowerState": None,
        "cleanSolenoidHealth": None,
        "cleanDoorStateBasis": None,
        "cleanerPhysicalCloseConfirmed": None,
    }


def test_native_identity_diagnostics_reach_the_public_projection() -> None:
    projection = _p7_projection()
    projection["mcuIdentity"] = {
        "fixedFrameRevision": 2,
        "firmwareVersion": "2.0.0-rc.25",
        "firmwareVersionCode": 25,
        "firmwareIdentityHex": "123456789abcdef0",
        "mcuBootId": 8_000_000_000_000_001,
        "mcuHighestCommandSequence": 17,
        "mcuCapabilityBitmapHex": "0000000000008100",
    }

    _validate_acceptance_projection(projection)


def test_factory_page_names_real_sensor_and_control_facts() -> None:
    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")
    assert "超声波测距" in app
    assert "满溢距离阈值" in app
    assert "关门控制已下发" in app
    assert "无独立门位反馈，不作额外推断" in app
    assert "清运员已现场确认" in app
    assert "结果帧红外遮挡" not in app

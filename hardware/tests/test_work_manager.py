from work_manager import _delivery_usable_weight, _reported_weight


def test_unstable_weight_is_usable_when_sensor_health_is_ok():
    payload = {
        "measurementStatus": "UNSTABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1234,
        "weightValueKind": "LAST_FOUR_MEAN",
        "weightSensorHealth": "OK",
    }

    assert _reported_weight(payload) == 1234
    assert _delivery_usable_weight(payload) == 1234


def test_fault_weight_is_retained_but_not_usable_for_delivery():
    payload = {
        "measurementStatus": "PROTOCOL_ERROR",
        "weightValuePresent": True,
        "reportedWeightGrams": 1200,
        "weightValueKind": "LAST_OBSERVED",
        "weightSensorHealth": "PROTOCOL_ERROR",
    }

    assert _reported_weight(payload) == 1200
    assert _delivery_usable_weight(payload) is None

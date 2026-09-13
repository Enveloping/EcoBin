"""Local delivery continue/end selection and immutable final-result assembly."""
import ctypes as c
import uuid

import pytest
import uart2_protocol as uart

from hardware.tests.test_mcu_delivery_postclose import advance, closed_cycle
from hardware.tests.test_mcu_simplified_execution import (
    closed_measurement,
    final,
    library,
    runtime,
    select as autonomous_select,
    setup,
    tick,
)
from hardware.tests.test_mcu_work_preparation import exchange, original_scope, start_values, take_samples
from hardware.tests.test_native_configuration import inputs


def measured_round(runtime, tmp_path, samples=None, **options):
    """Compatibility wrapper exposing the old tuple over a current START round."""
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path, **options)
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = now
    samples = [1700] * 5 if samples is None else samples
    if samples:
        now = take_samples(runtime, samples, start=now, measurement=2)
    if len(samples) < 5 or max(samples) - min(samples) > 100:
        now = advance(runtime, now, began + 5000 - now)
    pointer = runtime[0].TestSimple_DeliveryMeasurement(execution)
    kind = "UNAVAILABLE"
    weight = 0
    if len(samples) >= 5:
        kind = "STABLE_MEAN" if max(samples) - min(samples) <= 100 else "TIMEOUT_MEDIAN"
        ordered = sorted(samples)
        weight = sum(samples) // len(samples) if kind == "STABLE_MEAN" else (
            ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) // 2
    post = {
        "measurementUid": str(uuid.UUID(bytes=c.string_at(pointer, 16))),
        "measurementKind": kind,
        "reportedWeightGrams": weight,
        "sampleCount": len(samples),
        "uptimeMs": now,
    }
    return execution, start, initial, scope, post, now


def save_process(runtime, store, scope, values, now):
    name = scope["eventMessageType"]
    raw = uart.encode_payload(name, values)
    receipt = store.save_native_process_receipt(
        uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], name, raw)
    assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return receipt


def held_result(runtime, start, now):
    observed = exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]
    assert observed["status"] == "RESULT_HELD"
    identity = dict(
        mcuBootId=42,
        resultSequence=observed["resultSequence"],
        workUid=start["sessionUid"],
        resultDigestSha256=observed["resultDigestSha256"],
    )
    reply = exchange(runtime, "QUERY_RESULT", identity | {"queryId": 123}, now=now)
    assert reply[0][1]["status"] == "HELD"
    return reply[1][1]


def select(runtime, execution, measurement_uid, selection, now):
    return runtime[0].McuDeliveryExecution_Select(
        execution,
        runtime[1],
        uuid.UUID(measurement_uid).bytes,
        selection,
        now,
    )


def completed_round(runtime, samples):
    execution, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, samples, start=now, measurement=2)
    return execution, start, now


def test_end_button_is_bound_to_current_round_and_first_valid_choice_wins(runtime):
    execution, start, now = completed_round(runtime, [1700] * 5)
    current_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(execution), 16)
    assert autonomous_select(runtime, execution, now, "END", uid=current_uid)
    assert not autonomous_select(runtime, execution, now, "CONTINUE", uid=current_uid)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 1700
    assert result["deliveryRoundCount"] == 1


def test_continue_opens_one_new_round_and_rejects_the_stale_first_button(runtime):
    execution, start, now = completed_round(runtime, [1000] * 5)
    first_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(execution), 16)
    assert autonomous_select(runtime, execution, now, "CONTINUE", uid=first_uid)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1500] * 5, start=now, measurement=3)
    assert not autonomous_select(runtime, execution, now, "END", uid=first_uid)
    assert autonomous_select(runtime, execution, now, "END")
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["deliveryRoundCount"] == 2
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 1500


def test_choice_window_expiry_finishes_without_a_button_and_cannot_be_reopened(runtime):
    execution, start, now = completed_round(runtime, [700] * 5)
    measurement_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(execution), 16)
    now = tick(runtime, now, start["continueDeliveryWaitMs"])
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_WINDOW_EXPIRED"
    assert result["finalWeightGrams"] == 700
    assert not autonomous_select(runtime, execution, now, "END", uid=measurement_uid)
    assert final(runtime, start, now) == result


@pytest.mark.parametrize("grams,expected", [(1, False), (0, True), (-100, True)])
def test_final_result_freezes_the_configured_negative_weight_anomaly(runtime, grams, expected):
    execution, start, now = completed_round(runtime, [grams] * 5)
    assert start["negativeWeightThresholdGrams"] == 500
    assert autonomous_select(runtime, execution, now)
    result = final(runtime, start, now)
    assert result["negativeWeightAnomaly"] is expected
    assert result["finalWeightGrams"] == grams
    assert result["finishReason"] == "DELIVERY_END"


def test_final_result_remains_byte_stable_when_queried_late(runtime):
    execution, start, now = completed_round(runtime, [900] * 5)
    assert autonomous_select(runtime, execution, now)
    result = final(runtime, start, now)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result


def test_saved_result_allows_a_new_start_without_rewriting_the_old_result(runtime):
    execution, start, now = completed_round(runtime, [900] * 5)
    assert autonomous_select(runtime, execution, now)
    first = final(runtime, start, now)
    raw = uart.encode_payload("WORK_RESULT", first)
    assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"

    next_start = start_values(
        commandSequence=7,
        mcuCommandUid=str(uuid.uuid4()),
        sessionUid=str(uuid.uuid4()),
    )
    assert exchange(runtime, "START_DELIVERY_SESSION", next_start, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = tick(runtime, now, 250)
    now = take_samples(runtime, [900] * 5, start=now, measurement=3)
    now = tick(runtime, now, 0)
    now = closed_measurement(runtime, next_start, now)
    now = take_samples(runtime, [1200] * 5, start=now, measurement=4)
    assert autonomous_select(runtime, execution, now)
    second = final(runtime, next_start, now)
    assert second["workUid"] != first["workUid"]
    assert second["resultSequence"] == first["resultSequence"] + 1
    assert uart.decode_payload("WORK_RESULT", raw) == first

"""The local finish button is the sole clean-completion confirmation in rc.23."""
import uuid

import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_result_handoff import McuResultHandoff
from hardware.tests.test_mcu_clean_intent import active_clean, request
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import final, library, runtime, state, tick
from hardware.tests.test_mcu_work_preparation import exchange, original_scope, take_samples

NAME = "CLEAN_COMPLETION_CONFIRMED"


def measured_candidate(runtime, tmp_path, *, available=True, saved_final=True, interrupted=False):
    """Compatibility tuple backed by the current terminal result, not a Pi ACK gate."""
    del saved_final
    clean, start, initial, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    if interrupted:
        now = take_samples(runtime, [123], start=now, measurement=2)
        runtime[0].ActuatorRuntime_StopForUpdate()
        now = tick(runtime, now, 0)
    elif available:
        now = take_samples(runtime, [123] * 5, start=now, measurement=2)
    else:
        now = tick(runtime, now, 5000)
    result = final(runtime, start, now, clean=True)
    candidate = {
        "measurementUid": result["finalMeasurementUid"],
        "measurementKind": result["finalKind"],
        "reportedWeightGrams": result["finalWeightGrams"],
        "sampleCount": result["finalSampleCount"],
        "uptimeMs": result["completedUptimeMs"],
    }
    store = EdgeStore(str(tmp_path / "candidate.db"))
    store.initialize()
    return clean, start, initial, candidate, now, store


def confirm(runtime, clean, start, final_measurement, now, *, sequence=1):
    """Legacy confirmation API retained only to prove it cannot gate rc.23 completion."""
    function = getattr(runtime[0], "McuCleanExecution_Confirm", None)
    if function is None:
        return False
    return function(
        clean,
        runtime[1],
        uuid.UUID(start["operationUid"]).bytes,
        sequence,
        uuid.UUID(final_measurement["measurementUid"]).bytes,
        now,
    )


def held_result(runtime, start, now):
    observed = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
    assert observed["status"] == "RESULT_HELD"
    identity = dict(
        mcuBootId=42,
        resultSequence=observed["resultSequence"],
        workUid=start["operationUid"],
        resultDigestSha256=observed["resultDigestSha256"],
    )
    replies = exchange(runtime, "QUERY_RESULT", identity | {"queryId": 123}, now=now)
    assert replies[0][1]["status"] == "HELD"
    return replies[1][1]


def completed_clean(runtime, tmp_path, samples):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    now = take_samples(runtime, samples, start=now, measurement=2)
    return clean, start, now, final(runtime, start, now, clean=True)


def test_finish_button_alone_confirms_close_and_builds_the_complete_result(runtime, tmp_path):
    clean, start, now, result = completed_clean(runtime, tmp_path, [123] * 5)
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["physicalCloseConfirmed"]
    assert result["cleanActionSequence"] == 1
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 123
    assert result["finalKind"] == "STABLE_MEAN"
    assert state(runtime, start, now, clean=True)["status"] == "RESULT_HELD"
    assert not facts(runtime, now)["cleanLockPowered"]
    assert clean


def test_old_second_confirmation_is_rejected_and_cannot_rewrite_completed_result(runtime, tmp_path):
    clean, start, now, result = completed_clean(runtime, tmp_path, [123] * 5)
    candidate = {"measurementUid": result["finalMeasurementUid"]}
    assert not confirm(runtime, clean, start, candidate, now)
    assert final(runtime, start, now, clean=True) == result


def test_finish_with_no_final_weight_is_failed_but_retains_the_actual_human_fact(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    now = tick(runtime, now, 5000)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "FAILED"
    assert result["physicalCloseConfirmed"]
    assert result["finalKind"] == "UNAVAILABLE"
    assert result["finalWeightGrams"] == 0
    assert result["finalFaultCode"] == "WEIGHT_TIMEOUT"


def test_update_during_final_weight_keeps_finish_fact_but_cancels_normal_completion(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    now = take_samples(runtime, [123, 124], start=now, measurement=2)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CANCELLED"
    assert result["physicalCloseConfirmed"]
    assert result["finalKind"] == "INTERRUPTED"
    assert result["finalSampleCount"] == 2


def test_pi_restart_reopens_the_exact_saved_result_without_requiring_another_button(runtime, tmp_path):
    _, start, now, result = completed_clean(runtime, tmp_path, [123] * 5)
    raw = uart.encode_payload("WORK_RESULT", result)
    identity = uart.decode_payload("RESULT_SAVED", raw[:60])
    path = tmp_path / "result.db"
    store = EdgeStore(str(path))
    store.initialize()
    try:
        handoff = McuResultHandoff(store, lambda frame: len(frame), identity)
        assert handoff.accept_frame(uart.encode_frame("WORK_RESULT", 1, raw), now)
        assert store.get_native_mcu_result(42, result["resultSequence"])["payload"] == raw
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()

    reopened = EdgeStore(str(path))
    reopened.initialize()
    try:
        assert reopened.get_native_mcu_result(42, result["resultSequence"])["payload"] == raw
        assert len(reopened.list_native_result_report_tasks()) == 1
    finally:
        reopened.close()
    assert final(runtime, start, now, clean=True) == result


def test_completed_result_is_immutable_under_late_update_and_expiry(runtime, tmp_path):
    _, start, now, result = completed_clean(runtime, tmp_path, [123] * 5)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, start["operationWindowMs"])
    assert final(runtime, start, now, clean=True) == result

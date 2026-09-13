"""Autonomous delivery interruption after CLOSE preserves authoritative facts.

The MCU owns motion, weighing and the one immutable WORK_RESULT.  The Pi only
queries and durably confirms that complete result; there is no process-event or
per-action acknowledgement gate in these scenarios.
"""
import pytest
import uart2_protocol as uart

from edge_store import EdgeStore
from mcu_result_handoff import McuResultHandoff
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import (
    closed_measurement,
    final,
    library,
    runtime,
    select,
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import exchange, take_samples
from hardware.tests.test_native_configuration import inputs


def close_wait(runtime):
    delivery, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    observed = facts(runtime, now)
    assert observed["lastDeliveryDoorCommand"] == "CLOSE"
    assert observed["pb7Output"] and not observed["pb6Output"]
    return delivery, start, now


def interrupted_result(runtime, stage):
    delivery, start, now = close_wait(runtime)
    if stage == "measuring":
        now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
        now = take_samples(runtime, [900], start=now, measurement=2, publish=False)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 100)
    return delivery, start, now


def result_identity(runtime, start, now):
    observed = state(runtime, start, now)
    assert observed["status"] == "RESULT_HELD"
    return {
        "mcuBootId": 42,
        "resultSequence": observed["resultSequence"],
        "workUid": start["sessionUid"],
        "resultDigestSha256": observed["resultDigestSha256"],
    }


def pump(client, replies, now):
    while replies:
        client.accept_frame(replies.pop(0), now)


def test_stop_during_close_wait_keeps_real_close_and_finishes_once(runtime):
    delivery, start, now = close_wait(runtime)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 1000)
    detected = now

    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["completedUptimeMs"] == detected
    assert result["deliveryRoundCount"] == 1
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["finalWeightGrams"] == 0
    assert not result["physicalCloseConfirmed"]
    assert facts(runtime, now)["measurementSequence"] == 1
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]

    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result
    assert delivery


@pytest.mark.parametrize("stage,loss", [
    (stage, loss)
    for stage in ("wait", "measuring")
    for loss in (
        "before_query_restart",
        "query_write",
        "query_reply",
        "confirm_write",
        "confirm_reply",
    )
])
def test_result_handoff_survives_pi_restart_without_motion_or_early_release(
    runtime, tmp_path, stage, loss
):
    delivery, start, now = interrupted_result(runtime, stage)
    identity = result_identity(runtime, start, now)
    path = str(tmp_path / "pi-postclose.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot(
        "DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"}
    )
    occupancy = store.get_work_slot()
    lib, endpoint, _, replies, *_ = runtime
    sent = []
    broken = True

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        message = decoded["messageName"]
        sent.append(message)
        if broken and loss == "query_write" and message == "QUERY_RESULT":
            raise OSError("synthetic result query write loss")
        if message == "RESULT_SAVED":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                saved = reader.get_native_mcu_result(42, identity["resultSequence"])
                assert saved is not None
                assert reader.get_work_slot() == occupancy
            finally:
                reader.close()
            if broken and loss == "confirm_write":
                raise OSError("synthetic result confirmation write loss")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and (
            (loss == "query_reply" and message == "QUERY_RESULT")
            or (loss == "confirm_reply" and message == "RESULT_SAVED")
        ):
            replies.clear()
        return len(frame)

    try:
        if loss != "before_query_restart":
            replies.clear()
            first = McuResultHandoff(store, write, identity)
            first.poll(now)
            pump(first, replies, now)
            saved = store.get_native_mcu_result(42, identity["resultSequence"])
            if loss in {"query_write", "query_reply"}:
                assert saved is None
            else:
                assert saved is not None
            assert store.get_work_slot() == occupancy

        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        restarted = McuResultHandoff(store, write, identity)
        for _ in range(3):
            now = tick(runtime, now, 1000)
            restarted.poll(now)
            pump(restarted, replies, now)

        observation = restarted.query_observation(now)
        assert observation["status"] == "RELEASED"
        saved = store.get_native_mcu_result(42, identity["resultSequence"])
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "CANCELLED"
        assert result["completedUptimeMs"] < now
        assert result["deliveryRoundCount"] == 1
        assert result["finalKind"] == (
            "NOT_TAKEN" if stage == "wait" else "INTERRUPTED"
        )
        if stage == "measuring":
            assert result["finalSampleCount"] == 1
            assert result["finalElapsedMs"] == 120
            assert result["finalFaultCode"] == "MEASUREMENT_INTERRUPTED"
        assert saved["payload"][:60] == uart.encode_payload("RESULT_SAVED", identity)
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert store.get_work_slot() == occupancy
        assert set(sent) <= {"QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == (
            1 if stage == "wait" else 2
        )
        assert not facts(runtime, now)["pb6Output"]
        assert not facts(runtime, now)["pb7Output"]
        assert delivery
    finally:
        store.close()


@pytest.mark.parametrize(
    "choice_mode",
    ["none", "continue_selected", "end_selected", "result_queried", "result_released"],
)
def test_postweight_stop_preserves_current_choice_or_immutable_result(runtime, choice_mode):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1700] * 5, start=now, measurement=2)
    completed = None

    if choice_mode == "continue_selected":
        assert select(runtime, delivery, now, "CONTINUE")
    elif choice_mode != "none":
        assert select(runtime, delivery, now, "END")
        completed = final(runtime, start, now)
        if choice_mode == "result_queried":
            assert final(runtime, start, now) == completed
        elif choice_mode == "result_released":
            raw = uart.encode_payload("WORK_RESULT", completed)
            assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"

    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 100)

    if completed is not None:
        if choice_mode == "result_released":
            assert state(runtime, start, now)["status"] == "RESULT_RELEASED"
            raw = uart.encode_payload("WORK_RESULT", completed)
            assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "ALREADY_RELEASED"
        else:
            assert final(runtime, start, now) == completed
        assert completed["finishReason"] == "DELIVERY_END"
        assert completed["finalKind"] == "STABLE_MEAN"
        assert completed["finalWeightGrams"] == 1700
    else:
        result = final(runtime, start, now)
        assert result["finishReason"] == "CANCELLED"
        assert result["deliveryRoundCount"] == 1
        if choice_mode == "none":
            assert result["finalKind"] == "STABLE_MEAN"
            assert result["finalWeightGrams"] == 1700
        else:
            assert result["finalKind"] == "NOT_TAKEN"
            assert result["finalWeightGrams"] == 0
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]


@pytest.mark.parametrize("stage", ["wait", "failed_measurement"])
def test_lost_close_control_context_keeps_any_existing_terminal_failure(runtime, stage):
    if stage == "wait":
        delivery, start, now = close_wait(runtime)
        completed = None
    else:
        delivery, _, start, now = setup(runtime)
        now = closed_measurement(runtime, start, now)
        now = tick(runtime, now, 5000)
        completed = final(runtime, start, now)
        assert completed["finishReason"] == "FAILED"
        assert completed["finalKind"] == "UNAVAILABLE"

    assert not runtime[0].ActuatorRuntime_SetDoorTarget(2)
    runtime[0].TestFacts_ReinitializeActuator()
    now = tick(runtime, now, 100)
    observed = facts(runtime, now)
    assert not observed["updateLatched"]
    assert observed["lastDeliveryDoorCommand"] == "NONE"

    result = final(runtime, start, now)
    if completed is None:
        assert result["finishReason"] == "CANCELLED"
        assert result["finalKind"] == "NOT_TAKEN"
    else:
        assert result == completed
        assert result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        assert result["finalElapsedMs"] == 5000
    assert delivery


def test_second_round_postclose_interruption_preserves_prior_anomaly(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [0] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now, "CONTINUE")

    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)

    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["deliveryRoundCount"] == 2
    assert result["negativeWeightAnomaly"]
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["completedUptimeMs"] == now
    observed = facts(runtime, now)
    assert observed["measurementSequence"] == 2
    assert not observed["pb6Output"] and not observed["pb7Output"]


@pytest.mark.parametrize(
    "samples,kind", [([800], "INTERRUPTED"), ([1800] * 5, "STABLE_MEAN")]
)
def test_stop_during_postclose_measurement_preserves_partial_or_complete_data(
    runtime, samples, kind
):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    began = now
    now = take_samples(runtime, samples, start=now, measurement=2, publish=False)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 100)
    detected = now

    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["completedUptimeMs"] == detected
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == kind
    assert result["finalSampleCount"] == len(samples)
    assert result["finalElapsedMs"] == (120 if kind == "INTERRUPTED" else 1020)
    assert result["finalFaultCode"] == (
        "MEASUREMENT_INTERRUPTED" if kind == "INTERRUPTED" else "NONE"
    )
    assert result["finalWeightGrams"] == (0 if kind == "INTERRUPTED" else 1800)
    assert result["finalMeasurementUid"] != result["initialMeasurementUid"]
    assert facts(runtime, now)["measurementSequence"] == 2
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    assert began < detected and delivery

"""Interrupted weighing is preserved in the one autonomous WORK_RESULT.

START authorizes the complete local delivery.  An interrupted initial or final
measurement therefore ends locally without a process-event acknowledgement;
the Pi only queries, durably stores, and confirms the immutable final result.
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
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import take_samples


def interrupt_measurement(runtime, initial_stage, *, in_flight_delay_ms=0):
    """Start one current delivery and interrupt its real initial/final read."""
    lib, endpoint, preparation, *_ = runtime
    execution, _, start, now = setup(runtime, initial=False)
    measurement = 1

    if not initial_stage:
        now = take_samples(runtime, [500] * 5, start=now, measurement=1)
        # The preparation poll publishes the initial measurement first; one
        # further foreground cycle lets the autonomous delivery owner consume it.
        now = tick(runtime, now, 0)
        now = closed_measurement(runtime, start, now)
        assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
        measurement = 2

    began = now
    now = take_samples(
        runtime, [800], start=now, measurement=measurement, publish=False
    )
    reader = lib.TestPreparation_Weight(preparation)
    if in_flight_delay_ms:
        now = tick(runtime, now, in_flight_delay_ms)
        attempt = lib.McuWeightRun_StartOwnedAttempt(reader, now)
        assert attempt
        lib.RuntimeClock_Advance(50)
        lib.ActuatorRuntime_Tick()
        now += 50

    assert lib.McuWeightRun_Interrupt(reader, measurement, now)
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    # For the initial measurement the preparation owner freezes the result on
    # the first poll and the delivery owner consumes it on the following poll.
    now = tick(runtime, now, 0)
    assert state(runtime, start, now)["status"] == "RESULT_HELD"
    return execution, start, began, now


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


def door_control(runtime, now):
    observed = facts(runtime, now)
    return {
        key: observed[key]
        for key in (
            "lastDeliveryDoorCommand",
            "doorActionActive",
            "pb6Output",
            "pb7Output",
        )
    }


def test_interrupted_final_measurement_keeps_partial_attempt_without_timeout_or_measured_zero(
    runtime,
):
    execution, start, began, now = interrupt_measurement(
        runtime, False, in_flight_delay_ms=230
    )
    result = final(runtime, start, now)

    assert result["finishReason"] == "FAILED"
    assert result["deliveryRoundCount"] == 1
    assert result["initialKind"] == "STABLE_MEAN"
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == "INTERRUPTED"
    assert result["finalFaultCode"] == "MEASUREMENT_INTERRUPTED"
    assert result["finalSampleCount"] == 1
    assert result["finalSpanGrams"] == 0
    assert result["finalWeightGrams"] == 0  # Invalid slot, never a measured zero.
    assert result["finalElapsedMs"] == 300
    assert result["completedUptimeMs"] == began + 300
    assert result["finalMeasurementUid"] != result["initialMeasurementUid"]

    observed = facts(runtime, now)
    assert observed["measurementState"] == "INTERRUPTED"
    assert observed["measurementObservedUptimeMs"] == began + 300
    assert observed["measurementSequence"] == 2
    assert not runtime[0].McuWeightRun_StartOwnedAttempt(
        runtime[0].TestPreparation_Weight(runtime[2]), now
    )

    retained_control = door_control(runtime, now)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result
    assert door_control(runtime, now) == retained_control
    assert execution


@pytest.mark.parametrize("initial_stage", [True, False])
def test_interruption_finishes_failed_with_original_measurements_and_no_new_action(
    runtime, initial_stage
):
    execution, start, _, now = interrupt_measurement(runtime, initial_stage)
    result = final(runtime, start, now)
    prefix = "initial" if initial_stage else "final"

    assert result["finishReason"] == "FAILED"
    assert result["completedUptimeMs"] == now
    assert result["deliveryRoundCount"] == (0 if initial_stage else 1)
    assert result[prefix + "Kind"] == "INTERRUPTED"
    assert result[prefix + "FaultCode"] == "MEASUREMENT_INTERRUPTED"
    assert result[prefix + "ElapsedMs"] == 20
    assert result[prefix + "SampleCount"] == 1
    assert result[prefix + "WeightGrams"] == 0
    if initial_stage:
        assert result["finalKind"] == "NOT_TAKEN"
    else:
        assert result["initialKind"] == "STABLE_MEAN"
        assert result["initialWeightGrams"] == 500
        assert result["initialMeasurementUid"] != result["finalMeasurementUid"]

    retained_control = door_control(runtime, now)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result
    assert door_control(runtime, now) == retained_control
    assert facts(runtime, now)["measurementSequence"] == (
        1 if initial_stage else 2
    )
    assert execution


@pytest.mark.parametrize("initial_stage", [True, False])
@pytest.mark.parametrize(
    "loss", ["query_write", "query_reply", "result_write", "result_reply"]
)
def test_interrupted_result_survives_pi_restart_and_lost_confirmation(
    runtime, tmp_path, initial_stage, loss
):
    execution, start, _, now = interrupt_measurement(runtime, initial_stage)
    identity = result_identity(runtime, start, now)
    lib, endpoint, _, replies, *_ = runtime
    path = str(tmp_path / "lost-confirmation.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot(
        "DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"}
    )
    occupancy = store.get_work_slot()
    sent = []
    broken = True
    confirmed_after_durable_store = False
    retained_control = door_control(runtime, now)

    def write(frame):
        nonlocal confirmed_after_durable_store
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        message = decoded["messageName"]
        sent.append(message)
        if broken and loss == "query_write" and message == "QUERY_RESULT":
            raise OSError("synthetic result query write loss")
        if message == "RESULT_SAVED":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                saved = reader.get_native_mcu_result(
                    42, identity["resultSequence"]
                )
                assert saved is not None
                assert saved["payload"][:60] == uart.encode_payload(
                    "RESULT_SAVED", identity
                )
                assert len(reader.list_native_result_report_tasks()) == 1
                assert reader.get_work_slot() == occupancy
                confirmed_after_durable_store = True
            finally:
                reader.close()
            if broken and loss == "result_write":
                raise OSError("synthetic result confirmation write loss")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and (
            (loss == "query_reply" and message == "QUERY_RESULT")
            or (loss == "result_reply" and message == "RESULT_SAVED")
        ):
            replies.clear()
        return len(frame)

    try:
        replies.clear()
        first = McuResultHandoff(store, write, identity)
        first.poll(now)
        pump(first, replies, now)
        saved_before_restart = store.get_native_mcu_result(
            42, identity["resultSequence"]
        )
        if loss in {"query_write", "query_reply"}:
            assert saved_before_restart is None
        else:
            assert saved_before_restart is not None
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

        assert restarted.query_observation(now)["status"] == "RELEASED"
        saved = store.get_native_mcu_result(42, identity["resultSequence"])
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finishReason"] == "FAILED"
        assert result["completedUptimeMs"] < now
        assert result["deliveryRoundCount"] == (0 if initial_stage else 1)
        prefix = "initial" if initial_stage else "final"
        assert result[prefix + "Kind"] == "INTERRUPTED"
        assert result[prefix + "FaultCode"] == "MEASUREMENT_INTERRUPTED"
        assert result[prefix + "SampleCount"] == 1
        assert result[prefix + "WeightGrams"] == 0
        assert saved["payload"][:60] == uart.encode_payload(
            "RESULT_SAVED", identity
        )
        assert len(store.list_native_result_report_tasks()) == 1
        assert (
            store.list_native_result_report_tasks()[0]["state"]
            == "PENDING_CLASSIFICATION"
        )
        assert store.get_work_slot() == occupancy
        assert confirmed_after_durable_store
        assert set(sent) == {"QUERY_RESULT", "RESULT_SAVED"}
        assert door_control(runtime, now) == retained_control
        assert facts(runtime, now)["measurementSequence"] == (
            1 if initial_stage else 2
        )
        assert not lib.McuWeightRun_StartOwnedAttempt(
            lib.TestPreparation_Weight(runtime[2]), now
        )
        assert execution
    finally:
        store.close()

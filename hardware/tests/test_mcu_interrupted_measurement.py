"""Actual C interrupted acquisition -> process event/facts -> durable Pi receipt.

The caller invokes the interruption boundary; automatic business detection is
tested separately, not claimed by this acquisition-to-custody test.
"""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, take_samples
from hardware.tests.test_mcu_work_preparation import configured, start_values, original_scope
from hardware.tests.test_mcu_delivery_postclose import closed_cycle, advance
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_delivery_execution import enable
from hardware.tests.test_mcu_delivery_finalization import save_process, held_result
from hardware.tests.test_native_configuration import inputs


def test_interrupted_postclose_measurement_keeps_partial_attempt_without_timeout_or_measured_zero(runtime, tmp_path):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = now
    now = take_samples(runtime, [800], start=now, measurement=2)
    now = advance(runtime, now, 230)
    reader = lib.TestPreparation_Weight(preparation)
    attempt = lib.McuWeightRun_StartOwnedAttempt(reader, now)
    assert attempt
    now = advance(runtime, now, 50, poll=False)
    assert lib.McuWeightRun_Interrupt(reader, 2, now)
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    replies = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert replies[0][1]["status"] == "HELD"
    event = replies[1][1]
    assert event["measurementKind"] == "INTERRUPTED" and event["faultCode"] == "MEASUREMENT_INTERRUPTED"
    assert event["measurementElapsedMs"] == 300 and event["uptimeMs"] == began + 300
    assert event["sampleCount"] == 1 and event["sampleSpanGrams"] == 0
    assert event["reportedWeightGrams"] == 0  # invalid slot, never a measured zero
    assert event["measurementUid"] != initial["measurementUid"]
    assert event["sessionUid"] == start["sessionUid"] and event["configVersion"] == start["configVersion"]
    observed = facts(runtime, now)
    assert observed["measurementState"] == "INTERRUPTED"
    assert observed["measurementObservedUptimeMs"] == began + 300
    assert observed["measurementSequence"] == 2
    assert not lib.McuWeightRun_StartOwnedAttempt(reader, now)
    raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", event)
    path = str(tmp_path / "interrupted.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        save_process(runtime, store, scope, event, now)
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
        assert store.get_native_process_receipt(saved_scope)["payload"] == raw
    finally:
        store.close()
    now = advance(runtime, now, 60000)
    assert facts(runtime, now)["measurementState"] == "INTERRUPTED"
    assert facts(runtime, now)["measurementObservedUptimeMs"] == began + 300
    assert execution


def interrupted_stage(runtime, tmp_path, initial_stage):
    lib, endpoint, preparation, *_ = runtime
    if initial_stage:
        execution = enable(runtime)
        configured(runtime, applied=True)
        start = start_values()
        assert exchange(runtime, "START_DELIVERY_SESSION", start)[0][1]["outcome"] == "ACCEPTED"
        scope = original_scope(start) | dict(eventMessageType="WORK_PREOPEN_WEIGHT_READY",
            stepSequence=1, configVersion=start["configVersion"])
        now, measurement = 0, 1
    else:
        execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
        now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        measurement = 2
    now = take_samples(runtime, [800], start=now, measurement=measurement)
    assert lib.McuWeightRun_Interrupt(lib.TestPreparation_Weight(preparation), measurement, now)
    now = advance(runtime, now, 0)
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    return execution, start, None if initial_stage else initial, scope, event, now


@pytest.mark.parametrize("initial_stage", [True, False])
def test_saved_interruption_finishes_failed_with_original_measurements_and_no_new_action(runtime, tmp_path, initial_stage):
    execution, start, initial, scope, event, now = interrupted_stage(runtime, tmp_path, initial_stage)
    lib, endpoint, preparation, *_ = runtime
    now = advance(runtime, now, 60000)
    writes = lib.TestFacts_Writes()
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
    store = EdgeStore(str(tmp_path / "failed-interruption.db"))
    store.initialize()
    try:
        assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
        occupancy = store.get_work_slot()
        save_process(runtime, store, scope, event, now)
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        result = held_result(runtime, start, now)
        prefix = "initial" if initial_stage else "final"
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == event["uptimeMs"]
        assert result["deliveryRoundCount"] == (0 if initial_stage else 1)
        assert result[prefix + "Kind"] == "INTERRUPTED" and result[prefix + "FaultCode"] == "MEASUREMENT_INTERRUPTED"
        assert result[prefix + "MeasurementUid"] == event["measurementUid"]
        assert result[prefix + "ElapsedMs"] == 20 and result[prefix + "SampleCount"] == 1
        if initial_stage:
            assert result["finalKind"] == "NOT_TAKEN"
        else:
            assert result["initialKind"] == initial["measurementKind"]
            assert result["initialMeasurementUid"] == initial["measurementUid"]
        raw = uart.encode_payload("WORK_RESULT", result)
        saved = store.save_native_mcu_result(raw)
        assert exchange(runtime, "RESULT_SAVED", payload=saved["savedPayload"], now=now)[0][1]["status"] == "RELEASED"
        assert store.get_native_mcu_result(42, 1)["payload"] == raw
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert store.get_work_slot() == occupancy
        assert facts(runtime, now)["measurementSequence"] == (1 if initial_stage else 2)
        assert lib.TestFacts_Writes() == writes and execution
    finally:
        store.close()


@pytest.mark.parametrize("initial_stage", [True, False])
@pytest.mark.parametrize("loss", ["process_write", "process_reply", "result_write", "result_reply"])
def test_interrupted_data_and_result_survive_pi_restart_and_lost_confirmation(runtime, tmp_path, initial_stage, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_result_handoff import McuResultHandoff

    execution, start, _, scope, event, now = interrupted_stage(runtime, tmp_path, initial_stage)
    lib, endpoint, preparation, replies, *_ = runtime
    raw = uart.encode_payload(scope["eventMessageType"], event)
    saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    path = str(tmp_path / "lost-confirmation.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
    occupancy = store.get_work_slot()
    broken, sent, final_raw = True, [], None
    lost_message = "PROCESS_EVENT_SAVED" if loss.startswith("process") else "RESULT_SAVED"

    def write(frame):
        name = uart.decode_frame(frame, sender_role="EDGE")["messageName"]
        sent.append(name)
        if name in ("PROCESS_EVENT_SAVED", "RESULT_SAVED"):
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_native_process_receipt(saved_scope)["payload"] == raw
                assert reader.get_work_slot() == occupancy
                if name == "RESULT_SAVED":
                    assert reader.get_native_mcu_result(42, 1)["payload"] == final_raw
                    assert len(reader.list_native_result_report_tasks()) == 1
            finally:
                reader.close()
            if broken and name == lost_message and loss.endswith("write"):
                raise OSError("synthetic interruption confirmation loss")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and name == lost_message and loss.endswith("reply"):
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        process_identity = {key: value for key, value in scope.items() if key != "queryId"}
        transfer = McuProcessEventHandoff(store, write, process_identity)
        first_query = transfer.poll(now)
        pump(transfer)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = not loss.startswith("process")
        transfer = McuProcessEventHandoff(store, write, process_identity)
        now = advance(runtime, now, 1000)
        assert transfer.poll(now) > first_query
        pump(transfer)
        now = advance(runtime, now, 1000)
        transfer.poll(now)
        pump(transfer)
        assert transfer.observation(now)["status"] == "RELEASED"
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == event["uptimeMs"]
        final_raw = uart.encode_payload("WORK_RESULT", result)
        result_identity = uart.decode_payload("RESULT_SAVED", final_raw[:60])
        replies.clear()
        handoff = McuResultHandoff(store, write, result_identity)
        first_query = handoff.poll(now)
        pump(handoff)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        handoff = McuResultHandoff(store, write, result_identity)
        now = advance(runtime, now, 1000)
        assert handoff.poll(now) > first_query
        pump(handoff)
        now = advance(runtime, now, 1000)
        handoff.poll(now)
        pump(handoff)
        assert handoff.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_process_receipt(saved_scope)["payload"] == raw
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == occupancy
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == (1 if initial_stage else 2) and execution
        assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), now)
    finally:
        store.close()

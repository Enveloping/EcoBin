"""Actual C detects post-CLOSE interruption, preserves facts, and hands off failure."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope, take_samples
from hardware.tests.test_mcu_delivery_postclose import closed_cycle, advance
from hardware.tests.test_mcu_delivery_finalization import held_result, save_process, measured_round, select
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_delivery_abort import save_action


def action_records(runtime, now):
    result, after = [], 0
    while True:
        response = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100 + after,
            targetMcuBootId=42, afterMcuEventSequence=after), now=now)
        if response[0][1]["status"] == "NOT_FOUND":
            return result
        result.append(response[1])
        after = response[1][1]["mcuEventSequence"]


def test_stop_during_close_wait_retains_real_close_and_separate_failure_before_final(runtime, tmp_path):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
    lib, *_ = runtime
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 1000)
    detected = now
    records = action_records(runtime, now)
    assert [name for name, _ in records] == ["DELIVERY_DOOR_COMMAND_RESULT"] * 2 + ["DELIVERY_POSTCLOSE_INTERRUPTED"]
    assert [item["command"] for _, item in records[:2]] == ["OPEN", "CLOSE"]
    interrupted = records[2][1]
    assert interrupted["interruptionReason"] == "UPDATE_STOPPED"
    assert interrupted["interruptedPhase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    assert interrupted["postCloseMeasurementEventSequence"] == 0
    assert interrupted["uptimeMs"] == detected
    assert interrupted["mcuCommandUid"] == records[0][1]["mcuCommandUid"]
    assert interrupted["sessionUid"] == start["sessionUid"] and interrupted["roundIndex"] == 1
    assert facts(runtime, now)["retainedWorkPhase"] == "SAFETY_LOCKED"
    assert facts(runtime, now)["measurementSequence"] == 1
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[0][1]["status"] == "NOT_FOUND"
    store = EdgeStore(str(tmp_path / "postclose-stop.db"))
    store.initialize()
    try:
        save_action(runtime, store, *records[2], now)
        save_action(runtime, store, *records[0], now)
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_action(runtime, store, *records[1], now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == detected
        assert result["deliveryRoundCount"] == 1 and result["finalKind"] == "NOT_TAKEN"
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert not result["physicalCloseConfirmed"]
        now = advance(runtime, now, 60000)
        assert held_result(runtime, start, now) == result
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"] and execution
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("stage,loss", [(stage, loss) for stage in ("wait", "measuring")
    for loss in ("interrupt_write", "interrupt_reply", "process_write", "process_reply", "result_write", "result_reply")
    if stage == "measuring" or not loss.startswith("process")])
def test_postclose_failure_handoff_survives_pi_restart_without_motion_or_early_release(runtime, tmp_path, stage, loss):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_result_handoff import McuResultHandoff
    execution, start, _, scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, _, replies, *_ = runtime
    if stage == "measuring":
        now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        now = take_samples(runtime, [900], start=now, measurement=2)
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 100)
    detected = now
    interrupted = action_records(runtime, now)[-1][1]
    interrupt_raw = uart.encode_payload("DELIVERY_POSTCLOSE_INTERRUPTED", interrupted)
    process_raw = None
    saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    if stage == "measuring":
        post = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
        process_raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", post)
    path = str(tmp_path / "pi-postclose.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
    occupancy = store.get_work_slot()
    broken, sent, final_raw = True, [], None

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        message = decoded["messageName"]
        sent.append(message)
        target = None
        if message in ("ACTUATOR_EVENT_SAVED", "PROCESS_EVENT_SAVED", "RESULT_SAVED"):
            receipt = uart.decode_payload(message, decoded["payload"])
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_work_slot() == occupancy
                if message == "ACTUATOR_EVENT_SAVED":
                    row = reader.get_native_actuator_event(42, receipt["mcuEventSequence"])
                    assert row["saved_payload"] == decoded["payload"]
                    if receipt["eventMessageType"] == "DELIVERY_POSTCLOSE_INTERRUPTED":
                        assert row["payload"] == interrupt_raw
                        target = "interrupt"
                elif message == "PROCESS_EVENT_SAVED":
                    assert reader.get_native_process_receipt(saved_scope)["payload"] == process_raw
                    target = "process"
                else:
                    assert reader.get_native_mcu_result(42, 1)["payload"] == final_raw
                    assert len(reader.list_native_result_report_tasks()) == 1
                    target = "result"
            finally:
                reader.close()
            if broken and loss == str(target) + "_write":
                raise OSError("synthetic postclose custody loss")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and loss == str(target) + "_reply":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(3):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(3):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        if stage == "measuring":
            process_identity = {key: value for key, value in scope.items() if key != "queryId"}
            broken = True
            client = McuProcessEventHandoff(store, write, process_identity)
            first_query = client.poll(now)
            pump(client)
            store.close()
            store = EdgeStore(path)
            store.initialize()
            broken = False
            client = McuProcessEventHandoff(store, write, process_identity)
            now = advance(runtime, now, 1000)
            assert client.poll(now) > first_query
            pump(client)
            now = advance(runtime, now, 1000)
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 0)
            assert client.observation(now)["status"] == "RELEASED"
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == detected
        final_raw = uart.encode_payload("WORK_RESULT", result)
        identity = uart.decode_payload("RESULT_SAVED", final_raw[:60])
        replies.clear()
        broken = True
        client = McuResultHandoff(store, write, identity)
        first_query = client.poll(now)
        pump(client)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuResultHandoff(store, write, identity)
        now = advance(runtime, now, 1000)
        assert client.poll(now) > first_query
        pump(client)
        now = advance(runtime, now, 1000)
        client.poll(now)
        pump(client)
        assert client.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_actuator_event(42, interrupted["mcuEventSequence"])["payload"] == interrupt_raw
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == occupancy
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert set(sent) <= {"QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED", "QUERY_PROCESS_EVENT",
            "PROCESS_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == (1 if stage == "wait" else 2)
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"] and execution
    finally:
        store.close()


@pytest.mark.parametrize("choice_mode", ["none", "end_held", "continue_held", "end_saved", "result_held"])
def test_postweight_stop_preserves_existing_choice_or_final_result_without_continuing(runtime, tmp_path, choice_mode):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, *_ = runtime
    store = EdgeStore(str(tmp_path / "selection-stop.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        if choice_mode != "none":
            assert select(runtime, execution, post["measurementUid"], 1 if choice_mode == "continue_held" else 2, now)
            choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
            if choice_mode in ("end_saved", "result_held"):
                save_process(runtime, store, choice_scope, choice, now)
            if choice_mode == "result_held":
                now = advance(runtime, now, 0)
                completed = held_result(runtime, start, now)
        lib.ActuatorRuntime_StopForUpdate()
        now = advance(runtime, now, 100)
        detected = now
        records = action_records(runtime, now)
        if choice_mode == "result_held":
            assert held_result(runtime, start, now) == completed
            assert len(records) == 2  # Completed result wins; no new failure record.
            import ctypes as c
            from hardware.tests.test_mcu_actuator_event_journal import Reservation
            token = Reservation()
            assert lib.McuControlEndpoint_ReserveActuatorEvents(runtime[1], 6, c.byref(token))
            assert lib.McuControlEndpoint_CancelActuatorEvents(runtime[1], c.byref(token))
            return
        assert records[-1][0] == "DELIVERY_POSTCLOSE_INTERRUPTED"
        assert records[-1][1]["interruptedPhase"] == "DELIVERY_WAIT_SELECTION"
        assert records[-1][1]["postCloseMeasurementEventSequence"] == post["mcuEventSequence"]
        assert not select(runtime, execution, post["measurementUid"], 1, now)
        for name, item in records:
            save_action(runtime, store, name, item, now)
        now = advance(runtime, now, 60000)
        if choice_mode in ("end_held", "continue_held"):
            assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
            assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1] == choice
            save_process(runtime, store, choice_scope, choice, now)
            now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == detected
        assert result["finalMeasurementUid"] == post["measurementUid"]
        assert result["finalKind"] == post["measurementKind"]
        assert result["deliveryRoundCount"] == 1 and facts(runtime, now)["measurementSequence"] == 2
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["wait", "failed_measurement"])
def test_lost_close_control_context_is_distinct_from_update_and_keeps_existing_failure(runtime, tmp_path, stage):
    if stage == "wait":
        execution, start, _, scope, now = closed_cycle(runtime, tmp_path)
    else:
        execution, start, _, scope, post, now = measured_round(runtime, tmp_path, samples=[])
    lib, *_ = runtime
    assert not lib.ActuatorRuntime_SetDoorTarget(2)  # native work excludes legacy OPEN bypass
    lib.TestFacts_ReinitializeActuator()  # synthetic component loss, NOT a whole-MCU restart
    now = advance(runtime, now, 100)
    records = action_records(runtime, now)
    interrupted = records[-1][1]
    assert records[-1][0] == "DELIVERY_POSTCLOSE_INTERRUPTED"
    assert interrupted["interruptionReason"] == "CLOSE_CONTEXT_LOST"
    assert interrupted["interruptedPhase"] == ("DELIVERY_CLOSE_TRAVEL_WAIT" if stage == "wait" else "DELIVERY_FINALIZING")
    state = facts(runtime, now)
    assert not state["updateLatched"] and state["lastDeliveryDoorCommand"] == "NONE"
    store = EdgeStore(str(tmp_path / "close-context.db"))
    store.initialize()
    try:
        for name, item in records:
            save_action(runtime, store, name, item, now)
        if stage != "wait":
            save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED"
        assert result["finalKind"] == ("NOT_TAKEN" if stage == "wait" else "UNAVAILABLE")
        if stage != "wait":
            assert result["finalFaultCode"] == "WEIGHT_TIMEOUT" and result["finalElapsedMs"] == 5000
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "NONE" and execution  # no implicit recovery command
    finally:
        store.close()


def test_second_round_postclose_interruption_reuses_promise_and_preserves_prior_anomaly(runtime, tmp_path):
    from hardware.tests.test_mcu_delivery_continue import choose
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, samples=[0] * 5)
    lib, *_ = runtime
    store = EdgeStore(str(tmp_path / "local-postclose.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        save_process(runtime, store, choice_scope, choice, now)
        for delta in (0, 100, start["deliveryAutoCloseMs"], 100):
            now = advance(runtime, now, delta)
        lib.ActuatorRuntime_StopForUpdate()
        now = advance(runtime, now, 0)
        detected = now
        records = action_records(runtime, now)
        assert [name for name, _ in records] == ["DELIVERY_DOOR_COMMAND_RESULT"] * 2 + ["DELIVERY_LOCAL_DOOR_RESULT"] * 2 + ["DELIVERY_POSTCLOSE_INTERRUPTED"]
        interrupted = records[-1][1]
        assert interrupted["roundIndex"] == 2 and interrupted["postCloseMeasurementEventSequence"] == 0
        assert interrupted["mcuCommandUid"] == records[-2][1]["mcuCommandUid"] != start["mcuCommandUid"]
        for name, item in records:
            save_action(runtime, store, name, item, now)
        now = advance(runtime, now, 60000)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["deliveryRoundCount"] == 2
        assert result["negativeWeightAnomaly"] and result["finalKind"] == "NOT_TAKEN"
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["finalMeasurementUid"] != post["measurementUid"] and result["completedUptimeMs"] == detected
        assert facts(runtime, now)["measurementSequence"] == 2
    finally:
        store.close()


@pytest.mark.parametrize("samples,kind", [([800], "INTERRUPTED"), ([1800] * 5, "STABLE_MEAN")])
def test_stop_during_postclose_measurement_preserves_partial_or_already_complete_data(runtime, tmp_path, samples, kind):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path)
    lib, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = now
    now = take_samples(runtime, samples, start=now, measurement=2, publish=False)
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 100)
    detected = now
    records = action_records(runtime, now)
    assert records[-1][0] == "DELIVERY_POSTCLOSE_INTERRUPTED"
    interrupted = records[-1][1]
    post = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    assert interrupted["interruptedPhase"] == "DELIVERY_POSTCLOSE_MEASURING"
    assert interrupted["postCloseMeasurementEventSequence"] == post["mcuEventSequence"]
    assert post["measurementKind"] == kind and post["sampleCount"] == len(samples)
    assert post["measurementElapsedMs"] == (120 if kind == "INTERRUPTED" else 1020)
    assert post["uptimeMs"] == began + post["measurementElapsedMs"]
    assert post["faultCode"] == ("MEASUREMENT_INTERRUPTED" if kind == "INTERRUPTED" else "NONE")
    assert post["reportedWeightGrams"] == (0 if kind == "INTERRUPTED" else 1800)
    store = EdgeStore(str(tmp_path / "measurement-stop.db"))
    store.initialize()
    try:
        for name, item in records:
            save_action(runtime, store, name, item, now)
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == detected
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["finalMeasurementUid"] == post["measurementUid"] and result["finalKind"] == kind
        assert facts(runtime, now)["measurementSequence"] == 2 and execution
    finally:
        store.close()

"""Real C cycle interruption -> durable exact evidence -> failed result."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_delivery_execution import enable, facts, events
from hardware.tests.test_mcu_delivery_postclose import advance
from hardware.tests.test_mcu_delivery_finalization import held_result


def save_action(runtime, store, message, values, now):
    raw = uart.encode_payload(message, values)
    receipt = store.save_native_actuator_event(message, raw)
    assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return raw, receipt


def test_interrupted_open_records_abort_before_final_handoff_and_never_claims_closed(runtime, tmp_path):
    execution = enable(runtime)
    lib, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, 100)
    lib.ActuatorRuntime_StopForUpdate()
    interrupted_at = now
    now = advance(runtime, now, 60000)
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
    store = EdgeStore(str(tmp_path / "abort.db"))
    store.initialize()
    try:
        message, opened = events(runtime, now)[1]
        assert opened["command"] == "OPEN" and opened["outputStatus"] == "COMMAND_DISPATCHED"
        save_action(runtime, store, message, opened, now)
        message, aborted = events(runtime, now)[1]
        assert message == "DELIVERY_CYCLE_ABORTED"
        assert aborted["abortReason"] == "UPDATE_STOPPED" and aborted["openDispatched"]
        assert aborted["mcuCommandUid"] == command["mcuCommandUid"]
        assert aborted["sessionUid"] == start["sessionUid"] and aborted["selectionEventSequence"] == 0
        assert aborted["uptimeMs"] == interrupted_at and aborted["roundIndex"] == 1
        now = advance(runtime, now, 0)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_action(runtime, store, message, aborted, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["deliveryRoundCount"] == 1
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["finalKind"] == "NOT_TAKEN" and not result["physicalCloseConfirmed"]
        assert result["completedUptimeMs"] == interrupted_at
        assert facts(runtime, now)["measurementSequence"] == 1
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
        assert events(runtime, now)[0][1]["status"] == "NOT_FOUND" and execution
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["before_open", "expired", "close_dead_time"])
def test_abort_keeps_exact_actual_round_count_and_requires_both_records_saved(runtime, tmp_path, mode):
    execution = enable(runtime)
    lib, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial, remainingStartAuthorizationMs=101 if mode == "expired" else 10000)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    if mode == "close_dead_time":
        now = advance(runtime, now, 100)
        now = advance(runtime, now, start["deliveryAutoCloseMs"])
    if mode == "expired":
        now = advance(runtime, now, 101)
    else:
        lib.ActuatorRuntime_StopForUpdate()
    at = now
    now = advance(runtime, now, 60000)
    output_name, output = events(runtime, now)[1]
    query = dict(queryId=111, targetMcuBootId=42, afterMcuEventSequence=output["mcuEventSequence"])
    aborted = exchange(runtime, "QUERY_ACTUATOR_EVENT", query, now=now)[1][1]
    assert aborted["abortReason"] == ("OPEN_DEADLINE_EXPIRED" if mode == "expired" else "UPDATE_STOPPED")
    assert aborted["openDispatched"] is (mode == "close_dead_time")
    assert output["outputStatus"] == ("COMMAND_DISPATCHED" if mode == "close_dead_time" else "OUTPUT_REJECTED")
    assert aborted["uptimeMs"] == at
    store = EdgeStore(str(tmp_path / "abort-order.db"))
    store.initialize()
    try:
        save_action(runtime, store, "DELIVERY_CYCLE_ABORTED", aborted, now)
        now = advance(runtime, now, 0)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        wrong = dict(mcuBootId=42, mcuEventSequence=output["mcuEventSequence"], eventMessageType=output_name,
            eventDigestSha256="00" * 32)
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", wrong, now=now)[0][1]["status"] == "IDENTITY_CONFLICT"
        now = advance(runtime, now, 0)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_action(runtime, store, output_name, output, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["deliveryRoundCount"] == (1 if mode == "close_dead_time" else 0)
        assert result["finishReason"] == "FAILED" and result["finalKind"] == "NOT_TAKEN"
        assert result["completedUptimeMs"] == at and not result["negativeWeightAnomaly"]
        now = advance(runtime, now, 60000)
        assert held_result(runtime, start, now) == result
        assert facts(runtime, now)["measurementSequence"] == 1
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"] and execution
    finally:
        store.close()


@pytest.mark.parametrize("opened", [False, True])
def test_local_second_round_abort_preserves_cause_and_prior_anomaly_not_old_final(runtime, tmp_path, opened):
    from hardware.tests.test_mcu_delivery_continue import choose
    from hardware.tests.test_mcu_delivery_finalization import measured_round, save_process
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, [0] * 5)
    lib, *_ = runtime
    store = EdgeStore(str(tmp_path / "local-abort.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        if opened:
            now = advance(runtime, now, 100)
        lib.ActuatorRuntime_StopForUpdate()
        at = now
        now = advance(runtime, now, 1000)
        query = dict(queryId=111, targetMcuBootId=42, afterMcuEventSequence=choice["mcuEventSequence"])
        output_name, output = exchange(runtime, "QUERY_ACTUATOR_EVENT", query, now=now)[1]
        assert output_name == "DELIVERY_LOCAL_DOOR_RESULT" and output["roundIndex"] == 2
        query["afterMcuEventSequence"] = output["mcuEventSequence"]
        abort_name, aborted = exchange(runtime, "QUERY_ACTUATOR_EVENT", query, now=now)[1]
        assert abort_name == "DELIVERY_CYCLE_ABORTED" and aborted["roundIndex"] == 2
        assert aborted["selectionEventSequence"] == choice["mcuEventSequence"]
        assert aborted["mcuCommandUid"] == output["mcuCommandUid"] != start["mcuCommandUid"]
        save_action(runtime, store, output_name, output, now)
        save_action(runtime, store, abort_name, aborted, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["negativeWeightAnomaly"]
        assert result["deliveryRoundCount"] == 1 + int(opened)
        assert result["finalKind"] == "NOT_TAKEN" and result["finalMeasurementUid"] != post["measurementUid"]
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["completedUptimeMs"] == at and facts(runtime, now)["measurementSequence"] == 2
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["none", "abort_write", "abort_reply", "result_write", "result_reply"])
def test_pi_restart_during_abort_and_result_custody_retains_evidence_without_replaying_motion(runtime, tmp_path, loss):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    from mcu_result_handoff import McuResultHandoff
    execution = enable(runtime)
    lib, endpoint, _, replies, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, 100)
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 0)
    output_name, output = events(runtime, now)[1]
    abort = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=111, targetMcuBootId=42,
        afterMcuEventSequence=output["mcuEventSequence"]), now=now)[1][1]
    abort_raw = uart.encode_payload("DELIVERY_CYCLE_ABORTED", abort)
    path = str(tmp_path / "pi-abort.db")
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
        if message in ("ACTUATOR_EVENT_SAVED", "RESULT_SAVED"):
            receipt = uart.decode_payload(message, decoded["payload"])
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_work_slot() == occupancy
                if message == "ACTUATOR_EVENT_SAVED":
                    row = reader.get_native_actuator_event(42, receipt["mcuEventSequence"])
                    assert row["saved_payload"] == decoded["payload"]
                    if receipt["eventMessageType"] == "DELIVERY_CYCLE_ABORTED":
                        assert row["payload"] == abort_raw
                        target = "abort"
                else:
                    assert reader.get_native_mcu_result(42, 1)["payload"] == final_raw
                    assert len(reader.list_native_result_report_tasks()) == 1
                    target = "result"
            finally:
                reader.close()
            if broken and loss == str(target) + "_write":
                raise OSError("synthetic custody write loss")
        lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now)
        if broken and loss == str(target) + "_reply":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(2):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(2):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        assert store.get_native_actuator_event(42, abort["mcuEventSequence"])["payload"] == abort_raw
        final_raw = uart.encode_payload("WORK_RESULT", held_result(runtime, start, now))
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
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == occupancy
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert set(sent) <= {"QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"] and execution
    finally:
        store.close()

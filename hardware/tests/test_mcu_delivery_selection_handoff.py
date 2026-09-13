"""Actual measured round and C/Pi custody; choice intent remains a test boundary."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from mcu_process_handoff import McuProcessEventHandoff
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, take_samples, original_scope
from hardware.tests.test_mcu_delivery_postclose import closed_cycle, advance
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_native_configuration import inputs


def ready_choice(runtime, tmp_path, store):
    execution, start, _, post_scope, now = closed_cycle(runtime, tmp_path)
    lib, endpoint, preparation, replies, *_ = runtime
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [1700] * 5, start=now, measurement=2)
    replies.clear()

    def write(frame):
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        return len(frame)

    weight = McuProcessEventHandoff(store, write, {key: value for key, value in post_scope.items() if key != "queryId"})
    weight.poll(now)
    while replies:
        weight.accept_frame(replies.pop(0), now)
    post = store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", post_scope)[8:])
    measured = uart.decode_payload("WORK_POSTCLOSE_WEIGHT_READY", post["payload"])
    choice_scope = post_scope | {"eventMessageType": "DELIVERY_SELECTION"}
    scope = uart.encode_payload("QUERY_PROCESS_EVENT", choice_scope)[8:]
    # Explicit candidate producer boundary: emulate END intent, not an HMI driver.
    # Weight, its custody, global sequence and the retention guard are real C.
    values = {key: measured[key] for key in ("mcuBootId", "mcuCommandUid", "sessionUid", "portNo", "roundIndex", "configVersion")}
    values.update(mcuEventSequence=lib.McuControlEndpoint_ReserveEventSequence(endpoint), uptimeMs=now,
        postCloseMeasurementUid=measured["measurementUid"], selection="END")
    raw = uart.encode_payload("DELIVERY_SELECTION", values)
    assert lib.McuProcessEventSlot_Freeze(lib.TestPreparation_Process(endpoint), scope, len(scope),
        uart.MESSAGE_SPECS["DELIVERY_SELECTION"]["id"], raw, len(raw))
    return execution, start, choice_scope, raw, now


def test_query_body_conflict_is_retained_and_never_acknowledged(runtime, tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        execution, _, scope, raw, now = ready_choice(runtime, tmp_path, store)
        lib, endpoint, _, replies, *_ = runtime
        sent = []

        def write(frame):
            sent.append(frame)
            assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
            return len(frame)

        client = McuProcessEventHandoff(store, write, {key: value for key, value in scope.items() if key != "queryId"})
        client.poll(now)
        assert client.accept_frame(replies.pop(0), now)
        original = replies.pop(0)
        assert uart.decode_frame(original, sender_role="MCU")["messageName"] == "DELIVERY_SELECTION"
        values = uart.decode_payload("DELIVERY_SELECTION", raw) | {"selection": "CONTINUE"}
        conflict_raw = uart.encode_payload("DELIVERY_SELECTION", values)
        conflicting = uart.encode_frame("DELIVERY_SELECTION", 1, conflict_raw)
        assert not client.accept_frame(conflicting, now)
        assert len(sent) == 1
        assert any(row["payload"] == conflict_raw for row in store.list_native_actuator_event_conflicts())
        with pytest.raises(ValueError, match="conflict"):
            client.accept_frame(original, now)
        assert len(sent) == 1 and execution
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["none", "before_send", "after_delivery"])
def test_choice_commits_before_confirmation_and_pi_restart_does_not_repeat_motion_or_weighing(runtime, tmp_path, loss):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        execution, start, scope, raw, now = ready_choice(runtime, tmp_path, store)
        lib, endpoint, preparation, replies, *_ = runtime
        sent, broken = [], True
        saved_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]

        def write(frame):
            name = uart.decode_frame(frame, sender_role="EDGE")["messageName"]
            sent.append(name)
            if name == "PROCESS_EVENT_SAVED":
                other = EdgeStore(path)
                other.initialize()
                try:
                    assert other.get_native_process_receipt(saved_scope)["payload"] == raw
                finally:
                    other.close()
                if broken and loss == "before_send":
                    raise OSError("synthetic loss before save frame")
            assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
            if broken and loss == "after_delivery" and name == "PROCESS_EVENT_SAVED":
                replies.clear()
            return len(frame)

        def pump(client):
            while replies:
                client.accept_frame(replies.pop(0), now)

        original = {key: value for key, value in scope.items() if key != "queryId"}
        client = McuProcessEventHandoff(store, write, original)
        query_id = client.poll(now)
        pump(client)
        assert sent == ["QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"]
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        now = advance(runtime, now, 1000)
        resumed = McuProcessEventHandoff(store, write, original)
        assert resumed.poll(now) > query_id
        pump(resumed)
        now = advance(runtime, now, 1000)
        resumed.poll(now)
        pump(resumed)
        assert resumed.observation(now)["status"] == "RELEASED"
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"}
        assert sent.count("PROCESS_EVENT_SAVED") == (2 if loss == "before_send" else 1)
        assert facts(runtime, now)["measurementSequence"] == 2
        assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), now)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == [] and execution
    finally:
        store.close()


def test_contradictory_replies_to_the_same_query_remain_blocked_after_reconnect(runtime, tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        execution, _, scope, raw, now = ready_choice(runtime, tmp_path, store)
        lib, endpoint, _, replies, *_ = runtime

        def write(frame):
            assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
            return len(frame)

        client = McuProcessEventHandoff(store, write, {key: value for key, value in scope.items() if key != "queryId"})
        client.poll(now)
        first = replies.pop(0)
        values = uart.decode_payload("PROCESS_EVENT_QUERY_REPLY", uart.decode_frame(first)["payload"])
        assert client.accept_frame(first, now)
        different = uart.encode_payload("PROCESS_EVENT_QUERY_REPLY", values | {"eventDigestSha256": "ff" * 32})
        assert not client.accept_frame(uart.encode_frame("PROCESS_EVENT_QUERY_REPLY", 1, different), now)
        store.close()
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_event("DELIVERY_SELECTION", values["targetMcuBootId"], values["mcuEventSequence"])
        replies.clear()
        resumed = McuProcessEventHandoff(store, write, {key: value for key, value in scope.items() if key != "queryId"})
        resumed.poll(now)
        with pytest.raises(ValueError, match="conflict"):
            resumed.accept_frame(replies.pop(0), now)
        assert execution and raw
    finally:
        store.close()

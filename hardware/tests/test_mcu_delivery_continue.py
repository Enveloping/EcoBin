"""Real C local continuation: original work/config, separate local output cause."""
import ctypes as c
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, take_samples, original_scope
from hardware.tests.test_mcu_delivery_finalization import measured_round, save_process, select, held_result
from hardware.tests.test_mcu_delivery_postclose import advance
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_actuator_event_journal import Reservation


def choose(runtime, store, execution, scope, post, now, selection=1):
    save_process(runtime, store, scope, post, now)
    now = advance(runtime, now, 0)
    assert select(runtime, execution, post["measurementUid"], selection, now)
    choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
    choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
    return choice_scope, choice


def test_saved_continue_opens_and_closes_round_two_under_own_local_cause(runtime, tmp_path):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "continue.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "OPEN"
        reply = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
            afterMcuEventSequence=choice["mcuEventSequence"]), now=now)
        assert reply[1][0] == "DELIVERY_LOCAL_DOOR_RESULT"
        opened = reply[1][1]
        assert opened["roundIndex"] == 2 and opened["selectionEventSequence"] == choice["mcuEventSequence"]
        assert opened["sessionUid"] == start["sessionUid"] and opened["command"] == "OPEN"
        assert not select(runtime, execution, post["measurementUid"], 1, now)
        now = advance(runtime, now, start["deliveryAutoCloseMs"], poll=False)
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        closed = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=101, targetMcuBootId=42,
            afterMcuEventSequence=opened["mcuEventSequence"]), now=now)[1][1]
        assert closed["command"] == "CLOSE" and closed["roundIndex"] == 2
        assert closed["selectionEventSequence"] == choice["mcuEventSequence"]
        assert closed["mcuCommandUid"] == opened["mcuCommandUid"] != start["mcuCommandUid"]
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["median", "unavailable", "selection_timeout"])
def test_later_round_keeps_measurement_method_or_failure_and_finishes_original_work(runtime, tmp_path, mode):
    first = 0 if mode == "unavailable" else 1700
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, [first] * 5)
    store = EdgeStore(str(tmp_path / "later-terminal.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        for delta in (100, start["deliveryAutoCloseMs"], 100, inputs()["device"]["deliveryDoorTravelWaitMs"]):
            now = advance(runtime, now, delta)
        began = now
        if mode == "median":
            now = take_samples(runtime, [-500, 1500] * 10, start=now, measurement=3)
        elif mode == "selection_timeout":
            now = take_samples(runtime, [2000] * 5, start=now, measurement=3)
        if mode != "selection_timeout":
            now = advance(runtime, now, began + 5000 - now)
        scope = scope | {"stepSequence": 2}
        post = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        if mode != "unavailable":
            if mode == "selection_timeout":
                now = advance(runtime, now, start["continueDeliveryWaitMs"])
            else:
                assert select(runtime, execution, post["measurementUid"], 2, now)
            choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
            choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
            save_process(runtime, store, choice_scope, choice, now)
            now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["deliveryRoundCount"] == 2 and result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["finalMeasurementUid"] == post["measurementUid"]
        expected = {"median": ("TIMEOUT_MEDIAN", "DELIVERY_END", True),
            "unavailable": ("UNAVAILABLE", "FAILED", True),
            "selection_timeout": ("STABLE_MEAN", "DELIVERY_WINDOW_EXPIRED", False)}[mode]
        assert (result["finalKind"], result["finishReason"], result["negativeWeightAnomaly"]) == expected
    finally:
        store.close()


@pytest.mark.parametrize("reason", ["update", "guard", "configuration", "sequence_exhausted"])
def test_saved_continue_does_not_override_local_preconditions_or_renew_old_intent(runtime, tmp_path, reason):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, _, _, prerequisites, *_ = runtime
    store = EdgeStore(str(tmp_path / "blocked.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        if reason == "update":
            lib.ActuatorRuntime_StopForUpdate()
        elif reason == "guard":
            prerequisites["error"] = 65535  # Invalid callback values cannot become permission.
        elif reason == "configuration":
            assert lib.McuDeviceFacts_PublishConfiguration(lib.TestPreparation_Facts(endpoint),
                start["configVersion"] + 1, bytes.fromhex("aa" * 32), bytes.fromhex("bb" * 32), 0)
        else:
            lib.TestPreparation_SetEventSequence(endpoint, 0xFFFFFFFE)  # Only one number remains for two edges.
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        prerequisites["error"] = 0
        now = advance(runtime, now, 200)
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        reply = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
            afterMcuEventSequence=choice["mcuEventSequence"]), now=now)
        if reason == "update":
            assert reply[0][1]["status"] == "HELD" and reply[1][0] == "DELIVERY_POSTCLOSE_INTERRUPTED"
            assert reply[1][1]["interruptionReason"] == "UPDATE_STOPPED"
            assert reply[1][1]["roundIndex"] == 1  # no second-round opening
        else:
            assert reply[0][1]["status"] == "NOT_FOUND"
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == []
        assert not select(runtime, execution, post["measurementUid"], 2, now)
    finally:
        store.close()


def test_valid_last_moment_continue_survives_late_save_without_reusing_first_open_deadline(runtime, tmp_path):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "late-save.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, start["continueDeliveryWaitMs"] - 1)
        assert select(runtime, execution, post["measurementUid"], 1, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1] == choice
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "OPEN"
        assert facts(runtime, now)["retainedWorkPhase"] == "DELIVERY_OPEN_COUNTDOWN"
    finally:
        store.close()


def test_capacity_shortage_reserves_both_edges_or_neither_then_starts_only_once(runtime, tmp_path):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, *_ = runtime
    store = EdgeStore(str(tmp_path / "capacity.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        occupied, last_free = Reservation(), Reservation()
        # Two retained edges plus the one work-long interruption promise leave
        # five positions; occupy four so a partial two-edge reservation rolls back.
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 4, c.byref(occupied))
        receipt = save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 1, c.byref(last_free))
        assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(last_free))
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(occupied))
        now = advance(runtime, now, 0)
        for _ in range(3):
            exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)
            now = advance(runtime, now, 0)
        now = advance(runtime, now, 100)
        opened = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
            afterMcuEventSequence=choice["mcuEventSequence"]), now=now)[1][1]
        assert opened["roundIndex"] == 2 and opened["selectionEventSequence"] == choice["mcuEventSequence"]
        now = advance(runtime, now, start["deliveryAutoCloseMs"])
        now = advance(runtime, now, 100)
        closed = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
            afterMcuEventSequence=opened["mcuEventSequence"]), now=now)[1][1]
        assert closed["command"] == "CLOSE" and closed["mcuEventSequence"] == opened["mcuEventSequence"] + 1
    finally:
        store.close()


def test_local_open_ignores_pb5_and_automatic_close_pauses_then_resumes(runtime, tmp_path):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, *_ = runtime
    store = EdgeStore(str(tmp_path / "pinch.db"))
    store.initialize()
    try:
        choice_scope, choice = choose(runtime, store, execution, scope, post, now)
        lib.TestFacts_Pinch(1)
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, 99)
        state = facts(runtime, now)
        assert not state["pb6Output"] and not state["pb7Output"]
        now = advance(runtime, now, 1)
        state = facts(runtime, now)
        assert state["lastDeliveryDoorCommand"] == "OPEN" and state["pb6Output"] and not state["pinchPaused"]
        now = advance(runtime, now, start["deliveryAutoCloseMs"])
        now = advance(runtime, now, 100)
        state = facts(runtime, now)
        assert state["lastDeliveryDoorCommand"] == "CLOSE" and state["pinchPaused"]
        assert not state["pb6Output"] and not state["pb7Output"]
        lib.TestFacts_Pinch(0)
        now = advance(runtime, now, 1)
        assert facts(runtime, now)["pb7Output"] and not facts(runtime, now)["pinchPaused"]
    finally:
        store.close()


@pytest.mark.parametrize("weights,anomaly", [([1700, 1201], False), ([1700, 1200], True),
    ([0, 1700], True), ([1700, 1000, 2500], True)])
def test_local_rounds_keep_first_weight_and_latch_each_round_drop_not_total_net(runtime, tmp_path, weights, anomaly):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, [weights[0]] * 5)
    store = EdgeStore(str(tmp_path / "rounds.db"))
    store.initialize()
    try:
        for index, grams in enumerate(weights[1:], start=2):
            choice_scope, choice = choose(runtime, store, execution, scope, post, now)
            receipt = save_process(runtime, store, choice_scope, choice, now)
            now = advance(runtime, now, 0)
            assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "ALREADY_RELEASED"
            for delta in (100, start["deliveryAutoCloseMs"], 100, inputs()["device"]["deliveryDoorTravelWaitMs"]):
                now = advance(runtime, now, delta)
            now = take_samples(runtime, [grams] * 5, start=now, measurement=index + 1)
            scope = scope | {"stepSequence": index}
            previous = post
            post = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
            assert post["roundIndex"] == index and post["reportedWeightGrams"] == grams
            assert post["measurementUid"] != previous["measurementUid"]
            assert post["mcuCommandUid"] == start["mcuCommandUid"]
            assert not select(runtime, execution, previous["measurementUid"], 2, now)
            assert store.list_native_result_report_tasks() == []
        choice_scope, choice = choose(runtime, store, execution, scope, post, now, selection=2)
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["deliveryRoundCount"] == len(weights)
        assert result["initialWeightGrams"] == initial["reportedWeightGrams"] == 500
        assert result["initialMeasurementUid"] == initial["measurementUid"]
        assert result["finalWeightGrams"] == weights[-1] and result["finalMeasurementUid"] == post["measurementUid"]
        assert result["negativeWeightAnomaly"] is anomaly
        assert result["originCommandUid"] == start["mcuCommandUid"] and result["finishReason"] == "DELIVERY_END"
        now = advance(runtime, now, 60000)
        assert held_result(runtime, start, now) == result
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["before_delivery", "reply_lost"])
def test_pi_restart_and_choice_confirmation_loss_never_repeat_local_cycle_and_preserve_each_output(runtime, tmp_path, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_actuator_handoff import McuActuatorEventHandoff
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, _, replies, *_ = runtime
    path = str(tmp_path / "opening.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
    occupancy = store.get_work_slot()
    broken, sent = False, []

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        name = decoded["messageName"]
        sent.append(name)
        if name == "PROCESS_EVENT_SAVED":
            saved = uart.decode_payload(name, decoded["payload"])
            reader = EdgeStore(path)
            reader.initialize()
            try:
                current_scope = choice_scope if saved["eventMessageType"] == "DELIVERY_SELECTION" else scope
                assert reader.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", current_scope)[8:])["saved_payload"] == decoded["payload"]
                assert reader.get_work_slot() == occupancy
            finally:
                reader.close()
            if broken and loss == "before_delivery":
                return len(frame)
        lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now)
        if broken and name == "PROCESS_EVENT_SAVED":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        handoff = McuProcessEventHandoff(store, write, {k: v for k, v in scope.items() if k != "queryId"})
        handoff.poll(now)
        pump(handoff)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 1, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        replies.clear()
        broken = True
        handoff = McuProcessEventHandoff(store, write, {k: v for k, v in choice_scope.items() if k != "queryId"})
        handoff.poll(now)
        pump(handoff)
        now = advance(runtime, now, 0)
        original_choice = store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", choice_scope)[8:])
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        handoff = McuProcessEventHandoff(store, write, {k: v for k, v in choice_scope.items() if k != "queryId"})
        handoff.poll(now)
        pump(handoff)
        assert store.get_native_process_receipt(original_choice["scope"]) == original_choice
        now = advance(runtime, now, 0)
        for delta in (100, start["deliveryAutoCloseMs"], 100):
            now = advance(runtime, now, delta)
        custody = McuActuatorEventHandoff(store, write, 42)
        for _ in range(5):
            custody.poll(now)
            pump(custody)
            now = advance(runtime, now, 1000)
        custody.poll(now)
        pump(custody)
        assert custody.observation(now)["status"] == "NOT_FOUND"
        for sequence, direction in ((choice["mcuEventSequence"] + 1, "OPEN"), (choice["mcuEventSequence"] + 2, "CLOSE")):
            record = store.get_native_actuator_event(42, sequence)
            assert record["message_name"] == "DELIVERY_LOCAL_DOOR_RESULT"
            value = uart.decode_payload(record["message_name"], record["payload"])
            assert value["selectionEventSequence"] == choice["mcuEventSequence"]
            assert value["roundIndex"] == 2 and value["command"] == direction
            assert value["sessionUid"] == start["sessionUid"]
        assert set(sent) <= {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED", "QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED"}
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
        assert not select(runtime, execution, post["measurementUid"], 1, now)
    finally:
        store.close()

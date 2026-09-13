"""Actual delivery executor owns choice timing and immutable final assembly."""
import pytest

import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, take_samples, original_scope
from hardware.tests.test_mcu_delivery_postclose import closed_cycle, advance
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_native_configuration import inputs


def measured_round(runtime, tmp_path, samples=None, **options):
    execution, start, initial, scope, now = closed_cycle(runtime, tmp_path, **options)
    now = advance(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = now
    samples = [1700] * 5 if samples is None else samples
    if samples:
        now = take_samples(runtime, samples, start=now, measurement=2)
    if len(samples) < 5 or max(samples) - min(samples) > 100:
        now = advance(runtime, now, began + 5000 - now)
    post = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    return execution, start, initial, scope, post, now


def save_process(runtime, store, scope, values, now):
    name = scope["eventMessageType"]
    raw = uart.encode_payload(name, values)
    receipt = store.save_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], name, raw)
    assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return receipt


def held_result(runtime, start, now):
    observed = exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]
    assert observed["status"] == "RESULT_HELD"
    identity = dict(mcuBootId=42, resultSequence=observed["resultSequence"], workUid=start["sessionUid"],
        resultDigestSha256=observed["resultDigestSha256"])
    reply = exchange(runtime, "QUERY_RESULT", identity | {"queryId": 123}, now=now)
    assert reply[0][1]["status"] == "HELD"
    return reply[1][1]


def select(runtime, execution, measurement_uid, selection, now):
    lib, endpoint, *_ = runtime
    return lib.McuDeliveryExecution_Select(execution, endpoint,
        bytes.fromhex(measurement_uid.replace("-", "")), selection, now)


def test_choice_window_starts_after_exact_weight_save_and_expires_once_with_original_weight(runtime, tmp_path):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[0][1]["status"] == "NOT_FOUND"
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == post
        save_process(runtime, store, scope, post, now)
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        deadline = now + start["continueDeliveryWaitMs"]
        now = advance(runtime, now, start["continueDeliveryWaitMs"] - 1)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[0][1]["status"] == "NOT_FOUND"
        now = advance(runtime, now, 1)
        reply = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)
        assert reply[0][1]["status"] == "HELD"
        choice = reply[1][1]
        assert choice["selection"] == "WINDOW_EXPIRED" and choice["uptimeMs"] == deadline
        assert choice["postCloseMeasurementUid"] == post["measurementUid"] != initial["measurementUid"]
        assert choice["mcuCommandUid"] == start["mcuCommandUid"]
        assert choice["mcuEventSequence"] == post["mcuEventSequence"] + 1
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1] == choice
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert facts(runtime, now)["measurementSequence"] == 2
        assert store.list_native_result_report_tasks() == [] and execution
    finally:
        store.close()


def test_saved_timeout_choice_assembles_original_first_and_final_weights_without_resampling(runtime, tmp_path):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        now = advance(runtime, now, start["continueDeliveryWaitMs"])
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        now = advance(runtime, now, 50000)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "DELIVERY_WINDOW_EXPIRED"
        assert result["completedUptimeMs"] == choice["uptimeMs"]
        assert result["originCommandUid"] == start["mcuCommandUid"]
        assert result["originCommandSequence"] == start["commandSequence"]
        assert result["configVersion"] == start["configVersion"]
        assert result["deliveryRoundCount"] == 1 and result["cleanActionSequence"] == 0
        assert not result["physicalCloseConfirmed"] and not result["negativeWeightAnomaly"]
        for prefix, weight in (("initial", initial), ("final", post)):
            assert result[prefix + "MeasurementUid"] == weight["measurementUid"]
            assert result[prefix + "McuEventSequence"] == weight["mcuEventSequence"]
            assert result[prefix + "WeightGrams"] == weight["reportedWeightGrams"]
        now = advance(runtime, now, 60000)
        assert held_result(runtime, start, now) == result
        assert facts(runtime, now)["measurementSequence"] == 2 and execution
    finally:
        store.close()


def test_end_button_is_scoped_to_displayed_weight_and_first_valid_choice_wins(runtime, tmp_path):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        assert not select(runtime, execution, post["measurementUid"], 2, now)  # Weight not SAVED yet.
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert not select(runtime, execution, initial["measurementUid"], 2, now)
        assert not select(runtime, execution, post["measurementUid"], 3, now)  # Only MCU timer may expire.
        now = advance(runtime, now, 100)
        assert select(runtime, execution, post["measurementUid"], 2, now)
        assert not select(runtime, execution, post["measurementUid"], 1, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        assert choice["selection"] == "END" and choice["uptimeMs"] == now
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "DELIVERY_END" and result["finalWeightGrams"] == 1700
        assert not select(runtime, execution, post["measurementUid"], 2, now)
    finally:
        store.close()


@pytest.mark.parametrize("grams,expected", [(1, False), (0, True), (-100, True)])
def test_final_result_carries_frozen_negative_threshold_flag(runtime, tmp_path, grams, expected):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, [grams] * 5)
    assert initial["reportedWeightGrams"] == 500 and start["negativeWeightThresholdGrams"] == 500
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 2, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["negativeWeightAnomaly"] is expected
        assert result["finalWeightGrams"] == grams and result["finishReason"] == "DELIVERY_END"
    finally:
        store.close()


@pytest.mark.parametrize("samples", [[], [300] * 4])
def test_terminal_postclose_measurement_failure_is_an_explicit_final_fact_not_a_choice_or_zero_weight(runtime, tmp_path, samples):
    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, samples)
    assert post["measurementKind"] == "UNAVAILABLE"
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["completedUptimeMs"] == post["uptimeMs"]
        assert result["finalKind"] == "UNAVAILABLE" and result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        assert result["finalMeasurementUid"] == post["measurementUid"]
        assert result["finalSampleCount"] == len(samples) and result["initialWeightGrams"] == 500
        assert not result["negativeWeightAnomaly"]
        assert not select(runtime, execution, post["measurementUid"], 2, now)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope | {"eventMessageType": "DELIVERY_SELECTION"}, now=now)[0][1]["status"] == "NOT_FOUND"
    finally:
        store.close()


def test_future_or_regressing_callback_time_cannot_manufacture_or_reopen_the_choice_window(runtime, tmp_path):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert not select(runtime, execution, post["measurementUid"], 2, now + 1)
        lib.McuWorkPreparation_Poll(preparation, endpoint, now + start["continueDeliveryWaitMs"])
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[0][1]["status"] == "NOT_FOUND"
        now = advance(runtime, now, 10)
        assert not select(runtime, execution, post["measurementUid"], 2, now - 1)
        assert select(runtime, execution, post["measurementUid"], 2, now)
    finally:
        store.close()


@pytest.mark.parametrize("late_ms", [0, 50000])
def test_button_at_or_after_deadline_cannot_replace_timeout_even_when_foreground_was_delayed(runtime, tmp_path, late_ms):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path, start_at=0xFFFFFF00)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        deadline = now + start["continueDeliveryWaitMs"]
        now = advance(runtime, now, start["continueDeliveryWaitMs"] + late_ms, poll=False)
        assert not select(runtime, execution, post["measurementUid"], 1, now)
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", scope | {"eventMessageType": "DELIVERY_SELECTION"}, now=now)[1][1]
        assert choice["selection"] == "WINDOW_EXPIRED" and choice["uptimeMs"] == deadline
        assert choice["uptimeMs"] > 0xFFFFFFFF
    finally:
        store.close()


@pytest.mark.parametrize("stopped", [False, True])
def test_pinch_alone_allows_logical_close_finalization_but_update_stop_does_not(runtime, tmp_path, stopped):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    lib, *_ = runtime
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 2, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        lib.TestFacts_Pinch(1)
        if stopped:
            lib.ActuatorRuntime_StopForUpdate()
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 1)
        observed = exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]
        if stopped:
            assert observed["status"] == "RUNNING" and observed["phase"] == "SAFETY_LOCKED"
        else:
            assert held_result(runtime, start, now)["finishReason"] == "DELIVERY_END"
            assert facts(runtime, now)["pinchPaused"]
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    finally:
        store.close()


def test_unsaved_continue_choice_is_not_misreported_as_completion_or_used_to_replay_first_authorization(runtime, tmp_path):
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 1, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        assert choice["selection"] == "CONTINUE"
        now = advance(runtime, now, 60000)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert facts(runtime, now)["measurementSequence"] == 2
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        assert not select(runtime, execution, post["measurementUid"], 2, now)
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["none", "before_send", "after_delivery"])
def test_actual_round_choice_and_final_result_survive_pi_restart_and_lost_final_confirmation(runtime, tmp_path, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_result_handoff import McuResultHandoff

    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path)
    lib, endpoint, preparation, replies, *_ = runtime
    path = str(tmp_path / "opening.db")  # The same DB already holds the real initial weight.
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
    original_work = store.get_work_slot()
    sent, broken, final_raw = [], True, None

    def write(frame):
        name = uart.decode_frame(frame, sender_role="EDGE")["messageName"]
        sent.append(name)
        if name == "RESULT_SAVED":
            other = EdgeStore(path)
            other.initialize()
            try:
                assert other.get_native_mcu_result(42, 1)["payload"] == final_raw
                assert other.get_work_slot() == original_work
                assert len(other.list_native_result_report_tasks()) == 1
            finally:
                other.close()
            if broken and loss == "before_send":
                raise OSError("synthetic final confirmation write loss")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if name == "RESULT_SAVED" and broken and loss == "after_delivery":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    def transfer_process(current_scope):
        replies.clear()
        client = McuProcessEventHandoff(store, write, {key: value for key, value in current_scope.items() if key != "queryId"})
        client.poll(now)
        pump(client)

    try:
        transfer_process(scope)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 2, now)
        transfer_process(scope | {"eventMessageType": "DELIVERY_SELECTION"})
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        final_raw = uart.encode_payload("WORK_RESULT", result)
        identity = uart.decode_payload("RESULT_SAVED", final_raw[:60])
        replies.clear()
        client = McuResultHandoff(store, write, identity)
        first_query = client.poll(now)
        pump(client)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        now = advance(runtime, now, 1000)
        resumed = McuResultHandoff(store, write, identity)
        assert resumed.poll(now) > first_query
        pump(resumed)
        now = advance(runtime, now, 1000)
        resumed.poll(now)
        pump(resumed)
        assert resumed.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == original_work
        tasks = store.list_native_result_report_tasks()
        assert len(tasks) == 1 and tasks[0]["state"] == "PENDING_CLASSIFICATION"
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert sent.count("RESULT_SAVED") == (2 if loss == "before_send" else 1)
        assert facts(runtime, now)["measurementSequence"] == 2
        assert result["initialMeasurementUid"] == initial["measurementUid"] and result["finalMeasurementUid"] == post["measurementUid"]
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RESULT_RELEASED"
        assert not select(runtime, execution, post["measurementUid"], 2, now)
    finally:
        store.close()


def test_custom_frozen_window_and_timeout_median_are_used_without_default_or_stable_relabelling(runtime, tmp_path):
    import hashlib
    from mcu_configuration import NativeMcuConfiguration

    data = inputs()
    preimage = bytearray(NativeMcuConfiguration(**data).digest_preimage)
    offset = len(bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"])) + 8 + 32 + 1
    preimage[offset:offset + 4] = (1000).to_bytes(4, "big")
    data["device"] = data["device"] | {"continueDeliveryWaitMs": 1000}
    data["expected_sha256"] = hashlib.sha256(preimage).hexdigest()
    candidate = NativeMcuConfiguration(**data)
    execution, start, _, scope, post, now = measured_round(runtime, tmp_path, [-500, 1500] * 10,
        candidate=candidate, continueDeliveryWaitMs=1000)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        now = advance(runtime, now, 999)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[0][1]["status"] == "NOT_FOUND"
        now = advance(runtime, now, 1)
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finalKind"] == "TIMEOUT_MEDIAN" and result["finalWeightGrams"] == 500
        assert result["finalSpanGrams"] == 2000 and result["finalElapsedMs"] == 5000
        assert result["finishReason"] == "DELIVERY_WINDOW_EXPIRED" and execution
    finally:
        store.close()


def test_next_authorized_business_resets_choice_and_anomaly_without_inheriting_prior_result(runtime, tmp_path):
    from hardware.tests.test_mcu_opening_gate import grant

    execution, start, initial, scope, post, now = measured_round(runtime, tmp_path, [0] * 5)
    store = EdgeStore(str(tmp_path / "final.db"))
    store.initialize()

    def finish(current_start, current_scope, current_post):
        save_process(runtime, store, current_scope, current_post, now)
        advance(runtime, now, 0)
        assert select(runtime, execution, current_post["measurementUid"], 2, now)
        choice_scope = current_scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        save_process(runtime, store, choice_scope, choice, now)
        advance(runtime, now, 0)
        return held_result(runtime, current_start, now)

    try:
        first = finish(start, scope, post)
        assert first["negativeWeightAnomaly"]
        receipt = store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", first))
        assert exchange(runtime, "RESULT_SAVED", payload=receipt["savedPayload"], now=now)[0][1]["status"] == "RELEASED"
        second = start | {"mcuCommandUid": "88888888-8888-4888-8888-888888888888",
            "sessionUid": "99999999-9999-4999-8999-999999999999", "commandSequence": 8}
        second["commandDigestSha256"] = uart.compute_command_digest("START_DELIVERY_SESSION", second)
        assert exchange(runtime, "START_DELIVERY_SESSION", second, now=now)[0][1]["outcome"] == "ACCEPTED"
        assert not select(runtime, execution, post["measurementUid"], 2, now)
        # Request spacing is global across measurement windows; a new job does
        # not reset the last real RS485 attempt's 250 ms polling interval.
        now = advance(runtime, now, 250)
        now = take_samples(runtime, [600] * 5, start=now, measurement=3)
        pre_scope = original_scope(second) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
            "stepSequence": 1, "configVersion": second["configVersion"]}
        pre = exchange(runtime, "QUERY_PROCESS_EVENT", pre_scope, now=now)[1][1]
        save_process(runtime, store, pre_scope, pre, now)
        name, command = grant(second, pre, commandSequence=9, mcuCommandUid="66666666-6666-4666-8666-666666666666")
        assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
        for delta in (100, second["deliveryAutoCloseMs"], 100, inputs()["device"]["deliveryDoorTravelWaitMs"]):
            now = advance(runtime, now, delta)
        now = take_samples(runtime, [1600] * 5, start=now, measurement=4)
        second_scope = pre_scope | {"eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY"}
        second_post = exchange(runtime, "QUERY_PROCESS_EVENT", second_scope, now=now)[1][1]
        result = finish(second, second_scope, second_post)
        assert result["resultSequence"] == first["resultSequence"] + 1
        assert result["workUid"] == second["sessionUid"] and not result["negativeWeightAnomaly"]
        assert result["initialWeightGrams"] == 600 and result["finalWeightGrams"] == 1600
        assert result["initialMeasurementUid"] != first["initialMeasurementUid"]
        assert store.get_native_mcu_result(42, first["resultSequence"])["payload"] == uart.encode_payload("WORK_RESULT", first)
    finally:
        store.close()

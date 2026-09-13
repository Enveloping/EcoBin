"""Actual failed initial acquisition ends with explicit missing final data, no motion."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope, configured, start_values, take_samples
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_delivery_execution import enable, facts
from hardware.tests.test_mcu_delivery_finalization import save_process, held_result
from hardware.tests.test_mcu_delivery_postclose import advance


def test_failed_initial_measurement_finishes_only_after_exact_custody_without_opening(runtime, tmp_path):
    execution = enable(runtime)
    start, failed, now = prepared(runtime, tmp_path, mode="unavailable", saved=False)
    scope = original_scope(start) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
        "stepSequence": 1, "configVersion": start["configVersion"]}
    assert failed["measurementKind"] == "UNAVAILABLE" and failed["measurementElapsedMs"] == 5000
    now = advance(runtime, now, 60000)
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
    store = EdgeStore(str(tmp_path / "failed-initial.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, failed, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["deliveryRoundCount"] == 0
        assert result["initialKind"] == "UNAVAILABLE" and result["initialFaultCode"] == "WEIGHT_TIMEOUT"
        assert result["initialMeasurementUid"] == failed["measurementUid"]
        assert result["initialMcuEventSequence"] == failed["mcuEventSequence"]
        assert result["completedUptimeMs"] == failed["uptimeMs"] == 5000
        assert result["finalKind"] == "NOT_TAKEN" and result["finalMeasurementUid"] == "00000000-0000-0000-0000-000000000000"
        assert result["originCommandUid"] == start["mcuCommandUid"] and result["configVersion"] == start["configVersion"]
        assert not result["negativeWeightAnomaly"] and not result["physicalCloseConfirmed"]
        assert facts(runtime, now)["measurementSequence"] == 1
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
        assert exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=2, targetMcuBootId=42,
            afterMcuEventSequence=0), now=now)[0][1]["status"] == "NOT_FOUND"
        now = advance(runtime, now, 60000)
        assert held_result(runtime, start, now) == result
        assert store.list_native_result_report_tasks() == [] and execution
    finally:
        store.close()


@pytest.mark.parametrize("samples", [[300], [300] * 4])
def test_insufficient_initial_samples_keep_original_failure_identity_and_sample_count(runtime, tmp_path, samples):
    execution = enable(runtime)
    configured(runtime, applied=True)
    start = start_values()
    assert exchange(runtime, "START_DELIVERY_SESSION", start)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, samples)
    now = advance(runtime, now, 5000 - now)
    scope = original_scope(start) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
        "stepSequence": 1, "configVersion": start["configVersion"]}
    failed = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    store = EdgeStore(str(tmp_path / "insufficient.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, failed, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["initialKind"] == "UNAVAILABLE" and result["initialSampleCount"] == len(samples)
        assert result["initialMeasurementUid"] == failed["measurementUid"] and result["initialElapsedMs"] == 5000
        assert result["finalKind"] == "NOT_TAKEN" and result["deliveryRoundCount"] == 0
        assert facts(runtime, now)["measurementSequence"] == 1 and execution
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["stable", "median"])
def test_available_initial_value_is_never_closed_as_preopen_failure(runtime, tmp_path, mode):
    execution = enable(runtime)
    start, initial, now = prepared(runtime, tmp_path, mode=mode, saved=True)
    now = advance(runtime, now, 0)
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["phase"] == "DELIVERY_WAIT_FIRST_OPEN_AUTH"
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, 100)
    assert facts(runtime, now)["lastDeliveryDoorCommand"] == "OPEN" and execution


def test_wrong_confirmation_and_future_poll_cannot_complete_the_failed_work(runtime, tmp_path):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, failed, now = prepared(runtime, tmp_path, mode="unavailable", saved=False)
    scope = original_scope(start) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
        "stepSequence": 1, "configVersion": start["configVersion"]}
    wrong = dict(mcuBootId=42, mcuEventSequence=failed["mcuEventSequence"],
        eventMessageType="WORK_PREOPEN_WEIGHT_READY", eventDigestSha256="00" * 32)
    assert exchange(runtime, "PROCESS_EVENT_SAVED", wrong, now=now)[0][1]["status"] == "IDENTITY_CONFLICT"
    now = advance(runtime, now, 1)
    assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
    store = EdgeStore(str(tmp_path / "wrong-save.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, failed, now)
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now + 1)
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        now = advance(runtime, now, 0)
        assert held_result(runtime, start, now)["completedUptimeMs"] == failed["uptimeMs"]
        assert not facts(runtime, now)["pb6Output"] and execution
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["none", "before_delivery", "reply_lost"])
def test_failed_preopen_result_survives_pi_restart_without_motion_or_early_business_release(runtime, tmp_path, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_result_handoff import McuResultHandoff
    execution = enable(runtime)
    lib, endpoint, _, replies, *_ = runtime
    start, failed, now = prepared(runtime, tmp_path, mode="unavailable", saved=False)
    scope = original_scope(start) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
        "stepSequence": 1, "configVersion": start["configVersion"]}
    path = str(tmp_path / "failure-handoff.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", start["sessionUid"], 1, {"phase": "NATIVE_DELIVERY"})
    occupancy = store.get_work_slot()
    broken, sent, final_raw = True, [], None

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        name = decoded["messageName"]
        sent.append(name)
        if name == "RESULT_SAVED":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_native_mcu_result(42, 1)["payload"] == final_raw
                assert len(reader.list_native_result_report_tasks()) == 1
                assert reader.get_work_slot() == occupancy
            finally:
                reader.close()
            if broken and loss == "before_delivery":
                raise OSError("synthetic result confirmation loss")
        lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now)
        if broken and name == "RESULT_SAVED" and loss == "reply_lost":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        transfer = McuProcessEventHandoff(store, write, {k: v for k, v in scope.items() if k != "queryId"})
        transfer.poll(now)
        pump(transfer)
        now = advance(runtime, now, 0)
        final_raw = uart.encode_payload("WORK_RESULT", held_result(runtime, start, now))
        identity = uart.decode_payload("RESULT_SAVED", final_raw[:60])
        replies.clear()
        handoff = McuResultHandoff(store, write, identity)
        first_query = handoff.poll(now)
        pump(handoff)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        handoff = McuResultHandoff(store, write, identity)
        now = advance(runtime, now, 1000)
        assert handoff.poll(now) > first_query
        pump(handoff)
        now = advance(runtime, now, 1000)
        handoff.poll(now)
        pump(handoff)
        assert handoff.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == occupancy
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert set(sent) <= {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == 1
        assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"] and execution
    finally:
        store.close()


def test_initial_failure_after_a_previous_anomalous_delivery_never_reuses_old_round_or_final_weight(runtime, tmp_path):
    from hardware.tests.test_mcu_delivery_finalization import measured_round, select
    execution, previous_start, _, scope, post, now = measured_round(runtime, tmp_path, [0] * 5)
    store = EdgeStore(str(tmp_path / "two-works.db"))
    store.initialize()
    try:
        save_process(runtime, store, scope, post, now)
        now = advance(runtime, now, 0)
        assert select(runtime, execution, post["measurementUid"], 2, now)
        choice_scope = scope | {"eventMessageType": "DELIVERY_SELECTION"}
        choice = exchange(runtime, "QUERY_PROCESS_EVENT", choice_scope, now=now)[1][1]
        save_process(runtime, store, choice_scope, choice, now)
        now = advance(runtime, now, 0)
        old_result = held_result(runtime, previous_start, now)
        assert old_result["negativeWeightAnomaly"]
        old_raw = uart.encode_payload("WORK_RESULT", old_result)
        receipt = store.save_native_mcu_result(old_raw)
        assert exchange(runtime, "RESULT_SAVED", payload=receipt["savedPayload"], now=now)[0][1]["status"] == "RELEASED"
        start = start_values(commandSequence=8, mcuCommandUid="88888888-8888-4888-8888-888888888888",
            sessionUid="99999999-9999-4999-8999-999999999999")
        assert exchange(runtime, "START_DELIVERY_SESSION", start, now=now)[0][1]["outcome"] == "ACCEPTED"
        now = advance(runtime, now, 5000)
        scope = original_scope(start) | {"eventMessageType": "WORK_PREOPEN_WEIGHT_READY",
            "stepSequence": 1, "configVersion": start["configVersion"]}
        failed = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
        save_process(runtime, store, scope, failed, now)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["resultSequence"] == 2 and result["deliveryRoundCount"] == 0
        assert not result["negativeWeightAnomaly"] and result["finalKind"] == "NOT_TAKEN"
        assert result["initialMeasurementUid"] == failed["measurementUid"] != old_result["finalMeasurementUid"]
        assert store.get_native_mcu_result(42, 1)["payload"] == old_raw
    finally:
        store.close()


def test_delivery_owner_does_not_finalize_failed_clean_preunlock_measurement(runtime, tmp_path):
    execution = enable(runtime)
    start, failed, now = prepared(runtime, tmp_path, clean=True, mode="unavailable", saved=True)
    now = advance(runtime, now, 60000)
    status = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
    assert failed["measurementKind"] == "UNAVAILABLE"
    assert status["status"] == "RUNNING" and status["workType"] == "CLEAN_OPERATION"
    assert facts(runtime, now)["measurementSequence"] == 1
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    assert exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=2, targetMcuBootId=42,
        afterMcuEventSequence=0), now=now)[0][1]["status"] == "NOT_FOUND" and execution

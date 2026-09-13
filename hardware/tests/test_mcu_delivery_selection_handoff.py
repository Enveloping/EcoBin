"""END selection is diagnosed by one authoritative autonomous WORK_RESULT.

The screen choice is consumed locally by the MCU.  The Pi does not save a
DELIVERY_SELECTION event to authorize completion or later motion; it only
queries, stores and precisely confirms the immutable result.
"""
import ctypes as c
import uuid

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
from hardware.tests.test_mcu_work_preparation import take_samples


def ready_result(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1700] * 5, start=now, measurement=2)
    measurement_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(delivery), 16)
    assert select(runtime, delivery, now, "END", uid=measurement_uid)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["finalMeasurementUid"] == str(uuid.UUID(bytes=measurement_uid))
    assert result["finalWeightGrams"] == 1700
    identity = {
        "mcuBootId": result["mcuBootId"],
        "resultSequence": result["resultSequence"],
        "workUid": result["workUid"],
        "resultDigestSha256": result["resultDigestSha256"],
    }
    return delivery, start, result, identity, now


def changed_result(result):
    values = dict(result)
    values["finalWeightGrams"] += 1
    values["resultDigestSha256"] = uart.compute_result_digest(values)
    return uart.encode_payload("WORK_RESULT", values)


def pump(client, replies, now):
    while replies:
        client.accept_frame(replies.pop(0), now)


def test_conflicting_complete_result_is_retained_and_never_acknowledged(runtime, tmp_path):
    delivery, _, result, identity, now = ready_result(runtime)
    original = uart.encode_payload("WORK_RESULT", result)
    conflict = changed_result(result)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    try:
        client = McuResultHandoff(store, lambda frame: sent.append(frame) or len(frame), identity)
        assert client.accept_frame(uart.encode_frame("WORK_RESULT", 1, original), now)
        assert len(sent) == 1
        assert uart.decode_frame(sent[0], sender_role="EDGE")["messageName"] == "RESULT_SAVED"

        with pytest.raises(ValueError, match="identity conflict"):
            client.accept_frame(uart.encode_frame("WORK_RESULT", 2, conflict), now)
        assert len(sent) == 1
        assert store.get_native_mcu_result(42, result["resultSequence"])["payload"] == original
        assert store.list_native_mcu_result_conflicts()[0]["payload"] == conflict
        assert len(store.list_native_result_report_tasks()) == 1

        store.close()
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        resumed = McuResultHandoff(store, lambda frame: sent.append(frame) or len(frame), identity)
        with pytest.raises(ValueError, match="identity conflict"):
            resumed.accept_frame(uart.encode_frame("WORK_RESULT", 3, conflict), now + 1000)
        assert len(sent) == 1
        assert resumed.accept_frame(uart.encode_frame("WORK_RESULT", 4, original), now + 1000)
        assert len(sent) == 2
        assert len(store.list_native_result_report_tasks()) == 1
        assert delivery
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["none", "confirm_write", "confirm_reply"])
def test_result_commits_before_confirmation_and_pi_restart_never_repeats_work(
    runtime, tmp_path, loss
):
    delivery, start, result, identity, now = ready_result(runtime)
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    lib, endpoint, _, replies, *_ = runtime
    sent = []
    broken = True
    writes = lib.TestFacts_Writes()

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        message = decoded["messageName"]
        sent.append(message)
        if message == "RESULT_SAVED":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                saved = reader.get_native_mcu_result(42, identity["resultSequence"])
                assert saved["payload"] == uart.encode_payload("WORK_RESULT", result)
            finally:
                reader.close()
            if broken and loss == "confirm_write":
                raise OSError("synthetic loss before RESULT_SAVED write")
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and loss == "confirm_reply" and message == "RESULT_SAVED":
            replies.clear()
        return len(frame)

    try:
        replies.clear()
        client = McuResultHandoff(store, write, identity)
        first_query = client.poll(now)
        pump(client, replies, now)
        saved = store.get_native_mcu_result(42, identity["resultSequence"])
        assert saved["payload"] == uart.encode_payload("WORK_RESULT", result)
        assert len(store.list_native_result_report_tasks()) == 1

        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        resumed = McuResultHandoff(store, write, identity)
        for _ in range(2):
            lib.RuntimeClock_Advance(1000)
            now += 1000
            query = resumed.poll(now)
            pump(resumed, replies, now)
        assert query > first_query
        assert resumed.query_observation(now)["status"] == "RELEASED"
        assert state(runtime, start, now)["status"] == "RESULT_RELEASED"
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.get_native_mcu_result(42, identity["resultSequence"])["payload"] == saved["payload"]
        assert sent.count("RESULT_SAVED") == (2 if loss == "confirm_write" else 1)
        assert set(sent) <= {"QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == 2
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
        assert lib.TestFacts_Writes() == writes
        assert delivery
    finally:
        store.close()


def test_contradictory_query_replies_need_the_exact_result_and_restart_uses_a_new_query(runtime, tmp_path):
    delivery, start, result, identity, now = ready_result(runtime)
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    lib, endpoint, _, replies, *_ = runtime
    sent = []

    def write(frame):
        message = uart.decode_frame(frame, sender_role="EDGE")["messageName"]
        sent.append(message)
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        return len(frame)

    try:
        replies.clear()
        client = McuResultHandoff(store, write, identity)
        first_query = client.poll(now)
        first_reply = replies.pop(0)
        full_result = replies.pop(0)
        values = uart.decode_payload(
            "RESULT_QUERY_REPLY", uart.decode_frame(first_reply, sender_role="MCU")["payload"]
        )
        assert values["status"] == "HELD"
        assert client.accept_frame(first_reply, now)
        contradictory = uart.encode_payload(
            "RESULT_QUERY_REPLY", values | {"status": "RELEASED"}
        )
        assert not client.accept_frame(
            uart.encode_frame("RESULT_QUERY_REPLY", 99, contradictory), now
        )
        assert client.query_observation(now) is None
        assert store.get_native_mcu_result(42, identity["resultSequence"]) is None
        assert "RESULT_SAVED" not in sent
        # Query metadata is diagnostic.  The independently validated complete
        # result remains authoritative and is the only input that may create a
        # durable receipt and precise acknowledgement.
        assert client.accept_frame(full_result, now)
        assert store.get_native_mcu_result(42, identity["resultSequence"])["payload"] == uart.encode_payload("WORK_RESULT", result)
        assert sent[-1] == "RESULT_SAVED"

        store.close()
        store = EdgeStore(path)
        store.initialize()
        now = tick(runtime, now, 1000)
        replies.clear()
        resumed = McuResultHandoff(store, write, identity)
        second_query = resumed.poll(now)
        pump(resumed, replies, now)
        assert second_query > first_query
        now = tick(runtime, now, 1000)
        resumed.poll(now)
        pump(resumed, replies, now)
        assert resumed.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_mcu_result(42, identity["resultSequence"])["payload"] == uart.encode_payload("WORK_RESULT", result)
        assert len(store.list_native_result_report_tasks()) == 1
        assert state(runtime, start, now)["status"] == "RESULT_RELEASED"
        assert delivery
    finally:
        store.close()

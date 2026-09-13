"""Pi result receiver against real C stream endpoint and committed SQLite."""
import sqlite3

import pytest
from edge_store import EdgeStore
import uart2_protocol as uart
from hardware.tests.test_mcu_control_endpoint import endpoint, seed_completed_work
from hardware.tests.test_native_result_handoff import result_payload


def identity(payload):
    return uart.decode_payload("RESULT_SAVED", payload[:60])


def test_result_receiver_commits_before_confirmation_and_preserves_business(endpoint, tmp_path):
    from mcu_result_handoff import McuResultHandoff
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    replies.clear()
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    work_uid = identity(result)["workUid"]
    assert store.acquire_work_slot("DELIVERY", work_uid, 1, {"phase": "WAITING_RESULT"})
    original = store.get_work_slot()
    sent = []
    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        if decoded["messageName"] == "RESULT_SAVED":
            # An independent connection proves the COMMIT precedes UART.
            with sqlite3.connect(path) as other:
                assert other.execute("SELECT payload FROM native_mcu_result").fetchone()[0] == result
                assert other.execute("SELECT COUNT(*) FROM native_result_report_outbox").fetchone()[0] == 1
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuResultHandoff(store, write, identity(result))
        assert client.poll(0) == 1
        assert len(replies) == 2
        assert client.accept_frame(replies.pop(0), 0)
        assert client.query_observation(0)["status"] == "HELD"
        assert len(sent) == 1  # HELD alone cannot authorize saved confirmation.
        assert client.accept_frame(replies.pop(0), 0)
        assert [uart.decode_frame(frame)["messageName"] for frame in sent] == ["QUERY_RESULT", "RESULT_SAVED"]
        assert client.saved_receipt["savedPayload"] == result[:60]
        assert store.get_work_slot() == original
        assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert store._conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == 0
        # Saved reply is only historical: a new read-only query observes release.
        replies.clear()
        assert client.poll(1000) is not None
        assert client.accept_frame(replies.pop(0), 1000)
        assert client.query_observation(1000)["status"] == "RELEASED"
    finally:
        store.close()


def test_same_result_number_conflict_is_archived_without_confirming_it(tmp_path):
    from mcu_result_handoff import McuResultHandoff
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    result = result_payload()
    sent = []
    def write(frame):
        sent.append(frame)
        return len(frame)
    try:
        client = McuResultHandoff(store, write, identity(result))
        assert client.accept_frame(uart.encode_frame("WORK_RESULT", 1, result), 0)
        conflicting = result_payload(finalWeightGrams=999)
        with pytest.raises(ValueError, match="identity conflict"):
            client.accept_frame(uart.encode_frame("WORK_RESULT", 2, conflicting), 1000)
        assert len(sent) == 1
        assert store.get_native_mcu_result(42, 3)["payload"] == result
        assert store.list_native_mcu_result_conflicts()[0]["payload"] == conflicting
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


def test_storage_commit_failure_never_writes_confirmation(tmp_path):
    from mcu_result_handoff import McuResultHandoff
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    result = result_payload()
    sent = []
    client = McuResultHandoff(store, lambda frame: sent.append(frame) or len(frame), identity(result))
    def deny_commit(action, first, second, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK
    store._conn.set_authorizer(deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(uart.encode_frame("WORK_RESULT", 1, result), 0)
        assert sent == [] and client.saved_receipt is None
        assert store.get_native_mcu_result(42, 3) is None
        assert store.list_native_result_report_tasks() == []
        store._conn.set_authorizer(None)
        assert client.accept_frame(uart.encode_frame("WORK_RESULT", 1, result), 1000)
        assert len(sent) == 1
    finally:
        store._conn.set_authorizer(None)
        store.close()


@pytest.mark.parametrize("damage", ["missing_task", "corrupt_body"])
def test_fresh_held_reply_cannot_confirm_damaged_local_custody(endpoint, tmp_path, damage):
    from mcu_result_handoff import McuResultHandoff
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.save_native_mcu_result(result)
    with store.transaction(immediate=True) as conn:
        if damage == "missing_task":
            conn.execute("DELETE FROM native_result_report_outbox")
        else:
            broken = result[:80] + bytes([result[80] ^ 1]) + result[81:]
            conn.execute("UPDATE native_mcu_result SET payload=?", (broken,))
    sent = []
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuResultHandoff(store, write, identity(result))
        client.poll(0)
        with pytest.raises((RuntimeError, ValueError)):
            client.accept_frame(replies.pop(0), 0)
        assert len(sent) == 1 and client.saved_receipt is None
    finally:
        store.close()


def test_stale_held_and_unrelated_or_invalid_results_do_not_confirm(endpoint, tmp_path):
    from mcu_result_handoff import McuResultHandoff
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.save_native_mcu_result(result)
    sent = []
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuResultHandoff(store, write, identity(result))
        client.poll(0)
        stale = replies.pop(0)
        client.poll(1000)
        assert not client.accept_frame(stale, 1000)
        for body in [result_payload(resultSequence=4), result[:-1], result[:80] + bytes([result[80] ^ 1]) + result[81:]]:
            assert not client.accept_frame(uart.encode_frame("WORK_RESULT", 1, body), 1000)
        assert len(sent) == 2
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("write_failure", ["short", "error"])
def test_restart_rechecks_committed_result_before_retrying_saved_confirmation(endpoint, tmp_path, write_failure):
    from mcu_result_handoff import McuResultHandoff
    lib, memory, replies, sink = endpoint
    result, _ = seed_completed_work(endpoint)
    replies.clear()
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    sent = []
    def broken_write(frame):
        sent.append(frame)
        if write_failure == "error":
            raise OSError("lost serial connection")
        return 1
    receiver = McuResultHandoff(store, broken_write, identity(result))
    assert receiver.accept_frame(uart.encode_frame("WORK_RESULT", 1, result), 0)
    assert receiver.last_write_error == ("SHORT_WRITE" if write_failure == "short" else "WRITE_FAILED")
    assert len(sent) == 1
    store.close()
    store = EdgeStore(path)
    store.initialize()
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        resumed = McuResultHandoff(store, write, identity(result))
        assert resumed.saved_receipt is None  # not merely inferred from old RAM
        resumed.poll(0)
        held, repeated_result = replies.pop(0), replies.pop(0)
        assert uart.decode_frame(repeated_result)["messageName"] == "WORK_RESULT"
        # Drop repeated body: actual durable bytes + fresh exact HELD suffice.
        assert resumed.accept_frame(held, 0)
        assert uart.decode_frame(sent[-1])["messageName"] == "RESULT_SAVED"
        assert resumed.saved_receipt["savedPayload"] == result[:60]
        assert uart.decode_payload("RESULT_SAVED_REPLY", uart.decode_frame(replies.pop(0))["payload"])["status"] == "RELEASED"
        assert len(store.list_native_result_report_tasks()) == 1
        # A duplicate HELD cannot trigger another write in the same interval.
        assert resumed.accept_frame(held, 1)
        assert len(sent) == 3
    finally:
        store.close()

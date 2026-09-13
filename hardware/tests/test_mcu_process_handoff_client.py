"""Pi client and actual C endpoint, with real SQLite commit and restart."""
import pytest
import sqlite3
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_control_endpoint import endpoint, bind
from hardware.tests.test_mcu_process_event_slot import freeze, process_scope, process_values
from hardware.tests.test_native_process_receipt import scope, payload


def test_fresh_query_fetches_original_and_commits_before_precise_confirmation(endpoint, tmp_path):
    from mcu_process_handoff import McuProcessEventHandoff
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    assert freeze(lib, lib.TestControl_ProcessEvent(memory)) == 1
    replies.clear()
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    sent = []
    def write(frame):
        sent.append(frame)
        if uart.decode_frame(frame)["messageName"] == "PROCESS_EVENT_SAVED":
            other = EdgeStore(path)
            other.initialize()
            try:
                assert other.get_native_process_receipt(scope())["payload"] == payload()
            finally:
                other.close()
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuProcessEventHandoff(store, write, process_scope())
        client.poll(0)
        assert client.accept_frame(replies.pop(0), 0)
        assert len(sent) == 1
        assert client.accept_frame(replies.pop(0), 0)
        assert [uart.decode_frame(frame)["messageName"] for frame in sent] == ["QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"]
        assert not client.accept_frame(replies.pop(0), 0)  # uncorrelated saved reply is not fresh facts
        assert client.observation(0)["status"] == "HELD"
        client.poll(1000)
        assert client.accept_frame(replies.pop(0), 1000)
        assert client.observation(1000)["status"] == "RELEASED"
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["short", "error"])
def test_pi_restart_recovers_after_lost_saved_confirmation_without_new_measurement(endpoint, tmp_path, failure):
    from mcu_process_handoff import McuProcessEventHandoff
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    assert freeze(lib, lib.TestControl_ProcessEvent(memory)) == 1
    replies.clear()
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    sent = []
    broken = True
    def write(frame):
        sent.append(frame)
        if broken and uart.decode_frame(frame)["messageName"] == "PROCESS_EVENT_SAVED":
            if failure == "error":
                raise OSError("lost serial link")
            return 1
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    first = McuProcessEventHandoff(store, write, process_scope())
    first_query = first.poll(0)
    assert first.accept_frame(replies.pop(0), 0)
    event = replies.pop(0)
    assert first.accept_frame(event, 0)
    assert first.last_write_error == ("SHORT_WRITE" if failure == "short" else "WRITE_FAILED")
    assert first.accept_frame(event, 1)
    assert len(sent) == 2  # no immediate replay, even if the whole body is repeated
    store.close()
    store = EdgeStore(path)
    store.initialize()
    broken = False
    try:
        resumed = McuProcessEventHandoff(store, write, process_scope())
        assert resumed.poll(0) > first_query
        held, repeated_body = replies.pop(0), replies.pop(0)
        # Drop the repeated body: complete committed local bytes still exist.
        assert resumed.accept_frame(held, 0)
        assert len(sent) == 4
        assert resumed.accept_frame(held, 1)
        assert len(sent) == 4
        assert uart.decode_payload("PROCESS_EVENT_SAVED_REPLY", uart.decode_frame(replies.pop(0))["payload"])["status"] == "RELEASED"
        assert uart.decode_frame(repeated_body)["payload"] == payload()
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_failed_receipt_commit_cannot_confirm_or_leave_an_unscoped_partial_insert(endpoint, tmp_path):
    from mcu_process_handoff import McuProcessEventHandoff
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    assert freeze(lib, lib.TestControl_ProcessEvent(memory)) == 1
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    def deny_commit(action, first, second, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK
    try:
        client = McuProcessEventHandoff(store, write, process_scope())
        client.poll(0)
        assert client.accept_frame(replies.pop(0), 0)
        event = replies.pop(0)
        store._conn.set_authorizer(deny_commit)
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(event, 0)
        store._conn.set_authorizer(None)
        assert len(sent) == 1
        assert store.get_native_process_receipt(scope()) is None
        assert store.get_native_measurement_event(42, 3) is None
        assert client.accept_frame(event, 1)
        assert len(sent) == 2
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_stale_reply_and_unassociated_event_cannot_authorize_custody(endpoint, tmp_path):
    from mcu_process_handoff import McuProcessEventHandoff
    lib, memory, replies, sink = endpoint
    bind(endpoint)
    assert freeze(lib, lib.TestControl_ProcessEvent(memory)) == 1
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    def write(frame):
        sent.append(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuProcessEventHandoff(store, write, process_scope())
        client.poll(0)
        old_reply, event = replies.pop(0), replies.pop(0)
        assert not client.accept_frame(event, 0)
        client.poll(1000)
        assert not client.accept_frame(old_reply, 1000)
        assert not client.accept_frame(event, 1000)
        assert len(sent) == 2 and store.get_native_measurement_event(42, 3) is None
        assert client.accept_frame(replies.pop(0), 1000)
        assert client.accept_frame(event, 1000)
        assert len(sent) == 3
    finally:
        store.close()

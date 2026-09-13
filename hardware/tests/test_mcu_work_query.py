"""Real SQLite identities and a captured transport boundary; no serial device."""
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from edge_store import EdgeStore
import uart2_protocol as uart
from hardware.tests.test_mcu_work_state_c import query_payload
from hardware.tests.test_native_result_handoff import ROOT


def identity():
    values = uart.decode_payload("QUERY_WORK", query_payload())
    del values["queryId"]
    return values


def reply(request_frame, **changes):
    values = uart.decode_payload("QUERY_WORK", uart.decode_frame(request_frame, sender_role="EDGE")["payload"])
    values |= {"currentMcuBootId": 42, "status": "RUNNING", "phase": "DELIVERY_OPEN_COUNTDOWN",
               "resultSequence": 0, "resultDigestSha256": "00" * 32}
    values |= changes
    return uart.encode_frame("WORK_QUERY_REPLY", 1, uart.encode_payload("WORK_QUERY_REPLY", values))


def test_poll_uses_committed_unique_query_and_accepts_only_matching_reply(tmp_path):
    from mcu_work_query import McuWorkQuery
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    sent = []
    def write(frame):
        observer = EdgeStore(path)
        observer.initialize()
        try:
            values = uart.decode_payload("QUERY_WORK", uart.decode_frame(frame, sender_role="EDGE")["payload"])
            assert int(observer.get_state("native_query_sequence")) == values["queryId"]
        finally:
            observer.close()
        sent.append(frame)
        return len(frame)
    try:
        client = McuWorkQuery(store, write, identity())
        assert client.poll(0) == 1
        assert client.poll(100) is None
        assert client.observation(100) is None
        assert client.accept_frame(reply(sent[0]), 101)
        assert client.observation(101)["status"] == "RUNNING"
        assert not client.accept_frame(reply(sent[0], portNo=2), 102)
        assert client.observation(1000) is None
        assert client.poll(1000) == 2
        assert not client.accept_frame(reply(sent[0]), 1001)
        assert client.accept_frame(reply(sent[1], status="NOT_FOUND", phase="IDLE"), 1002)
        assert client.observation(1002)["status"] == "NOT_FOUND"
        assert len(sent) == 2
        assert store.get_work_slot() is None
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["short", "raise"])
def test_incomplete_write_never_retries_the_same_request(tmp_path, failure):
    from mcu_work_query import McuWorkQuery
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    def write(frame):
        sent.append(frame)
        if failure == "raise":
            raise OSError("disconnected")
        return 3
    try:
        client = McuWorkQuery(store, write, identity())
        assert client.poll(0) == 1
        assert client.last_write_error == ("SHORT_WRITE" if failure == "short" else "WRITE_FAILED")
        assert client.observation(999) is None
        assert client.poll(999) is None
        assert len(sent) == 1
        assert client.poll(1000) == 2
        assert len(sent) == 2 and sent[0] != sent[1]
        assert all(uart.decode_frame(frame, sender_role="EDGE")["messageName"] == "QUERY_WORK" for frame in sent)
    finally:
        store.close()


def test_process_exit_after_reservation_does_not_reuse_id_or_accept_old_reply(tmp_path):
    from mcu_work_query import McuWorkQuery
    path = str(tmp_path / "edge.db")
    child = '''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1]); store.initialize()
assert store.reserve_native_query_id() == 1
os._exit(77)
'''
    run = subprocess.run([sys.executable, "-c", child, path],
                         env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), timeout=15)
    assert run.returncode == 77
    store = EdgeStore(path)
    store.initialize()
    sent = []
    try:
        client = McuWorkQuery(store, lambda frame: sent.append(frame) or len(frame), identity())
        assert client.poll(0) == 2
        assert not client.accept_frame(reply(sent[0], queryId=1), 1)
        assert client.accept_frame(reply(sent[0], status="BOOT_MISMATCH", phase="IDLE", currentMcuBootId=0), 2)
        assert client.observation(2)["status"] == "BOOT_MISMATCH"
        assert store.get_state("native_query_sequence") == "2"
    finally:
        store.close()


def test_query_counter_serializes_connections_and_rejects_nested_transactions(tmp_path):
    path = str(tmp_path / "edge.db")
    stores = [EdgeStore(path), EdgeStore(path)]
    for store in stores:
        store.initialize()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda i: stores[i % 2].reserve_native_query_id(), range(40)))
        assert sorted(results) == list(range(1, 41))
        with stores[0].transaction(immediate=True) as conn:
            conn.execute("INSERT INTO device_state (state_key, state_value) VALUES ('outer', 'keep')")
            with pytest.raises(RuntimeError, match="standalone"):
                stores[0].reserve_native_query_id()
            assert conn.in_transaction
        assert stores[0].get_state("outer") == "keep"
        assert stores[0].reserve_native_query_id() == 41
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("bad", ["-1", "01", "1.0", "abc", "", "١", "9007199254740991", "99999999999999999"])
def test_corrupt_or_exhausted_counter_sends_nothing(tmp_path, bad):
    from mcu_work_query import McuWorkQuery
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    try:
        store.set_state("native_query_sequence", bad)
        client = McuWorkQuery(store, lambda frame: sent.append(frame) or len(frame), identity())
        with pytest.raises(ValueError, match="counter"):
            client.poll(0)
        assert sent == []
        assert store.get_state("native_query_sequence") == bad
    finally:
        store.close()


def test_commit_failure_sends_nothing_and_does_not_consume_id(tmp_path):
    from mcu_work_query import McuWorkQuery
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    try:
        client = McuWorkQuery(store, lambda frame: sent.append(frame) or len(frame), identity())
        store._conn.set_authorizer(lambda action, value, *args:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and value == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            client.poll(0)
        assert sent == []
        store._conn.set_authorizer(None)
        assert client.poll(1) == 1
        assert len(sent) == 1
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_conflicting_duplicate_reply_becomes_unknown_until_new_query(tmp_path):
    from mcu_work_query import McuWorkQuery
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    try:
        client = McuWorkQuery(store, lambda frame: sent.append(frame) or len(frame), identity())
        client.poll(0)
        valid = reply(sent[0])
        assert client.accept_frame(valid, 1)
        assert client.accept_frame(valid, 2)
        assert not client.accept_frame(reply(sent[0], phase="DELIVERY_CLOSE_COMMAND"), 3)
        assert client.observation(3) is None
        assert not client.accept_frame(valid, 4)
        assert client.poll(1000) == 2
        assert client.accept_frame(reply(sent[1], status="RESULT_HELD", phase="DELIVERY_FINALIZING",
                                         resultSequence=3, resultDigestSha256="ab" * 32), 1001)
        observation = client.observation(1001)
        assert observation["resultSequence"] == 3
        observation["status"] = "tampered"
        assert client.observation(1002)["status"] == "RESULT_HELD"
        assert store.get_native_mcu_result(42, 3) is None  # a reference is not a saved full result
        assert not client.accept_frame(b"bad", 1003)
        with pytest.raises(ValueError, match="monotonic"):
            client.poll(999)
        assert len(sent) == 2
    finally:
        store.close()

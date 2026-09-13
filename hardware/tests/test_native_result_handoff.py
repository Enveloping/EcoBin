"""Real EdgeStore transactions; native facts cannot enter the money relay yet."""
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from edge_store import CURRENT_SCHEMA_VERSION, EdgeStore
import uart2_protocol as uart

ROOT = Path(__file__).resolve().parents[2]


def result_payload(**changes):
    vectors = json.loads((ROOT / "contracts/examples/uart/golden-vectors.json").read_text("utf-8"))
    vector = next(item for item in vectors["vectors"] if item["name"] == "work_result_delivery")
    values = uart.decode_payload("WORK_RESULT", bytes.fromhex(vector["payloadHex"])) | changes
    values["resultDigestSha256"] = uart.compute_result_digest(values)
    return uart.encode_payload("WORK_RESULT", values)


def test_committed_full_result_and_report_task_survive_restart_and_deduplicate(tmp_path):
    path = str(tmp_path / "edge.db")
    payload = result_payload()
    store = EdgeStore(path)
    store.initialize()
    receipt = store.save_native_mcu_result(payload)
    assert receipt["savedPayload"] == payload[:60]
    assert store.get_native_mcu_result(42, 3)["payload"] == payload
    assert len(store.list_native_result_report_tasks()) == 1
    assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
    assert store.get_work_slot() is None
    assert store._conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == 0
    store.close()
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.save_native_mcu_result(payload) == receipt
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("boundary", ["before_task", "after_task", "after_commit"])
def test_process_exit_at_real_sqlite_boundary_never_leaves_half_handoff(tmp_path, boundary):
    path = str(tmp_path / "edge.db")
    payload = result_payload()
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
boundary = sys.argv[3]
store._conn.create_function("crash_now", 0, lambda: os._exit(77))
if boundary != "after_commit":
    timing = "BEFORE" if boundary == "before_task" else "AFTER"
    store._conn.execute("CREATE TEMP TRIGGER crash_handoff " + timing +
        " INSERT ON native_result_report_outbox BEGIN SELECT crash_now(); END")
receipt = store.save_native_mcu_result(bytes.fromhex(sys.argv[2]))
assert receipt["savedPayload"] == bytes.fromhex(sys.argv[2])[:60]
os._exit(78)  # committed, but no UART acknowledgement has been transmitted
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT / "hardware"))
    result = subprocess.run([sys.executable, "-c", child, path, payload.hex(), boundary],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == (78 if boundary == "after_commit" else 77), result.stderr
    store = EdgeStore(path)
    store.initialize()
    try:
        committed = boundary == "after_commit"
        assert (store.get_native_mcu_result(42, 3) is not None) == committed
        assert len(store.list_native_result_report_tasks()) == int(committed)
        receipt = store.save_native_mcu_result(payload)
        assert receipt["savedPayload"] == payload[:60]
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.integrity_check()
    finally:
        store.close()


def test_invalid_or_conflicting_body_never_receives_saved_receipt(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    payload = result_payload()
    try:
        for invalid in (payload[:-1], payload + b"\0", payload[:80] + bytes([payload[80] ^ 1]) + payload[81:]):
            with pytest.raises(ValueError):
                store.save_native_mcu_result(invalid)
        assert store.list_native_result_report_tasks() == []
        receipt = store.save_native_mcu_result(payload)
        with pytest.raises(ValueError, match="identity conflict"):
            store.save_native_mcu_result(result_payload(finalWeightGrams=999))
        assert store.list_native_mcu_result_conflicts()[0]["payload"] == result_payload(finalWeightGrams=999)
        assert store.save_native_mcu_result(payload) == receipt
        assert len(store.list_native_result_report_tasks()) == 1
    finally:
        store.close()


def test_nested_save_is_rejected_without_rolling_back_callers_transaction(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        with store.transaction(immediate=True) as conn:
            conn.execute("UPDATE device_state SET state_value='7' WHERE state_key='edge_event_sequence'")
            with pytest.raises(RuntimeError, match="standalone"):
                store.save_native_mcu_result(result_payload())
        assert store.get_edge_event_sequence() == 7
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_commit_failure_rolls_back_result_and_task_without_receipt(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    def deny_commit(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store._conn.set_authorizer(deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.save_native_mcu_result(result_payload())
        assert store.get_native_mcu_result(42, 3) is None
        assert store.list_native_result_report_tasks() == []
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_concurrent_connections_only_create_one_task(tmp_path):
    path = str(tmp_path / "edge.db")
    first, second = EdgeStore(path), EdgeStore(path)
    first.initialize()
    second.initialize()
    payload = result_payload()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda store: store.save_native_mcu_result(payload), (first, second)))
        assert receipts[0] == receipts[1]
        assert len(first.list_native_result_report_tasks()) == 1
    finally:
        first.close()
        second.close()


def test_v18_upgrade_is_atomic_and_preserves_existing_business(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    work_uid = "22222222-2222-4222-8222-222222222222"
    assert store.acquire_work_slot("DELIVERY", work_uid, 1, {"phase": "WAITING_COMPAT_DELIVERY_RESULT"})
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_work_recovery_fact")
        conn.execute("DROP TABLE native_work_recovery_intent")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DROP TABLE native_clean_intent")
        conn.execute("DROP TABLE native_delivery_selection")
        conn.execute("DROP TABLE native_actuator_event_conflict")
        conn.execute("DROP TABLE native_actuator_event")
        conn.execute("DROP TABLE native_process_receipt_conflict")
        conn.execute("DROP TABLE native_process_receipt")
        conn.execute("DROP TABLE native_measurement_event_conflict")
        conn.execute("DROP TABLE native_measurement_event")
        conn.execute("DROP TABLE native_mcu_command_observation")
        conn.execute("DROP TABLE native_mcu_command")
        conn.execute("DROP TABLE native_mcu_boot_observation")
        conn.execute("DROP TABLE native_mcu_boot")
        conn.execute("DROP TABLE native_mcu_result_conflict")
        conn.execute("DROP TABLE native_result_report_outbox")
        conn.execute("DROP TABLE native_mcu_result")
        conn.execute("DELETE FROM schema_version WHERE version >= 19")
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'native_%'").fetchone()[0] == 0
    previous = store.get_work_slot()
    store.close()
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store._open_connection()
store._conn.create_function("crash_now", 0, lambda: os._exit(79))
store._conn.execute("CREATE TEMP TRIGGER crash_migration AFTER INSERT ON schema_version "
    "WHEN new.version = 19 BEGIN SELECT crash_now(); END")
store.initialize()
'''
    result = subprocess.run([sys.executable, "-c", child, path],
                            env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert result.returncode == 79, result.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 18
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'native_%'").fetchone()[0] == 0
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.get_work_slot() == previous
        assert store._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert store.save_native_mcu_result(result_payload())["savedPayload"] == result_payload()[:60]
        assert store.get_work_slot() == previous  # handoff does not release occupancy
    finally:
        store.close()

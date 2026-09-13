"""Local process evidence survives Pi restart, without cloud tasks or motion receipts."""
import ctypes as c
import sqlite3
import uuid
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from edge_store import CURRENT_SCHEMA_VERSION, EdgeStore
import uart2_protocol as uart
from hardware.tests.test_mcu_process_measurement import Meta, producer
from hardware.tests.test_mcu_result_builder import available, builder, work
from contracts.tests.test_uart_v2_process_measurement import process_message_values

ROOT = Path(__file__).resolve().parents[2]


def payload(name="WORK_PREOPEN_WEIGHT_READY", **changes):
    return uart.encode_payload(name, process_message_values(uart.REGISTRY, name) | changes)


@pytest.fixture
def store(tmp_path):
    database = EdgeStore(str(tmp_path / "edge.db"))
    database.initialize()
    try:
        yield database
    finally:
        database.close()


def test_actual_c_process_bytes_are_saved_before_return_and_survive_reopen(producer, builder, tmp_path):
    state = work(builder)
    assert builder.McuWorkState_SetPhase(state, 17)
    measurement = available(builder, 1, 0)
    meta, scratch = Meta(7, 2000, 1), c.create_string_buffer(242)
    length = producer(state, c.byref(measurement), c.byref(meta), 48, scratch, 242)
    payload = scratch.raw[:length]
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        before = bytes(state)
        assert store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload) == "STORED"
        assert bytes(state) == before
        assert store.list_native_result_report_tasks() == []
        # A second connection sees the COMMIT, not the original connection's uncommitted write.
        observer = EdgeStore(path)
        observer.initialize()
        try:
            row = observer.get_native_measurement_event(42, 1)
            assert row["payload"] == payload and row["message_name"] == "WORK_PREOPEN_WEIGHT_READY"
        finally:
            observer.close()
    finally:
        store.close()
    reopened = EdgeStore(path)
    reopened.initialize()
    try:
        assert reopened.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload) == "DUPLICATE"
        decoded = uart.decode_payload("WORK_PREOPEN_WEIGHT_READY", reopened.get_native_measurement_event(42, 1)["payload"])
        assert decoded["measurementKind"] == "STABLE_MEAN" and decoded["reportedWeightGrams"] == 0
        assert reopened.list_native_result_report_tasks() == []
    finally:
        reopened.close()


@pytest.mark.parametrize("name", uart.REGISTRY["sessionPolicy"]["processMeasurementMessages"])
def test_each_process_message_is_isolated_from_cloud_and_final_result_queues(store, name):
    raw = payload(name)
    assert store.save_native_measurement_event(name, raw) == "STORED"
    assert store.save_native_measurement_event(name, raw) == "DUPLICATE"
    assert store.get_native_measurement_event(42, 3)["payload"] == raw
    assert store.list_native_result_report_tasks() == []


def test_conflicting_event_bytes_are_archived_and_original_cannot_be_reaccepted(store):
    name, original = "WORK_PREOPEN_WEIGHT_READY", payload()
    changed = payload(reportedWeightGrams=10)
    assert store.save_native_measurement_event(name, original) == "STORED"
    for raw in (changed, changed, original):
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_measurement_event(name, raw)
    conflicts = store.list_native_measurement_event_conflicts()
    assert len(conflicts) == 1 and conflicts[0]["payload"] == changed
    with pytest.raises(ValueError, match="conflict"):
        store.get_native_measurement_event(42, 3)
    assert store._conn.execute("SELECT payload FROM native_measurement_event").fetchone()[0] == original


def test_conflicts_also_latch_the_incoming_event_key_and_incoming_measurement_uid(store):
    name = "WORK_PREOPEN_WEIGHT_READY"
    assert store.save_native_measurement_event(name, payload()) == "STORED"
    # Reuse the actual measurement UID at a different event identity; no second primary record.
    with pytest.raises(ValueError, match="conflict"):
        store.save_native_measurement_event(name, payload(mcuEventSequence=4))
    # A new UUID cannot wash away the known conflicting event key.
    changed_uid = str(uuid.UUID(int=888))
    with pytest.raises(ValueError, match="conflict"):
        store.save_native_measurement_event(name, payload(mcuEventSequence=4, measurementUid=changed_uid))
    # Nor can the newly conflicting UUID be reused under another event key.
    with pytest.raises(ValueError, match="conflict"):
        store.save_native_measurement_event(name, payload(mcuEventSequence=5, measurementUid=changed_uid))
    assert store._conn.execute("SELECT count(*) FROM native_measurement_event").fetchone()[0] == 1
    assert len(store.list_native_measurement_event_conflicts()) == 3


def test_one_incoming_record_colliding_with_two_originals_blocks_both(store):
    name, second_uid = "WORK_PREOPEN_WEIGHT_READY", str(uuid.UUID(int=999))
    store.save_native_measurement_event(name, payload())
    store.save_native_measurement_event(name, payload(mcuEventSequence=4, measurementUid=second_uid))
    with pytest.raises(ValueError, match="conflict"):
        store.save_native_measurement_event(name, payload(measurementUid=second_uid))
    assert len(store.list_native_measurement_event_conflicts()) == 2
    for sequence in (3, 4):
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_measurement_event(42, sequence)


@pytest.mark.parametrize("invalid", ["bytes_type", "message_type", "shape", "digest_corruption", "metadata_corruption"])
def test_invalid_or_corrupt_data_never_returns_a_successful_storage_result(store, invalid):
    name, raw = "WORK_PREOPEN_WEIGHT_READY", payload()
    if invalid == "digest_corruption":
        store.save_native_measurement_event(name, raw)
        store._conn.execute("UPDATE native_measurement_event SET payload_sha256=?", ("ab" * 32,))
        store._conn.commit()
    elif invalid == "metadata_corruption":
        store.save_native_measurement_event(name, raw)
        store._conn.execute("UPDATE native_measurement_event SET measurement_uid=?", (str(uuid.UUID(int=222)),))
        store._conn.commit()
    elif invalid == "bytes_type": raw = bytearray(raw)
    elif invalid == "message_type": name = "WORK_RESULT"
    elif invalid == "shape": raw = raw[:-1]
    with pytest.raises(ValueError):
        store.save_native_measurement_event(name, raw)
    if invalid.endswith("corruption"):
        with pytest.raises(ValueError):
            store.get_native_measurement_event(42, 3)


def test_nested_transaction_is_rejected_without_rolling_back_its_owner(store):
    with store.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO device_state (state_key,state_value) VALUES ('process_test','kept')")
        with pytest.raises(RuntimeError, match="standalone"):
            store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload())
    assert store._conn.execute("SELECT state_value FROM device_state WHERE state_key='process_test'").fetchone()[0] == "kept"
    assert store.get_native_measurement_event(42, 3) is None


def test_failed_database_write_cannot_return_stored_or_erase_original(store):
    store._conn.execute("""CREATE TRIGGER fail_process_insert BEFORE INSERT ON native_measurement_event
        BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="simulated disk failure"):
        store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload())
    assert store.get_native_measurement_event(42, 3) is None


def test_commit_denial_rolls_back_without_success(store):
    def deny_commit(action, first, second, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK
    store._conn.set_authorizer(deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload())
    finally:
        store._conn.set_authorizer(None)
    assert store.get_native_measurement_event(42, 3) is None


@pytest.mark.parametrize("boundary", ["before_insert", "after_insert", "after_commit"])
def test_process_exit_on_real_sqlite_boundaries_retains_only_committed_evidence(tmp_path, boundary):
    path, raw = str(tmp_path / "edge.db"), payload()
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
store._conn.create_function("crash_now", 0, lambda: os._exit(77))
if sys.argv[3] != "after_commit":
    timing = "BEFORE" if sys.argv[3] == "before_insert" else "AFTER"
    store._conn.execute("CREATE TEMP TRIGGER crash_process " + timing +
        " INSERT ON native_measurement_event BEGIN SELECT crash_now(); END")
assert store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", bytes.fromhex(sys.argv[2])) == "STORED"
os._exit(78)
'''
    run = subprocess.run([sys.executable, "-c", child, path, raw.hex(), boundary],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert run.returncode == (78 if boundary == "after_commit" else 77), run.stderr
    database = EdgeStore(path)
    database.initialize()
    try:
        assert (database.get_native_measurement_event(42, 3) is not None) == (boundary == "after_commit")
        assert database.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", raw) == (
            "DUPLICATE" if boundary == "after_commit" else "STORED")
        assert database.list_native_result_report_tasks() == [] and database.integrity_check()
    finally:
        database.close()


def test_simultaneous_connections_deduplicate_the_same_event(tmp_path):
    path = str(tmp_path / "edge.db")
    first, second = EdgeStore(path), EdgeStore(path)
    first.initialize()
    second.initialize()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda db: db.save_native_measurement_event(
                "WORK_PREOPEN_WEIGHT_READY", payload()), (first, second)))
        assert sorted(results) == ["DUPLICATE", "STORED"]
        assert first._conn.execute("SELECT count(*) FROM native_measurement_event").fetchone()[0] == 1
    finally:
        first.close()
        second.close()


def test_v20_migration_rolls_back_on_exit_then_preserves_work_command_and_result(tmp_path):
    from hardware.tests.test_native_result_handoff import result_payload
    from hardware.tests.test_native_command_session import COMMAND_UID, open_fields
    path = str(tmp_path / "edge.db")
    database = EdgeStore(path)
    database.initialize()
    original_result = result_payload()
    database.save_native_mcu_result(original_result)
    assert database.reserve_native_boot_id(database.reserve_native_query_id()) == 1
    assert database.recognize_native_boot_id(1)
    previous_command = database.prepare_native_command("AUTHORIZE_DELIVERY_FIRST_OPEN", COMMAND_UID, 1, open_fields())
    assert database.acquire_work_slot("DELIVERY", open_fields()["sessionUid"], 1, {"phase": "TEST_PENDING"})
    previous_work, previous_boot = database.get_work_slot(), database.get_native_boot(1)
    with database.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_process_receipt_conflict")
        conn.execute("DROP TABLE native_process_receipt")
        conn.execute("DROP TABLE native_measurement_event_conflict")
        conn.execute("DROP TABLE native_measurement_event")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=21")
    database.close()
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store._open_connection()
store._conn.create_function("crash_now", 0, lambda: os._exit(79))
store._conn.execute("CREATE TEMP TRIGGER crash_v21 AFTER INSERT ON schema_version "
    "WHEN new.version=21 BEGIN SELECT crash_now(); END")
store.initialize()
'''
    run = subprocess.run([sys.executable, "-c", child, path],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert run.returncode == 79, run.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 20
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_measurement_event'").fetchone()
    database = EdgeStore(path)
    database.initialize()
    try:
        assert database._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert database.get_work_slot() == previous_work and database.get_native_boot(1) == previous_boot
        assert database.get_native_command(COMMAND_UID) == previous_command
        assert database.get_native_mcu_result(42, 3)["payload"] == original_result
        assert len(database.list_native_result_report_tasks()) == 1
        assert database.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload()) == "STORED"
        assert database.get_work_slot() == previous_work
    finally:
        database.close()

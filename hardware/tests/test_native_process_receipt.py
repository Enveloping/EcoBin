"""Receipt is returned only after full evidence AND original context commit."""
import sqlite3
import os
import sys
import subprocess
import ctypes
from pathlib import Path
import pytest
from edge_store import EdgeStore
import uart2_protocol as uart
from contracts.tests.test_uart_v2_process_handoff import process_scope
from contracts.tests.test_uart_v2_process_measurement import process_values
from contracts.tests.test_uart_v2_process_measurement import process_message_values
from hardware.tests.test_mcu_process_event_slot import slot_lib, freeze
from hardware.tests.test_mcu_process_measurement import Meta, producer
from hardware.tests.test_mcu_result_builder import available, builder, work
from hardware.tests.test_mcu_work_state_c import query_payload

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name,phase,clean,step", [("WORK_PREOPEN_WEIGHT_READY", 17, False, 1),
    ("WORK_POSTCLOSE_WEIGHT_READY", 24, False, 3), ("WORK_PREUNLOCK_WEIGHT_READY", 33, True, 0),
    ("CLEAN_FINAL_WEIGHT_READY", 37, True, 7)])
def test_actual_c_producer_slot_and_sqlite_share_one_immutable_measurement(producer, builder, slot_lib, tmp_path, name, phase, clean, step):
    changes = {"workType": "CLEAN_OPERATION"} if clean else {}
    state = work(builder, **changes)
    assert builder.McuWorkState_SetPhase(state, phase)
    measured = available(builder, 1, 0)
    meta, scratch = Meta(7, 2000, step), ctypes.create_string_buffer(242)
    size = producer(state, ctypes.byref(measured), ctypes.byref(meta), uart.MESSAGE_SPECS[name]["id"], scratch, 242)
    assert size > 0
    raw = scratch.raw[:size]
    original = uart.decode_payload("QUERY_WORK", query_payload(**changes))
    original.update(eventMessageType=name, stepSequence=step, configVersion=7)
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", original)[8:]
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert slot_lib.McuProcessEventSlot_Freeze(slot, raw_scope, len(raw_scope), uart.MESSAGE_SPECS[name]["id"], raw, size) == 1
    before = bytes(state)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(raw_scope, name, raw)
        assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        assert bytes(state) == before  # receipt does not move the work state machine
    finally:
        store.close()


def scope(**changes):
    values = dict(process_scope(), **changes)
    return uart.encode_payload("QUERY_PROCESS_EVENT", dict(queryId=1, **values))[8:]


def payload(**changes):
    return uart.encode_payload("WORK_PREOPEN_WEIGHT_READY", dict(process_values(), **changes))


def test_process_receipt_and_complete_context_are_visible_after_commit_and_restart(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
        assert uart.decode_payload("PROCESS_EVENT_SAVED", receipt)["mcuEventSequence"] == 3
        other = EdgeStore(path)
        other.initialize()
        try:
            saved = other.get_native_process_receipt(scope())
            assert saved["payload"] == payload() and saved["saved_payload"] == receipt
            assert saved["scope"] == scope()
        finally:
            other.close()
    finally:
        store.close()
    reopened = EdgeStore(path)
    reopened.initialize()
    try:
        assert reopened.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload()) == receipt
    finally:
        reopened.close()


@pytest.mark.parametrize("name", uart.REGISTRY["sessionPolicy"]["processMeasurementMessages"])
def test_six_message_types_match_the_same_c_slot_and_durable_pi_context(slot_lib, tmp_path, name):
    values = process_message_values(uart.REGISTRY, name)
    subject, work_type = {"WORK_PREOPEN_WEIGHT_READY": ("sessionUid", "DELIVERY_SESSION"),
        "WORK_POSTCLOSE_WEIGHT_READY": ("sessionUid", "DELIVERY_SESSION"),
        "WORK_PREUNLOCK_WEIGHT_READY": ("operationUid", "CLEAN_OPERATION"),
        "CLEAN_FINAL_WEIGHT_READY": ("operationUid", "CLEAN_OPERATION"),
        "FULLNESS_SAMPLE_RESULT": ("detectionUid", "FULLNESS_DETECTION"),
        "BASELINE_MEASUREMENT_RESULT": ("measurementUid", "BASELINE_MEASUREMENT")}[name]
    for step in ("roundIndex", "cleanActionSequence"):
        if step in values:
            values[step] = 1
    original = dict(process_scope(), workType=work_type, workUid=values[subject], eventMessageType=name,
                    stepSequence=values.get("roundIndex", values.get("cleanActionSequence", 0)))
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", dict(queryId=1, **original))[8:]
    raw = uart.encode_payload(name, values)
    slot = (ctypes.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot, values, original, name) == 1
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(raw_scope, name, raw)
        assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        assert store.list_native_result_report_tasks() == [] and store.get_work_slot() is None
    finally:
        store.close()
@pytest.mark.parametrize("changed_scope,changed_payload", [
    (dict(commandDigestSha256="33" * 32), {}),
    ({}, dict(mcuEventSequence=4, measurementUid="55555555-5555-4555-8555-555555555555"))])
def test_context_collision_keeps_both_claims_and_blocks_future_receipts(tmp_path, changed_scope, changed_payload):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(**changed_scope), "WORK_PREOPEN_WEIGHT_READY", payload(**changed_payload))
        assert len(store.list_native_process_receipt_conflicts()) == 1
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_receipt(scope())
    finally:
        store.close()


def test_prior_context_conflict_cannot_be_laundered_with_new_event_and_scope(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
        for digest, seq, uid in [("33", 3, process_values()["measurementUid"]),
            ("33", 4, "55555555-5555-4555-8555-555555555555"),
            ("44", 4, "55555555-5555-4555-8555-555555555555")]:
            with pytest.raises(ValueError, match="conflict"):
                store.save_native_process_receipt(scope(commandDigestSha256=digest * 32), "WORK_PREOPEN_WEIGHT_READY",
                    payload(mcuEventSequence=seq, measurementUid=uid))
        assert len(store.list_native_process_receipt_conflicts()) == 3
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_receipt(scope())
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["nested", "insert_failure", "saved_corrupt", "body_corrupt", "scope_mismatch"])
def test_receipt_is_not_returned_for_incomplete_or_corrupt_custody(tmp_path, damage):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        if damage in {"saved_corrupt", "body_corrupt"}:
            store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
            with store.transaction(immediate=True) as conn:
                if damage == "saved_corrupt":
                    conn.execute("UPDATE native_process_receipt SET saved_payload=?", (bytes(45),))
                else:
                    conn.execute("UPDATE native_measurement_event SET payload=?", (payload(reportedWeightGrams=1),))
            with pytest.raises(ValueError):
                store.get_native_process_receipt(scope())
        elif damage == "insert_failure":
            store._conn.execute("""CREATE TEMP TRIGGER fail_receipt BEFORE INSERT ON native_process_receipt
                BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
        elif damage == "nested":
            with store.transaction(immediate=True) as conn:
                conn.execute("INSERT INTO device_state (state_key,state_value) VALUES ('receipt_test','kept')")
                with pytest.raises(RuntimeError, match="standalone"):
                    store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
            assert store._conn.execute("SELECT state_value FROM device_state WHERE state_key='receipt_test'").fetchone()[0] == "kept"
            assert store.get_native_measurement_event(42, 3) is None
            return
        with pytest.raises((ValueError, sqlite3.DatabaseError)):
            store.save_native_process_receipt(scope(stepSequence=2) if damage == "scope_mismatch" else scope(),
                                             "WORK_PREOPEN_WEIGHT_READY", payload())
        if damage in {"insert_failure", "scope_mismatch"}:
            assert store.get_native_measurement_event(42, 3) is None
    finally:
        store.close()


@pytest.mark.parametrize("boundary", ["before_insert", "after_insert", "after_commit"])
def test_process_exit_cannot_split_raw_evidence_from_receipt_context(tmp_path, boundary):
    path = str(tmp_path / "edge.db")
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
store._conn.create_function("crash_now", 0, lambda: os._exit(77))
if sys.argv[4] != "after_commit":
    timing = "BEFORE" if sys.argv[4] == "before_insert" else "AFTER"
    store._conn.execute("CREATE TEMP TRIGGER crash_receipt " + timing +
        " INSERT ON native_process_receipt BEGIN SELECT crash_now(); END")
store.save_native_process_receipt(bytes.fromhex(sys.argv[2]), "WORK_PREOPEN_WEIGHT_READY", bytes.fromhex(sys.argv[3]))
os._exit(78)
'''
    run = subprocess.run([sys.executable, "-c", child, path, scope().hex(), payload().hex(), boundary],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")), capture_output=True, text=True, timeout=15)
    assert run.returncode == (78 if boundary == "after_commit" else 77), run.stderr
    store = EdgeStore(path)
    store.initialize()
    try:
        assert (store.get_native_measurement_event(42, 3) is not None) == (boundary == "after_commit")
        assert (store.get_native_process_receipt(scope()) is not None) == (boundary == "after_commit")
        assert len(store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())) == 45
        assert store.integrity_check() and store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_v21_to_v22_migration_keeps_original_evidence_after_exit_and_retry(tmp_path):
    from hardware.tests.test_native_result_handoff import result_payload
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", payload())
    store.save_native_mcu_result(result_payload())
    assert store.acquire_work_slot("DELIVERY", process_scope()["workUid"], 1, {"phase": "TEST_PENDING"})
    original_work = store.get_work_slot()
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_process_receipt_conflict")
        conn.execute("DROP TABLE native_process_receipt")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=22")
    store.close()
    child = r'''
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store._open_connection()
store._conn.create_function("crash_now", 0, lambda: os._exit(79))
store._conn.execute("CREATE TEMP TRIGGER crash_v22 AFTER INSERT ON schema_version "
    "WHEN new.version=22 BEGIN SELECT crash_now(); END")
store.initialize()
'''
    run = subprocess.run([sys.executable, "-c", child, path], env=dict(os.environ, PYTHONPATH=str(ROOT / "hardware")),
        capture_output=True, text=True, timeout=15)
    assert run.returncode == 79, run.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 21
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_process_receipt'").fetchone()
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.get_work_slot() == original_work
        assert store.get_native_measurement_event(42, 3)["payload"] == payload()
        assert store.get_native_mcu_result(42, 3)["payload"] == result_payload()
        assert len(store.list_native_result_report_tasks()) == 1
        assert store.get_native_process_receipt(scope()) is None  # raw archive alone is not confirmation
        store.save_native_process_receipt(scope(), "WORK_PREOPEN_WEIGHT_READY", payload())
        assert store.get_native_process_receipt(scope())["payload"] == payload()
        assert store.get_work_slot() == original_work
    finally:
        store.close()

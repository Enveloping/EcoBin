"""Standalone SQLite custody commits, not command acceptance or business effects."""
from edge_store import EdgeStore
from hardware.tests.test_mcu_actuator_event_journal import payload
import uart2_protocol as uart
import pytest
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_complete_actuator_bytes_and_reported_command_survive_pi_restart(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        saved = store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
        assert uart.decode_payload("ACTUATOR_EVENT_SAVED", saved)["mcuEventSequence"] == 7
        assert store.list_native_result_report_tasks() == [] and store.get_work_slot() is None
    finally:
        store.close()
    reopened = EdgeStore(path)
    reopened.initialize()
    try:
        row = reopened.get_native_actuator_event(42, 7)
        assert row["payload"] == payload() and row["saved_payload"] == saved
        assert row["reported_command_uid"] == "11111111-1111-4111-8111-111111111111"
        assert row["reported_work_uid"] == "33333333-3333-4333-8333-333333333333"
        assert reopened.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload()) == saved
    finally:
        reopened.close()


def test_conflicting_original_bytes_are_retained_and_block_later_receipts(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload(lockPowerState="DEENERGIZED"))
        assert len(store.list_native_actuator_event_conflicts()) == 1
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_actuator_event(42, 7)
    finally:
        store.close()


@pytest.mark.parametrize("actuator_first", [False, True])
def test_global_event_identity_cannot_be_reused_across_measurements_and_actuators(tmp_path, actuator_first):
    from contracts.tests.test_uart_v2_process_measurement import process_values
    measured = uart.encode_payload("WORK_PREOPEN_WEIGHT_READY", process_values() | {"mcuEventSequence": 7})
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    act = lambda: store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
    measure = lambda: store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", measured)
    first, second = (act, measure) if actuator_first else (measure, act)
    try:
        first()
        with pytest.raises(ValueError, match="conflict"):
            second()
        assert store.list_native_actuator_event_conflicts()
        with pytest.raises(ValueError, match="conflict"):
            first()
    finally:
        store.close()


@pytest.mark.parametrize("name", ["CLEAN_LOCK_POWER_CHANGED", "DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT"])
def test_all_three_output_types_commit_their_own_reported_context(tmp_path, name):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        raw = payload(name)
        receipt = store.save_native_actuator_event(name, raw)
        assert store.get_native_actuator_event(42, 7)["payload"] == raw
        assert uart.decode_payload("ACTUATOR_EVENT_SAVED", receipt)["eventMessageType"] == name
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["saved_payload", "reported_command_uid", "reported_work_uid", "payload", "nested"])
def test_corrupt_or_uncommitted_custody_cannot_return_a_receipt(tmp_path, damage):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        if damage == "nested":
            with store.transaction(immediate=True):
                with pytest.raises(RuntimeError, match="standalone"):
                    store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
            assert store.get_native_actuator_event(42, 7) is None
            return
        store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
        value = {"saved_payload": bytes(45), "reported_command_uid": "wrong", "reported_work_uid": "wrong",
                 "payload": payload(lockPowerState="DEENERGIZED")}[damage]
        # Test-only corruption of a selected fixed column, never production repair.
        with store.transaction(immediate=True) as conn:
            conn.execute(f"UPDATE native_actuator_event SET {damage}=?", (value,))
        with pytest.raises(ValueError, match="corrupt"):
            store.get_native_actuator_event(42, 7)
        with pytest.raises(ValueError, match="corrupt"):
            store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
    finally:
        store.close()


def test_schema22_upgrade_keeps_existing_process_data(tmp_path):
    from contracts.tests.test_uart_v2_process_measurement import process_values
    path = str(tmp_path / "edge.db")
    raw = uart.encode_payload("WORK_PREOPEN_WEIGHT_READY", process_values())
    store = EdgeStore(path)
    store.initialize()
    store.save_native_measurement_event("WORK_PREOPEN_WEIGHT_READY", raw)
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DROP TABLE native_clean_intent")
        conn.execute("DROP TABLE native_delivery_selection")
        conn.execute("DROP TABLE native_actuator_event_conflict")
        conn.execute("DROP TABLE native_actuator_event")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=23")
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 22
    store.close()
    upgraded = EdgeStore(path)
    upgraded.initialize()
    try:
        assert upgraded.get_native_measurement_event(42, 3)["payload"] == raw
        assert upgraded.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", payload())
    finally:
        upgraded.close()


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_forced_process_exit_preserves_only_committed_actuator_custody(tmp_path, point):
    path = str(tmp_path / "edge.db")
    code = """
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
if sys.argv[2] == 'before_commit':
    store._conn.set_trace_callback(lambda sql: os._exit(51) if sql.strip().upper() == 'COMMIT' else None)
store.save_native_actuator_event('CLEAN_LOCK_POWER_CHANGED', bytes.fromhex(sys.argv[3]))
os._exit(52)
"""
    run = subprocess.run([sys.executable, "-c", code, path, point, payload().hex()],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert run.returncode == (51 if point == "before_commit" else 52), run.stdout + run.stderr
    recovered = EdgeStore(path)
    recovered.initialize()
    try:
        row = recovered.get_native_actuator_event(42, 7)
        if point == "before_commit":
            assert row is None
        else:
            assert row["payload"] == payload()
            assert uart.decode_payload("ACTUATOR_EVENT_SAVED", row["saved_payload"])["mcuEventSequence"] == 7
    finally:
        recovered.close()

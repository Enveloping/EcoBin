"""Exact clean intent custody, additive migration and independent failure evidence."""
import sqlite3

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
from contracts.tests.test_uart_v2_clean_intent import INTENTS, intent_values, intent_scope


def scope(name=INTENTS[0], **changes):
    return uart.encode_payload("QUERY_PROCESS_EVENT", intent_scope(name) | {"queryId": 1} | changes)[8:]


def payload(name=INTENTS[0], **changes):
    return uart.encode_payload(name, intent_values() | changes)


@pytest.mark.parametrize("failure", ["none", "create", "version"])
def test_schema25_upgrade_is_atomic_and_preserves_original_custody_and_occupancy(tmp_path, failure):
    from hardware.tests.test_native_delivery_selection import save_weight, scope as choice_scope, selection
    from contracts.tests.test_uart_v2_actuator_events import actuator_values
    path = str(tmp_path / "upgrade.db")
    store = EdgeStore(path)
    store.initialize()
    save_weight(store)
    weight = store.get_native_process_receipt(choice_scope("WORK_POSTCLOSE_WEIGHT_READY"))
    choice = store.save_native_process_receipt(choice_scope(), "DELIVERY_SELECTION", selection())
    name = "CLEAN_LOCK_POWER_CHANGED"
    raw = uart.encode_payload(name, actuator_values(name))
    edge = store.save_native_actuator_event(name, raw)
    assert store.acquire_work_slot("CLEAN", intent_values()["operationUid"], 1, {"phase": "NATIVE_CLEAN"})
    occupancy = store.get_work_slot()
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DROP TABLE native_clean_intent")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=26")
    store.close()
    upgrade = EdgeStore(path)
    upgrade._open_connection()  # Failure injection only at the SQLite boundary.
    blocked = {"create": (sqlite3.SQLITE_CREATE_TABLE, "native_clean_intent"),
        "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
    if blocked:
        upgrade._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if (action, first) == blocked else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            upgrade.initialize()
        upgrade.close()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 25
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_clean_intent'").fetchone()
        upgrade = EdgeStore(path)
    try:
        upgrade.initialize()
        assert CURRENT_SCHEMA_VERSION >= 30
        assert upgrade._conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert upgrade.get_native_process_receipt(choice_scope("WORK_POSTCLOSE_WEIGHT_READY")) == weight
        assert upgrade.get_native_process_receipt(choice_scope())["saved_payload"] == choice
        assert upgrade.get_native_actuator_event(42, 7)["saved_payload"] == edge
        assert upgrade.get_work_slot() == occupancy and upgrade.list_native_result_report_tasks() == []
        receipt = upgrade.save_native_process_receipt(scope(), INTENTS[0], payload(mcuEventSequence=8))
        upgrade.close()
        upgrade = EdgeStore(path)
        upgrade.initialize()
        assert upgrade.get_native_process_receipt(scope())["saved_payload"] == receipt
        assert upgrade.get_work_slot() == occupancy
    finally:
        upgrade.close()


@pytest.mark.parametrize("name", INTENTS)
@pytest.mark.parametrize("change", ["type", "event", "scope", "body"])
def test_conflicting_intent_cannot_replace_original_or_reuse_its_generation(tmp_path, name, change):
    store = EdgeStore(str(tmp_path / "conflict.db"))
    store.initialize()
    try:
        raw, original_scope = payload(name), scope(name)
        store.save_native_process_receipt(original_scope, name, raw)
        other_name = next(item for item in INTENTS if item != name) if change == "type" else name
        incoming_scope = scope(other_name, **({"commandDigestSha256": "55" * 32} if change == "scope" else {}))
        incoming = payload(other_name, **({"mcuEventSequence": 5} if change in {"type", "event"} else
            {"uptimeMs": 11001} if change == "body" else {}))
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(incoming_scope, other_name, incoming)
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(original_scope, name, raw)
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_receipt(original_scope)
        records = store.list_native_actuator_event_conflicts()
        assert any(row["payload"] == incoming for row in records)
        if change in {"type", "event"}:
            with pytest.raises(ValueError, match="conflict"):
                store.get_native_process_event(other_name, 42, 5)
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("other", ["measurement", "actuator", "selection"])
@pytest.mark.parametrize("intent_first", [True, False])
def test_global_event_number_cannot_alias_other_evidence_in_either_order(tmp_path, other, intent_first):
    from contracts.tests.test_uart_v2_process_measurement import process_values
    from contracts.tests.test_uart_v2_actuator_events import actuator_values
    from hardware.tests.test_native_delivery_selection import save_weight, scope as choice_scope, selection
    store = EdgeStore(str(tmp_path / "global-key.db"))
    store.initialize()
    try:
        save_intent = lambda: store.save_native_process_receipt(scope(), INTENTS[0], payload())
        if other == "measurement":
            raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", process_values("WORK_POSTCLOSE_WEIGHT_READY") | {"mcuEventSequence": 4})
            save_other = lambda: store.save_native_measurement_event("WORK_POSTCLOSE_WEIGHT_READY", raw)
            read_other = lambda: store.get_native_measurement_event(42, 4)
        elif other == "actuator":
            raw = uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", actuator_values("CLEAN_LOCK_POWER_CHANGED", mcuEventSequence=4))
            save_other = lambda: store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", raw)
            read_other = lambda: store.get_native_actuator_event(42, 4)
        else:
            save_weight(store)
            save_other = lambda: store.save_native_process_receipt(choice_scope(), "DELIVERY_SELECTION", selection())
            read_other = lambda: store.get_native_process_event("DELIVERY_SELECTION", 42, 4)
        (save_intent if intent_first else save_other)()
        with pytest.raises(ValueError, match="conflict"):
            (save_other if intent_first else save_intent)()
        with pytest.raises(ValueError, match="conflict"):
            read_other()
        with pytest.raises(ValueError, match="conflict"):
            save_intent()
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("name", INTENTS)
def test_failed_commit_returns_no_saved_reference_and_nested_transaction_is_rejected(tmp_path, name):
    store = EdgeStore(str(tmp_path / "commit.db"))
    store.initialize()
    try:
        with store.transaction(immediate=True):
            with pytest.raises(RuntimeError, match="transaction"):
                store.save_native_process_receipt(scope(name), name, payload(name))
        store._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            store.save_native_process_receipt(scope(name), name, payload(name))
        store._conn.set_authorizer(None)
        assert store.get_native_process_receipt(scope(name)) is None
        assert store.get_native_process_event(name, 42, 4) is None
        assert store.save_native_process_receipt(scope(name), name, payload(name))
    finally:
        store._conn.set_authorizer(None)
        store.close()


@pytest.mark.parametrize("column,value", [("saved_payload", b"x" * 45), ("work_uid", "wrong"),
    ("clean_action_sequence", 2), ("payload", payload(uptimeMs=12000))])
def test_corrupted_custody_is_not_returned_or_acknowledged(tmp_path, column, value):
    store = EdgeStore(str(tmp_path / "corrupt.db"))
    store.initialize()
    try:
        store.save_native_process_receipt(scope(), INTENTS[0], payload())
        with store.transaction(immediate=True) as conn:
            conn.execute(f"UPDATE native_clean_intent SET {column}=?", (value,))
        with pytest.raises(ValueError, match="corrupt"):
            store.get_native_process_receipt(scope())
        with pytest.raises(ValueError, match="corrupt"):
            store.save_native_process_receipt(scope(), INTENTS[0], payload())
    finally:
        store.close()


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
@pytest.mark.parametrize("name", INTENTS)
def test_process_exit_only_retains_committed_intent_without_releasing_business(tmp_path, point, name):
    import subprocess
    import sys
    from pathlib import Path
    from hardware.tests.sqlite_failure_evidence import capture_failure_evidence
    path = tmp_path / "exited-intent.db"
    store = EdgeStore(str(path))
    store.initialize()
    assert store.acquire_work_slot("CLEAN", intent_values()["operationUid"], 1, {"phase": "NATIVE_CLEAN"})
    occupancy = store.get_work_slot()
    store.close()
    script = """
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
if sys.argv[2] == 'before_commit':
    store._conn.set_trace_callback(lambda sql: os._exit(71) if sql == 'COMMIT' else None)
store.save_native_process_receipt(bytes.fromhex(sys.argv[4]), sys.argv[3], bytes.fromhex(sys.argv[5]))
os._exit(72)
"""
    run = subprocess.run([sys.executable, "-c", script, str(path), point, name, scope(name).hex(), payload(name).hex()],
        cwd=Path(__file__).resolve().parents[1], timeout=30, capture_output=True, text=True)
    assert run.returncode == (71 if point == "before_commit" else 72), run.stdout + run.stderr
    recovered = EdgeStore(str(path))
    try:
        recovered.initialize()
    except sqlite3.Error as exc:
        evidence = capture_failure_evidence(path, exc, child_returncode=run.returncode)
        exc.add_note(str(evidence))
        raise
    try:
        row = recovered.get_native_process_receipt(scope(name))
        assert (row is not None) == (point == "after_commit")
        if row:
            assert row["payload"] == payload(name)
        assert recovered.get_work_slot() == occupancy and recovered.list_native_result_report_tasks() == []
    finally:
        recovered.close()

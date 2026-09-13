"""Selection custody requires a committed, exactly scoped post-close weight."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from contracts.tests.test_uart_v2_delivery_selection import selection_values
from contracts.tests.test_uart_v2_process_measurement import process_values
from contracts.tests.test_uart_v2_process_handoff import process_scope


def scope(name="DELIVERY_SELECTION", **changes):
    values = process_scope() | {"eventMessageType": name} | changes
    return uart.encode_payload("QUERY_PROCESS_EVENT", values | {"queryId": 1})[8:]


def selection(**changes):
    return uart.encode_payload("DELIVERY_SELECTION", selection_values() | changes)


def save_weight(store, **changes):
    values = process_values("WORK_POSTCLOSE_WEIGHT_READY") | changes
    raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", values)
    return store.save_native_process_receipt(scope("WORK_POSTCLOSE_WEIGHT_READY"), "WORK_POSTCLOSE_WEIGHT_READY", raw)


def test_saved_weight_and_choice_remain_distinct_and_are_readable_after_restart(tmp_path):
    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        save_weight(store)
        receipt = store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        assert uart.decode_payload("PROCESS_EVENT_SAVED", receipt)["eventMessageType"] == "DELIVERY_SELECTION"
        assert store.get_native_process_receipt(scope())["payload"] == selection()
        assert store.get_native_process_event("DELIVERY_SELECTION", 42, 4)["payload"] == selection()
        assert store.get_native_measurement_event(42, 4) is None  # A choice is NOT a new measurement.
        store.close()
        store = EdgeStore(path)
        store.initialize()
        assert store.get_native_process_receipt(scope())["saved_payload"] == receipt
        assert store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection()) == receipt
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("source", ["absent", "unscoped", "unavailable"])
def test_choice_requires_a_committed_scoped_available_weight_not_just_raw_data(tmp_path, source):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        if source == "unscoped":
            store.save_native_measurement_event("WORK_POSTCLOSE_WEIGHT_READY",
                uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", process_values("WORK_POSTCLOSE_WEIGHT_READY")))
        elif source == "unavailable":
            save_weight(store, measurementKind="UNAVAILABLE", faultCode="WEIGHT_TIMEOUT")
        with pytest.raises(ValueError, match="weight"):
            store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        assert store.get_native_process_receipt(scope()) is None
    finally:
        store.close()


@pytest.mark.parametrize("changes,scope_changes", [
    ({"postCloseMeasurementUid": "44444444-4444-4444-8444-444444444444"}, {}),
    ({"uptimeMs": 9999}, {}), ({"roundIndex": 2}, {"stepSequence": 2}),
    ({"configVersion": 8}, {"configVersion": 8}),
    ({}, {"commandDigestSha256": "55" * 32}),
])
def test_choice_must_match_saved_measurement_and_exact_original_scope(tmp_path, changes, scope_changes):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        save_weight(store)
        with pytest.raises(ValueError, match="weight"):
            store.save_native_process_receipt(scope(**scope_changes), "DELIVERY_SELECTION", selection(**changes))
        assert store.get_native_process_event("DELIVERY_SELECTION", 42, 4) is None
    finally:
        store.close()


def test_another_event_number_cannot_create_a_second_choice_for_the_same_round(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        save_weight(store)
        store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection(mcuEventSequence=5))
        for sequence in (4, 5):
            with pytest.raises(ValueError, match="conflict"):
                store.get_native_process_event("DELIVERY_SELECTION", 42, sequence)
        assert len(store.list_native_actuator_event_conflicts()) == 2
    finally:
        store.close()


@pytest.mark.parametrize("other,choice_first", [(kind, first) for kind in ("measurement", "actuator") for first in (True, False)])
def test_event_numbers_cannot_be_reused_across_selection_measurement_or_actuator_records(tmp_path, other, choice_first):
    from contracts.tests.test_uart_v2_actuator_events import actuator_values

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        save_weight(store)
        choose = lambda: store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        if other == "measurement":
            values = process_values("WORK_POSTCLOSE_WEIGHT_READY") | {"mcuEventSequence": 4, "measurementUid": "44444444-4444-4444-8444-444444444444"}
            save_other = lambda: store.save_native_measurement_event("WORK_POSTCLOSE_WEIGHT_READY", uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", values))
            read_other = lambda: store.get_native_measurement_event(42, 4)
        else:
            values = actuator_values("CLEAN_LOCK_POWER_CHANGED") | {"mcuEventSequence": 4}
            save_other = lambda: store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", values))
            read_other = lambda: store.get_native_actuator_event(42, 4)
        (choose if choice_first else save_other)()
        with pytest.raises(ValueError, match="conflict"):
            (save_other if choice_first else choose)()
        with pytest.raises(ValueError, match="conflict"):
            read_other()
        with pytest.raises(ValueError, match="conflict"):
            choose()
    finally:
        store.close()


def test_failed_commit_returns_no_receipt_and_rolls_back_choice_without_losing_saved_weight(tmp_path):
    import sqlite3

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        save_weight(store)
        store._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        store._conn.set_authorizer(None)
        assert store.get_native_process_receipt(scope()) is None
        assert store.get_native_process_receipt(scope("WORK_POSTCLOSE_WEIGHT_READY"))
        assert store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_schema23_upgrade_preserves_prior_weight_and_actuator_custody(tmp_path):
    from contracts.tests.test_uart_v2_actuator_events import actuator_values
    from edge_store import CURRENT_SCHEMA_VERSION

    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    save_weight(store)
    name = "CLEAN_LOCK_POWER_CHANGED"
    raw = uart.encode_payload(name, actuator_values(name))
    receipt = store.save_native_actuator_event(name, raw)
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DROP TABLE native_clean_intent")
        conn.execute("DROP TABLE native_delivery_selection")
        conn.execute("DELETE FROM schema_version WHERE version>=24")
    store.close()
    upgraded = EdgeStore(path)
    upgraded.initialize()
    try:
        assert upgraded.get_native_process_receipt(scope("WORK_POSTCLOSE_WEIGHT_READY"))
        assert upgraded.get_native_actuator_event(42, 7)["saved_payload"] == receipt
        assert upgraded.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        assert CURRENT_SCHEMA_VERSION >= 30
        assert upgraded._conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    finally:
        upgraded.close()


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_forced_process_exit_preserves_only_committed_choice_and_never_loses_weight(tmp_path, point):
    import subprocess
    import sys
    from pathlib import Path

    path = str(tmp_path / "edge.db")
    store = EdgeStore(path)
    store.initialize()
    save_weight(store)
    store.close()
    code = """
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
if sys.argv[2] == 'before_commit':
    store._conn.set_trace_callback(lambda sql: os._exit(51) if sql.strip().upper() == 'COMMIT' else None)
store.save_native_process_receipt(bytes.fromhex(sys.argv[3]), 'DELIVERY_SELECTION', bytes.fromhex(sys.argv[4]))
os._exit(52)
"""
    result = subprocess.run([sys.executable, "-c", code, path, point, scope().hex(), selection().hex()],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert result.returncode == (51 if point == "before_commit" else 52), result.stdout + result.stderr
    recovered = EdgeStore(path)
    recovered.initialize()
    try:
        assert recovered.get_native_process_receipt(scope("WORK_POSTCLOSE_WEIGHT_READY"))
        record = recovered.get_native_process_receipt(scope())
        assert (record is not None) == (point == "after_commit")
        assert recovered.list_native_result_report_tasks() == []
    finally:
        recovered.close()


def test_changed_choice_poisoning_is_sticky_and_keeps_the_original_and_conflicting_bytes(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        save_weight(store)
        store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
        changed = selection(selection="CONTINUE")
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", changed)
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_receipt(scope())
        assert any(row["payload"] == changed for row in store.list_native_actuator_event_conflicts())
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(), "DELIVERY_SELECTION", selection())
    finally:
        store.close()

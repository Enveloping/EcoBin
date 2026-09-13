"""Human completion has separate durable custody, bound to original finish/weight."""
import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from contracts.tests.test_uart_v2_clean_confirmation import NAME, confirmation_values, final_values
from contracts.tests.test_uart_v2_clean_intent import intent_scope, intent_values


def scope(name=NAME, **changes):
    return uart.encode_payload("QUERY_PROCESS_EVENT", intent_scope(name) | {"queryId": 1} | changes)[8:]


def payload(**changes):
    return uart.encode_payload(NAME, confirmation_values() | changes)


def save_candidate(store):
    finish = intent_values() | {"mcuEventSequence": 3}
    store.save_native_process_receipt(scope("CLEAN_FINISH_REQUESTED"), "CLEAN_FINISH_REQUESTED", uart.encode_payload("CLEAN_FINISH_REQUESTED", finish))
    final = final_values()
    store.save_native_process_receipt(scope("CLEAN_FINAL_WEIGHT_READY"), "CLEAN_FINAL_WEIGHT_READY", uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", final))


def test_confirmation_is_distinct_from_finish_and_weight_and_survives_restart_without_business_effect(tmp_path):
    path = str(tmp_path / "confirmation.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        save_candidate(store)
        assert store.acquire_work_slot("CLEAN", confirmation_values()["operationUid"], 1, {"phase": "CLEAN_FINALIZING"})
        occupied = store.get_work_slot()
        receipt = store.save_native_process_receipt(scope(), NAME, payload())
        assert store.get_native_process_receipt(scope())["payload"] == payload()
        assert store.get_native_process_event(NAME, 42, 5)["saved_payload"] == receipt
        assert store.get_native_measurement_event(42, 5) is None
        assert store.get_native_process_receipt(scope("CLEAN_FINISH_REQUESTED"))["event_sequence"] == 3
        store.close()
        store = EdgeStore(path)
        store.initialize()
        assert store.save_native_process_receipt(scope(), NAME, payload()) == receipt
        assert store.get_work_slot() == occupied and store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("other", ["measurement", "actuator", "intent", "selection"])
@pytest.mark.parametrize("confirmation_first", [True, False])
def test_confirmation_cannot_share_global_event_identity_with_any_other_record(tmp_path, other, confirmation_first):
    from contracts.tests.test_uart_v2_actuator_events import actuator_values
    from contracts.tests.test_uart_v2_process_measurement import process_values
    from contracts.tests.test_uart_v2_delivery_selection import selection_values
    from hardware.tests.test_native_delivery_selection import scope as delivery_scope
    store = EdgeStore(str(tmp_path / "collision.db"))
    store.initialize()
    try:
        save_candidate(store)
        save_confirmation = lambda: store.save_native_process_receipt(scope(), NAME, payload())
        if other == "measurement":
            raw = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", process_values("WORK_POSTCLOSE_WEIGHT_READY") | {"mcuEventSequence": 5,
                "measurementUid": "99999999-9999-4999-8999-999999999999"})
            save_other = lambda: store.save_native_measurement_event("WORK_POSTCLOSE_WEIGHT_READY", raw)
        elif other == "actuator":
            raw = uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", actuator_values("CLEAN_LOCK_POWER_CHANGED", mcuEventSequence=5))
            save_other = lambda: store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", raw)
        elif other == "intent":
            raw = uart.encode_payload("CLEAN_UNLOCK_REQUESTED", intent_values() | {"mcuEventSequence": 5, "cleanActionSequence": 2})
            save_other = lambda: store.save_native_process_receipt(scope("CLEAN_UNLOCK_REQUESTED", stepSequence=2), "CLEAN_UNLOCK_REQUESTED", raw)
        else:
            uid = "99999999-9999-4999-8999-999999999999"
            weight = uart.encode_payload("WORK_POSTCLOSE_WEIGHT_READY", process_values("WORK_POSTCLOSE_WEIGHT_READY") | {"mcuEventSequence": 1, "measurementUid": uid})
            store.save_native_process_receipt(delivery_scope("WORK_POSTCLOSE_WEIGHT_READY"), "WORK_POSTCLOSE_WEIGHT_READY", weight)
            raw = uart.encode_payload("DELIVERY_SELECTION", selection_values() | {"mcuEventSequence": 5, "postCloseMeasurementUid": uid})
            save_other = lambda: store.save_native_process_receipt(delivery_scope(), "DELIVERY_SELECTION", raw)
        (save_confirmation if confirmation_first else save_other)()
        with pytest.raises(ValueError, match="conflict"):
            (save_other if confirmation_first else save_confirmation)()
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_event(NAME, 42, 5)
        with pytest.raises(ValueError, match="conflict"):
            save_confirmation()
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("prior", ["absent", "only_finish", "only_weight", "unscoped_weight"])
def test_confirmation_requires_both_precisely_saved_predecessors(tmp_path, prior):
    store = EdgeStore(str(tmp_path / "missing-predecessor.db"))
    store.initialize()
    try:
        if prior in {"only_finish", "unscoped_weight"}:
            store.save_native_process_receipt(scope("CLEAN_FINISH_REQUESTED"), "CLEAN_FINISH_REQUESTED",
                uart.encode_payload("CLEAN_FINISH_REQUESTED", intent_values() | {"mcuEventSequence": 3}))
        if prior == "only_weight":
            store.save_native_process_receipt(scope("CLEAN_FINAL_WEIGHT_READY"), "CLEAN_FINAL_WEIGHT_READY",
                uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", final_values()))
        elif prior == "unscoped_weight":
            store.save_native_measurement_event("CLEAN_FINAL_WEIGHT_READY", uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", final_values()))
        with pytest.raises(ValueError, match="saved finish and final weight"):
            store.save_native_process_receipt(scope(), NAME, payload())
        assert store.get_native_process_event(NAME, 42, 5) is None
    finally:
        store.close()


@pytest.mark.parametrize("changes,scope_changes", [
    ({"finalMeasurementUid": "99999999-9999-4999-8999-999999999999"}, {}),
    ({"uptimeMs": 12019}, {}), ({"mcuEventSequence": 2}, {}),
    ({"cleanActionSequence": 2}, {"stepSequence": 2}), ({"configVersion": 8}, {"configVersion": 8}),
    ({}, {"commandDigestSha256": "55" * 32}),
])
def test_confirmation_must_match_actual_candidate_order_and_full_scope(tmp_path, changes, scope_changes):
    store = EdgeStore(str(tmp_path / "wrong-candidate.db"))
    store.initialize()
    try:
        save_candidate(store)
        with pytest.raises(ValueError):
            store.save_native_process_receipt(scope(**scope_changes), NAME, payload(**changes))
        assert store.get_native_process_receipt(scope()) is None
    finally:
        store.close()


def test_terminal_unavailable_final_is_preserved_not_relabelled_by_human_confirmation(tmp_path):
    store = EdgeStore(str(tmp_path / "unavailable.db"))
    store.initialize()
    try:
        store.save_native_process_receipt(scope("CLEAN_FINISH_REQUESTED"), "CLEAN_FINISH_REQUESTED",
            uart.encode_payload("CLEAN_FINISH_REQUESTED", intent_values() | {"mcuEventSequence": 3}))
        final = final_values() | {"measurementKind": "UNAVAILABLE", "reportedWeightGrams": 0, "measurementElapsedMs": 5000,
            "sampleCount": 0, "sampleSpanGrams": 0, "uptimeMs": 16000, "faultCode": "WEIGHT_TIMEOUT"}
        raw = uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", final)
        store.save_native_process_receipt(scope("CLEAN_FINAL_WEIGHT_READY"), "CLEAN_FINAL_WEIGHT_READY", raw)
        assert store.save_native_process_receipt(scope(), NAME, payload(uptimeMs=17000))
        assert store.get_native_process_receipt(scope("CLEAN_FINAL_WEIGHT_READY"))["payload"] == raw
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["none", "create", "version"])
def test_schema26_migration_preserves_candidate_and_work_and_is_atomic(tmp_path, failure):
    import sqlite3
    from edge_store import CURRENT_SCHEMA_VERSION
    path = str(tmp_path / "upgrade.db")
    store = EdgeStore(path)
    store.initialize()
    save_candidate(store)
    before = {name: store.get_native_process_receipt(scope(name)) for name in ("CLEAN_FINISH_REQUESTED", "CLEAN_FINAL_WEIGHT_READY")}
    assert store.acquire_work_slot("CLEAN", confirmation_values()["operationUid"], 1, {"phase": "CLEAN_RESULT_CONFIRMATION"})
    occupied = store.get_work_slot()
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DELETE FROM schema_version WHERE version>=27")
    store.close()
    upgrade = EdgeStore(path)
    upgrade._open_connection()
    denied = {"create": (sqlite3.SQLITE_CREATE_TABLE, "native_clean_confirmation"),
        "version": (sqlite3.SQLITE_INSERT, "schema_version")}.get(failure)
    if denied:
        upgrade._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if (action, first) == denied else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            upgrade.initialize()
        upgrade.close()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 26
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_clean_confirmation'").fetchone()
        upgrade = EdgeStore(path)
    try:
        upgrade.initialize()
        assert CURRENT_SCHEMA_VERSION >= 30
        assert upgrade._conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        for name, row in before.items():
            assert upgrade.get_native_process_receipt(scope(name)) == row
        assert upgrade.get_work_slot() == occupied
        assert upgrade.save_native_process_receipt(scope(), NAME, payload())
        assert upgrade.list_native_result_report_tasks() == []
    finally:
        upgrade.close()


@pytest.mark.parametrize("change", ["event", "scope", "body", "step"])
def test_conflicting_confirmation_preserves_original_and_poisoned_keys_after_restart(tmp_path, change):
    path = str(tmp_path / "conflicting-confirmation.db")
    store = EdgeStore(path)
    store.initialize()
    save_candidate(store)
    store.save_native_process_receipt(scope(), NAME, payload())
    incoming_scope = scope(**({"commandDigestSha256": "55" * 32} if change == "scope" else
        {"stepSequence": 2} if change == "step" else {}))
    incoming = payload(**({"mcuEventSequence": 6} if change == "event" else
        {"uptimeMs": 13001} if change == "body" else {"cleanActionSequence": 2} if change == "step" else {}))
    try:
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(incoming_scope, NAME, incoming)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_receipt(scope())
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_process_receipt(scope(), NAME, payload())
        with pytest.raises(ValueError, match="conflict"):
            store.get_native_process_event(NAME, 42, 6 if change == "event" else 5)
        assert any(row["payload"] == incoming for row in store.list_native_actuator_event_conflicts())
        # Forensic storage check: conflict blocks ordinary reads but never
        # replaces the prior evidence with the incoming contradictory record.
        row = store._conn.execute("SELECT payload FROM native_clean_confirmation").fetchone()
        assert bytes(row[0]) == payload()
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


@pytest.mark.parametrize("column,value", [("saved_payload", b"x" * 45), ("work_uid", "wrong"),
    ("clean_action_sequence", 2), ("finish_event_sequence", 2), ("final_event_sequence", 3),
    ("payload", payload(uptimeMs=13001))])
def test_corrupt_confirmation_cannot_be_read_or_acknowledged(tmp_path, column, value):
    store = EdgeStore(str(tmp_path / "corrupt-confirmation.db"))
    store.initialize()
    try:
        save_candidate(store)
        store.save_native_process_receipt(scope(), NAME, payload())
        with store.transaction(immediate=True) as conn:
            conn.execute(f"UPDATE native_clean_confirmation SET {column}=?", (value,))
        with pytest.raises(ValueError, match="corrupt"):
            store.get_native_process_receipt(scope())
        with pytest.raises(ValueError, match="corrupt"):
            store.save_native_process_receipt(scope(), NAME, payload())
    finally:
        store.close()


def test_corrupt_final_dependency_invalidates_confirmation_receipt(tmp_path):
    store = EdgeStore(str(tmp_path / "corrupt-final.db"))
    store.initialize()
    try:
        save_candidate(store)
        store.save_native_process_receipt(scope(), NAME, payload())
        with store.transaction(immediate=True) as conn:
            conn.execute("UPDATE native_clean_intent SET saved_payload=?", (b"x" * 45,))
        with pytest.raises(ValueError, match="corrupt"):
            store.get_native_process_receipt(scope())
        with pytest.raises(ValueError, match="corrupt"):
            store.save_native_process_receipt(scope(), NAME, payload())
    finally:
        store.close()


def test_confirmation_returns_no_receipt_before_standalone_commit(tmp_path):
    import sqlite3
    store = EdgeStore(str(tmp_path / "commit.db"))
    store.initialize()
    try:
        save_candidate(store)
        with store.transaction(immediate=True):
            with pytest.raises(RuntimeError, match="transaction"):
                store.save_native_process_receipt(scope(), NAME, payload())
        store._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            store.save_native_process_receipt(scope(), NAME, payload())
        store._conn.set_authorizer(None)
        assert store.get_native_process_receipt(scope()) is None
        assert store.save_native_process_receipt(scope(), NAME, payload())
        assert store.list_native_result_report_tasks() == []
    finally:
        store._conn.set_authorizer(None)
        store.close()


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_process_exit_only_retains_committed_human_fact_and_keeps_original_work(tmp_path, point):
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    from hardware.tests.sqlite_failure_evidence import capture_failure_evidence
    path = tmp_path / "exited-confirmation.db"
    store = EdgeStore(str(path))
    store.initialize()
    save_candidate(store)
    assert store.acquire_work_slot("CLEAN", confirmation_values()["operationUid"], 1, {"phase": "CLEAN_FINALIZING"})
    occupied = store.get_work_slot()
    store.close()
    script = """
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store.initialize()
if sys.argv[2] == 'before_commit':
    store._conn.set_trace_callback(lambda sql: os._exit(71) if sql == 'COMMIT' else None)
store.save_native_process_receipt(bytes.fromhex(sys.argv[3]), 'CLEAN_COMPLETION_CONFIRMED', bytes.fromhex(sys.argv[4]))
os._exit(72)
"""
    run = subprocess.run([sys.executable, "-c", script, str(path), point, scope().hex(), payload().hex()],
        cwd=Path(__file__).resolve().parents[1], timeout=30, capture_output=True, text=True)
    assert run.returncode == (71 if point == "before_commit" else 72), run.stdout + run.stderr
    recovered = EdgeStore(str(path))
    try:
        recovered.initialize()
    except sqlite3.Error as exc:
        exc.add_note(str(capture_failure_evidence(path, exc, child_returncode=run.returncode)))
        raise
    try:
        row = recovered.get_native_process_receipt(scope())
        assert (row is not None) == (point == "after_commit")
        if row:
            assert row["payload"] == payload()
        assert recovered.get_native_process_receipt(scope("CLEAN_FINISH_REQUESTED")) is not None
        assert recovered.get_native_process_receipt(scope("CLEAN_FINAL_WEIGHT_READY")) is not None
        assert recovered.get_work_slot() == occupied and recovered.list_native_result_report_tasks() == []
    finally:
        recovered.close()

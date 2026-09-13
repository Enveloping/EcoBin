"""Schema 24 -> 25: retain exact old custody while admitting 64-byte local facts."""
import sqlite3

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
from contracts.tests.test_uart_v2_actuator_events import actuator_values


def old_store(path):
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("DELIVERY", "22222222-2222-4222-8222-222222222222", 1, {"phase": "NATIVE_DELIVERY"})
    name = "DELIVERY_DOOR_COMMAND_RESULT"
    raw = uart.encode_payload(name, actuator_values(name))
    saved = store.save_native_actuator_event(name, raw)
    original = store.get_native_actuator_event(42, 7)
    # Reconstruct the actual prior payload constraint, not merely its version number.
    with store.transaction(immediate=True) as conn:
        conn.execute("DROP TABLE native_clean_confirmation")
        conn.execute("DROP TABLE native_clean_intent")
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='native_actuator_event'").fetchone()[0]
        assert "BETWEEN 1 AND 64" in sql
        conn.execute(sql.replace("native_actuator_event", "native_actuator_event_v24_fixture", 1)
            .replace("BETWEEN 1 AND 64", "BETWEEN 1 AND 60"))
        conn.execute("INSERT INTO native_actuator_event_v24_fixture SELECT * FROM native_actuator_event")
        conn.execute("DROP TABLE native_actuator_event")
        conn.execute("ALTER TABLE native_actuator_event_v24_fixture RENAME TO native_actuator_event")
        conn.execute("DROP TABLE native_action_confirmation")
        conn.execute("DROP TABLE native_action_binding")
        conn.execute("DELETE FROM schema_version WHERE version>=25")
    occupancy = store.get_work_slot()
    store.close()
    return original, occupancy, saved


@pytest.mark.parametrize("failure", ["none", "copy", "drop", "rename"])
def test_schema_upgrade_preserves_old_evidence_and_rolls_back_every_partial_swap(tmp_path, failure):
    path = str(tmp_path / "edge.db")
    original, occupancy, saved = old_store(path)
    upgrade = EdgeStore(path)
    upgrade._open_connection()  # Inject SQLite's authorizer at the storage boundary.
    blocked = {
        "copy": (sqlite3.SQLITE_INSERT, "native_actuator_event_v25"),
        "drop": (sqlite3.SQLITE_DROP_TABLE, "native_actuator_event"),
        "rename": (sqlite3.SQLITE_ALTER_TABLE, "main"),
    }.get(failure)
    if blocked:
        upgrade._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if (action, first) == blocked else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            upgrade.initialize()
        upgrade.close()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 24
            assert "BETWEEN 1 AND 60" in conn.execute("SELECT sql FROM sqlite_master WHERE name='native_actuator_event'").fetchone()[0]
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='native_actuator_event_v25'").fetchone()
            assert conn.execute("SELECT payload,saved_payload FROM native_actuator_event").fetchone() == (original["payload"], saved)
        upgrade = EdgeStore(path)
    try:
        upgrade.initialize()
        assert CURRENT_SCHEMA_VERSION == 30
        assert upgrade._conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert upgrade.get_native_actuator_event(42, 7) == original
        assert upgrade.get_work_slot() == occupancy
        name = "DELIVERY_LOCAL_DOOR_RESULT"
        values = actuator_values("DELIVERY_DOOR_COMMAND_RESULT", roundIndex=2,
            mcuEventSequence=9, selectionEventSequence=8)
        raw = uart.encode_payload(name, values)
        assert len(raw) == 64
        receipt = upgrade.save_native_actuator_event(name, raw)
        upgrade.close()
        upgrade = EdgeStore(path)
        upgrade.initialize()
        assert upgrade.get_native_actuator_event(42, 7) == original
        assert upgrade.get_native_actuator_event(42, 9)["payload"] == raw
        assert upgrade.save_native_actuator_event(name, raw) == receipt
        assert upgrade.get_work_slot() == occupancy and upgrade.list_native_result_report_tasks() == []
    finally:
        upgrade.close()


@pytest.mark.parametrize("point", ["before_rename", "before_commit", "after_commit"])
def test_process_exit_during_upgrade_preserves_original_evidence(tmp_path, point):
    import os
    from pathlib import Path
    import subprocess
    import sys
    path = str(tmp_path / "killed-upgrade.db")
    original, occupancy, _ = old_store(path)
    script = """
import os, sys
from edge_store import EdgeStore
store = EdgeStore(sys.argv[1])
store._open_connection()
point = sys.argv[2]
def trace(sql):
    if ((point == 'before_rename' and sql.startswith('ALTER TABLE native_actuator_event_v25'))
        or (point == 'before_commit' and sql == 'COMMIT')):
        os._exit(81)
store._conn.set_trace_callback(trace)
store.initialize()
os._exit(82)
"""
    run = subprocess.run([sys.executable, "-c", script, path, point], timeout=30,
        env=os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}, capture_output=True, text=True)
    assert run.returncode == (82 if point == "after_commit" else 81), run.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == (CURRENT_SCHEMA_VERSION if point == "after_commit" else 24)
    recovered = EdgeStore(path)
    try:
        recovered.initialize()
        assert recovered.get_native_actuator_event(42, 7) == original
        assert recovered.get_work_slot() == occupancy and recovered.list_native_result_report_tasks() == []
    finally:
        recovered.close()

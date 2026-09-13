"""Real v38 custody upgrade and v39 corruption boundaries; no hardware motion."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from hardware.tests.test_native_recovery_close_confirmation import closed
from native_delivery_recovery_close import NativeRecoveryCloseRetirement, NativeRecoveryCloseReconciler


OLD_BINDING_SCHEMA = """CREATE TABLE native_delivery_recovery_close_v38 (
    action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_mcu_command(command_uid),
    issue_uid TEXT NOT NULL UNIQUE REFERENCES native_delivery_issue(issue_uid),
    binding_json TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL CHECK(length(binding_sha256)=64)
)"""


@pytest.fixture
def retired(archived):
    case, wire, boot, issue, _ = archived
    binding = coordinator(case, wire, boot).prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    proof = NativeRecoveryCloseRetirement(case.store, case.safety).reconcile(uid)
    return case, wire, issue, binding, proof


def restore_v38(path):
    """Fixture-only old schema, preserving every original proof byte and FK."""
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        restore_old_binding(conn)
        conn.execute("DROP INDEX native_mcu_one_pending_command")
        conn.execute("ALTER TABLE native_mcu_command DROP COLUMN dispatch_retired")
        conn.execute("""CREATE UNIQUE INDEX native_mcu_one_pending_command
            ON native_mcu_command ((1)) WHERE decision_outcome IS NULL AND boot_retired=0""")
        conn.execute("DELETE FROM schema_version WHERE version>38")
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def restore_old_binding(conn):
    conn.execute(OLD_BINDING_SCHEMA)
    conn.execute("""INSERT INTO native_delivery_recovery_close_v38
        SELECT action_uid,issue_uid,binding_json,binding_sha256 FROM native_delivery_recovery_close""")
    conn.execute("DROP TABLE native_delivery_recovery_close")
    conn.execute("ALTER TABLE native_delivery_recovery_close_v38 RENAME TO native_delivery_recovery_close")


def snapshot(path):
    """Inspect the storage boundary including old schema and unmodified bytes."""
    with sqlite3.connect(path) as conn:
        schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = [row[1] for row in schema if row[0] == "table"]
        return schema, {name: conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in tables}


def test_v38_upgrade_preserves_original_retirement_and_adds_only_dispatch_fence(retired):
    case, wire, issue, binding, proof = retired
    uid = binding["action"].action_uid
    command = case.store.get_native_command(uid)
    ledger = case.safety.get_physical_action(uid)
    sent = list(wire.sent)
    path = case.store.db_path
    case.store.close()
    restore_v38(path)
    case.store = EdgeStore(path)
    case.store.initialize()
    assert case.store._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    columns = {row["name"] for row in case.store._conn.execute("PRAGMA table_info(native_delivery_recovery_close)")}
    assert columns == {"action_uid", "issue_uid", "binding_json", "binding_sha256", "predecessor_action_uid"}
    assert case.store._conn.execute("SELECT predecessor_action_uid FROM native_delivery_recovery_close WHERE action_uid=?",
        (uid,)).fetchone()[0] == case.action.action_uid
    assert case.store.get_native_delivery_recovery_close(uid) == binding
    assert case.store.get_native_recovery_close_retirement(uid) == proof
    assert case.store.get_native_command(uid) == command
    assert command["dispatch_retired"] == 1
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert case.safety.get_physical_action(uid) == ledger
    assert ledger["receiptUid"] == binding["action"].receipt_uid
    assert not case.store._conn.execute("PRAGMA foreign_key_check").fetchall()
    assert wire.sent == sent


@pytest.mark.parametrize("damage", ["missing_marker", "missing_proof", "false_marker", "wrong_command", "pending_proof",
    "missing_parent_column", "wrong_parent"])
def test_current_v39_rejects_dispatch_retirement_corruption_without_repair(retired, damage):
    case, wire, issue, binding, proof = retired
    uid = binding["action"].action_uid
    path = case.store.db_path
    ledger = case.safety.get_physical_action(uid)
    sent = list(wire.sent)
    case.store.close()
    with sqlite3.connect(path) as conn:
        if damage == "missing_marker":
            conn.execute("DROP INDEX native_mcu_one_pending_command")
            conn.execute("ALTER TABLE native_mcu_command DROP COLUMN dispatch_retired")
            conn.execute("""CREATE UNIQUE INDEX native_mcu_one_pending_command
                ON native_mcu_command ((1)) WHERE decision_outcome IS NULL AND boot_retired=0""")
        elif damage == "missing_proof":
            conn.execute("DELETE FROM native_recovery_close_retirement WHERE action_uid=?", (uid,))
        elif damage == "false_marker":
            conn.execute("UPDATE native_mcu_command SET dispatch_retired=0 WHERE command_uid=?", (uid,))
        elif damage == "wrong_command":
            conn.execute("UPDATE native_mcu_command SET dispatch_retired=1 WHERE command_uid=?", (case.action.action_uid,))
        elif damage == "pending_proof":
            conn.execute("UPDATE native_recovery_close_retirement SET state='PENDING' WHERE action_uid=?", (uid,))
        elif damage == "missing_parent_column":
            restore_old_binding(conn)
        else:
            conn.execute("UPDATE native_delivery_recovery_close SET predecessor_action_uid=? WHERE action_uid=?", (uid, uid))
    before = snapshot(path)
    case.store = EdgeStore(path)
    with pytest.raises(ValueError, match="retirement|marker|ancestry|predecessor"):
        case.store.initialize()
    case.store.close()
    assert snapshot(path) == before
    assert case.safety.get_physical_action(uid) == ledger
    assert wire.sent == sent


@pytest.mark.parametrize("denied", [
    (sqlite3.SQLITE_ALTER_TABLE, "main", "native_mcu_command"),
    (sqlite3.SQLITE_UPDATE, "native_mcu_command", "dispatch_retired"),
    (sqlite3.SQLITE_INSERT, "schema_version", None),
    (sqlite3.SQLITE_DROP_TABLE, "native_delivery_recovery_close", None),
    (sqlite3.SQLITE_CREATE_TABLE, "native_delivery_recovery_close", None),
    (sqlite3.SQLITE_INSERT, "native_delivery_recovery_close", None),
    (sqlite3.SQLITE_CREATE_TABLE, "native_recovery_close_retirement", None),
    (sqlite3.SQLITE_INSERT, "native_recovery_close_retirement", None),
])
def test_v38_upgrade_failure_rolls_back_schema_proofs_markers_and_version(retired, monkeypatch, denied):
    case, wire, _, binding, proof = retired
    uid = binding["action"].action_uid
    path = case.store.db_path
    ledger = case.safety.get_physical_action(uid)
    sent = list(wire.sent)
    case.store.close()
    restore_v38(path)
    before = snapshot(path)
    real_connect = sqlite3.connect
    denied_seen = []

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        def authorize(op, name, column, *_):
            if (op, name, column) == denied:
                denied_seen.append(denied)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        return conn

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", connect)
        case.store = EdgeStore(path)
        with pytest.raises(sqlite3.DatabaseError):
            case.store.initialize()
        case.store.close()
    assert denied_seen
    assert snapshot(path) == before
    assert case.safety.get_physical_action(uid) == ledger
    assert wire.sent == sent
    case.store = EdgeStore(path)
    case.store.initialize()
    assert case.store.get_native_recovery_close_retirement(uid) == proof
    assert case.store.get_native_command(uid)["dispatch_retired"] == 1


def test_concurrent_v38_openers_observe_one_complete_migration(retired):
    case, wire, issue, binding, proof = retired
    uid = binding["action"].action_uid
    path = case.store.db_path
    ledger = case.safety.get_physical_action(uid)
    sent = list(wire.sent)
    case.store.close()
    restore_v38(path)
    barrier = Barrier(2)

    def reopen(_):
        store = EdgeStore(path)
        try:
            barrier.wait(timeout=10)
            store.initialize()
            return (store.get_native_delivery_recovery_close(uid), store.get_native_recovery_close_retirement(uid),
                store.get_native_command(uid)["dispatch_retired"], store.get_native_delivery_issue(case.permit.work_uid),
                store.get_work_slot())
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reopen, range(2)))
    assert results == [(binding, proof, 1, issue, case.occupancy)] * 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=39").fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert case.safety.get_physical_action(uid) == ledger
    assert wire.sent == sent


def test_v38_upgrade_preserves_actual_output_confirmation_without_retiring_it(closed):
    case, wire, _, issue, binding, _ = closed
    uid = binding["action"].action_uid
    proof = NativeRecoveryCloseReconciler(case.store, case.safety).reconcile(uid)
    command = case.store.get_native_command(uid)
    ledger = case.safety.get_physical_action(uid)
    sent = list(wire.sent)
    path = case.store.db_path
    case.store.close()
    restore_v38(path)
    case.store = EdgeStore(path)
    case.store.initialize()
    assert case.store.get_native_delivery_recovery_close(uid) == binding
    assert case.store.get_native_recovery_close_confirmation(uid) == proof
    assert case.store.get_native_recovery_close_retirement(uid) is None
    assert case.store.get_native_command(uid) == command
    assert command["dispatch_retired"] == 0
    assert case.safety.get_physical_action(uid) == ledger
    assert ledger["confirmedOutcome"] == "EXECUTED"
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert case.store.get_work_slot() == case.occupancy
    assert not case.store._conn.execute("PRAGMA foreign_key_check").fetchall()
    assert wire.sent == sent

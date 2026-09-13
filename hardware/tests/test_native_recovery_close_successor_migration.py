"""Schema upgrades preserve frozen ancestry without manufacturing successors."""
import sqlite3

import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
)


def test_current_schema_preserves_only_the_frozen_root_ancestry(tmp_path):
    from edge_store import CURRENT_SCHEMA_VERSION

    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        source_uid = case.binding["evidence"]["sourceActionUid"]
        with case.store.transaction() as connection:
            row = connection.execute(
                "SELECT predecessor_action_uid FROM native_delivery_recovery_close WHERE action_uid=?",
                (uid,),
            ).fetchone()
            version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            count = connection.execute("SELECT COUNT(*) FROM native_delivery_recovery_close").fetchone()[0]
        assert CURRENT_SCHEMA_VERSION >= 40
        assert version == CURRENT_SCHEMA_VERSION
        assert row[0] == source_uid
        assert count == 1
    finally:
        case.close()


def test_cold_upgrade_open_is_idempotent_and_adds_no_binding(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        before = case.store.get_native_delivery_recovery_close(uid)
        cold_reopen_runtime_history(case)
        cold_reopen_runtime_history(case)
        assert case.store.get_native_delivery_recovery_close(uid) == before
        with case.store.transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM native_delivery_recovery_close").fetchone()[0] == 1
    finally:
        case.close()


@pytest.mark.parametrize("damage", ["self", "wrong-parent"])
def test_corrupt_frozen_predecessor_index_is_rejected_not_repaired(tmp_path, damage):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    uid = case.variant["targetActionUid"]
    try:
        case.store.close()
        with sqlite3.connect(case.path) as connection:
            if damage == "self":
                connection.execute(
                    "UPDATE native_delivery_recovery_close SET predecessor_action_uid=? WHERE action_uid=?", (uid, uid)
                )
            else:
                connection.execute(
                    "UPDATE native_delivery_recovery_close SET predecessor_action_uid=? WHERE action_uid=?",
                    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", uid),
                )
        from edge_store import EdgeStore
        case.store = EdgeStore(str(case.path))
        with pytest.raises(ValueError):
            case.store.initialize_existing_recovery()
    finally:
        case.close()

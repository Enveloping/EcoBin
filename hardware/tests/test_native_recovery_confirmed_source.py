"""Historical source evidence remains immutable and grants no new action."""
import sqlite3

import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    load_native_recovery_runtime_history,
    poll_candidate,
)


def test_confirming_saved_close_does_not_relabel_original_source_action(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    try:
        source_uid = case.fixture["common"]["sourceAction"]["actionUid"]
        source_before = case.safety.get_physical_action(source_uid)
        serial = RecoverySerial(mcu_boot_id=0)
        status = poll_candidate(candidate_loop(case, serial), serial, count=12)
        assert case.safety.get_physical_action(source_uid) == source_before
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.issue["settlementAllowed"] is False
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("damage", ["source-command", "source-binding"])
def test_changed_original_source_custody_is_rejected_on_reopen(tmp_path, damage):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    source_uid = case.fixture["common"]["sourceAction"]["actionUid"]
    try:
        case.store.close()
        with sqlite3.connect(case.path) as connection:
            if damage == "source-command":
                row = connection.execute(
                    "SELECT payload FROM native_mcu_command WHERE command_uid=?", (source_uid,)
                ).fetchone()
                changed = bytearray(row[0])
                changed[-1] ^= 0x01
                connection.execute(
                    "UPDATE native_mcu_command SET payload=? WHERE command_uid=?",
                    (bytes(changed), source_uid),
                )
            else:
                connection.execute(
                    "UPDATE native_action_binding SET action_key='changed' WHERE action_uid=?", (source_uid,)
                )
        from edge_store import EdgeStore
        case.store = EdgeStore(str(case.path))
        with pytest.raises((ValueError, RuntimeError)):
            case.store.initialize_existing_recovery()
    finally:
        case.close()


def test_late_result_stays_issue_evidence_and_never_becomes_settlement(tmp_path):
    import uart2_protocol as uart

    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        serial = RecoverySerial()
        serial.inject_mcu_frame(uart.encode_frame("WORK_RESULT", 800, case.saved["payload"]))
        status = candidate_loop(case, serial).poll()
        assert len(case.store.list_native_delivery_issue_results(case.issue["issueUid"])) == 1
        assert case.store.list_native_result_report_tasks() == []
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()

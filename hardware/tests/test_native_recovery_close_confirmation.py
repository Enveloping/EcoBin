"""A saved historical close result may be confirmed, but never replayed."""
import sqlite3

import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate,
)


@pytest.mark.parametrize("mcu_boot_id", [None, 0], ids=["offline", "new-boot"])
def test_saved_output_confirms_frozen_action_without_serial_motion(tmp_path, mcu_boot_id):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=mcu_boot_id)
        status = poll_candidate(candidate_loop(case, serial), serial, count=12)
        proof = case.store.get_native_recovery_close_confirmation(uid)
        ledger = case.safety.get_physical_action(uid)
        assert proof["state"] == "CONFIRMED"
        assert (ledger["state"], ledger["confirmedOutcome"]) == ("CONFIRMED", "EXECUTED")
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_confirmation_survives_both_database_reopens_idempotently(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        first = poll_candidate(candidate_loop(case, serial), serial)
        proof = case.store.get_native_recovery_close_confirmation(uid)
        ledger = case.safety.get_physical_action(uid)

        cold_reopen_runtime_history(case)
        second_serial = RecoverySerial()
        second = poll_candidate(candidate_loop(case, second_serial), second_serial, count=4)
        assert case.store.get_native_recovery_close_confirmation(uid) == proof
        assert case.safety.get_physical_action(uid) == ledger
        assert_recovery_only(first, serial)
        assert_recovery_only(second, second_serial)
    finally:
        case.close()


@pytest.mark.parametrize("column", ["payload", "saved_payload"])
def test_changed_saved_output_is_rejected_instead_of_reinterpreted(tmp_path, column):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    uid = case.variant["targetActionUid"]
    try:
        case.store.close()
        with sqlite3.connect(case.path) as connection:
            row = connection.execute(
                f"SELECT {column} FROM native_actuator_event WHERE reported_command_uid=?", (uid,)
            ).fetchone()
            changed = bytearray(row[0])
            changed[-1] ^= 0x01
            connection.execute(
                f"UPDATE native_actuator_event SET {column}=? WHERE reported_command_uid=?",
                (bytes(changed), uid),
            )
        from edge_store import EdgeStore
        case.store = EdgeStore(str(case.path))
        with pytest.raises(ValueError):
            case.store.initialize_existing_recovery()
            event = case.store._conn.execute(
                "SELECT mcu_boot_id,event_sequence FROM native_actuator_event WHERE reported_command_uid=?",
                (uid,),
            ).fetchone()
            case.store.get_native_actuator_event(event[0], event[1])
    finally:
        case.close()

"""A newer MCU boot isolates claimed history; it never causes a retry."""
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


def _isolate_on_new_boot(case, serial):
    from mcu_session import McuBootSession
    from native_recovery_close_isolation import NativeRecoveryCloseIsolation
    from uart2_transport import NativeUartTransport

    transport = NativeUartTransport(serial)
    boot = McuBootSession(case.store, serial.write)
    boot.poll(serial.read_clock())
    for _ in range(2):
        for raw in transport.poll(serial.read_clock()):
            assert boot.accept_frame(raw, serial.read_clock())
    assert boot.current_boot(serial.read_clock()) == 3
    uid = case.variant["targetActionUid"]
    return NativeRecoveryCloseIsolation(
        case.store, case.safety, boot, clock=serial.read_clock
    ).reconcile(uid)


def test_new_boot_isolates_claimed_unknown_history_without_replay(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        before = case.safety.get_physical_action(uid)
        serial = RecoverySerial(mcu_boot_id=0)
        proof = _isolate_on_new_boot(case, serial)
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        assert proof["state"] == "ISOLATED"
        assert disposition["state"] == "ISOLATED_BY_REBOOT"
        assert disposition["pastEffect"] == "UNKNOWN"
        assert case.safety.get_physical_action(uid) == before
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only({"admissionAllowed": False}, serial)
    finally:
        case.close()


def test_same_boot_or_timeout_does_not_invent_reboot_isolation(tmp_path):
    for index, mcu_boot_id in enumerate((2, None)):
        case = load_native_recovery_runtime_history(tmp_path / str(index), "claimed-unknown")
        try:
            uid = case.variant["targetActionUid"]
            serial = RecoverySerial(mcu_boot_id=mcu_boot_id)
            status = poll_candidate(candidate_loop(case, serial), serial, count=8)
            assert case.store.get_native_recovery_close_isolation(uid) is None
            assert case.store.get_native_recovery_close_confirmation(uid) is None
            assert_recovery_only(status, serial)
        finally:
            case.close()


def test_saved_output_wins_over_new_boot_isolation(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-with-output")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=0)
        status = poll_candidate(candidate_loop(case, serial), serial, count=12)
        assert case.store.get_native_recovery_close_confirmation(uid)["state"] == "CONFIRMED"
        assert case.store.get_native_recovery_close_isolation(uid) is None
        assert case.safety.get_physical_action(uid)["confirmedOutcome"] == "EXECUTED"
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_isolation_proof_survives_restart_without_new_serial_action(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=0)
        proof = _isolate_on_new_boot(case, serial)
        first = {"admissionAllowed": False}
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        cold_reopen_runtime_history(case)
        second_serial = RecoverySerial(mcu_boot_id=3)
        second = poll_candidate(candidate_loop(case, second_serial), second_serial, count=5)
        assert case.store.get_native_recovery_close_isolation(uid) == proof
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        assert_recovery_only(first, serial)
        assert_recovery_only(second, second_serial)
    finally:
        case.close()


@pytest.mark.parametrize("column", ["evidence_sha256", "bundle_json"])
def test_changed_isolation_proof_is_rejected_on_reopen(tmp_path, column):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    uid = case.variant["targetActionUid"]
    try:
        serial = RecoverySerial(mcu_boot_id=0)
        _isolate_on_new_boot(case, serial)
        case.store.close()
        with sqlite3.connect(case.path) as connection:
            if column == "evidence_sha256":
                connection.execute(
                    "UPDATE native_recovery_close_isolation SET evidence_sha256=? WHERE action_uid=?",
                    ("f" * 64, uid),
                )
            else:
                connection.execute(
                    "UPDATE native_recovery_close_isolation SET bundle_json='{}' WHERE action_uid=?",
                    (uid,),
                )
        from edge_store import EdgeStore
        case.store = EdgeStore(str(case.path))
        with pytest.raises((ValueError, KeyError)):
            case.store.initialize_existing_recovery()
    finally:
        case.close()

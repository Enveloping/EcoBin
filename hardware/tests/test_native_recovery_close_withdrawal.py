"""Authorized-but-unclaimed history is withdrawn without rewriting its ledger."""
from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate,
)


def test_armed_unclaimed_history_is_withdrawn_without_serial_motion(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        before = case.safety.get_physical_action(uid)
        serial = RecoverySerial()
        status = poll_candidate(candidate_loop(case, serial), serial)
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        assert disposition["state"] == "DISPATCH_WITHDRAWN"
        assert disposition["mayExecute"] is False
        assert case.safety.get_physical_action(uid) == before
        assert case.store.get_native_recovery_close_retirement(uid)["state"] == "RETIRED"
        assert case.store.get_work_slot() == case.occupancy
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_withdrawal_is_idempotent_across_pi_and_permanent_store_restart(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        first = poll_candidate(candidate_loop(case, serial), serial)
        retirement = case.store.get_native_recovery_close_retirement(uid)
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        ledger = case.safety.get_physical_action(uid)

        cold_reopen_runtime_history(case)
        second_serial = RecoverySerial()
        second = poll_candidate(candidate_loop(case, second_serial), second_serial, count=5)
        assert case.store.get_native_recovery_close_retirement(uid) == retirement
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        assert case.safety.get_physical_action(uid) == ledger
        assert_recovery_only(first, serial)
        assert_recovery_only(second, second_serial)
    finally:
        case.close()


def test_claimed_command_never_uses_unclaimed_withdrawal_path(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=None)
        status = poll_candidate(candidate_loop(case, serial), serial, count=5)
        assert case.store.get_native_recovery_close_retirement(uid) is None
        from job_safety import JobSafetyError
        try:
            case.safety.get_native_recovery_close_disposition(uid)
        except Exception as error:
            assert isinstance(error, JobSafetyError)
            assert error.code == "NATIVE_RECOVERY_DISPOSITION_NOT_FOUND"
        else:
            raise AssertionError("claimed history unexpectedly received a withdrawal disposition")
        assert_recovery_only(status, serial)
    finally:
        case.close()

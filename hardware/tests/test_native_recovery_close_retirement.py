"""Unsent historical close preparations are retired, not dispatched."""
import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate,
)


def test_prepared_unclaimed_history_is_permanently_retired_without_motion(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        status = poll_candidate(candidate_loop(case, serial), serial)
        retirement = case.store.get_native_recovery_close_retirement(uid)
        ledger = case.safety.get_physical_action(uid)
        assert retirement["state"] == "RETIRED"
        assert (ledger["state"], ledger["dispatchMode"], ledger["confirmedOutcome"]) == (
            "CONFIRMED", "PREPARED_ONLY", "NOT_EXECUTED")
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_retirement_survives_restart_and_does_not_create_another_action(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial()
        first = poll_candidate(candidate_loop(case, serial), serial)
        proof = case.store.get_native_recovery_close_retirement(uid)
        cold_reopen_runtime_history(case)
        second_serial = RecoverySerial()
        second = poll_candidate(candidate_loop(case, second_serial), second_serial, count=5)
        bindings = case.store.snapshot_native_recovery_close_startup()["bindings"]
        assert [binding["action"].action_uid for binding in bindings] == [uid]
        assert case.store.get_native_recovery_close_retirement(uid) == proof
        assert_recovery_only(first, serial)
        assert_recovery_only(second, second_serial)
    finally:
        case.close()


def test_claimed_history_cannot_be_retired_as_never_executed(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        serial = RecoverySerial(mcu_boot_id=None)
        status = poll_candidate(candidate_loop(case, serial), serial, count=5)
        assert case.store.get_native_recovery_close_retirement(uid) is None
        assert case.safety.get_physical_action(uid)["state"] == "ARMED"
        assert status["state"] == "WAITING_BOOT"
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("variant", ["prepared-unclaimed", "armed-unclaimed"])
def test_retirement_or_withdrawal_never_releases_archived_occupancy(tmp_path, variant):
    case = load_native_recovery_runtime_history(tmp_path, variant)
    try:
        serial = RecoverySerial()
        status = poll_candidate(candidate_loop(case, serial), serial)
        assert case.store.get_work_slot() == case.occupancy
        assert status["admissionAllowed"] is False
        assert_recovery_only(status, serial)
    finally:
        case.close()

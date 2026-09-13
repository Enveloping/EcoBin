"""Current recovery consumes frozen attempts but never creates a successor action."""
import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    load_native_recovery_runtime_history,
    poll_candidate,
)


@pytest.mark.parametrize(
    ("variant", "mcu_boot_id"),
    [
        ("prepared-unclaimed", 2),
        ("armed-unclaimed", 2),
        ("claimed-unknown", 0),
        ("claimed-with-output", 0),
    ],
)
def test_recovery_outcome_never_adds_a_successor_binding(tmp_path, variant, mcu_boot_id):
    case = load_native_recovery_runtime_history(tmp_path, variant)
    try:
        uid = case.variant["targetActionUid"]
        before = case.store.snapshot_native_recovery_close_startup()["bindings"]
        serial = RecoverySerial(mcu_boot_id=mcu_boot_id)
        status = poll_candidate(candidate_loop(case, serial), serial, count=12)
        after = case.store.snapshot_native_recovery_close_startup()["bindings"]
        assert [row["action"].action_uid for row in before] == [uid]
        assert [row["action"].action_uid for row in after] == [uid]
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_archived_work_remains_problem_only_after_historical_attempt_disposition(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "armed-unclaimed")
    try:
        serial = RecoverySerial()
        status = poll_candidate(candidate_loop(case, serial), serial)
        issue = case.store.get_native_delivery_issue(case.permit.work_uid)
        assert issue == case.issue
        assert issue["settlementAllowed"] is False
        assert case.store.get_work_slot() == case.occupancy
        assert status["admissionAllowed"] is False
        assert_recovery_only(status, serial)
    finally:
        case.close()

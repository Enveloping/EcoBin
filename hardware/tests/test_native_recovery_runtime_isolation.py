"""Foreground recovery after a proven reset preserves unknown old effects."""
import pytest
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_native_delivery_recovery_close import archived, coordinator
from hardware.tests.test_native_recovery_runtime import candidate_loop, CSerial


def test_candidate_isolates_sent_old_close_after_new_positive_boot_without_replay(archived):
    case, wire, boot, issue, _ = archived
    owner = coordinator(case, wire, boot)
    binding = owner.prepare(case.permit.work_uid, execution_window_ms=5000)
    uid = binding["action"].action_uid
    assert owner.send_once(uid)
    wire.runtime[3].clear()  # Lost reply is not a negative execution fact.
    before = case.safety.get_physical_action(uid)
    wire.reset_mcu()
    candidate = candidate_loop(case, wire, issue)
    for _ in range(12):
        status = candidate.poll()
        wire.advance(200)
    disposition = case.safety.get_native_recovery_close_disposition(uid)
    assert disposition["state"] == "ISOLATED_BY_REBOOT"
    assert disposition["pastEffect"] == "UNKNOWN"
    assert disposition["observedMcuBootId"] > binding["evidence"]["targetMcuBootId"]
    assert case.safety.get_physical_action(uid) == before
    assert status["admissionAllowed"] is False
    assert case.store.get_work_slot() == case.occupancy
    assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
    assert sum(name == "SAFE_CLOSE" for name, _ in wire.sent) == 1

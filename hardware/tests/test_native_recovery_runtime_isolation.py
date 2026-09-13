"""A newer owned boot isolates frozen claimed history without replay."""
from mcu_session import McuBootSession
from native_recovery_close_isolation import NativeRecoveryCloseIsolation
from uart2_transport import NativeUartTransport

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate as poll,
)


def test_claimed_old_close_isolated_after_new_positive_boot_without_replay(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        before = case.safety.get_physical_action(uid)
        serial = RecoverySerial(mcu_boot_id=0)
        transport = NativeUartTransport(serial)
        boot = McuBootSession(case.store, serial.write)
        boot.poll(serial.read_clock())
        for raw in transport.poll(serial.read_clock()):
            assert boot.accept_frame(raw, serial.read_clock())
        for raw in transport.poll(serial.read_clock()):
            assert boot.accept_frame(raw, serial.read_clock())
        assert boot.current_boot(serial.read_clock()) == 3
        # The production isolation reconciler consumes only the frozen claimed
        # row plus this newer owned boot; it does not create or send an action.
        assert NativeRecoveryCloseIsolation(
            case.store, case.safety, boot, clock=serial.read_clock
        ).reconcile(uid)["state"] == "ISOLATED"
        status = candidate_loop(case, serial).poll()
        disposition = case.safety.get_native_recovery_close_disposition(uid)
        assert disposition["state"] == "ISOLATED_BY_REBOOT"
        assert disposition["pastEffect"] == "UNKNOWN"
        assert disposition["observedMcuBootId"] > case.binding["evidence"]["targetMcuBootId"]
        assert case.safety.get_physical_action(uid) == before
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert_recovery_only(status, serial)

        local_proof = case.store.get_native_recovery_close_isolation(uid)
        cold_reopen_runtime_history(case)
        assert case.store.get_native_recovery_close_isolation(uid) == local_proof
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        second_serial = RecoverySerial(mcu_boot_id=serial.mcu_boot_id)
        second = poll(candidate_loop(case, second_serial), second_serial, count=5, step=150)
        assert case.store.get_native_recovery_close_isolation(uid) == local_proof
        assert case.safety.get_native_recovery_close_disposition(uid) == disposition
        assert_recovery_only(second, second_serial)
    finally:
        case.close()

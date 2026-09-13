"""An asynchronously obtained permit is still checked at the durable report boundary."""
import pytest

from hardware.tests.test_native_simplified_result import original_work


def test_report_uses_supplied_real_permit_without_foreground_rpc(tmp_path, monkeypatch):
    with original_work(tmp_path) as case:
        case.store.save_native_mcu_result(case.raw)
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        def no_rpc(*args, **kwargs):
            pytest.fail("report preparation performed a foreground permit RPC")
        monkeypatch.setattr(case.safety, "get_job_permit", no_rpc)
        report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"], permit_snapshot=snapshot)
        assert report["state"] == "REPORT_CREATED"
        assert len(case.store.list_native_result_report_tasks()) == 1
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


@pytest.mark.parametrize("fault", ["missing", "other_permit", "not_active"])
def test_async_snapshot_cannot_bypass_original_active_permit(tmp_path, monkeypatch, fault):
    with original_work(tmp_path) as case:
        case.store.save_native_mcu_result(case.raw)
        pending = case.store.list_native_result_report_tasks()
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        if fault == "missing":
            snapshot = None
        elif fault == "other_permit":
            snapshot = snapshot | {"permitUid": "77777777-7777-4777-8777-777777777777"}
        else:
            snapshot = snapshot | {"state": "GRANTED"}
        def no_rpc(*args, **kwargs):
            pytest.fail("bad explicit snapshot silently fell back to a fresh RPC")
        monkeypatch.setattr(case.safety, "get_job_permit", no_rpc)
        with pytest.raises(ValueError, match="original active job permit"):
            case.reporter.prepare(case.permit, case.start["mcuCommandUid"], permit_snapshot=snapshot)
        assert case.store.list_native_result_report_tasks() == pending
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid

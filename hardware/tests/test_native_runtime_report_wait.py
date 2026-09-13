"""Waiting for the original backend result must not block the UART on job IPC."""
from types import SimpleNamespace

import pytest

from hardware.tests.test_mcu_simplified_execution import library, runtime, select
from hardware.tests.test_native_business_runtime import retained_context, open_owner, poll_until
from hardware.tests.test_simplified_mcu_pi_business import real_work, finish_delivery_round


def test_waiting_for_backend_confirmation_does_not_requery_permanent_job(runtime, tmp_path, monkeypatch):
    with real_work(runtime, tmp_path, False) as case:
        retained_context(case)
        clock = SimpleNamespace(now=0)
        owner = open_owner(case, clock)
        try:
            now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
            assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            poll_until(owner, clock, lambda: case.store.get_native_result_report(case.permit,
                case.start["mcuCommandUid"], device_name="device-1") is not None)
            slot = case.store.get_work_slot()
            def no_rpc(*args, **kwargs):
                pytest.fail("backend wait entered synchronous permanent job RPC")
            monkeypatch.setattr(case.safety, "get_job_permit", no_rpc)
            for _ in range(120):
                owner.poll()
                clock.now += 100
            assert owner.uart_state == "READY"
            assert case.store.get_work_slot() == slot
            assert len(case.store.list_native_result_report_tasks()) == 1
        finally:
            owner.close()

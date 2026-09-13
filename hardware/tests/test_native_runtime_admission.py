"""Local configuration intent owns the gap before MCU staging begins."""
import pytest

from job_safety import JobSafetyError
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.test_native_business_runtime import (
    completed_first_work, apply_configuration, await_start_facts,
    configuration_command, start_command,
)
from hardware.tests.test_native_configuration_resume import newer


def test_new_start_cannot_starve_configuration_waiting_for_async_maintenance(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        command = newer(configuration_command())
        assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
        owner.apply_configuration_command(command)
        sent = len(case.wire.sent)
        # No CONFIG_BEGIN has yet been sent. MCU facts still correctly show
        # the applied old configuration, so only local durable custody covers it.
        with pytest.raises(JobSafetyError, match="configuration is waiting"):
            owner.start_delivery_command(start_command())
        assert len(case.wire.sent) == sent
        assert case.store.get_work_slot() is None
        assert case.store.get_configuration(command["payload"]["applicationUid"])["state"] == "EDGE_SAVED"

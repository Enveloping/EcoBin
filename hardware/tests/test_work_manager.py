import pytest

from work_manager import (
    WorkManager,
    _delivery_usable_weight,
    _reported_weight,
)


class AdmissionStore:

    def __init__(self, *, interlocked=False, fullness="NOT_FULL"):
        self.interlocked = interlocked
        self.fullness = fullness
        self.observations = []

    def get_state(self, key, default=None):
        values = {
            "applied_config_version": "8",
            "applied_config_content_sha256": "a" * 64,
        }
        return values.get(key, default)

    def clean_restart_interlock_active(self, port_no):
        return self.interlocked

    def get_port_fullness_state(self, port_no, bag_uid):
        return self.fullness

    def record_command_observation(
        self,
        command,
        stage,
        *,
        mcu_command_uid=None,
        error_code=None,
    ):
        self.observations.append((stage, error_code))
        return "ACCEPTED"


def delivery_command():
    return {
        "commandUid": "10000000-0000-4000-8000-000000000001",
        "commandType": "START_DELIVERY_SESSION",
        "targetDeviceName": "SN-DEMO-0001",
        "payload": {
            "portNo": 1,
            "bagUid": "20000000-0000-4000-8000-000000000001",
            "config": {
                "version": 8,
                "contentSha256": "a" * 64,
            },
        },
    }


def test_unstable_weight_is_usable_when_sensor_health_is_ok():
    payload = {
        "measurementStatus": "UNSTABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1234,
        "weightValueKind": "LAST_FOUR_MEAN",
        "weightSensorHealth": "OK",
    }

    assert _reported_weight(payload) == 1234
    assert _delivery_usable_weight(payload) == 1234


def test_fault_weight_is_retained_but_not_usable_for_delivery():
    payload = {
        "measurementStatus": "PROTOCOL_ERROR",
        "weightValuePresent": True,
        "reportedWeightGrams": 1200,
        "weightValueKind": "LAST_OBSERVED",
        "weightSensorHealth": "PROTOCOL_ERROR",
    }

    assert _reported_weight(payload) == 1200
    assert _delivery_usable_weight(payload) is None


@pytest.mark.parametrize(
    ("store", "expected"),
    [
        (
            AdmissionStore(interlocked=True),
            "CLEAN_RESTARTED_CLEAN_REQUIRED",
        ),
        (AdmissionStore(fullness="FULL"), "PORT_FULL"),
    ],
)
def test_delivery_physical_admission_is_enforced_locally(
    store,
    expected,
):
    manager = WorkManager(store, object(), None, None)

    result = manager.start_delivery_command(delivery_command())

    assert result == {"acked": False, "error": expected}
    assert store.observations == [("REJECTED", expected)]


def test_candidate_job_gate_disables_all_legacy_debug_entry_points():
    class EnabledSafety:
        enabled = True

    manager = WorkManager(
        object(),
        object(),
        None,
        None,
        job_safety=EnabledSafety(),
    )

    assert manager.start_delivery_session(
        "session", 1, 25_000, "bag"
    ) == {"success": False, "reason": "JOB_PERMIT_REQUIRED"}
    assert manager.start_clean_operation(
        "operation", 1, "old", "new"
    ) == {"success": False, "reason": "JOB_PERMIT_REQUIRED"}
    assert manager.authorize_first_open("session") == {
        "success": False,
        "reason": "LEGACY_ENTRY_DISABLED",
    }
    assert manager.authorize_clean_unlock("operation") == {
        "success": False,
        "reason": "LEGACY_ENTRY_DISABLED",
    }

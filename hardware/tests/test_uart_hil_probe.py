import uuid

import pytest

from tools.uart_hil_probe import (
    _apply_sample_configuration,
    _confirm_no_active_work,
    _run_delivery_door_cycle,
    _run_safe_close_only,
    _sample_configuration,
)
from uart_link import compute_mcu_payload_sha256


class FakeUartLink:
    def __init__(self, repeat=False):
        self.applied = []
        self.acks = []
        event = {
            "message_name": "CONFIG_APPLY_RESULT",
            "message_type": 20,
            "tx_sequence": 9,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 7,
                "status": "APPLIED",
                "configVersion": 23,
            },
        }
        self.events = [event]
        if repeat:
            self.events.append(
                {
                    **event,
                    "tx_sequence": 10,
                    "payload": dict(event["payload"]),
                }
            )

    def apply_configuration(self, command, part_uids):
        self.applied.append((command, list(part_uids)))
        for event in self.events:
            if event["message_name"] == "CONFIG_APPLY_RESULT":
                event["payload"]["applicationUid"] = command["payload"][
                    "applicationUid"
                ]
        disposition = (
            "DUPLICATE_ACCEPTED"
            if len(self.applied) > 1
            else "ACCEPTED"
        )
        return {
            "acked": True,
            "parts": [{"disposition": disposition}],
            "commit_mcu_command_uid": part_uids[-1],
        }

    def read_mcu_event(self, timeout_ms):
        del timeout_ms
        return self.events.pop(0) if self.events else None

    def send_ack(
        self,
        referenced_boot_id,
        referenced_tx_sequence,
        referenced_message_type,
        disposition="ACCEPTED",
    ):
        self.acks.append(
            (
                referenced_boot_id,
                referenced_tx_sequence,
                referenced_message_type,
                disposition,
            )
        )


class FakeDoorHilLink:
    def __init__(self):
        self.acks = []
        self.calls = []
        self.session_uid = None
        self.events = []

    def send_confirm_no_active_work(self, config_version, content_sha256):
        self.calls.append(("confirm", config_version, content_sha256))
        self.events.append(
            self._event(
                "BOOT_RECONCILIATION_RESULT",
                64,
                {"status": "ACCEPTED"},
            )
        )
        return {"acked": True, "mcu_command_uid": str(uuid.uuid4())}

    def send_start_delivery_session(self, **kwargs):
        self.calls.append(("start", kwargs))
        self.session_uid = kwargs["session_uid"]
        self.events.append(
            self._event(
                "WORK_PREOPEN_WEIGHT_READY",
                48,
                {
                    "sessionUid": self.session_uid,
                    "measurementUid": str(uuid.uuid4()),
                    "weightSensorHealth": "OK",
                    "measurementStatus": "UNSTABLE",
                    "weightValuePresent": True,
                    "reportedWeightGrams": 123,
                },
            )
        )
        return {"acked": True, "mcu_command_uid": str(uuid.uuid4())}

    def send_authorize_delivery_first_open(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        self.events.append(
            self._event(
                "DELIVERY_DOOR_COMMAND_RESULT",
                49,
                {
                    "sessionUid": self.session_uid,
                    "command": "OPEN",
                    "outputStatus": "COMMAND_DISPATCHED",
                },
            )
        )
        return {"acked": True, "mcu_command_uid": str(uuid.uuid4())}

    def send_safe_close_all(self):
        self.calls.append(("safe_close",))
        self.events.append(
            self._event(
                "SAFE_CLOSE_RESULT",
                56,
                {
                    "command": "CLOSE",
                    "outputStatus": "COMMAND_DISPATCHED",
                },
            )
        )
        return {"acked": True, "mcu_command_uid": str(uuid.uuid4())}

    def read_mcu_event(self, timeout_ms):
        del timeout_ms
        return self.events.pop(0) if self.events else None

    def send_ack(
        self,
        referenced_boot_id,
        referenced_tx_sequence,
        referenced_message_type,
        disposition="ACCEPTED",
    ):
        self.acks.append(
            (
                referenced_boot_id,
                referenced_tx_sequence,
                referenced_message_type,
                disposition,
            )
        )

    @staticmethod
    def _event(message_name, message_type, payload):
        return {
            "message_name": message_name,
            "message_type": message_type,
            "tx_sequence": message_type + 100,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": message_type,
                **payload,
            },
        }


class FakeAlreadyIdleLink(FakeDoorHilLink):
    def send_confirm_no_active_work(self, config_version, content_sha256):
        self.config_version = config_version
        self.content_sha256 = content_sha256
        return {"acked": False, "error": "STATE_CONFLICT"}

    def query_state(self, on_segment):
        frame = self._event(
            "STATE_SNAPSHOT_BEGIN",
            80,
            {
                "activeWorkType": "NONE",
                "activeWorkPhase": "IDLE",
                "appliedConfigVersion": self.config_version,
                "appliedContentSha256": self.content_sha256,
            },
        )
        on_segment(frame)
        return [frame]


def test_sample_configuration_has_valid_frozen_mcu_digest():
    command = _sample_configuration(23)
    payload = command["payload"]

    assert payload["config"]["version"] == 23
    assert len(payload["ports"]) == 1
    assert "deliveryDoorOpenCommandSignalMs" not in payload["deviceConfig"]
    assert "deliveryDoorCloseCommandSignalMs" not in payload["deviceConfig"]
    assert payload["config"]["mcuPayloadSha256"] == (
        compute_mcu_payload_sha256(payload)
    )


def test_sample_configuration_exposes_door_travel_wait():
    command = _sample_configuration(
        24,
        door_travel_wait_ms=35000,
    )

    device = command["payload"]["deviceConfig"]
    assert device["deliveryDoorTravelWaitMs"] == 35000
    assert command["payload"]["config"]["mcuPayloadSha256"] == (
        compute_mcu_payload_sha256(command["payload"])
    )


@pytest.mark.parametrize("version", [0, 9007199254740992])
def test_sample_configuration_rejects_unsafe_version(version):
    with pytest.raises(ValueError, match="config version"):
        _sample_configuration(version)


def test_sample_configuration_rejects_unsafe_door_tuning():
    with pytest.raises(ValueError, match="door travel wait"):
        _sample_configuration(23, door_travel_wait_ms=29999)


def test_apply_sample_configuration_waits_for_result_and_acks_it():
    link = FakeUartLink()

    result = _apply_sample_configuration(link, _sample_configuration(23))

    assert len(link.applied) == 1
    assert len(link.applied[0][1]) == 4
    assert result["result"]["payload"]["status"] == "APPLIED"
    assert link.acks == [(42, 9, 20, "ACCEPTED")]


def test_apply_configuration_acks_interleaved_critical_event():
    link = FakeUartLink()
    link.events.insert(
        0,
        {
            "message_name": "SAFETY_SENSOR_EVENT",
            "message_type": 64,
            "tx_sequence": 8,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 6,
            },
        },
    )

    result = _apply_sample_configuration(link, _sample_configuration(23))

    assert result["result"]["message_name"] == "CONFIG_APPLY_RESULT"
    assert link.acks == [
        (42, 8, 64, "ACCEPTED"),
        (42, 9, 20, "ACCEPTED"),
    ]


def test_repeat_sample_configuration_reuses_identities_and_event_sequence():
    link = FakeUartLink(repeat=True)

    result = _apply_sample_configuration(
        link,
        _sample_configuration(23),
        repeat=True,
    )

    assert len(link.applied) == 2
    assert link.applied[0] == link.applied[1]
    assert result["duplicateReplay"]["delivery"]["parts"][-1][
        "disposition"
    ] == "DUPLICATE_ACCEPTED"
    assert result["duplicateReplay"]["result"]["payload"][
        "mcuEventSequence"
    ] == result["result"]["payload"]["mcuEventSequence"]
    assert link.acks == [
        (42, 9, 20, "ACCEPTED"),
        (42, 10, 20, "ACCEPTED"),
    ]


def test_door_hil_cycle_waits_for_travel_before_safe_close(monkeypatch):
    link = FakeDoorHilLink()
    command = _sample_configuration(23)
    sleeps = []
    monkeypatch.setattr(
        "tools.uart_hil_probe.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    result = _run_delivery_door_cycle(
        link,
        command,
        boot_recovery_timeout_s=1.0,
    )

    assert [call[0] for call in link.calls] == [
        "confirm",
        "start",
        "authorize",
        "safe_close",
    ]
    assert result["openResult"]["payload"]["outputStatus"] == (
        "COMMAND_DISPATCHED"
    )
    assert "actualOutputMs" not in result["openResult"]["payload"]
    assert result["safeCloseResult"]["payload"]["outputStatus"] == (
        "COMMAND_DISPATCHED"
    )
    assert "actualOutputMs" not in result["safeCloseResult"]["payload"]
    assert len(link.acks) == 4
    assert sleeps == [30.0]


def test_safe_close_only_acks_command_and_result():
    link = FakeDoorHilLink()

    result = _run_safe_close_only(link)

    assert result["command"]["acked"] is True
    assert result["result"]["payload"]["outputStatus"] == (
        "COMMAND_DISPATCHED"
    )
    assert link.calls == [("safe_close",)]
    assert len(link.acks) == 1


def test_reconciliation_accepts_only_matching_already_idle_snapshot():
    link = FakeAlreadyIdleLink()
    command = _sample_configuration(23)

    result = _confirm_no_active_work(link, command, timeout_s=1.0)

    assert result["alreadyIdle"] is True
    assert len(link.acks) == 1

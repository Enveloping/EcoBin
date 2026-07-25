import pytest

from tools.uart_hil_probe import (
    _apply_sample_configuration,
    _sample_configuration,
)
from uart_link import compute_mcu_payload_sha256


class FakeUartLink:
    def __init__(self):
        self.applied = []
        self.acks = []
        self.events = [
            {
                "message_name": "CONFIG_APPLY_RESULT",
                "message_type": 20,
                "tx_sequence": 9,
                "payload": {
                    "mcuBootId": 42,
                    "status": "APPLIED",
                    "configVersion": 23,
                },
            }
        ]

    def apply_configuration(self, command, part_uids):
        self.applied.append((command, list(part_uids)))
        return {
            "acked": True,
            "parts": [],
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


def test_sample_configuration_has_valid_frozen_mcu_digest():
    command = _sample_configuration(23)
    payload = command["payload"]

    assert payload["config"]["version"] == 23
    assert len(payload["ports"]) == 1
    assert payload["config"]["mcuPayloadSha256"] == (
        compute_mcu_payload_sha256(payload)
    )


@pytest.mark.parametrize("version", [0, 9007199254740992])
def test_sample_configuration_rejects_unsafe_version(version):
    with pytest.raises(ValueError, match="config version"):
        _sample_configuration(version)


def test_apply_sample_configuration_waits_for_result_and_acks_it():
    link = FakeUartLink()

    result = _apply_sample_configuration(link, 23)

    assert len(link.applied) == 1
    assert len(link.applied[0][1]) == 4
    assert result["result"]["payload"]["status"] == "APPLIED"
    assert link.acks == [(42, 9, 20, "ACCEPTED")]

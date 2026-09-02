"""UART v1 post-ARM transport uncertainty must remain recovery-locked."""

from __future__ import annotations

import uuid

import pytest

from command_processor import CommandProcessor
from onenet_wire import canonical_payload_sha256
from tests.test_command_processor import (
    FakePhotoManager,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from uart_link import UartLink
from work_manager import WorkManager


class FlushFailureSerial:
    """Accept one complete OS write, then lose the flush result."""

    def __init__(self) -> None:
        self.is_open = True
        self.timeout = 0.5
        self.writes: list[bytes] = []
        self.flush_count = 0

    @property
    def in_waiting(self) -> int:
        return 0

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1
        raise OSError("simulated flush failure after full UART write")

    def read(self, _size: int) -> bytes:
        return b""

    def close(self) -> None:
        self.is_open = False


def _uart_v1_with_flush_failure() -> tuple[UartLink, FlushFailureSerial]:
    uart = UartLink(port="test", edge_boot_id=7, port_count=6)
    serial = FlushFailureSerial()
    uart._ser = serial
    uart._mcu_boot_id = 42
    return uart, serial


def _new_conflicting_work() -> dict:
    second = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    second["commandUid"] = str(uuid.uuid4())
    second["payload"]["sessionUid"] = str(uuid.uuid4())
    second["target"]["uid"] = second["payload"]["sessionUid"]
    second["payloadSha256"] = canonical_payload_sha256(second["payload"])
    return second


@pytest.mark.parametrize(
    ("example_name", "work_type", "work_uid_key", "expected_phase"),
    [
        (
            "start-delivery-session.service-wire.json",
            "DELIVERY",
            "sessionUid",
            "START_RESULT_UNKNOWN",
        ),
        (
            "start-clean-operation.service-wire.json",
            "CLEAN",
            "operationUid",
            "START_RESULT_UNKNOWN",
        ),
        (
            "sample-fullness.service-wire.json",
            "FULLNESS",
            "detectionUid",
            "RESULT_UNKNOWN",
        ),
        (
            "measure-empty-bag-baseline.service-wire.json",
            "BASELINE",
            "measurementUid",
            "RESULT_UNKNOWN",
        ),
    ],
)
def test_uart_v1_flush_failure_after_arm_is_unknown_and_blocks_new_work(
    tmp_path,
    example_name,
    work_type,
    work_uid_key,
    expected_phase,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart, serial = _uart_v1_with_flush_failure()
    work = WorkManager(
        edge,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(edge, uart, work)
    command = valid_service_command(example_name)
    assert edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert processor.process_next() is True

    inbox = edge.get_command(command["commandUid"])
    slot = edge.get_work_slot()
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    assert inbox["result"]["error"] == "UART_WRITE_RESULT_UNKNOWN"
    assert inbox["result"]["physicalEffect"] == "UNKNOWN"
    assert slot is not None
    assert slot["work_type"] == work_type
    assert slot["work_uid"] == command["payload"][work_uid_key]
    assert slot["context"]["phase"] == expected_phase
    action = next(iter(slot["context"]["job_safety"]["actions"].values()))
    assert action["dispatch_result"] == "ARMED"
    permanent = updater.get_physical_action(
        {"actionUid": action["action_uid"]}
    )
    assert permanent["state"] == "ARMED"
    assert permanent["confirmedOutcome"] is None
    assert len(serial.writes) == 1
    assert serial.flush_count == 1
    stages = {
        row["stage"]
        for row in edge._conn.execute(
            "SELECT stage FROM command_observation WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchall()
    }
    assert "FAILED" not in stages

    second = _new_conflicting_work()
    assert edge.receive_command(
        second["commandUid"],
        second["commandType"],
        second,
    ) == "ACCEPTED"
    assert processor.process_next() is True
    assert len(serial.writes) == 1
    assert edge.get_work_slot()["work_uid"] == slot["work_uid"]
    assert edge.get_command(second["commandUid"])["state"] != "COMPLETED"
    assert updater.get_status()["jobGateState"] == "LOCKED"
    updater.close()
    edge.close()

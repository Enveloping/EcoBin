from __future__ import annotations

import pytest

from command_processor import CommandProcessor
from fixed_frame_mcu_adapter import FixedFrameMcuAdapter
from job_safety import JobSafetyError
from onenet_wire import canonical_payload_sha256
from tests.test_stage4_two_phase_dispatch import (
    ACTION_KEY,
    ACTION_UID,
    DISPATCH_TOKEN,
    GatedFixedFrameQuery,
    _manager,
)
from tests.test_stage4_fixed_frame_convergence import (
    _runtime,
    valid_compat_service_command,
)
from work_manager import WorkManager


class FlushFailureSerial:
    def __init__(self) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self.flush_count = 0

    @property
    def in_waiting(self) -> int:
        return 0

    def read(self, size: int) -> bytes:
        del size
        return b""

    def write(self, data: bytes) -> int:
        wire = bytes(data)
        self.writes.append(wire)
        return len(wire)

    def flush(self) -> None:
        self.flush_count += 1
        raise OSError("serial flush result unavailable")

    def close(self) -> None:
        self.is_open = False


def test_fixed_frame_query_does_not_reset_deadline_after_prepare(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    uart = GatedFixedFrameQuery()
    updater, client, _contexts, _unused_uart, manager, context = _manager(
        tmp_path,
        uart=uart,
    )

    # The caller derives this absolute deadline from the cloud command before
    # PREPARE. A scheduling pause after PREPARE must not create a fresh UART
    # timeout window when the protected F0 query eventually reaches its gate.
    dispatch_deadline_monotonic_cap = 101.0
    action = manager._prepare_physical_action(
        context,
        message_name="FIXED_FRAME_F0_QUERY",
        values={"portNo": 1},
        mcu_command_uid=ACTION_UID,
        action_key=ACTION_KEY,
        action_kind="FIXED_FRAME_SELF_TEST",
        dispatch_attempt_token=DISPATCH_TOKEN,
    )
    ticks["value"] = 102.0

    with pytest.raises(JobSafetyError) as raised:
        manager._query_fixed_frame_self_test_with_gate(
            context,
            action=action,
            action_key=ACTION_KEY,
            timeout_ms=1_000,
            dispatch_attempt_token=DISPATCH_TOKEN,
            dispatch_deadline_monotonic_cap=(
                dispatch_deadline_monotonic_cap
            ),
        )

    assert raised.value.code == "COMMAND_EXPIRED"
    assert uart.gate_calls == 1
    assert uart.write_count == 0
    assert client.arm_calls == []
    result = updater.get_physical_action({"actionUid": ACTION_UID})
    assert result["state"] == "CONFIRMED"
    assert result["confirmedOutcome"] == "NOT_EXECUTED"
    assert result["confirmationBasis"] == "PREPARED_NOT_ARMED"


def test_baseline_entry_keeps_query_deadline_from_before_prepare(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    runtime = _runtime(tmp_path)
    original_request = runtime.client.request

    def request_with_slow_prepare(action: str, payload: dict) -> dict:
        result = original_request(action, payload)
        if action == "PREPARE_PHYSICAL_ACTION":
            # The command's wall-clock expiresAt is still five minutes away,
            # but the fixed-frame query's original three-second budget has
            # already elapsed while PREPARE was completing.
            ticks["value"] = 104.0
        return result

    monkeypatch.setattr(runtime.client, "request", request_with_slow_prepare)
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    runtime.processor.process_next()

    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "COMMAND_EXPIRED"
    assert runtime.client.arm_calls == []
    assert runtime.uart.self_test_calls == []
    action = runtime.updater.get_physical_action(
        {"actionUid": command["payload"]["measurementUid"]}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "NOT_EXECUTED"
    assert action["confirmationBasis"] == "PREPARED_NOT_ARMED"


def test_baseline_f0_flush_failure_after_arm_requires_recovery(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    serial = FlushFailureSerial()
    uart = FixedFrameMcuAdapter(
        "TEST",
        edge_boot_id=42,
        serial_factory=lambda **_kwargs: serial,
    )
    assert uart.open() is True
    work = WorkManager(
        runtime.edge,
        uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    processor = CommandProcessor(runtime.edge, uart, work)
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert processor.process_next() is True

    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    assert inbox["result"]["physicalEffect"] == "UNKNOWN"
    slot = runtime.edge.get_work_slot()
    assert slot is not None
    assert slot["work_type"] == "BASELINE"
    assert slot["work_uid"] == command["payload"]["measurementUid"]
    assert slot["context"]["phase"] == "QUERY_RESULT_UNKNOWN"
    action = runtime.updater.get_physical_action({
        "actionUid": command["payload"]["measurementUid"],
    })
    assert action["state"] == "ARMED"
    assert action["confirmedOutcome"] is None
    assert serial.writes == [bytes.fromhex("f001f0")]
    assert serial.flush_count == 1
    assert runtime.edge._conn.execute(
        """SELECT COUNT(*) FROM command_observation
           WHERE command_uid=? AND stage='FAILED'""",
        (command["commandUid"],),
    ).fetchone()[0] == 0

    second = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    second["commandUid"] = "6f000000-0000-4000-8000-000000000099"
    second["payload"]["measurementUid"] = (
        "6f000000-0000-4000-8000-000000000098"
    )
    second["target"]["uid"] = second["payload"]["measurementUid"]
    second["payloadSha256"] = canonical_payload_sha256(second["payload"])
    assert runtime.edge.receive_command(
        second["commandUid"],
        second["commandType"],
        second,
    ) == "ACCEPTED"
    assert processor.process_next() is True
    assert runtime.edge.get_command(second["commandUid"])["state"] == "FAILED"
    assert serial.writes == [bytes.fromhex("f001f0")]

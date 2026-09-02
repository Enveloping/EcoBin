"""Startup must retain exact stage-four physical work as recovery-locked."""

from __future__ import annotations

import uuid

import pytest

from command_processor import CommandProcessor
from edge_store import EdgeStore
from onenet_wire import canonical_payload_sha256
from tests.test_command_processor import (
    EndCleanControlUart,
    FakePhotoManager,
    clean_preunlock_event,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from tests.test_stage4_uart_v1_post_arm_write_error import (
    FlushFailureSerial,
)
from tests.test_uart_link_v1 import AutoAckSerial
from uart_link import UartLink
from work_manager import WorkManager


def _uart_v1(serial_kind: str) -> tuple[UartLink, object]:
    uart = UartLink(port="test", edge_boot_id=7, port_count=6)
    if serial_kind == "post_arm_unknown":
        serial: object = FlushFailureSerial()
    elif serial_kind == "waiting":
        serial = AutoAckSerial(edge_boot_id=7, mcu_boot_id=42)
    else:  # pragma: no cover - test-case authoring guard
        raise AssertionError(f"unsupported serial kind: {serial_kind}")
    uart._ser = serial
    uart._mcu_boot_id = 42
    return uart, serial


def _runtime(edge, uart, safety) -> tuple[WorkManager, CommandProcessor]:
    work = WorkManager(
        edge,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    return work, CommandProcessor(edge, uart, work)


def _observation_stages(edge: EdgeStore, command_uid: str) -> list[str]:
    rows = edge._conn.execute(
        """SELECT stage FROM command_observation
           WHERE command_uid=? ORDER BY rowid""",
        (command_uid,),
    ).fetchall()
    return [row["stage"] for row in rows]


def _new_delivery_command() -> dict:
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    command["commandUid"] = str(uuid.uuid4())
    command["payload"]["sessionUid"] = str(uuid.uuid4())
    command["target"]["uid"] = command["payload"]["sessionUid"]
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def _clean_lock_deenergized_event(command: dict, context: dict) -> dict:
    return {
        "message_name": "CLEAN_LOCK_POWER_CHANGED",
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2_000,
            "mcuCommandUid": context["unlock_mcu_command_uid"],
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": context["action_sequence"],
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "OK",
            "faultCode": "NONE",
        },
    }


@pytest.mark.parametrize(
    ("example_name", "work_type", "work_uid_key"),
    [
        (
            "start-delivery-session.service-wire.json",
            "DELIVERY",
            "sessionUid",
        ),
        (
            "start-clean-operation.service-wire.json",
            "CLEAN",
            "operationUid",
        ),
    ],
)
def test_startup_keeps_post_arm_unknown_physical_work_recovery_locked(
    tmp_path,
    example_name,
    work_type,
    work_uid_key,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart, serial = _uart_v1("post_arm_unknown")
    _work, processor = _runtime(edge, uart, safety)
    command = valid_service_command(example_name)
    assert edge.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"

    assert processor.process_next() is True
    assert edge.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )
    original_slot = edge.get_work_slot()
    assert original_slot is not None
    assert original_slot["context"]["phase"] == "START_RESULT_UNKNOWN"
    assert len(serial.writes) == 1

    edge.close()
    restarted = make_store(tmp_path)
    recovered = restarted.recover_interrupted_commands()

    assert recovered["physical_locked"] == 1
    assert recovered["physical_failed"] == 0
    inbox = restarted.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    slot = restarted.get_work_slot()
    assert slot is not None
    assert slot["work_type"] == work_type
    assert slot["work_uid"] == command["payload"][work_uid_key]
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "START_RESULT_UNKNOWN"
    assert "FAILED" not in _observation_stages(
        restarted, command["commandUid"]
    )

    restarted_work, restarted_processor = _runtime(
        restarted, uart, safety
    )
    assert restarted_work.reconcile_pending_job_safety_completion() is False
    assert restarted_work.reconcile_pre_action_job_safety_failure() is False
    assert restarted_work.reconcile_orphan_granted_job_permits() == 0
    assert restarted_processor.process_next() is False
    assert len(serial.writes) == 1
    assert restarted.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    second = _new_delivery_command()
    assert restarted.receive_command(
        second["commandUid"], second["commandType"], second
    ) == "ACCEPTED"
    assert restarted_processor.process_next() is True
    assert len(serial.writes) == 1
    assert restarted.get_command(second["commandUid"])["state"] != (
        "COMPLETED"
    )
    assert restarted.get_work_slot()["work_uid"] == slot["work_uid"]
    assert updater.get_status()["jobGateState"] == "LOCKED"

    updater.close()
    restarted.close()


@pytest.mark.parametrize(
    (
        "example_name",
        "work_type",
        "work_uid_key",
        "waiting_phase",
    ),
    [
        (
            "start-delivery-session.service-wire.json",
            "DELIVERY",
            "sessionUid",
            "WAITING_PREOPEN_WEIGHT",
        ),
        (
            "start-clean-operation.service-wire.json",
            "CLEAN",
            "operationUid",
            "WAITING_PREUNLOCK_WEIGHT",
        ),
        (
            "sample-fullness.service-wire.json",
            "FULLNESS",
            "detectionUid",
            "WAITING_RESULT",
        ),
    ],
)
def test_startup_converts_exact_waiting_physical_work_to_recovery_lock(
    tmp_path,
    example_name,
    work_type,
    work_uid_key,
    waiting_phase,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart, serial = _uart_v1("waiting")
    _work, processor = _runtime(edge, uart, safety)
    command = valid_service_command(example_name)
    assert edge.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"

    assert processor.process_next() is True
    inbox_before_restart = edge.get_command(command["commandUid"])
    assert inbox_before_restart["state"] == "WAITING_MCU_RESULT"
    original_slot = edge.get_work_slot()
    assert original_slot is not None
    assert original_slot["work_type"] == work_type
    assert original_slot["context"]["phase"] == waiting_phase
    action = next(
        iter(original_slot["context"]["job_safety"]["actions"].values())
    )
    assert updater.get_physical_action(
        {"actionUid": action["action_uid"]}
    )["state"] == "ARMED"
    assert len(serial.writes) == 1

    edge.close()
    restarted = make_store(tmp_path)
    recovered = restarted.recover_interrupted_commands()

    assert recovered["physical_locked"] == 1
    assert recovered["physical_failed"] == 0
    inbox = restarted.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "EDGE_RESTARTED_RESULT_UNKNOWN"
    slot = restarted.get_work_slot()
    assert slot is not None
    assert slot["work_type"] == work_type
    assert slot["work_uid"] == command["payload"][work_uid_key]
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == waiting_phase
    assert "FAILED" not in _observation_stages(
        restarted, command["commandUid"]
    )

    restarted_work, restarted_processor = _runtime(
        restarted, uart, safety
    )
    assert restarted_work.reconcile_pending_job_safety_completion() is False
    assert restarted_work.reconcile_pre_action_job_safety_failure() is False
    assert restarted_work.reconcile_orphan_granted_job_permits() == 0
    assert restarted_processor.process_next() is False
    assert len(serial.writes) == 1
    assert restarted.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    second = _new_delivery_command()
    assert restarted.receive_command(
        second["commandUid"], second["commandType"], second
    ) == "ACCEPTED"
    assert restarted_processor.process_next() is True
    assert len(serial.writes) == 1
    assert restarted.get_command(second["commandUid"])["state"] != (
        "COMPLETED"
    )
    assert restarted.get_work_slot()["work_uid"] == slot["work_uid"]
    assert updater.get_status()["jobGateState"] == "LOCKED"

    updater.close()
    restarted.close()


def test_startup_keeps_exact_armed_clean_resume_recovery_locked(
    tmp_path,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart, serial = _uart_v1("waiting")
    work, processor = _runtime(edge, uart, safety)
    start = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    assert edge.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next() is True

    start_action_uid = edge.get_command(start["commandUid"])[
        "mcu_command_uid"
    ]
    work.handle_mcu_event(
        clean_preunlock_event(
            start,
            start_action_uid,
            str(uuid.uuid4()),
        )
    )
    active = edge.get_work_slot()
    assert active is not None
    work.handle_mcu_event(
        _clean_lock_deenergized_event(start, active["context"])
    )
    operation_uid = start["payload"]["operationUid"]
    assert edge.mark_clean_window_expired_for_recovery(
        operation_uid
    ) == "RECOVERY_REQUIRED"

    resume = valid_service_command(
        "resume-clean-operation.service-wire.json"
    )
    resume["payload"]["operationUid"] = operation_uid
    resume["payload"]["newBagUid"] = start["payload"]["newBagUid"]
    resume["target"]["uid"] = operation_uid
    resume["payloadSha256"] = canonical_payload_sha256(resume["payload"])
    assert edge.receive_command(
        resume["commandUid"], resume["commandType"], resume
    ) == "ACCEPTED"
    assert processor.process_next() is True

    inbox_before_restart = edge.get_command(resume["commandUid"])
    assert inbox_before_restart["state"] == "WAITING_MCU_RESULT"
    slot_before_restart = edge.get_work_slot()
    context_before_restart = slot_before_restart["context"]
    assert context_before_restart["phase"] == "RESUMING_CLEAN"
    assert context_before_restart["resume_command_uid"] == (
        resume["commandUid"]
    )
    resume_action_key = context_before_restart["resume_action_key"]
    resume_action_uid = context_before_restart["resume_mcu_command_uid"]
    resume_action = context_before_restart["job_safety"]["actions"][
        resume_action_key
    ]
    assert resume_action["action_uid"] == resume_action_uid
    assert resume_action["dispatch_result"] == "ARMED"
    assert updater.get_physical_action(
        {"actionUid": resume_action_uid}
    )["state"] == "ARMED"
    writes_before_restart = len(serial.writes)
    assert writes_before_restart == 3

    edge.close()
    restarted = make_store(tmp_path)
    recovered = restarted.recover_interrupted_commands()

    assert recovered["physical_locked"] == 1
    assert recovered["physical_failed"] == 0
    inbox = restarted.get_command(resume["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "EDGE_RESTARTED_RESULT_UNKNOWN"
    slot = restarted.get_work_slot()
    assert slot is not None
    assert slot["work_type"] == "CLEAN"
    assert slot["work_uid"] == operation_uid
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "RESUMING_CLEAN"
    assert slot["context"]["resume_command_uid"] == resume["commandUid"]
    assert slot["context"]["resume_action_key"] == resume_action_key
    assert slot["context"]["resume_mcu_command_uid"] == resume_action_uid
    assert "FAILED" not in _observation_stages(
        restarted, resume["commandUid"]
    )

    restarted_work, restarted_processor = _runtime(
        restarted, uart, safety
    )
    assert restarted_work.reconcile_pending_job_safety_completion() is False
    assert restarted_work.reconcile_pre_action_job_safety_failure() is False
    assert restarted_work.reconcile_orphan_granted_job_permits() == 0
    assert restarted_processor.process_next() is False
    assert len(serial.writes) == writes_before_restart
    assert restarted.get_command(resume["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    updater.close()
    restarted.close()


def test_startup_keeps_unknown_clean_end_intent_recovery_locked(
    tmp_path,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart = EndCleanControlUart(
        end_result={"acked": False, "error": "TIMEOUT", "fatal": False}
    )
    _work, processor = _runtime(edge, uart, safety)
    start = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    assert edge.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next() is True

    end = valid_service_command(
        "end-clean-before-unlock.service-wire.json"
    )
    assert edge.receive_command(
        end["commandUid"], end["commandType"], end
    ) == "ACCEPTED"
    assert processor.process_next() is True
    end_before_restart = edge.get_command(end["commandUid"])
    assert end_before_restart["state"] == "RECOVERY_REQUIRED"
    context_before_restart = edge.get_work_slot()["context"]
    intent_before_restart = context_before_restart["end_before_unlock"]
    assert intent_before_restart["command_uid"] == end["commandUid"]
    assert intent_before_restart["mcu_command_uid"] == (
        end_before_restart["mcu_command_uid"]
    )
    assert intent_before_restart["reason"] == end["payload"]["reason"]
    assert intent_before_restart["state"] == "RESULT_UNKNOWN"
    calls_before_restart = list(uart.calls)

    edge.close()
    restarted = make_store(tmp_path)
    recovered = restarted.recover_interrupted_commands()

    assert recovered["physical_locked"] == 2
    assert recovered["physical_failed"] == 0
    inbox = restarted.get_command(end["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    slot = restarted.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    intent = slot["context"]["end_before_unlock"]
    assert intent == intent_before_restart
    assert "FAILED" not in _observation_stages(
        restarted, end["commandUid"]
    )
    assert "FAILED" not in _observation_stages(
        restarted, start["commandUid"]
    )

    restarted_work, restarted_processor = _runtime(
        restarted, uart, safety
    )
    assert restarted_work.reconcile_pending_job_safety_completion() is False
    assert restarted_work.reconcile_pre_action_job_safety_failure() is False
    assert restarted_work.reconcile_orphan_granted_job_permits() == 0
    assert restarted_processor.process_next() is False
    assert uart.calls == calls_before_restart
    assert restarted.get_command(end["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    updater.close()
    restarted.close()


def test_disabled_physical_recovery_keeps_legacy_restart_failure(
    tmp_path,
) -> None:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater, safety = make_real_job_safety(tmp_path)
    uart, _serial = _uart_v1("waiting")
    _work, processor = _runtime(edge, uart, safety)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert edge.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert processor.process_next() is True
    assert edge.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )

    edge.close()
    restarted = make_store(tmp_path)
    recovered = restarted.recover_interrupted_commands(
        physical_recovery_required=False
    )

    assert recovered["physical_locked"] == 0
    assert recovered["physical_failed"] == 1
    inbox = restarted.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "EDGE_RESTARTED"
    assert "FAILED" in _observation_stages(
        restarted, command["commandUid"]
    )

    updater.close()
    restarted.close()

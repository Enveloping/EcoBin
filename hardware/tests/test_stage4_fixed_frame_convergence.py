from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from command_processor import CommandProcessor
from edge_store import EdgeStore
from job_safety import JobSafetyError, PermanentJobSafety
from local_control import LocalControlRemoteError
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
)
from uart_link import compute_mcu_payload_sha256
from updater_store import UpdaterStore, UpdaterStoreError
from work_manager import WorkManager


def _activate_candidate(updater: UpdaterStore) -> None:
    status = updater.get_status()
    updater.activate_stage4_job_gate(
        {
            "operationUid": "90000000-0000-4000-8000-000000000002",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


class FakeCompatUart:
    compatibility_mode = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.self_test_calls: list[int] = []
        self.self_test_result = {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "portNo": 1,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 1_234,
            "weightMeasurementUid": (
                "6f000000-0000-4000-8000-000000000002"
            ),
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
            "faultCode": None,
            "rawFrameHex": "f1030004d20000f1",
        }
        self.firmware_status_calls: list[tuple[int, int]] = []
        self.firmware_status_result = {
            "queryStatus": "OK",
            "mode": 1,
            "statusCode": 0,
            "status": "OK",
            "protocolRevision": 2,
            "firmwareVersionCode": 1,
            "firmwareVersion": "v2-test",
            "firmwareIdentityHex": "11" * 16,
            "safeFlags": 0x0F,
            "rawFrameHex": "f3010002000000010f00f3",
        }
        self.pending_business_result = False

    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid=None,
        dispatch_deadline_monotonic,
        dispatch_gate=None,
    ):
        if time.monotonic() >= dispatch_deadline_monotonic:
            return {
                "acked": False,
                "error": "COMMAND_EXPIRED",
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
            }
        if dispatch_gate is not None:
            dispatch_gate()
        self.calls.append((message_name, dict(values), mcu_command_uid))
        return {
            "acked": True,
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            "disposition": "ACCEPTED",
        }

    def query_self_test(
        self,
        timeout_ms=3_000,
        on_result=None,
        dispatch_gate=None,
    ):
        if dispatch_gate is not None:
            dispatch_gate()
        self.self_test_calls.append(timeout_ms)
        result = dict(self.self_test_result)
        if on_result is not None:
            on_result(result)
        return result

    def query_firmware_status(self, mode, timeout_ms=3_000):
        self.firmware_status_calls.append((mode, timeout_ms))
        return dict(self.firmware_status_result)

    def has_pending_business_result(self) -> bool:
        return self.pending_business_result


class FakePhotoManager:
    def __init__(self) -> None:
        self.captured: list[tuple[str, str]] = []

    def _capture(self, label: str, work_uid: str) -> bool:
        self.captured.append((label, work_uid))
        return True

    def capture_open_photos(self, work_uid):
        return self._capture("open", work_uid)

    def capture_close_photos(self, work_uid):
        return self._capture("close", work_uid)

    def capture_clean_open_photos(self, work_uid):
        return self._capture("clean_open", work_uid)

    def capture_clean_close_photos(self, work_uid):
        return self._capture("clean_close", work_uid)


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def _wire_command(example_name: str) -> dict[str, Any]:
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        example_name,
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return command


def valid_compat_service_command(example_name: str) -> dict[str, Any]:
    command = _wire_command(example_name)
    if "portNo" in command["payload"]:
        command["payload"]["portNo"] = 1
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def _valid_configuration_command() -> dict[str, Any]:
    command = _wire_command("apply-configuration.service-wire.json")
    command["payload"]["config"][
        "mcuPayloadSha256"
    ] = compute_mcu_payload_sha256(command["payload"])
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def mark_configuration_applied(store: EdgeStore) -> None:
    command = _valid_configuration_command()
    part_uids = [
        str(__import__("uuid").uuid4())
        for _ in range(len(command["payload"]["ports"]) + 3)
    ]
    assert store.save_configuration_edge(command, part_uids) == "ACCEPTED"
    assert store.apply_configuration_result(
        {
            "mcuCommandUid": part_uids[-1],
            "applicationUid": command["payload"]["applicationUid"],
            "status": "APPLIED",
            "configVersion": command["payload"]["config"]["version"],
            "contentSha256": command["payload"]["config"][
                "contentSha256"
            ],
            "mcuPayloadSha256": command["payload"]["config"][
                "mcuPayloadSha256"
            ],
            "faultCode": "NONE",
        }
    ) == "ACCEPTED"
    store.save_fixed_frame_self_test(
        {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "portNo": 1,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 10_000,
            "weightMeasurementUid": (
                "6f000000-0000-4000-8000-000000000001"
            ),
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
            "faultCode": None,
            "rawFrameHex": "f1030027100000f1",
        }
    )


class CapturingStoreClient:
    """Run the production client contract against a real updater database."""

    def __init__(self, store: UpdaterStore) -> None:
        self.store = store
        self.permit_uids: list[str] = []
        self.arm_calls: list[dict[str, Any]] = []
        self.live_confirmation_calls: list[dict[str, Any]] = []

    def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        operations = {
            "REQUEST_JOB_PERMIT": self.store.request_job_permit,
            "BEGIN_JOB": self.store.begin_job,
            "GET_JOB_PERMIT": self.store.get_job_permit,
            "ABANDON_JOB_PERMIT": self.store.abandon_job_permit,
            "COMPLETE_JOB": self.store.complete_job,
            "PREPARE_PHYSICAL_ACTION": self.store.prepare_physical_action,
            "ARM_PHYSICAL_ACTION": self.store.arm_physical_action,
            "CANCEL_PREPARED_PHYSICAL_ACTION": (
                self.store.cancel_prepared_physical_action
            ),
            "ABORT_PHYSICAL_ACTION_DISPATCH": (
                self.store.abort_physical_action_dispatch
            ),
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT": (
                self.store.confirm_live_physical_action_result
            ),
            "GET_PHYSICAL_ACTION": self.store.get_physical_action,
            "CONFIRM_PHYSICAL_ACTION": self.store.confirm_physical_action,
            "QUARANTINE_UNKNOWN_PHYSICAL_ACTION": (
                self.store.quarantine_unknown_physical_action
            ),
        }
        if action == "ARM_PHYSICAL_ACTION":
            self.arm_calls.append(dict(payload))
        elif action == "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT":
            self.live_confirmation_calls.append(dict(payload))
        try:
            result = operations[action](payload)
        except UpdaterStoreError as error:
            raise LocalControlRemoteError(
                error.code,
                str(error),
                "90000000-0000-4000-8000-000000000001",
            ) from error
        if action == "REQUEST_JOB_PERMIT":
            self.permit_uids.append(str(result["permitUid"]))
        return result


@dataclass
class Stage4Runtime:
    edge: Any
    updater: UpdaterStore
    client: CapturingStoreClient
    uart: FakeCompatUart
    photos: FakePhotoManager
    safety: PermanentJobSafety
    work: WorkManager
    processor: CommandProcessor


def _runtime(tmp_path) -> Stage4Runtime:
    edge = make_store(tmp_path)
    mark_configuration_applied(edge)
    updater = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-fixed-frame-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    client = CapturingStoreClient(updater)
    safety = PermanentJobSafety(client)
    uart = FakeCompatUart()
    photos = FakePhotoManager()
    work = WorkManager(
        edge,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(edge, uart, work)
    return Stage4Runtime(
        edge=edge,
        updater=updater,
        client=client,
        uart=uart,
        photos=photos,
        safety=safety,
        work=work,
        processor=processor,
    )


def _receive_and_start(runtime: Stage4Runtime, example_name: str) -> dict:
    command = valid_compat_service_command(example_name)
    if command["commandType"] == "START_CLEAN_OPERATION":
        command["payload"]["oldBaselineWeightGrams"] = 1_500
        command["payloadSha256"] = canonical_payload_sha256(
            command["payload"]
        )
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    runtime.processor.process_next()
    return command


def _compat_result(command: dict) -> dict[str, Any]:
    if command["commandType"] == "START_DELIVERY_SESSION":
        return {
            "message_name": "COMPAT_DELIVERY_RESULT",
            "message_type": 240,
            "source_tx_sequence": 1,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "preWeightGrams": 12_300,
                "postWeightGrams": 14_800,
                "infraredBlocked": True,
                "rawFrameHex": "dd00300c0039d001dd",
            },
        }
    return {
        "message_name": "COMPAT_CLEAN_RESULT",
        "message_type": 241,
        "source_tx_sequence": 2,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2_000,
            "preWeightGrams": 50_000,
            "postWeightGrams": 2_000,
            "infraredBlocked": False,
            "rawFrameHex": "ef00c3500007d000ef",
        },
    }


def _only_action(runtime: Stage4Runtime) -> tuple[str, dict[str, Any]]:
    slot = runtime.edge.get_work_slot()
    assert slot is not None
    actions = slot["context"]["job_safety"]["actions"]
    assert len(actions) == 1
    action = next(iter(actions.values()))
    action_uid = str(action["action_uid"])
    return action_uid, runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )


def _assert_permit_completed(runtime: Stage4Runtime) -> None:
    assert len(runtime.client.permit_uids) == 1
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert runtime.updater.get_status()["jobGateState"] == "OPEN"


def _assert_dispatch_token_is_volatile(
    runtime: Stage4Runtime,
    *,
    dispatch_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slot = runtime.edge.get_work_slot()
    assert slot is not None
    serialized_context = json.dumps(
        slot["context"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert dispatch_token not in serialized_context
    assert "dispatchAttemptToken" not in serialized_context
    assert "dispatch_attempt_token" not in serialized_context
    assert dispatch_token not in "\n".join(runtime.edge._conn.iterdump())
    assert dispatch_token not in caplog.text


def test_stage4_fixed_frame_f0_f1_live_result_confirms_action_and_permit(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    runtime.processor.process_next()

    action_uid = command["payload"]["measurementUid"]
    action = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "EXECUTED"
    assert action["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert len(runtime.client.arm_calls) == 1
    assert len(runtime.client.live_confirmation_calls) == 1
    assert (
        runtime.client.live_confirmation_calls[0]["dispatchAttemptToken"]
        == runtime.client.arm_calls[0]["dispatchAttemptToken"]
    )
    _assert_permit_completed(runtime)


@pytest.mark.parametrize(
    "example_name",
    [
        "start-delivery-session.service-wire.json",
        "start-clean-operation.service-wire.json",
    ],
)
def test_stage4_fixed_frame_dd_ef_same_process_confirms_and_completes(
    tmp_path,
    caplog,
    example_name,
) -> None:
    caplog.set_level(logging.DEBUG)
    runtime = _runtime(tmp_path)
    command = _receive_and_start(runtime, example_name)
    action_uid, action = _only_action(runtime)
    assert action["state"] == "ARMED"
    assert len(runtime.client.arm_calls) == 1
    dispatch_token = runtime.client.arm_calls[0]["dispatchAttemptToken"]
    _assert_dispatch_token_is_volatile(
        runtime,
        dispatch_token=dispatch_token,
        caplog=caplog,
    )

    runtime.processor.process_mcu_event(_compat_result(command))

    confirmed = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert confirmed["state"] == "CONFIRMED"
    assert confirmed["confirmedOutcome"] == "EXECUTED"
    assert confirmed["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"
    assert len(runtime.client.live_confirmation_calls) == 1
    assert (
        runtime.client.live_confirmation_calls[0]["dispatchAttemptToken"]
        == dispatch_token
    )
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert dispatch_token not in "\n".join(runtime.edge._conn.iterdump())
    assert dispatch_token not in caplog.text
    _assert_permit_completed(runtime)


@pytest.mark.parametrize(
    "example_name",
    [
        "start-delivery-session.service-wire.json",
        "start-clean-operation.service-wire.json",
    ],
)
def test_stage4_fixed_frame_late_dd_ef_after_business_restart_stays_locked(
    tmp_path,
    example_name,
) -> None:
    runtime = _runtime(tmp_path)
    command = _receive_and_start(runtime, example_name)
    action_uid, action = _only_action(runtime)
    assert action["state"] == "ARMED"

    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    try:
        restarted_work.handle_mcu_event(_compat_result(command))
    except JobSafetyError:
        # Refusing the late result may be surfaced or left for the command
        # processor's retry path; the durable locked facts below are the
        # required safety behavior.
        pass

    still_armed = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert still_armed["state"] == "ARMED"
    assert still_armed["confirmedOutcome"] is None
    assert runtime.client.live_confirmation_calls == []
    assert runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    assert runtime.edge.get_work_slot()["work_uid"] in {
        command["payload"].get("sessionUid"),
        command["payload"].get("operationUid"),
    }
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )


def test_remote_delivery_quarantine_reports_evidence_without_business_complete(
    tmp_path,
    monkeypatch,
) -> None:
    boot_identity = ["linux:10000000-0000-4000-8000-000000000001"]
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: boot_identity[0],
    )
    runtime = _runtime(tmp_path)
    original = _receive_and_start(
        runtime, "start-delivery-session.service-wire.json"
    )
    session_uid = original["payload"]["sessionUid"]
    action_uid, armed = _only_action(runtime)
    assert armed["state"] == "ARMED"

    boot_identity[0] = "linux:20000000-0000-4000-8000-000000000002"
    assert runtime.work.expire_fixed_frame_work() is True
    assert runtime.edge.get_command(original["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    recovery_uid = "93000000-0000-4000-8000-000000000001"
    recovery = {
        "schemaVersion": 2,
        "commandUid": "93000000-0000-4000-8000-000000000002",
        "commandType": "QUARANTINE_DELIVERY_RECOVERY",
        "targetDeviceName": original["targetDeviceName"],
        "target": {"type": "DELIVERY_SESSION", "uid": session_uid},
        "issuedAt": original["issuedAt"],
        "expiresAt": original["expiresAt"],
        "payloadSchemaVersion": 2,
        "payloadSha256": "0" * 64,
        "payload": {
            "recoveryUid": recovery_uid,
            "sessionUid": session_uid,
            "originalCommandUid": original["commandUid"],
            "physicalOutcomeUnknownConfirmed": True,
            "causeFixedConfirmed": True,
            "devicePowerCycledConfirmed": True,
            "motionAreaClearConfirmed": True,
            "deliveryDoorClosedConfirmed": True,
            "mechanismClearConfirmed": True,
            "reason": "现场已断电重启并确认机构安全，隔离旧投递。",
        },
        "cosGrant": None,
    }
    recovery["payloadSha256"] = canonical_payload_sha256(
        recovery["payload"]
    )
    assert runtime.edge.receive_command(
        recovery["commandUid"], recovery["commandType"], recovery
    ) == "ACCEPTED"
    assert runtime.edge.claim_next_command()["command_uid"] == recovery[
        "commandUid"
    ]

    result = runtime.work.quarantine_delivery_recovery(recovery)

    assert result["disposition"] == "QUARANTINED"
    assert runtime.uart.firmware_status_calls == [(1, 3_000)]
    assert runtime.uart.self_test_calls == [3_000]
    # The recovery path performs read-only F2/F0 queries; it sends no door or
    # clean actuation command.
    assert [call[0] for call in runtime.uart.calls] == [
        "START_DELIVERY_SESSION"
    ]
    resolved = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert resolved["state"] == "ARMED"
    assert resolved["confirmedOutcome"] is None
    assert resolved["unknownEffectResolution"]["resolutionUid"] == (
        recovery_uid
    )
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "CANCELLED"
    assert runtime.edge.get_work_slot() is None
    assert runtime.edge.get_command(original["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_command(recovery["commandUid"])["state"] == (
        "COMPLETED"
    )
    events = [
        json.loads(row["payload_json"])
        for row in runtime.edge.list_pending_events(100)
    ]
    assert len(
        [
            event
            for event in events
            if event["eventType"] == "DELIVERY_RECOVERY_QUARANTINED"
        ]
    ) == 1
    assert not any(
        event["eventType"] == "DELIVERY_COMPLETE" for event in events
    )


def _delivery_recovery_command(
    original: dict[str, Any],
    *,
    recovery_uid: str,
    recovery_command_uid: str,
) -> dict[str, Any]:
    session_uid = original["payload"]["sessionUid"]
    recovery = {
        "schemaVersion": 2,
        "commandUid": recovery_command_uid,
        "commandType": "QUARANTINE_DELIVERY_RECOVERY",
        "targetDeviceName": original["targetDeviceName"],
        "target": {"type": "DELIVERY_SESSION", "uid": session_uid},
        "issuedAt": original["issuedAt"],
        "expiresAt": original["expiresAt"],
        "payloadSchemaVersion": 2,
        "payloadSha256": "0" * 64,
        "payload": {
            "recoveryUid": recovery_uid,
            "sessionUid": session_uid,
            "originalCommandUid": original["commandUid"],
            "physicalOutcomeUnknownConfirmed": True,
            "causeFixedConfirmed": True,
            "devicePowerCycledConfirmed": True,
            "motionAreaClearConfirmed": True,
            "deliveryDoorClosedConfirmed": True,
            "mechanismClearConfirmed": True,
            "reason": "现场重新核对后执行失败关闭测试。",
        },
        "cosGrant": None,
    }
    recovery["payloadSha256"] = canonical_payload_sha256(
        recovery["payload"]
    )
    return recovery


@pytest.mark.parametrize(
    ("unsafe_fact", "expected_code"),
    (
        (
            "operator_confirmation",
            "RECOVERY_OPERATOR_CONFIRMATION_REQUIRED",
        ),
        ("unchanged_boot", "RECOVERY_POWER_CYCLE_NOT_PROVEN"),
        ("unsafe_firmware", "RECOVERY_MCU_NOT_IDLE_SAFE"),
        (
            "unhealthy_self_test",
            "RECOVERY_SENSOR_EVIDENCE_UNHEALTHY",
        ),
        ("pending_normal_result", "RECOVERY_NORMAL_RESULT_PENDING"),
    ),
)
def test_remote_delivery_quarantine_fails_closed_for_unsafe_facts(
    tmp_path,
    monkeypatch,
    unsafe_fact: str,
    expected_code: str,
) -> None:
    boot_identity = ["linux:30000000-0000-4000-8000-000000000001"]
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: boot_identity[0],
    )
    runtime = _runtime(tmp_path)
    original = _receive_and_start(
        runtime, "start-delivery-session.service-wire.json"
    )
    action_uid, armed = _only_action(runtime)
    assert armed["state"] == "ARMED"

    if unsafe_fact == "unchanged_boot":
        deadline = runtime.edge.get_work_slot()["context"][
            "delivery_result_deadline_monotonic_ms"
        ]
        monkeypatch.setattr(
            "work_manager._monotonic_ms", lambda: deadline + 1
        )
    else:
        boot_identity[0] = (
            "linux:40000000-0000-4000-8000-000000000002"
        )
    assert runtime.work.expire_fixed_frame_work() is True

    recovery = _delivery_recovery_command(
        original,
        recovery_uid="94000000-0000-4000-8000-000000000001",
        recovery_command_uid=(
            "94000000-0000-4000-8000-000000000002"
        ),
    )
    if unsafe_fact == "operator_confirmation":
        recovery["payload"]["motionAreaClearConfirmed"] = False
        recovery["payloadSha256"] = canonical_payload_sha256(
            recovery["payload"]
        )
    elif unsafe_fact == "unsafe_firmware":
        runtime.uart.firmware_status_result["safeFlags"] = 0x07
    elif unsafe_fact == "unhealthy_self_test":
        runtime.uart.self_test_result["communicationHealthy"] = False
    elif unsafe_fact == "pending_normal_result":
        runtime.uart.pending_business_result = True

    assert runtime.edge.receive_command(
        recovery["commandUid"], recovery["commandType"], recovery
    ) == "ACCEPTED"
    assert runtime.edge.claim_next_command()["command_uid"] == recovery[
        "commandUid"
    ]

    with pytest.raises(JobSafetyError) as raised:
        runtime.work.quarantine_delivery_recovery(recovery)

    assert raised.value.code == expected_code
    unresolved = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert unresolved["state"] == "ARMED"
    assert unresolved["confirmedOutcome"] is None
    assert unresolved["unknownEffectResolution"] is None
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.edge.get_work_slot()["work_state"] == (
        "RECOVERY_REQUIRED"
    )
    events = [
        json.loads(row["payload_json"])
        for row in runtime.edge.list_pending_events(100)
    ]
    assert not any(
        event["eventType"] == "DELIVERY_RECOVERY_QUARANTINED"
        for event in events
    )
    assert not any(
        event["eventType"] == "DELIVERY_COMPLETE" for event in events
    )

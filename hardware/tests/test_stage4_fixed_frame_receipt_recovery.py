from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from command_processor import CommandProcessor
from edge_store import EdgeStore
from job_safety import (
    JobSafetyError,
    PermanentJobSafety,
    PhysicalAction,
)
from local_control import LocalControlRemoteError, LocalControlUnavailable
from onenet_wire import canonical_payload_sha256, decode_service_command
from uart_link import compute_mcu_payload_sha256
from updater_store import UpdaterStore, UpdaterStoreError
from work_manager import WorkManager


def _activate_candidate(updater: UpdaterStore) -> None:
    status = updater.get_status()
    updater.activate_stage4_job_gate(
        {
            "operationUid": "90000000-0000-4000-8000-000000000003",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


class CountingCompatUart:
    compatibility_mode = True

    def __init__(self) -> None:
        self.query_count = 0
        self.snapshot = {
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

    def query_self_test(
        self,
        timeout_ms=3_000,
        on_result=None,
        dispatch_gate=None,
    ) -> dict[str, Any]:
        del timeout_ms
        if dispatch_gate is not None:
            dispatch_gate()
        self.query_count += 1
        result = dict(self.snapshot)
        if on_result is not None:
            on_result(result)
        return result


class NoopPhotoManager:
    def capture_open_photos(self, work_uid):
        del work_uid
        return True

    def capture_close_photos(self, work_uid):
        del work_uid
        return True

    def capture_clean_open_photos(self, work_uid):
        del work_uid
        return True

    def capture_clean_close_photos(self, work_uid):
        del work_uid
        return True


class FaultInjectingUpdaterClient:
    """Delegate to a real ledger while selectively losing local replies."""

    def __init__(
        self,
        store: UpdaterStore,
        *,
        live_commit_then_unavailable: int = 0,
        live_unavailable_before_commit: int = 0,
        get_unavailable: int = 0,
    ) -> None:
        self.store = store
        self.live_commit_then_unavailable = live_commit_then_unavailable
        self.live_unavailable_before_commit = (
            live_unavailable_before_commit
        )
        self.get_unavailable = get_unavailable
        self.action_calls: list[str] = []

    def restore(self) -> None:
        self.live_commit_then_unavailable = 0
        self.live_unavailable_before_commit = 0
        self.get_unavailable = 0

    def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.action_calls.append(action)
        if action == "GET_PHYSICAL_ACTION" and self.get_unavailable > 0:
            self.get_unavailable -= 1
            raise LocalControlUnavailable("simulated GET response outage")
        if (
            action == "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT"
            and self.live_unavailable_before_commit > 0
        ):
            self.live_unavailable_before_commit -= 1
            raise LocalControlUnavailable(
                "simulated live-confirmation request outage"
            )

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
        }
        try:
            result = operations[action](payload)
        except UpdaterStoreError as error:
            raise LocalControlRemoteError(
                error.code,
                str(error),
                "90000000-0000-4000-8000-000000000001",
            ) from error
        if (
            action == "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT"
            and self.live_commit_then_unavailable > 0
        ):
            self.live_commit_then_unavailable -= 1
            raise LocalControlUnavailable(
                "simulated committed live-confirmation response loss"
            )
        return result


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


def _configuration_command() -> dict[str, Any]:
    command = _wire_command("apply-configuration.service-wire.json")
    command["payload"]["config"]["mcuPayloadSha256"] = (
        compute_mcu_payload_sha256(command["payload"])
    )
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def _mark_configuration_applied(store: EdgeStore) -> None:
    command = _configuration_command()
    part_uids = [
        str(uuid.uuid4())
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


def _baseline_command() -> dict[str, Any]:
    command = _wire_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    command["payload"]["portNo"] = 1
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def _start_uncertain_baseline(
    tmp_path,
    *,
    remote_confirmation_commits: bool,
) -> tuple[
    EdgeStore,
    UpdaterStore,
    FaultInjectingUpdaterClient,
    PermanentJobSafety,
    CountingCompatUart,
    NoopPhotoManager,
    WorkManager,
    dict[str, Any],
]:
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()
    _mark_configuration_applied(edge)
    updater = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-receipt-recovery-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    client = FaultInjectingUpdaterClient(
        updater,
        live_commit_then_unavailable=(
            2 if remote_confirmation_commits else 0
        ),
        live_unavailable_before_commit=(
            0 if remote_confirmation_commits else 2
        ),
        # The immediate exact-fact adoption also uses the client's two
        # bounded attempts. Keep both unavailable so the business command
        # must enter durable recovery instead of completing on this stack.
        get_unavailable=2,
    )
    safety = PermanentJobSafety(client)
    uart = CountingCompatUart()
    photos = NoopPhotoManager()
    work = WorkManager(
        edge,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(edge, uart, work)
    command = _baseline_command()
    assert edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert processor.process_next()
    inbox = edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "JOB_GATE_UNAVAILABLE"
    slot = edge.get_work_slot()
    assert slot is not None
    pending = slot["context"]["pending_fixed_frame_query_result"]
    assert pending["action_key"] == "BASELINE:FIXED_FRAME_QUERY:0"
    assert pending["result"] == uart.snapshot
    assert uart.query_count == 1
    return (
        edge,
        updater,
        client,
        safety,
        uart,
        photos,
        work,
        command,
    )


def _assert_baseline_finished(
    edge: EdgeStore,
    updater: UpdaterStore,
    uart: CountingCompatUart,
    command: dict[str, Any],
) -> None:
    inbox = edge.get_command(command["commandUid"])
    assert inbox["state"] == "COMPLETED"
    assert inbox["result"] == {
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1_234,
        "compatibilitySource": "FRESH_F0_F1_SNAPSHOT",
    }
    events = [
        row
        for row in edge.list_pending_events(limit=100)
        if row["event_type"] == "BASELINE_MEASUREMENT_COMPLETE"
    ]
    assert len(events) == 1
    assert edge.get_bag_baseline(command["payload"]["bagUid"])[
        "weight_grams"
    ] == 1_234
    assert edge.get_work_slot() is None
    permit = updater.get_job_permit(
        {"permitUid": command["commandUid"]}
    )
    assert permit["state"] == "COMPLETED"
    assert updater.get_status()["jobGateState"] == "OPEN"
    # Reconciliation consumes the frozen F1 result. It must never issue a
    # second physical F0 query to rediscover an already-recorded fact.
    assert uart.query_count == 1


def test_committed_live_receipt_is_adopted_after_all_immediate_reads_fail(
    tmp_path,
) -> None:
    (
        edge,
        updater,
        client,
        _safety,
        uart,
        _photos,
        work,
        command,
    ) = _start_uncertain_baseline(
        tmp_path,
        remote_confirmation_commits=True,
    )
    action = updater.get_physical_action(
        {"actionUid": command["payload"]["measurementUid"]}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"

    client.restore()
    assert work.reconcile_pending_physical_action_confirmations()
    _assert_baseline_finished(edge, updater, uart, command)

    updater.close()
    edge.close()


def test_restarted_manager_adopts_exact_live_receipt_without_volatile_token(
    tmp_path,
) -> None:
    (
        edge,
        updater,
        client,
        safety,
        uart,
        photos,
        _old_work,
        command,
    ) = _start_uncertain_baseline(
        tmp_path,
        remote_confirmation_commits=True,
    )
    client.restore()
    restarted = WorkManager(
        edge,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    assert restarted._live_fixed_frame_dispatch_tokens == {}

    assert restarted.reconcile_pending_physical_action_confirmations()
    _assert_baseline_finished(edge, updater, uart, command)

    updater.close()
    edge.close()


def test_restarted_manager_keeps_armed_query_locked_without_volatile_token(
    tmp_path,
) -> None:
    (
        edge,
        updater,
        client,
        safety,
        uart,
        photos,
        _old_work,
        command,
    ) = _start_uncertain_baseline(
        tmp_path,
        remote_confirmation_commits=False,
    )
    client.restore()
    restarted = WorkManager(
        edge,
        uart,
        None,
        photos,
        job_safety=safety,
    )

    assert not restarted.reconcile_pending_physical_action_confirmations()
    assert updater.get_physical_action(
        {"actionUid": command["payload"]["measurementUid"]}
    )["state"] == "ARMED"
    assert updater.get_status()["jobGateState"] == "LOCKED"
    assert edge.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )
    assert edge.get_work_slot() is not None
    assert edge.get_bag_baseline(command["payload"]["bagUid"]) is None
    assert not [
        row
        for row in edge.list_pending_events(limit=100)
        if row["event_type"] == "BASELINE_MEASUREMENT_COMPLETE"
    ]
    assert uart.query_count == 1

    updater.close()
    edge.close()


class RecordingContextStore:
    def __init__(self, work_uid: str) -> None:
        self.work_uid = work_uid
        self.contexts: list[dict[str, Any]] = []

    def update_work_context(
        self,
        work_uid: str,
        context: dict[str, Any],
    ) -> None:
        assert work_uid == self.work_uid
        self.contexts.append(copy.deepcopy(context))


def _seed_armed_action(tmp_path) -> tuple[
    UpdaterStore,
    WorkManager,
    dict[str, Any],
    PhysicalAction,
    str,
]:
    updater = UpdaterStore(
        tmp_path / "conflict-updater.db",
        release_version="stage4-receipt-conflict-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    client = FaultInjectingUpdaterClient(updater)
    safety = PermanentJobSafety(client)
    command_uid = str(uuid.uuid4())
    work_uid = str(uuid.uuid4())
    action_uid = str(uuid.uuid4())
    command = {
        "commandUid": command_uid,
        "commandType": "TEST_PHYSICAL_COMMAND",
        "payload": {"portNo": 1},
    }
    permit = safety.request_job(
        command,
        work_type="DELIVERY",
        work_uid=work_uid,
    )
    safety.begin_job(
        permit,
        begin_uid=work_uid,
        digest=permit.request_digest_sha256,
    )
    action = PhysicalAction(
        action_uid=action_uid,
        receipt_uid=action_uid,
        action_key="DELIVERY:START:0",
        action_kind="START_DELIVERY_SESSION",
        action_digest_sha256="a" * 64,
    )
    dispatch_token = "stage4-conflict-token-0123456789abcdef"
    safety.prepare_physical_action(
        permit,
        action=action,
        dispatch_attempt_token=dispatch_token,
    )
    safety.arm_physical_action(
        action,
        dispatch_attempt_token=dispatch_token,
    )
    context = {
        "job_safety": {
            "permit_uid": permit.permit_uid,
            "work_uid": permit.work_uid,
            "command_uid": permit.command_uid,
            "work_type": permit.work_type,
            "request_digest_sha256": permit.request_digest_sha256,
            "begin_uid": work_uid,
            "completion_uid": command_uid,
            "actions": {
                action.action_key: {
                    "action_uid": action.action_uid,
                    "receipt_uid": action.receipt_uid,
                    "action_key": action.action_key,
                    "action_kind": action.action_kind,
                    "action_digest_sha256": action.action_digest_sha256,
                    "preparation_result": "PREPARED",
                    "dispatch_result": "ARMED",
                }
            },
        }
    }
    work = WorkManager(
        RecordingContextStore(work_uid),
        None,
        None,
        None,
        job_safety=safety,
    )
    return updater, work, context, action, dispatch_token


@pytest.mark.parametrize(
    ("changed_payload", "changed_outcome"),
    [
        ({"mcuEventSequence": 8}, "EXECUTED"),
        ({}, "FAILED_SAFE"),
    ],
)
def test_identity_bound_receipt_rejects_changed_fact_after_confirmation(
    tmp_path,
    changed_payload,
    changed_outcome,
) -> None:
    updater, work, context, action, _token = _seed_armed_action(tmp_path)
    payload = {
        "mcuCommandUid": action.action_uid,
        "mcuBootId": 42,
        "mcuEventSequence": 7,
        "result": "SUCCEEDED",
    }
    work._confirm_action_from_mcu_event(
        context,
        expected_action_key=action.action_key,
        expected_action_kind=action.action_kind,
        event_type="TEST_MCU_COMMAND_RESULT",
        payload=payload,
        outcome="EXECUTED",
    )
    changed = {**payload, **changed_payload}

    with pytest.raises(JobSafetyError) as raised:
        work._confirm_action_from_mcu_event(
            context,
            expected_action_key=action.action_key,
            expected_action_kind=action.action_kind,
            event_type="TEST_MCU_COMMAND_RESULT",
            payload=changed,
            outcome=changed_outcome,
        )

    assert raised.value.code == "PHYSICAL_ACTION_EVIDENCE_CONFLICT"
    remote = updater.get_physical_action({"actionUid": action.action_uid})
    assert remote["state"] == "CONFIRMED"
    assert remote["confirmedOutcome"] == "EXECUTED"
    assert remote["confirmationBasis"] == "MCU_IDENTITY_BOUND_FACT"
    updater.close()


def test_live_fixed_frame_receipt_rejects_changed_payload_after_confirmation(
    tmp_path,
) -> None:
    updater, work, context, action, token = _seed_armed_action(tmp_path)
    payload = {
        "queryStatus": "OK",
        "communicationHealthy": True,
        "portNo": 1,
        "weightValid": True,
        "weightGrams": 1_234,
        "rawFrameHex": "f1030004d20000f1",
    }
    work._confirm_live_fixed_frame_action(
        context,
        action_key=action.action_key,
        event_type="FIXED_FRAME_F0_F1_QUERY_RESULT",
        payload=payload,
        outcome="EXECUTED",
        dispatch_attempt_token=token,
    )

    with pytest.raises(JobSafetyError) as raised:
        work._confirm_live_fixed_frame_action(
            context,
            action_key=action.action_key,
            event_type="FIXED_FRAME_F0_F1_QUERY_RESULT",
            payload={**payload, "weightGrams": 1_235},
            outcome="EXECUTED",
            dispatch_attempt_token=token,
        )

    assert raised.value.code == "PHYSICAL_ACTION_EVIDENCE_CONFLICT"
    remote = updater.get_physical_action({"actionUid": action.action_uid})
    assert remote["state"] == "CONFIRMED"
    assert remote["confirmedOutcome"] == "EXECUTED"
    assert remote["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"
    updater.close()

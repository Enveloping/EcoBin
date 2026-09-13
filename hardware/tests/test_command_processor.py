import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from business_message_handler import BusinessMessageHandler
from cloud_transport import CloudServiceRequest
from command_processor import CommandProcessor
from device_identity import DeviceIdentity
from edge_store import (
    EdgeStore,
    FACTORY_SEAL_RETRYABLE_ERROR_CODES,
    FACTORY_SEAL_TERMINAL_ERROR_CODES,
)
from factory_seal.errors import FactorySealError
from job_safety import (
    JobPermit,
    JobSafetyError,
    PermanentJobSafety,
)
from local_control import LocalControlRemoteError, LocalControlUnavailable
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
    encode_event_post,
)
from uart_link import compute_mcu_payload_sha256
from work_manager import (
    CleanUnlockDecisionDeferred,
    WorkManager,
    _remaining_operation_window_ms,
)
from updater_store import UpdaterStore, UpdaterStoreError


def _activate_candidate(updater: UpdaterStore) -> None:
    status = updater.get_status()
    updater.activate_stage4_job_gate(
        {
            "operationUid": "90000000-0000-4000-8000-000000000001",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


class FakeUart:
    def __init__(self, trace=None):
        self.calls = []
        self.command_result = None
        self.trace = trace

    def apply_configuration(self, command, part_command_uids):
        self.calls.append((command, list(part_command_uids)))
        return {
            "acked": True,
            "commit_mcu_command_uid": part_command_uids[-1],
            "parts": [
                {
                    "message_name": "CONFIG_PART",
                    "mcu_command_uid": part_uid,
                    "acked": True,
                }
                for part_uid in part_command_uids
            ],
        }

    def send_command(self, message_name, values, *, mcu_command_uid=None):
        if self.trace is not None:
            self.trace.append(("uart", message_name))
        self.calls.append((message_name, dict(values), mcu_command_uid))
        if self.command_result is not None:
            return {
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
                **self.command_result,
            }
        return {
            "acked": True,
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            "disposition": "ACCEPTED",
        }

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
        if (
            self.command_result is not None
            and not self.command_result.get("acked")
            and self.command_result.get("error")
            in {
                "UART_CLOSED",
                "UART_NOT_READY",
                "COMMAND_EXPIRED",
                "MCU_FEATURE_NOT_SUPPORTED",
            }
        ):
            return {
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
                **self.command_result,
            }
        if dispatch_gate is not None:
            dispatch_gate()
        return self.send_command(
            message_name,
            values,
            mcu_command_uid=mcu_command_uid,
        )


class CrashBeforeDispatchGateUart(FakeUart):
    def send_command_before_deadline(self, *args, **kwargs):
        del args, kwargs
        raise SystemExit("simulated crash while waiting for UART lock")


class EndCleanControlUart(FakeUart):
    def __init__(self, *, end_result, before_end_send=None):
        super().__init__()
        self.end_result = dict(end_result)
        self.before_end_send = before_end_send

    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid=None,
        dispatch_deadline_monotonic,
        dispatch_gate=None,
    ):
        if message_name != "END_CLEAN_BEFORE_UNLOCK":
            return super().send_command_before_deadline(
                message_name,
                values,
                mcu_command_uid=mcu_command_uid,
                dispatch_deadline_monotonic=dispatch_deadline_monotonic,
                dispatch_gate=dispatch_gate,
            )
        assert time.monotonic() < dispatch_deadline_monotonic
        assert dispatch_gate is None
        if self.before_end_send is not None:
            self.before_end_send()
        self.calls.append((message_name, dict(values), mcu_command_uid))
        return {
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            **self.end_result,
        }


class FakeJobSafety:
    enabled = True

    def __init__(
        self,
        trace,
        store,
        *,
        request_error=None,
        begin_errors=None,
        authorize_errors=None,
        complete_errors=None,
    ):
        self.trace = trace
        self.store = store
        self.request_error = request_error
        self.begin_errors = list(begin_errors or [])
        self.authorize_errors = list(authorize_errors or [])
        self.complete_errors = list(complete_errors or [])
        self.confirmation_digests = []
        self.confirmation_bases = []
        self.completion_digests = []
        self.actions = {}

    @staticmethod
    def new_uid():
        return str(uuid.uuid4())

    def request_job(self, command, *, work_type, work_uid):
        self.trace.append(("safety", "request"))
        if self.request_error is not None:
            raise self.request_error
        return JobPermit(
            permit_uid=self.new_uid(),
            work_uid=work_uid,
            command_uid=command["commandUid"],
            work_type=work_type,
            request_digest_sha256="a" * 64,
        )

    def begin_job(self, permit, *, begin_uid, digest):
        del permit, begin_uid, digest
        assert self.store.get_work_slot() is not None
        self.trace.append(("safety", "begin"))
        if self.begin_errors:
            raise self.begin_errors.pop(0)

    def abandon_job(self, permit, *, disposition_uid, evidence_sha256):
        del permit, disposition_uid, evidence_sha256
        self.trace.append(("safety", "abandon"))

    def prepare_physical_action(
        self,
        permit,
        *,
        action,
        dispatch_attempt_token,
    ):
        slot = self.store.get_work_slot()
        if slot is not None:
            assert action.action_key in slot["context"]["job_safety"]["actions"]
        if action.action_uid not in self.actions:
            self.actions[action.action_uid] = {
                "actionUid": action.action_uid,
                "permitUid": permit.permit_uid,
                "workUid": permit.work_uid,
                "commandUid": permit.command_uid,
                "actionKey": action.action_key,
                "actionKind": action.action_kind,
                "actionDigestSha256": action.action_digest_sha256,
                "receiptUid": action.receipt_uid,
                "state": "PREPARED",
                "confirmedOutcome": None,
                "confirmationBasis": None,
                "evidenceDigestSha256": None,
                "dispatchAttemptToken": dispatch_attempt_token,
            }
        elif (
            self.actions[action.action_uid].get("dispatchAttemptToken")
            != dispatch_attempt_token
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_DISPATCH_TOKEN_MISMATCH",
                "prepared action belongs to a different live call",
            )
        self.trace.append(("safety", "prepare"))

    def arm_physical_action(self, action, *, dispatch_attempt_token):
        remote = self.actions[action.action_uid]
        self.trace.append(("safety", "arm"))
        if remote.get("dispatchAttemptToken") != dispatch_attempt_token:
            raise JobSafetyError(
                "PHYSICAL_ACTION_DISPATCH_TOKEN_MISMATCH",
                "live dispatch attempt does not own this action",
            )
        if self.authorize_errors:
            error = self.authorize_errors.pop(0)
            remote["state"] = "ARMED"
            remote["dispatchAttemptToken"] = dispatch_attempt_token
            raise error
        if remote["state"] != "PREPARED":
            raise JobSafetyError(
                "PHYSICAL_ACTION_STATE_CONFLICT",
                "physical action is not prepared",
            )
        remote["state"] = "ARMED"
        remote["dispatchAttemptToken"] = dispatch_attempt_token

    def cancel_prepared_physical_action(
        self,
        action,
        *,
        dispatch_attempt_token,
        evidence_sha256,
    ):
        remote = self.actions[action.action_uid]
        if (
            remote["state"] != "PREPARED"
            or remote.get("dispatchAttemptToken")
            != dispatch_attempt_token
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_STATE_CONFLICT",
                "physical action is not prepared",
            )
        remote.update(
            state="CONFIRMED",
            confirmedOutcome="NOT_EXECUTED",
            confirmationBasis="PREPARED_NOT_ARMED",
            evidenceDigestSha256=evidence_sha256,
        )
        self.trace.append(("safety", "cancel"))

    def abort_physical_action_dispatch(
        self,
        action,
        *,
        dispatch_attempt_token,
        evidence_sha256,
    ):
        remote = self.actions[action.action_uid]
        if (
            remote["state"] != "ARMED"
            or remote.get("dispatchAttemptToken")
            != dispatch_attempt_token
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_DISPATCH_TOKEN_MISMATCH",
                "live dispatch attempt does not own this action",
            )
        remote.update(
            state="CONFIRMED",
            confirmedOutcome="NOT_EXECUTED",
            confirmationBasis="LIVE_DISPATCH_NOT_WRITTEN",
            evidenceDigestSha256=evidence_sha256,
        )
        self.trace.append(("safety", "abort"))

    def get_physical_action(self, action_uid):
        self.trace.append(("safety", "get_action"))
        if action_uid not in self.actions:
            raise JobSafetyError(
                "PHYSICAL_ACTION_NOT_FOUND",
                "physical action does not exist",
            )
        return dict(self.actions[action_uid])

    def confirm_physical_action(
        self,
        action,
        *,
        outcome,
        evidence_sha256,
        confirmation_basis,
    ):
        remote = self.actions[action.action_uid]
        assert remote["state"] == "ARMED"
        remote.update(
            state="CONFIRMED",
            confirmedOutcome=outcome,
            confirmationBasis=confirmation_basis,
            evidenceDigestSha256=evidence_sha256,
        )
        self.confirmation_digests.append(evidence_sha256)
        self.confirmation_bases.append(confirmation_basis)
        self.trace.append(("safety", "confirm"))

    def complete_job(
        self,
        permit,
        *,
        completion_uid,
        outcome,
        completion_digest_sha256,
    ):
        del permit, completion_uid, outcome
        self.completion_digests.append(completion_digest_sha256)
        self.trace.append(("safety", "complete"))
        if self.complete_errors:
            raise self.complete_errors.pop(0)


class StoreBackedUpdaterClient:
    """Exercise the real permanent ledger without a platform Unix socket."""

    def __init__(self, store):
        self.store = store

    def request(self, action, payload):
        operations = {
            "REQUEST_JOB_PERMIT": self.store.request_job_permit,
            "BEGIN_JOB": self.store.begin_job,
            "GET_JOB_PERMIT": self.store.get_job_permit,
            "ABANDON_JOB_PERMIT": self.store.abandon_job_permit,
            "PREPARE_PHYSICAL_ACTION": (
                self.store.prepare_physical_action
            ),
            "ARM_PHYSICAL_ACTION": self.store.arm_physical_action,
            "CANCEL_PREPARED_PHYSICAL_ACTION": (
                self.store.cancel_prepared_physical_action
            ),
            "ABORT_PHYSICAL_ACTION_DISPATCH": (
                self.store.abort_physical_action_dispatch
            ),
            "GET_PHYSICAL_ACTION": self.store.get_physical_action,
            "PREPARE_NATIVE_RECOVERY_CLOSE": self.store.prepare_native_recovery_close,
            "PREPARE_NATIVE_RECOVERY_CLOSE_SUCCESSOR": self.store.prepare_native_recovery_close_successor,
            "GET_NATIVE_RECOVERY_CLOSE": self.store.get_native_recovery_close,
            "RETIRE_NATIVE_RECOVERY_CLOSE": self.store.retire_native_recovery_close,
            "RETIRE_NATIVE_RECOVERY_CLOSE_SUCCESSOR": self.store.retire_native_recovery_close_successor,
            "WITHDRAW_NATIVE_RECOVERY_CLOSE_DISPATCH": self.store.withdraw_native_recovery_close_dispatch,
            "WITHDRAW_NATIVE_RECOVERY_CLOSE_SUCCESSOR_DISPATCH": self.store.withdraw_native_recovery_close_successor_dispatch,
            "GET_NATIVE_RECOVERY_CLOSE_DISPOSITION": self.store.get_native_recovery_close_disposition,
            "ISOLATE_NATIVE_RECOVERY_CLOSE_AFTER_REBOOT": self.store.isolate_native_recovery_close_after_reboot,
            "ISOLATE_NATIVE_RECOVERY_CLOSE_SUCCESSOR_AFTER_REBOOT": self.store.isolate_native_recovery_close_successor_after_reboot,
            "CONFIRM_PHYSICAL_ACTION": (
                self.store.confirm_physical_action
            ),
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT": (
                self.store.confirm_live_physical_action_result
            ),
            "COMPLETE_JOB": self.store.complete_job,
        }
        try:
            return operations[action](payload)
        except UpdaterStoreError as error:
            raise LocalControlRemoteError(
                error.code,
                str(error),
                "00000000-0000-4000-8000-000000000001",
            ) from error


class LoseFirstArmResponseClient:
    """Commit ARM once, then emulate a lost local-socket response."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.arm_requests = []

    def request(self, action, payload):
        if action == "ARM_PHYSICAL_ACTION":
            self.arm_requests.append(dict(payload))
            result = self.delegate.request(action, payload)
            if len(self.arm_requests) == 1:
                raise LocalControlUnavailable("ARM response lost")
            return result
        return self.delegate.request(action, payload)


def make_real_job_safety(tmp_path):
    updater = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    return updater, PermanentJobSafety(StoreBackedUpdaterClient(updater))


class FakeCompatUart(FakeUart):
    compatibility_mode = True

    def __init__(self, self_test_result=None, trace=None):
        super().__init__(trace=trace)
        self.self_test_calls = []
        self.self_test_result = self_test_result or {
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

    def apply_configuration(self, command, part_command_uids):
        raise AssertionError(
            "fixed-frame compatibility must not project config to MCU"
        )

    def query_self_test(
        self,
        timeout_ms=3000,
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


class FakePhotoManager:
    def __init__(
        self,
        queue_result=True,
        trace=None,
        capture_result=True,
    ):
        self.captured = []
        self.queue_result = queue_result
        self.trace = trace
        self.capture_result = capture_result

    def _capture(self, label, work_uid):
        if self.trace is not None:
            self.trace.append(("photo", label))
        self.captured.append((label, work_uid))
        return self.capture_result

    def capture_open_photos(self, work_uid):
        return self._capture("open", work_uid)

    def capture_close_photos(self, work_uid):
        return self._capture("close", work_uid)

    def capture_clean_open_photos(self, work_uid):
        return self._capture("clean_open", work_uid)

    def capture_clean_close_photos(self, work_uid):
        return self._capture("clean_close", work_uid)

    def capture_open_photos_async(self, work_uid):
        if self.trace is not None:
            self.trace.append(("photo", "open_async"))
        self.captured.append(("open", work_uid))
        return self.queue_result

    def capture_close_photos_async(self, work_uid):
        if self.trace is not None:
            self.trace.append(("photo", "close_async"))
        self.captured.append(("close", work_uid))
        return self.queue_result

    def capture_clean_photos_async(self, work_uid):
        if self.trace is not None:
            self.trace.append(("photo", "clean_all_async"))
        self.captured.append(("clean", work_uid))
        return self.queue_result

    def get_slot_urls(self, work_uid):
        return {"CLOSE_OUTSIDE": "cos://after.jpg"}


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def valid_configuration_command():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        command["payload"]
    )
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def valid_service_command(example_name):
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
    command["issuedAt"] = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def valid_end_clean_service_invocation():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "end-clean-before-unlock.service-wire.json",
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    service_id = body["identifier"]
    params = copy.deepcopy(body["params"])
    now = datetime.now(timezone.utc)
    params["issuedAt"] = now.isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    params["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    decoded = decode_service_command(service_id, params)
    params["payloadSha256"] = canonical_payload_sha256(decoded["payload"])
    return service_id, params, decode_service_command(service_id, params)


def invoke_business_service(handler, service_id, params):
    return handler.handle_service_request(
        CloudServiceRequest(
            delivery_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            service_id=service_id,
            params=params,
            received_at=None,
            clock_quality="UNAVAILABLE",
        )
    )


def clean_preunlock_event(start, start_action_uid, measurement_uid):
    return {
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 0x32,
        "source_tx_sequence": 1,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1_000,
            "mcuCommandUid": start_action_uid,
            "operationUid": start["payload"]["operationUid"],
            "portNo": start["payload"]["portNo"],
            "measurementUid": measurement_uid,
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 50_000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1_000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    }


def mark_configuration_applied(store):
    command = valid_configuration_command()
    part_uids = [
        str(__import__("uuid").uuid4())
        for _ in range(len(command["payload"]["ports"]) + 3)
    ]
    assert store.save_configuration_edge(command, part_uids) == "ACCEPTED"
    assert store.apply_configuration_result({
        "mcuCommandUid": part_uids[-1],
        "applicationUid": command["payload"]["applicationUid"],
        "status": "APPLIED",
        "configVersion": command["payload"]["config"]["version"],
        "contentSha256": command["payload"]["config"]["contentSha256"],
        "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
        "faultCode": "NONE",
    }) == "ACCEPTED"
    store.save_fixed_frame_self_test({
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
    })
    return command


def valid_compat_service_command(example_name):
    command = valid_service_command(example_name)
    if "portNo" in command["payload"]:
        command["payload"]["portNo"] = 1
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


class FakeMcuFirmwareUpdater:
    def __init__(self):
        self.calls = []

    def queue_cloud(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "disposition": "QUEUED",
            "updateUid": "8c000000-0000-4000-8000-000000000004",
            "deploymentUid": kwargs["deployment_uid"],
            "state": "QUEUED",
            "manifest": {
                "firmwareVersion": kwargs["firmware_version"],
                "firmwareVersionCode": kwargs["firmware_version_code"],
                "firmwareIdentityHex": kwargs["firmware_identity_hex"],
            },
        }


class FailingFactorySealAuthorizer:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def authorize(self, command):
        self.calls.append(command["commandUid"])
        raise self.error


class CompletingFactorySealAuthorizer:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def authorize(self, command):
        self.calls.append(command["commandUid"])
        assert self.store.complete_command(
            command["commandUid"],
            {"disposition": "FACTORY_SEAL_AUTHORIZED"},
        )


def persist_factory_seal_received_before_expiry(store, command):
    """Reconstruct a command accepted before its now-past deadline."""

    now = datetime.now(timezone.utc)
    command["issuedAt"] = (
        now - timedelta(minutes=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now - timedelta(minutes=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    received_at = (
        now - timedelta(seconds=90)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with store.transaction():
        store._conn.execute(
            "UPDATE command_inbox SET received_at=? WHERE command_uid=?",
            (received_at, command["commandUid"]),
        )


class StoreFactorySealAuthorizer:
    def __init__(self, store):
        self.store = store

    def authorize(self, command):
        return self.store.accept_factory_seal_authorization(
            command,
            {
                "imageReleaseId": "image-release-1",
                "imageReleaseSha256": "b" * 64,
                "factoryReportSha256": "c" * 64,
            },
        )


@pytest.mark.parametrize(
    "error_code",
    sorted(FACTORY_SEAL_RETRYABLE_ERROR_CODES),
)
def test_factory_seal_repairable_error_waits_for_duplicate_without_rejection(
    tmp_path,
    error_code,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    authorizer = FailingFactorySealAuthorizer(
        FactorySealError(error_code)
    )
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=authorizer,
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == error_code
    assert authorizer.calls == [command["commandUid"]]
    assert store.list_pending_events() == []
    store.close()


def test_factory_seal_unknown_internal_error_is_retryable_and_not_rejected(
    tmp_path,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=FailingFactorySealAuthorizer(
            RuntimeError("temporary sqlite failure")
        ),
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == "FACTORY_SEAL_RETRYABLE_FAILURE"
    assert store.list_pending_events() == []
    store.close()


def test_factory_seal_receipt_read_io_error_remains_retryable(
    tmp_path,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    def fail_receipt_read(_command):
        raise OSError("temporary receipt read failure")

    store.validate_claimed_factory_seal_command = fail_receipt_read
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=CompletingFactorySealAuthorizer(store),
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == "FACTORY_SEAL_RETRYABLE_FAILURE"
    assert store.list_pending_events() == []
    store.close()


def test_factory_seal_queued_before_deadline_executes_after_deadline(
    tmp_path,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    persist_factory_seal_received_before_expiry(store, command)
    authorizer = CompletingFactorySealAuthorizer(store)
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=authorizer,
    )

    assert processor.process_next()

    assert authorizer.calls == [command["commandUid"]]
    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    store.close()


def test_factory_seal_restart_keeps_original_acceptance_deadline_fact(
    tmp_path,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    persist_factory_seal_received_before_expiry(store, command)
    assert store.claim_next_command()["command_uid"] == command["commandUid"]
    assert store.recover_interrupted_commands()["factory_seal_requeued"] == 1
    authorizer = CompletingFactorySealAuthorizer(store)
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=authorizer,
    )

    assert processor.process_next()

    assert authorizer.calls == [command["commandUid"]]
    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    store.close()


def test_expired_factory_seal_inserted_locally_is_terminally_rejected(
    tmp_path,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    now = datetime.now(timezone.utc)
    command["issuedAt"] = (
        now - timedelta(minutes=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now - timedelta(minutes=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    authorizer = CompletingFactorySealAuthorizer(store)
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=authorizer,
    )

    assert processor.process_next()

    assert authorizer.calls == []
    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID"
    events = store.list_pending_events()
    assert len(events) == 1
    observation = json.loads(events[0]["payload_json"])
    assert observation["payload"]["stage"] == "REJECTED"
    assert observation["payload"]["errorCode"] == (
        "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID"
    )
    store.close()


def test_other_expired_command_still_uses_execution_time(tmp_path):
    store = make_store(tmp_path)
    command = valid_configuration_command()
    now = datetime.now(timezone.utc)
    command["issuedAt"] = (
        now - timedelta(minutes=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now - timedelta(minutes=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    uart = FakeUart()
    processor = CommandProcessor(store, uart)

    assert processor.process_next()

    assert uart.calls == []
    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == "COMMAND_EXPIRED"
    store.close()


@pytest.mark.parametrize(
    "error_code",
    sorted(FACTORY_SEAL_TERMINAL_ERROR_CODES),
)
def test_factory_seal_deterministic_error_emits_terminal_rejection(
    tmp_path,
    error_code,
):
    store = make_store(tmp_path)
    command = valid_service_command(
        "authorize-factory-seal.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=FailingFactorySealAuthorizer(
            FactorySealError(error_code)
        ),
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == error_code
    events = store.list_pending_events()
    assert len(events) == 1
    observation = json.loads(events[0]["payload_json"])
    assert observation["payload"] == {
        "observedCommandType": "AUTHORIZE_FACTORY_SEAL",
        "stage": "REJECTED",
        "mcuCommandUid": None,
        "errorCode": error_code,
    }
    store.close()


def test_factory_seal_processor_rejects_a_after_newer_evidence_b(
    tmp_path,
):
    store = make_store(tmp_path)
    device_name = "SN-CONTRACT-0001"
    bag_digest = "a" * 64

    def record_evidence() -> dict:
        acceptance_command = {
            "commandUid": str(uuid.uuid4()),
            "commandType": "REQUEST_DEVICE_ACCEPTANCE",
            "targetDeviceName": device_name,
        }
        store.receive_command(
            acceptance_command["commandUid"],
            acceptance_command["commandType"],
            acceptance_command,
        )
        store.claim_next_command()
        return store.complete_device_acceptance(
            acceptance_command,
            {
                "evidenceSchemaVersion": 3,
                "challengeUid": str(uuid.uuid4()),
                "factoryBagRevision": 2,
                "factoryBagSetSha256": bag_digest,
            },
        )

    evidence_a = record_evidence()
    evidence_b = record_evidence()

    def seal_command(evidence: dict) -> dict:
        command = valid_service_command(
            "authorize-factory-seal.service-wire.json"
        )
        command["commandUid"] = str(uuid.uuid4())
        command["payload"].update({
            "acceptanceEvidenceUid": evidence["eventUid"],
            "acceptanceChallengeUid": evidence["payload"][
                "challengeUid"
            ],
            "acceptanceEvidenceSha256": evidence["payloadSha256"],
            "factoryBagRevision": evidence["payload"][
                "factoryBagRevision"
            ],
            "factoryBagSetSha256": evidence["payload"][
                "factoryBagSetSha256"
            ],
        })
        command["payloadSha256"] = canonical_payload_sha256(
            command["payload"]
        )
        return command

    processor = CommandProcessor(
        store,
        FakeUart(),
        factory_seal_authorizer=StoreFactorySealAuthorizer(store),
    )
    stale = seal_command(evidence_a)
    store.receive_command(
        stale["commandUid"],
        stale["commandType"],
        stale,
    )
    assert processor.process_next()
    stale_row = store.get_command(stale["commandUid"])
    assert stale_row["state"] == "REJECTED"
    assert stale_row["last_error"] == "ACCEPTANCE_EVIDENCE_NOT_LATEST"

    current = seal_command(evidence_b)
    store.receive_command(
        current["commandUid"],
        current["commandType"],
        current,
    )
    assert processor.process_next()
    assert store.get_command(current["commandUid"])[
        "state"
    ] == "COMPLETED"
    authorization = store._conn.execute(
        "SELECT evidence_event_uid FROM factory_seal_authorization"
    ).fetchone()
    assert authorization["evidence_event_uid"] == evidence_b["eventUid"]
    store.close()


def test_mcu_firmware_command_uses_volatile_grant_and_queues_updater(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    updater = FakeMcuFirmwareUpdater()
    command = valid_service_command(
        "start-mcu-firmware-update.service-wire.json"
    )
    command["cosGrant"]["expiresAt"] = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    trusted = {
        field: command["cosGrant"][field]
        for field in ("bucket", "region", "baseUrl")
    }
    grant = command["cosGrant"]
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    processor = CommandProcessor(
        store,
        uart,
        trusted_cos_environment=trusted,
        mcu_firmware_updater=updater,
    )
    assert processor.offer_cos_grant(command["commandUid"], grant)

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "COMPLETED"
    assert row["payload"]["cosGrant"] is None
    assert row["result"]["state"] == "QUEUED"
    assert updater.calls[0]["deployment_uid"] == command["payload"][
        "deploymentUid"
    ]
    assert updater.calls[0]["release_uid"] == command["payload"]["releaseUid"]
    assert updater.calls[0]["cos_grant"] == grant
    store.close()


def test_mcu_firmware_command_without_volatile_grant_fails_closed(tmp_path):
    store = make_store(tmp_path)
    command = valid_service_command(
        "start-mcu-firmware-update.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    processor = CommandProcessor(
        store,
        FakeUart(),
        mcu_firmware_updater=FakeMcuFirmwareUpdater(),
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == "FIRMWARE_GRANT_NOT_AVAILABLE"

    command["cosGrant"]["expiresAt"] = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert processor.offer_cos_grant(
        command["commandUid"],
        command["cosGrant"],
    )
    assert store.get_command(command["commandUid"])["state"] == "PENDING"
    store.close()


def test_missing_update_lines_reject_after_grant_loss_and_expiry(tmp_path):
    store = make_store(tmp_path)
    command = valid_service_command(
        "start-mcu-firmware-update.service-wire.json"
    )
    command["cosGrant"]["expiresAt"] = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["issuedAt"] = (
        datetime.now(timezone.utc) - timedelta(minutes=20)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    trusted = {
        field: command["cosGrant"][field]
        for field in ("bucket", "region", "baseUrl")
    }
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    processor = CommandProcessor(
        store,
        FakeUart(),
        trusted_cos_environment=trusted,
        mcu_firmware_updater=None,
        device_name="SN-TEST-1",
    )
    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "FAILED"
    assert row["last_error"] == "MCU_REMOTE_UPDATE_UNAVAILABLE"
    update = store.get_mcu_firmware_update_by_deployment(
        command["payload"]["deploymentUid"]
    )
    assert update["state"] == "REJECTED"
    assert (
        update["last_error_code"]
        == "MCU_REMOTE_UPDATE_UNAVAILABLE"
    )
    assert store.get_maintenance_lock() is None
    events = [
        json.loads(item["payload_json"])
        for item in store.list_pending_events()
        if item["event_type"] == "MCU_FIRMWARE_UPDATE_PROGRESS"
    ]
    assert [event["payload"]["stage"] for event in events] == [
        "REJECTED"
    ]
    store.close()


def test_apply_configuration_consumes_inbox_and_waits_for_mcu_result(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    assert configuration["state"] == "WAITING_MCU_RESULT"
    assert len(configuration["part_command_uids"]) == len(command["payload"]["ports"]) + 3
    events = store._conn.execute(
        "SELECT * FROM event_outbox WHERE event_type='CONFIGURATION_PROGRESS'"
    ).fetchall()
    assert len(events) == 1
    edge_saved = json.loads(events[0]["payload_json"])
    assert edge_saved["payload"]["stage"] == "EDGE_SAVED"
    store.close()


def test_configuration_apply_result_completes_command_and_creates_event(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    commit_uid = configuration["part_command_uids"][-1]

    processor.process_mcu_event({
        "message_name": "CONFIG_APPLY_RESULT",
        "message_type": 20,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": commit_uid,
            "applicationUid": command["payload"]["applicationUid"],
            "status": "APPLIED",
            "configVersion": command["payload"]["config"]["version"],
            "contentSha256": command["payload"]["config"]["contentSha256"],
            "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
            "faultCode": "NONE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED"
    assert store.get_state("applied_config_version") == "8"
    events = store._conn.execute(
        "SELECT payload_json FROM event_outbox "
        "WHERE event_type='CONFIGURATION_PROGRESS' ORDER BY edge_event_sequence"
    ).fetchall()
    assert [json.loads(row["payload_json"])["payload"]["stage"] for row in events] == [
        "EDGE_SAVED",
        "APPLIED",
    ]
    store.close()


def test_invalid_previously_accepted_command_is_failed_without_uart(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    invalid = valid_configuration_command()
    invalid["commandUid"] = "12"
    store.receive_command("12", "APPLY_CONFIGURATION", invalid)

    processor.process_next()

    command = store.get_command("12")
    assert command["state"] == "FAILED"
    assert command["last_error"] == "INVALID_COMMAND_IDENTITY"
    assert uart.calls == []
    store.close()


def test_start_delivery_is_persisted_before_waiting_for_mcu_result(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    slot = store.get_work_slot()
    assert slot["work_type"] == "DELIVERY"
    assert slot["work_uid"] == command["payload"]["sessionUid"]
    message_name, values, command_uid = uart.calls[0]
    assert message_name == "START_DELIVERY_SESSION"
    assert command_uid == inbox["mcu_command_uid"]
    assert values["deliveryAutoCloseMs"] == 120000
    assert values["startExecutionWindowMs"] > 0
    store.close()


def test_candidate_permit_and_action_are_durable_before_delivery_uart(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    uart = FakeUart(trace=trace)
    safety = FakeJobSafety(trace, store)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    safety_and_uart = [
        entry
        for entry in trace
        if entry[0] in {"safety", "uart"}
    ]
    assert safety_and_uart == [
        ("safety", "request"),
        ("safety", "begin"),
        ("safety", "prepare"),
        ("safety", "arm"),
        ("uart", "START_DELIVERY_SESSION"),
    ]
    slot = store.get_work_slot()
    assert slot is not None
    assert "DELIVERY:START:0" in slot["context"]["job_safety"]["actions"]
    store.close()


@pytest.mark.parametrize("preflight_error", ["UART_CLOSED", "COMMAND_EXPIRED"])
def test_uart_preflight_failure_cancels_prepared_action_without_opening_gate(
    tmp_path,
    preflight_error,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    uart = FakeUart(trace=trace)
    uart.command_result = {"acked": False, "error": preflight_error}
    safety = FakeJobSafety(trace, store)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )

    result = work.start_delivery_command(command)

    assert result["acked"] is False
    assert [item for item in trace if item[0] in {"safety", "uart"}] == [
        ("safety", "request"),
        ("safety", "begin"),
        ("safety", "prepare"),
        ("safety", "cancel"),
        ("safety", "get_action"),
        ("safety", "complete"),
    ]
    assert uart.calls == []
    remote = next(iter(safety.actions.values()))
    assert remote["state"] == "CONFIRMED"
    assert remote["confirmedOutcome"] == "NOT_EXECUTED"
    assert remote["confirmationBasis"] == "PREPARED_NOT_ARMED"
    store.close()


def test_lost_arm_response_retries_same_token_and_writes_uart_once(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    updater = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    client = LoseFirstArmResponseClient(StoreBackedUpdaterClient(updater))
    safety = PermanentJobSafety(client)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )

    result = work.start_delivery_command(command)

    assert result["acked"] is True
    assert len(uart.calls) == 1
    assert len(client.arm_requests) == 2
    assert client.arm_requests[0] == client.arm_requests[1]
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 1
    action_uid = store.get_work_slot()["context"]["job_safety"][
        "actions"
    ]["DELIVERY:START:0"]["action_uid"]
    remote = updater.get_physical_action({"actionUid": action_uid})
    assert remote["state"] == "ARMED"
    assert remote["mayExecute"] is False
    updater.close()
    store.close()


def test_local_unknown_cannot_turn_remote_armed_action_into_not_executed(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    uart.command_result = {"acked": False, "error": "TIMEOUT"}
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()
    slot = store.get_work_slot()
    assert slot is not None
    action = slot["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]
    action_uid = action["action_uid"]
    assert updater.get_physical_action({"actionUid": action_uid})[
        "state"
    ] == "ARMED"

    # Model an older edge.db snapshot which only remembers an uncertain
    # preparation.  This rollbackable local value is not negative evidence.
    action.pop("dispatch_result", None)
    action["preparation_result"] = "UNKNOWN"
    store.update_work_context(slot["work_uid"], slot["context"])

    assert work.reconcile_pre_action_job_safety_failure() is False
    remote = updater.get_physical_action({"actionUid": action_uid})
    assert remote["state"] == "ARMED"
    assert remote["confirmedOutcome"] is None
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 1
    assert store.get_work_slot() is not None
    assert len(uart.calls) == 1
    updater.close()
    store.close()


def test_restart_keeps_remote_prepared_action_for_manual_recovery(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    uart = CrashBeforeDispatchGateUart(trace=trace)
    safety = FakeJobSafety(trace, store)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    with pytest.raises(SystemExit, match="waiting for UART lock"):
        processor.process_next()

    slot = store.get_work_slot()
    assert slot is not None
    action = slot["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]
    remote = safety.actions[action["action_uid"]]
    assert remote["state"] == "PREPARED"
    assert uart.calls == []
    assert ("safety", "arm") not in trace

    recovered = store.recover_interrupted_commands()
    assert recovered["physical_failed"] == 1
    assert work.reconcile_pre_action_job_safety_failure() is False
    assert remote["state"] == "PREPARED"
    assert remote["confirmedOutcome"] is None
    assert ("safety", "cancel") not in trace
    assert ("safety", "complete") not in trace
    assert uart.calls == []
    assert store.get_work_slot() is not None
    assert store.get_command(command["commandUid"])["state"] == "FAILED"
    store.close()


def test_real_permanent_ledger_allows_delivery_only_after_prior_mcu_fact(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )

    assert work.start_delivery_command(command)["acked"] is True
    session_uid = command["payload"]["sessionUid"]
    start_uid = uart.calls[-1][2]
    preopen_uid = "52000000-0000-4000-8000-000000000091"
    work.handle_mcu_event(
        {
            "message_name": "WORK_PREOPEN_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_uid,
                "sessionUid": session_uid,
                "portNo": command["payload"]["portNo"],
                "roundIndex": 1,
                "measurementUid": preopen_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    authorize_uid = uart.calls[-1][2]
    assert uart.calls[-1][0] == "AUTHORIZE_DELIVERY_FIRST_OPEN"
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 1

    work.handle_mcu_event(
        {
            "message_name": "DELIVERY_DOOR_COMMAND_RESULT",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 2_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": command["payload"]["portNo"],
                "roundIndex": 1,
                "command": "OPEN",
                "outputStatus": "COMMAND_DISPATCHED",
                "physicalDoorStateBasis": "NOT_OBSERVABLE",
                "faultCode": "NONE",
            },
        }
    )
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 0
    resolved = updater.get_physical_action(
        {"actionUid": authorize_uid}
    )
    assert resolved["confirmationBasis"] == "MCU_IDENTITY_BOUND_FACT"

    post_uid = "52000000-0000-4000-8000-000000000092"
    work.handle_mcu_event(
        {
            "message_name": "WORK_POSTCLOSE_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 3,
                "uptimeMs": 3_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": command["payload"]["portNo"],
                "roundIndex": 1,
                "measurementUid": post_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_500,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    work.handle_mcu_event(
        {
            "message_name": "DELIVERY_SELECTION",
            "payload": {
                "sessionUid": session_uid,
                "portNo": command["payload"]["portNo"],
                "roundIndex": 1,
                "postCloseMeasurementUid": post_uid,
                "selection": "END",
            },
        }
    )
    work.finalize_delivery(session_uid)

    status = updater.get_status()
    assert status["activeJobPermitCount"] == 0
    assert status["unreconciledPhysicalActionCount"] == 0
    assert status["jobGateState"] == "OPEN"
    assert store.get_work_slot() is None
    updater.close()
    store.close()


def test_real_permanent_ledger_clean_flow_confirms_each_physical_action(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    store.receive_command(
        command["commandUid"], command["commandType"], command
    )
    assert work.start_clean_command(command)["acked"] is True
    operation_uid = command["payload"]["operationUid"]
    port_no = command["payload"]["portNo"]
    start_uid = uart.calls[-1][2]
    preunlock_uid = "53000000-0000-4000-8000-000000000091"

    work.handle_mcu_event(
        {
            "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_uid,
                "operationUid": operation_uid,
                "portNo": port_no,
                "measurementUid": preunlock_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    unlock_uid = uart.calls[-1][2]
    assert uart.calls[-1][0] == "UNLOCK_CLEAN_DOOR"
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 1

    work.handle_mcu_event(
        {
            "message_name": "CLEAN_LOCK_POWER_CHANGED",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 2_000,
                "mcuCommandUid": unlock_uid,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 0,
                "lockPowerState": "ENERGIZED",
                "solenoidHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 0

    work.handle_mcu_event(
        {
            "message_name": "CLEAN_FINISH_REQUESTED",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 3,
                "uptimeMs": 3_000,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 1,
            },
        }
    )
    final_uid = "54000000-0000-4000-8000-000000000091"
    work.handle_mcu_event(
        {
            "message_name": "CLEAN_FINAL_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 4,
                "uptimeMs": 4_000,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 1,
                "measurementUid": final_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 2_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    work.handle_mcu_event(
        {
            "message_name": "CLEAN_COMPLETION_CONFIRMED",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 5,
                "uptimeMs": 5_000,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 1,
                "finalMeasurementUid": final_uid,
                "lockPowerState": "DEENERGIZED",
                "solenoidHealth": "OK",
                "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
                "cleanerPhysicalCloseConfirmed": True,
            },
        }
    )
    work.finalize_clean(operation_uid)

    status = updater.get_status()
    assert status["activeJobPermitCount"] == 0
    assert status["unreconciledPhysicalActionCount"] == 0
    assert status["jobGateState"] == "OPEN"
    assert store.get_work_slot() is None
    updater.close()
    store.close()


def test_expired_clean_preunlock_fact_resolves_permanent_start_action(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 1_000}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    store.receive_command(
        command["commandUid"], command["commandType"], command
    )
    assert work.start_clean_command(command)["acked"] is True
    operation_uid = command["payload"]["operationUid"]
    start_uid = uart.calls[-1][2]
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 1

    ticks["milliseconds"] += command["payload"]["operationWindowMs"] + 1
    work.handle_mcu_event(
        {
            "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_801_001,
                "mcuCommandUid": start_uid,
                "operationUid": operation_uid,
                "portNo": command["payload"]["portNo"],
                "measurementUid": (
                    "53000000-0000-4000-8000-000000000099"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )

    status = updater.get_status()
    assert status["unreconciledPhysicalActionCount"] == 0
    assert status["reconciliationRequired"] is False
    slot = store.get_work_slot()
    assert slot["work_uid"] == operation_uid
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    assert slot["context"]["recovery_error_code"] == "COMMAND_EXPIRED"
    assert [call[0] for call in uart.calls] == ["START_CLEAN_OPERATION"]
    updater.close()
    store.close()


def test_restart_reconciles_grant_committed_before_business_slot(
    tmp_path,
):
    store = make_store(tmp_path)
    updater, safety = make_real_job_safety(tmp_path)
    work = WorkManager(
        store,
        FakeUart(),
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)
    assert store.claim_next_command() is not None
    safety.request_job(
        command,
        work_type="DELIVERY",
        work_uid=command["payload"]["sessionUid"],
    )

    recovered = store.recover_interrupted_commands()
    assert recovered["physical_failed"] == 1
    assert store.get_work_slot() is None
    assert updater.get_status()["activeJobPermitCount"] == 1

    assert work.reconcile_orphan_granted_job_permits() == 1
    assert updater.get_status()["activeJobPermitCount"] == 0
    assert updater.get_status()["jobGateState"] == "OPEN"
    assert store.get_command(command["commandUid"])["last_error"] == (
        "EDGE_RESTARTED_BEFORE_PHYSICAL_START"
    )
    updater.close()
    store.close()


def test_unavailable_permanent_job_gate_fails_before_slot_or_uart(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    uart = FakeUart(trace=trace)
    safety = FakeJobSafety(
        trace,
        store,
        request_error=JobSafetyError(
            "JOB_GATE_UNAVAILABLE",
            "updater is unavailable",
        ),
    )
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next() is False

    assert trace == [("safety", "request")]
    assert store.get_work_slot() is None
    pending = store.get_command(command["commandUid"])
    assert pending["state"] == "PENDING"
    assert pending["last_error"] == "JOB_GATE_UNAVAILABLE"
    store.close()


def test_uncertain_begin_retains_slot_then_closes_known_zero_effect_job(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    safety = FakeJobSafety(
        trace,
        store,
        begin_errors=[
            JobSafetyError(
                "JOB_GATE_UNAVAILABLE",
                "begin response was lost",
            )
        ],
    )
    uart = FakeUart(trace=trace)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()
    retained = store.get_work_slot()
    assert retained is not None
    assert retained["context"]["job_safety"]["begin_result"] == "UNKNOWN"
    assert store.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )
    assert uart.calls == []

    assert work.reconcile_pre_action_job_safety_failure() is True
    assert store.get_work_slot() is None
    assert store.get_command(command["commandUid"])["state"] == "FAILED"
    assert [entry for entry in trace if entry[0] == "safety"] == [
        ("safety", "request"),
        ("safety", "begin"),
        ("safety", "begin"),
        ("safety", "complete"),
    ]
    store.close()


def test_lost_arm_response_is_aborted_by_the_same_live_dispatch_attempt(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    safety = FakeJobSafety(
        trace,
        store,
        authorize_errors=[
            JobSafetyError(
                "JOB_GATE_UNAVAILABLE",
                "authorization response was lost",
            )
        ],
    )
    uart = FakeUart(trace=trace)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()
    slot = store.get_work_slot()
    assert slot is not None
    action = slot["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]
    assert action["dispatch_result"] == "CONFIRMED"
    assert uart.calls == []
    assert safety.actions[action["action_uid"]]["state"] == "CONFIRMED"
    assert safety.actions[action["action_uid"]]["confirmationBasis"] == (
        "LIVE_DISPATCH_NOT_WRITTEN"
    )

    assert work.reconcile_pre_action_job_safety_failure() is True
    assert uart.calls == []
    assert store.get_work_slot() is None
    assert store.get_command(command["commandUid"])["state"] == "FAILED"
    assert ("safety", "abort") in trace
    store.close()


def test_lost_permanent_completion_keeps_slot_until_retry_succeeds(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    safety = FakeJobSafety(
        trace,
        store,
        complete_errors=[
            JobSafetyError(
                "JOB_GATE_UNAVAILABLE",
                "completion response was lost",
            )
        ],
    )
    uart = FakeUart(trace=trace)
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )

    result = work.start_delivery_command(command)
    assert result["acked"] is True
    slot = store.get_work_slot()
    start_action = slot["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]
    # Isolate completion-response recovery from action reconciliation by
    # supplying the same identity-bound MCU fact used by normal handlers.
    work._confirm_action_from_mcu_event(
        slot["context"],
        expected_action_key="DELIVERY:START:0",
        expected_action_kind="START_DELIVERY_SESSION",
        event_type="TEST_MCU_COMMAND_RESULT",
        payload={
            "mcuCommandUid": start_action["action_uid"],
            "mcuBootId": 42,
            "mcuEventSequence": 7,
        },
    )
    work.finalize_delivery(command["payload"]["sessionUid"])

    retained = store.get_work_slot()
    assert retained is not None
    assert retained["context"]["job_safety"][
        "pending_completion"
    ]["outcome"] == "SUCCEEDED"
    # A duplicated business finalization must replay the exact frozen receipt,
    # not hash pending_completion into a different identity.
    work.finalize_delivery(command["payload"]["sessionUid"])
    assert store.get_work_slot() is None
    assert [item for item in trace if item == ("safety", "complete")] == [
        ("safety", "complete"),
        ("safety", "complete"),
    ]
    assert len(set(safety.confirmation_digests)) == 1
    assert len(set(safety.completion_digests)) == 1
    store.close()


def test_terminal_business_transaction_freezes_receipt_before_process_crash(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    command = valid_compat_service_command(
        "sample-fullness.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    assert store.claim_next_command() is not None
    work = WorkManager(
        store,
        FakeCompatUart(),
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    original_complete = work._complete_job_safety

    def crash_after_business_commit(*_args, **_kwargs):
        raise SystemExit("simulated hard stop")

    work._complete_job_safety = crash_after_business_commit
    with pytest.raises(SystemExit):
        work.start_fullness_command(command)

    retained = store.get_work_slot()
    assert retained is not None
    assert retained["context"]["job_safety"]["pending_completion"][
        "outcome"
    ] == "SUCCEEDED"
    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert updater.get_status()["activeJobPermitCount"] == 1

    work._complete_job_safety = original_complete
    assert work.reconcile_pending_job_safety_completion() is True
    assert store.get_work_slot() is None
    status = updater.get_status()
    assert status["activeJobPermitCount"] == 0
    assert status["jobGateState"] == "OPEN"
    updater.close()
    store.close()


def test_start_delivery_ack_timeout_requires_reconciliation(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    uart.command_result = {"acked": False, "error": "TIMEOUT"}
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    slot = store.get_work_slot()
    assert slot["work_type"] == "DELIVERY"
    assert slot["work_uid"] == command["payload"]["sessionUid"]
    assert slot["context"]["phase"] == "START_RESULT_UNKNOWN"
    store.close()


def test_unstable_preopen_and_persisted_photos_authorize_first_open(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    trace = []
    uart = FakeUart(trace=trace)
    photos = FakePhotoManager(trace=trace)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    start_mcu_command_uid = store.get_command(
        command["commandUid"]
    )["mcu_command_uid"]
    measurement_uid = "52000000-0000-4000-8000-000000000001"

    processor.process_mcu_event({
        "message_name": "WORK_PREOPEN_WEIGHT_READY",
        "message_type": 48,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": start_mcu_command_uid,
            "sessionUid": command["payload"]["sessionUid"],
            "portNo": command["payload"]["portNo"],
            "roundIndex": 1,
            "measurementUid": measurement_uid,
            "measurementStatus": "UNSTABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1234,
            "weightValueKind": "LAST_FOUR_MEAN",
            "measurementElapsedMs": 6000,
            "sampleCount": 60,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "WEIGHT_UNSTABLE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert photos.captured == [("open", command["payload"]["sessionUid"])]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "AUTHORIZE_DELIVERY_FIRST_OPEN"
    assert values["firstPreOpenMeasurementUid"] == measurement_uid
    assert values["parentStartCommandUid"] == start_mcu_command_uid
    assert values["remainingStartAuthorizationMs"] > 0
    assert trace[-2:] == [
        ("photo", "open"),
        ("uart", "AUTHORIZE_DELIVERY_FIRST_OPEN"),
    ]
    store.close()


def test_unpersisted_preopen_photo_fact_blocks_first_open(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    photos = FakePhotoManager(capture_result=False)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    processor.process_next()
    start_mcu_command_uid = store.get_command(
        command["commandUid"]
    )["mcu_command_uid"]

    processor.process_mcu_event({
        "message_name": "WORK_PREOPEN_WEIGHT_READY",
        "message_type": 48,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": start_mcu_command_uid,
            "sessionUid": command["payload"]["sessionUid"],
            "portNo": command["payload"]["portNo"],
            "roundIndex": 1,
            "measurementUid": (
                "52000000-0000-4000-8000-000000000002"
            ),
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1234,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    })

    assert [call[0] for call in uart.calls] == [
        "START_DELIVERY_SESSION"
    ]
    assert store.get_work_slot()["context"]["phase"] == (
        "PREOPEN_PHOTO_BLOCKED"
    )
    store.close()


def test_delivery_complete_reports_latched_command_without_pulse_duration(
    tmp_path,
):
    store = make_store(tmp_path)
    session_uid = "53000000-0000-4000-8000-000000000001"
    measurement_uid = "53000000-0000-4000-8000-000000000002"
    measurement = {
        "measurementUid": measurement_uid,
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1500,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 1000,
        "sampleCount": 10,
        "calibrationVersion": 1,
        "weightSensorHealth": "OK",
        "faultCode": "NONE",
        "mcuBootId": 42,
        "mcuEventSequence": 7,
    }
    assert store.acquire_work_slot(
        "DELIVERY",
        session_uid,
        1,
        {
            "session_uid": session_uid,
            "port_no": 1,
            "start_command_uid": (
                "53000000-0000-4000-8000-000000000003"
            ),
            "device_name": "SN-DEMO-0001",
            "unit_price_ten_thousandths": 4500,
            "config": {
                "version": 8,
                "contentSha256": "a" * 64,
                "mcuPayloadSha256": "b" * 64,
            },
            "first_measurement": measurement,
            "final_measurement": measurement,
            "first_weight_grams": 1000,
            "final_weight_grams": 1500,
            "round_index": 1,
            "round_1_measurement_uid": measurement_uid,
            "last_delivery_door_command": "CLOSE",
            "last_delivery_door_output_status": "COMMAND_DISPATCHED",
            "delivery_door_physical_state_basis": "NOT_OBSERVABLE",
            "negative_weight_anomaly": False,
        },
    )
    work = WorkManager(
        store,
        FakeUart(),
        None,
        FakePhotoManager(),
    )

    work.handle_mcu_event(
        {
            "message_name": "DELIVERY_SELECTION",
            "payload": {
                "sessionUid": session_uid,
                "portNo": 1,
                "roundIndex": 1,
                "postCloseMeasurementUid": measurement_uid,
                "selection": "END",
            },
        }
    )

    envelope = json.loads(store.list_pending_events()[-1]["payload_json"])
    door_fact = envelope["payload"]["finalDoorCommand"]
    assert door_fact == {
        "command": "CLOSE",
        "outputStatus": "COMMAND_DISPATCHED",
        "physicalStateBasis": "NOT_OBSERVABLE",
    }
    store.close()


def test_clean_preunlock_failure_is_reported_but_does_not_block_unlock(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 1_000}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    trace = []
    uart = FakeUart(trace=trace)
    photos = FakePhotoManager(trace=trace)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    start_mcu_command_uid = inbox["mcu_command_uid"]
    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_mcu_command_uid,
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "measurementUid": "53000000-0000-4000-8000-000000000001",
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    message_name, values, _ = uart.calls[-1]
    assert message_name == "UNLOCK_CLEAN_DOOR"
    assert values["cleanActionSequence"] == 0
    assert values["recoveryGeneration"] == 0
    assert values["unlockPulseMs"] == 1000
    assert 1_799_000 <= values["remainingOperationWindowMs"] <= 1_800_000
    assert values["parentCommandUid"] == start_mcu_command_uid
    assert trace[-2:] == [
        ("photo", "clean_open"),
        ("uart", "UNLOCK_CLEAN_DOOR"),
    ]
    store.close()


def test_clean_final_weight_failure_still_allows_manual_completion(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    photos = FakePhotoManager()
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    start_uid = store.get_command(command["commandUid"])["mcu_command_uid"]
    operation_uid = command["payload"]["operationUid"]
    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_uid,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "measurementUid": "53000000-0000-4000-8000-000000000001",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 50000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    })
    processor.process_mcu_event({
        "message_name": "CLEAN_FINISH_REQUESTED",
        "message_type": 55,
        "source_tx_sequence": 11,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 3,
            "uptimeMs": 3000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
        },
    })
    final_measurement_uid = "54000000-0000-4000-8000-000000000001"
    processor.process_mcu_event({
        "message_name": "CLEAN_FINAL_WEIGHT_READY",
        "message_type": 56,
        "source_tx_sequence": 12,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 4,
            "uptimeMs": 9000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
            "measurementUid": final_measurement_uid,
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })
    processor.process_mcu_event({
        "message_name": "CLEAN_COMPLETION_CONFIRMED",
        "message_type": 62,
        "source_tx_sequence": 13,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 5,
            "uptimeMs": 10000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
            "finalMeasurementUid": final_measurement_uid,
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "OK",
            "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
            "cleanerPhysicalCloseConfirmed": True,
        },
    })

    slot = store.get_work_slot()
    assert slot["work_state"] == "COMPLETING"
    event = store.list_pending_events()[-1]
    envelope = json.loads(event["payload_json"])
    assert envelope["eventType"] == "CLEAN_COMPLETE"
    assert envelope["edgeEventSequence"] == event["edge_event_sequence"]
    event_payload = envelope["payload"]
    assert (
        event_payload["cleanerConfirmedFinalMeasurement"]["status"]
        == "DISCONNECTED"
    )
    assert (
        event_payload["cleanLockAndManualDoorConfirmation"][
            "cleanerPhysicalCloseConfirmed"
        ]
        is True
    )
    assert (
        event_payload["cleanLockAndManualDoorConfirmation"][
            "physicalDoorStateBasis"
        ]
        == "CLEANER_CONFIRMATION"
    )
    assert photos.captured == [
        ("clean_open", operation_uid),
        ("clean_close", operation_uid),
    ]
    store.close()


def test_fullness_no_echo_is_reported_as_clear_without_fault(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("sample-fullness.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    mcu_command_uid = inbox["mcu_command_uid"]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "SAMPLE_FULLNESS"
    assert values["measurementTimeoutMs"] == 6000
    processor.process_mcu_event({
        "message_name": "FULLNESS_SAMPLE_RESULT",
        "message_type": 57,
        "source_tx_sequence": 14,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 6,
            "uptimeMs": 12000,
            "mcuCommandUid": mcu_command_uid,
            "detectionUid": command["payload"]["detectionUid"],
            "portNo": command["payload"]["portNo"],
            "sampleRole": "INITIAL",
            "fullnessSensorKind": "ULTRASONIC",
            "fullnessSensorValue": "CLEAR",
            "fullnessSampleBasis": "NO_ECHO_CLEAR_FALLBACK",
            "representativeDistancePresent": False,
            "representativeDistanceMm": 0,
            "requestedSampleCount": 5,
            "validSampleCount": 0,
            "measurementUid": "55000000-0000-4000-8000-000000000001",
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    assert store.list_active_faults() == []
    event = json.loads(store.list_pending_events()[-1]["payload_json"])
    assert event["eventType"] == "FULLNESS_SAMPLE_COMPLETE"
    assert event["payload"]["fullnessSensorValue"] == "CLEAR"
    assert (
        event["payload"]["fullnessSampleBasis"]
        == "NO_ECHO_CLEAR_FALLBACK"
    )
    assert event["payload"]["representativeDistanceMm"] is None
    store.close()


def test_unstable_empty_bag_baseline_keeps_value_and_quality(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    mcu_command_uid = inbox["mcu_command_uid"]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "MEASURE_BASELINE"
    assert values["measurementTimeoutMs"] == 6000
    processor.process_mcu_event({
        "message_name": "BASELINE_MEASUREMENT_RESULT",
        "message_type": 58,
        "source_tx_sequence": 15,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 7,
            "uptimeMs": 18000,
            "mcuCommandUid": mcu_command_uid,
            "measurementUid": command["payload"]["measurementUid"],
            "portNo": command["payload"]["portNo"],
            "measurementStatus": "UNSTABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1180,
            "weightValueKind": "LAST_FOUR_MEAN",
            "measurementElapsedMs": 6000,
            "sampleCount": 60,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "WEIGHT_UNSTABLE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    event = json.loads(store.list_pending_events()[-1]["payload_json"])
    measurement = event["payload"]["totalWeightMeasurement"]
    assert measurement["reportedWeightGrams"] == 1180
    assert measurement["status"] == "UNSTABLE"
    assert measurement["weightValueAvailable"] is True
    assert measurement["weightValueKind"] == "LAST_FOUR_MEAN"
    store.close()


def test_end_clean_before_unlock_releases_reserved_operation(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    processor.process_next()
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)

    processor.process_next()

    assert store.get_command(end["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    message_name, values, _ = uart.calls[-1]
    assert message_name == "END_CLEAN_BEFORE_UNLOCK"
    assert values["operationUid"] == start["payload"]["operationUid"]
    assert values["reason"] == "CLEANER_CANCELLED"
    store.close()


def test_candidate_end_clean_before_unlock_is_not_a_physical_action(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert processor.process_next()

    slot = store.get_work_slot()
    start_action = slot["context"]["job_safety"]["actions"][
        "CLEAN:START:0"
    ]
    work._confirm_action_from_mcu_event(
        slot["context"],
        expected_action_key="CLEAN:START:0",
        expected_action_kind="START_CLEAN_OPERATION",
        event_type="TEST_MCU_COMMAND_RESULT",
        payload={
            "mcuCommandUid": start_action["action_uid"],
            "mcuBootId": 42,
            "mcuEventSequence": 1,
        },
    )
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 0

    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)
    assert processor.process_next()

    assert store.get_command(end["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    assert updater.get_status()["activeJobPermitCount"] == 0
    assert updater.get_status()["unreconciledPhysicalActionCount"] == 0
    action_count = updater._connection.execute(
        "SELECT COUNT(*) FROM physical_action_ledger"
    ).fetchone()[0]
    assert action_count == 1
    assert set(slot["context"]["job_safety"]["actions"]) == {
        "CLEAN:START:0"
    }
    assert [call[0] for call in uart.calls] == [
        "START_CLEAN_OPERATION",
        "END_CLEAN_BEFORE_UNLOCK",
    ]
    updater.close()
    store.close()


def test_pending_end_from_business_ingress_blocks_event_thread_unlock(
    tmp_path,
):
    class BlockingCleanOpenPhotos(FakePhotoManager):
        def __init__(self):
            super().__init__()
            self.capture_started = threading.Event()
            self.release_capture = threading.Event()

        def capture_clean_open_photos(self, work_uid):
            self.capture_started.set()
            if not self.release_capture.wait(5):
                raise AssertionError("timed out waiting to release clean photos")
            return self._capture("clean_open", work_uid)

    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    photos = BlockingCleanOpenPhotos()
    work = WorkManager(
        store,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    handler = BusinessMessageHandler(
        store,
        DeviceIdentity("SN-CONTRACT-0001"),
        edge_boot_id=9_001,
    )
    command_wakes = []
    handler.on_command_received = lambda *args: command_wakes.append(args)

    start = valid_service_command("start-clean-operation.service-wire.json")
    assert store.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next()
    start_action_uid = uart.calls[-1][2]
    event_errors = []

    def process_preunlock_event():
        try:
            processor.process_mcu_event(
                clean_preunlock_event(
                    start,
                    start_action_uid,
                    "53000000-0000-4000-8000-0000000000d1",
                )
            )
        except BaseException as error:  # surfaced after the thread joins
            event_errors.append(error)

    event_thread = threading.Thread(target=process_preunlock_event)
    event_thread.start()
    try:
        assert photos.capture_started.wait(3)
        service_id, params, end = valid_end_clean_service_invocation()

        response = invoke_business_service(handler, service_id, params)

        assert response.data["receiptState"] == 1
        assert command_wakes == []
        assert store.get_command(end["commandUid"])["state"] == "PENDING"

        photos.release_capture.set()
        event_thread.join(3)
        assert not event_thread.is_alive()
        assert len(event_errors) == 1
        assert isinstance(event_errors[0], CleanUnlockDecisionDeferred)
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION"
        ]
        retained = store.get_work_slot()
        assert retained is not None
        assert retained["context"]["phase"] == "PREUNLOCK_MEASURED"
        assert "unlock_mcu_command_uid" not in retained["context"]
        assert set(retained["context"]["job_safety"]["actions"]) == {
            "CLEAN:START:0"
        }
        action_rows = updater._connection.execute(
            "SELECT action_kind FROM physical_action_ledger"
        ).fetchall()
        assert [row["action_kind"] for row in action_rows] == [
            "START_CLEAN_OPERATION"
        ]

        assert response.after_reply is not None
        response.after_reply()
        assert len(command_wakes) == 1
        assert command_wakes[0][0] == end["commandUid"]
        assert processor.process_next()

        assert store.get_command(end["commandUid"])["state"] == "COMPLETED"
        assert store.get_work_slot() is None
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "END_CLEAN_BEFORE_UNLOCK",
        ]
        action_rows = updater._connection.execute(
            "SELECT action_kind FROM physical_action_ledger"
        ).fetchall()
        assert [row["action_kind"] for row in action_rows] == [
            "START_CLEAN_OPERATION"
        ]
        permit = updater.get_job_permit({"permitUid": start["commandUid"]})
        assert permit["state"] == "COMPLETED"
        assert permit["completionOutcome"] == "CANCELLED"
    finally:
        photos.release_capture.set()
        event_thread.join(3)
        updater.close()
        store.close()


def test_end_persisted_after_unlock_claim_fails_in_single_consumer(tmp_path):
    class BlockingUnlockUart(FakeUart):
        def __init__(self):
            super().__init__()
            self.unlock_dispatch_started = threading.Event()
            self.release_unlock = threading.Event()

        def send_command_before_deadline(
            self,
            message_name,
            values,
            *,
            mcu_command_uid=None,
            dispatch_deadline_monotonic,
            dispatch_gate=None,
        ):
            if message_name == "UNLOCK_CLEAN_DOOR":
                self.unlock_dispatch_started.set()
                if not self.release_unlock.wait(5):
                    raise AssertionError("timed out waiting to release unlock")
            return super().send_command_before_deadline(
                message_name,
                values,
                mcu_command_uid=mcu_command_uid,
                dispatch_deadline_monotonic=dispatch_deadline_monotonic,
                dispatch_gate=dispatch_gate,
            )

    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = BlockingUnlockUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    handler = BusinessMessageHandler(
        store,
        DeviceIdentity("SN-CONTRACT-0001"),
        edge_boot_id=9_001,
    )
    command_wakes = []
    handler.on_command_received = lambda *args: command_wakes.append(args)

    start = valid_service_command("start-clean-operation.service-wire.json")
    assert store.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next()
    start_action_uid = uart.calls[-1][2]
    event_errors = []

    def process_preunlock_event():
        try:
            processor.process_mcu_event(
                clean_preunlock_event(
                    start,
                    start_action_uid,
                    "53000000-0000-4000-8000-0000000000d2",
                )
            )
        except BaseException as error:  # surfaced after the thread joins
            event_errors.append(error)

    event_thread = threading.Thread(target=process_preunlock_event)
    event_thread.start()
    try:
        assert uart.unlock_dispatch_started.wait(3)
        claimed = store.get_work_slot()["context"]
        assert claimed["phase"] == "UNLOCKING"
        assert "unlock_mcu_command_uid" in claimed

        service_id, params, end = valid_end_clean_service_invocation()
        response = invoke_business_service(handler, service_id, params)
        assert response.data["receiptState"] == 1
        assert store.get_command(end["commandUid"])["state"] == "PENDING"
        assert command_wakes == []

        assert response.after_reply is not None
        response.after_reply()
        assert len(command_wakes) == 1
        # The sole command consumer is still inside the MCU event. It can
        # claim the newly accepted END only after the winning unlock call
        # returns.
        uart.release_unlock.set()
        event_thread.join(3)
        assert not event_thread.is_alive()
        assert event_errors == []
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "UNLOCK_CLEAN_DOOR",
        ]
        assert processor.process_next()
        rejected = store.get_command(end["commandUid"])
        assert rejected["state"] == "FAILED"
        assert rejected["last_error"] == "STATE_CONFLICT"
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "UNLOCK_CLEAN_DOOR",
        ]
        retained = store.get_work_slot()
        assert retained["context"]["phase"] == "WAITING_LOCK_OUTPUT"
        assert "end_before_unlock" not in retained["context"]
        action_rows = updater._connection.execute(
            """SELECT action_kind, state
               FROM physical_action_ledger ORDER BY rowid"""
        ).fetchall()
        assert [row["action_kind"] for row in action_rows] == [
            "START_CLEAN_OPERATION",
            "UNLOCK_CLEAN_DOOR",
        ]
        assert action_rows[-1]["state"] == "ARMED"
    finally:
        uart.release_unlock.set()
        event_thread.join(3)
        updater.close()
        store.close()


def test_end_clean_wins_while_preunlock_event_is_blocked_taking_photos(
    tmp_path,
):
    class BlockingCleanOpenPhotos(FakePhotoManager):
        def __init__(self):
            super().__init__()
            self.capture_started = threading.Event()
            self.release_capture = threading.Event()

        def capture_clean_open_photos(self, work_uid):
            self.capture_started.set()
            if not self.release_capture.wait(5):
                raise AssertionError("timed out waiting to release clean photos")
            return self._capture("clean_open", work_uid)

    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    end_sent = threading.Event()
    context_seen_before_end_send = []

    def observe_persisted_end_intent():
        context_seen_before_end_send.append(
            copy.deepcopy(store.get_work_slot()["context"])
        )
        end_sent.set()

    uart = EndCleanControlUart(
        end_result={"acked": False, "error": "TIMEOUT", "fatal": False},
        before_end_send=observe_persisted_end_intent,
    )
    photos = BlockingCleanOpenPhotos()
    work = WorkManager(
        store,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert work.start_clean_command(start)["acked"] is True
    operation_uid = start["payload"]["operationUid"]
    port_no = start["payload"]["portNo"]
    start_action_uid = uart.calls[-1][2]
    preunlock_event = {
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1_000,
            "mcuCommandUid": start_action_uid,
            "operationUid": operation_uid,
            "portNo": port_no,
            "measurementUid": "53000000-0000-4000-8000-0000000000b1",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 50_000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1_000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    }
    event_errors = []
    end_errors = []
    end_results = []

    def process_preunlock_event():
        try:
            work.handle_mcu_event(preunlock_event)
        except BaseException as error:  # surfaced after both threads join
            event_errors.append(error)

    end = valid_service_command("end-clean-before-unlock.service-wire.json")

    def process_end_command():
        try:
            end_results.append(work.end_clean_before_unlock_command(end))
        except BaseException as error:  # surfaced after both threads join
            end_errors.append(error)

    event_thread = threading.Thread(target=process_preunlock_event)
    end_thread = threading.Thread(target=process_end_command)
    event_thread.start()
    try:
        assert photos.capture_started.wait(3)
        before_end = store.get_work_slot()["context"]
        assert before_end["phase"] == "PREUNLOCK_MEASURED"

        end_thread.start()
        end_thread.join(3)
        assert not end_thread.is_alive()
        assert end_errors == []
        assert end_results and end_results[0]["acked"] is False
        assert end_sent.is_set()
        assert context_seen_before_end_send[0]["phase"] == (
            "ENDING_BEFORE_UNLOCK"
        )
        assert context_seen_before_end_send[0]["end_before_unlock"][
            "state"
        ] == "REQUESTED"

        committed_end = copy.deepcopy(store.get_work_slot()["context"])
        assert committed_end["phase"] == "ENDING_BEFORE_UNLOCK"
        assert committed_end["end_before_unlock"]["state"] == (
            "RESULT_UNKNOWN"
        )

        photos.release_capture.set()
        event_thread.join(3)
        assert not event_thread.is_alive()
        assert event_errors == []

        retained = store.get_work_slot()
        assert retained is not None
        assert retained["context"]["phase"] == committed_end["phase"]
        assert retained["context"]["end_before_unlock"] == (
            committed_end["end_before_unlock"]
        )
        assert "unlock_mcu_command_uid" not in retained["context"]
        assert set(retained["context"]["job_safety"]["actions"]) == {
            "CLEAN:START:0"
        }
        assert photos.captured == [("clean_open", operation_uid)]
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "END_CLEAN_BEFORE_UNLOCK",
        ]
        start_action = updater.get_physical_action(
            {"actionUid": start_action_uid}
        )
        assert start_action["state"] == "CONFIRMED"
        assert start_action["confirmedOutcome"] == "EXECUTED"
    finally:
        photos.release_capture.set()
        event_thread.join(3)
        if end_thread.ident is not None:
            end_thread.join(3)
        updater.close()
        store.close()


def test_unlock_claim_wins_before_end_and_end_returns_state_conflict(tmp_path):
    class BlockingUnlockUart(FakeUart):
        def __init__(self):
            super().__init__()
            self.unlock_dispatch_started = threading.Event()
            self.release_unlock = threading.Event()

        def send_command_before_deadline(
            self,
            message_name,
            values,
            *,
            mcu_command_uid=None,
            dispatch_deadline_monotonic,
            dispatch_gate=None,
        ):
            if message_name == "UNLOCK_CLEAN_DOOR":
                self.unlock_dispatch_started.set()
                if not self.release_unlock.wait(5):
                    raise AssertionError("timed out waiting to release unlock")
            return super().send_command_before_deadline(
                message_name,
                values,
                mcu_command_uid=mcu_command_uid,
                dispatch_deadline_monotonic=dispatch_deadline_monotonic,
                dispatch_gate=dispatch_gate,
            )

    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = BlockingUnlockUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert work.start_clean_command(start)["acked"] is True
    operation_uid = start["payload"]["operationUid"]
    start_action_uid = uart.calls[-1][2]
    event = {
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1_000,
            "mcuCommandUid": start_action_uid,
            "operationUid": operation_uid,
            "portNo": start["payload"]["portNo"],
            "measurementUid": "53000000-0000-4000-8000-0000000000b2",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 50_000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1_000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    }
    event_errors = []

    def process_preunlock_event():
        try:
            work.handle_mcu_event(event)
        except BaseException as error:  # surfaced after the thread joins
            event_errors.append(error)

    event_thread = threading.Thread(target=process_preunlock_event)
    event_thread.start()
    try:
        assert uart.unlock_dispatch_started.wait(3)
        claimed = store.get_work_slot()["context"]
        assert claimed["phase"] == "UNLOCKING"
        assert "unlock_mcu_command_uid" in claimed

        end = valid_service_command("end-clean-before-unlock.service-wire.json")
        end_result = work.end_clean_before_unlock_command(end)
        assert end_result == {"acked": False, "error": "STATE_CONFLICT"}
        assert "end_before_unlock" not in store.get_work_slot()["context"]

        uart.release_unlock.set()
        event_thread.join(3)
        assert not event_thread.is_alive()
        assert event_errors == []
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "UNLOCK_CLEAN_DOOR",
        ]
        assert store.get_work_slot()["context"]["phase"] == (
            "WAITING_LOCK_OUTPUT"
        )
    finally:
        uart.release_unlock.set()
        event_thread.join(3)
        updater.close()
        store.close()


def test_late_clean_start_fact_after_end_ack_only_finishes_cancellation(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    before_end_send = []

    def capture_persisted_end_intent():
        before_end_send.append(copy.deepcopy(store.get_work_slot()["context"]))

    uart = EndCleanControlUart(
        end_result={"acked": True, "disposition": "ACCEPTED"},
        before_end_send=capture_persisted_end_intent,
    )
    photos = FakePhotoManager()
    work = WorkManager(
        store,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert processor.process_next()
    start_action_uid = uart.calls[-1][2]
    assert updater.get_physical_action(
        {"actionUid": start_action_uid}
    )["state"] == "ARMED"

    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)
    assert processor.process_next()

    assert len(before_end_send) == 1
    persisted_before_uart = before_end_send[0]
    assert persisted_before_uart["phase"] == "ENDING_BEFORE_UNLOCK"
    assert persisted_before_uart["end_before_unlock"]["state"] == (
        "REQUESTED"
    )
    retained = store.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == "END_BEFORE_UNLOCK_ACKED"
    assert retained["context"]["end_before_unlock"]["state"] == "ACKED"
    assert retained["context"]["job_safety"]["pending_completion"][
        "outcome"
    ] == "CANCELLED"
    assert updater.get_status()["activeJobPermitCount"] == 1

    work.handle_mcu_event(
        {
            "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_action_uid,
                "operationUid": start["payload"]["operationUid"],
                "portNo": start["payload"]["portNo"],
                "measurementUid": (
                    "53000000-0000-4000-8000-0000000000a1"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )

    assert store.get_work_slot() is None
    assert photos.captured == []
    assert [call[0] for call in uart.calls] == [
        "START_CLEAN_OPERATION",
        "END_CLEAN_BEFORE_UNLOCK",
    ]
    action = updater.get_physical_action({"actionUid": start_action_uid})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "EXECUTED"
    permit = updater.get_job_permit({"permitUid": start["commandUid"]})
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "CANCELLED"
    updater.close()
    store.close()


def test_late_clean_start_fact_after_unknown_end_keeps_recovery_locked(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    before_end_send = []

    def capture_persisted_end_intent():
        before_end_send.append(copy.deepcopy(store.get_work_slot()["context"]))

    uart = EndCleanControlUart(
        end_result={"acked": False, "error": "TIMEOUT", "fatal": False},
        before_end_send=capture_persisted_end_intent,
    )
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert processor.process_next()
    start_action_uid = uart.calls[-1][2]
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)
    assert processor.process_next()

    assert before_end_send[0]["phase"] == "ENDING_BEFORE_UNLOCK"
    assert before_end_send[0]["end_before_unlock"]["state"] == "REQUESTED"
    retained = store.get_work_slot()
    assert retained["context"]["end_before_unlock"]["state"] == (
        "RESULT_UNKNOWN"
    )
    assert updater.get_physical_action(
        {"actionUid": start_action_uid}
    )["state"] == "ARMED"

    store.close()
    restarted_store = make_store(tmp_path)
    restarted_safety = PermanentJobSafety(
        StoreBackedUpdaterClient(updater)
    )
    restarted_uart = FakeUart()
    restarted_photos = FakePhotoManager()
    restarted_work = WorkManager(
        restarted_store,
        restarted_uart,
        None,
        restarted_photos,
        job_safety=restarted_safety,
    )
    restarted_work.handle_mcu_event(
        {
            "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 2_000,
                "mcuCommandUid": start_action_uid,
                "operationUid": start["payload"]["operationUid"],
                "portNo": start["payload"]["portNo"],
                "measurementUid": (
                    "53000000-0000-4000-8000-0000000000a2"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )

    after_late_start = restarted_store.get_work_slot()
    assert after_late_start is not None
    assert after_late_start["context"]["phase"] == (
        "END_BEFORE_UNLOCK_RESULT_UNKNOWN"
    )
    assert after_late_start["context"]["end_before_unlock"]["state"] == (
        "RESULT_UNKNOWN"
    )
    assert restarted_photos.captured == []
    assert restarted_uart.calls == []
    action = updater.get_physical_action({"actionUid": start_action_uid})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "EXECUTED"
    status = updater.get_status()
    assert status["activeJobPermitCount"] == 1
    assert status["unreconciledPhysicalActionCount"] == 0
    assert updater.get_job_permit(
        {"permitUid": start["commandUid"]}
    )["state"] == "ACTIVE"
    updater.close()
    restarted_store.close()


def _reach_unknown_end_after_late_clean_start(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = EndCleanControlUart(
        end_result={"acked": False, "error": "TIMEOUT", "fatal": False},
    )
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    assert processor.process_next()
    start_action_uid = uart.calls[-1][2]
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)

    assert processor.process_next()
    first_end_call = uart.calls[-1]
    assert first_end_call[0] == "END_CLEAN_BEFORE_UNLOCK"
    assert store.get_command(end["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )

    work.handle_mcu_event(
        {
            "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_action_uid,
                "operationUid": start["payload"]["operationUid"],
                "portNo": start["payload"]["portNo"],
                "measurementUid": (
                    "53000000-0000-4000-8000-0000000000c1"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    retained = store.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == (
        "END_BEFORE_UNLOCK_RESULT_UNKNOWN"
    )
    assert retained["context"]["end_before_unlock"]["state"] == (
        "RESULT_UNKNOWN"
    )
    return store, updater, uart, work, start, end, first_end_call[2]


def test_same_unknown_end_command_reuses_mcu_uid_and_converges_after_ack(
    tmp_path,
):
    store, updater, uart, work, start, end, first_end_mcu_uid = (
        _reach_unknown_end_after_late_clean_start(tmp_path)
    )
    try:
        uart.end_result = {"acked": True, "disposition": "DUPLICATE"}

        retry = work.end_clean_before_unlock_command(end)

        assert retry["acked"] is True
        end_calls = [
            call for call in uart.calls
            if call[0] == "END_CLEAN_BEFORE_UNLOCK"
        ]
        assert len(end_calls) == 2
        assert end_calls[0][2] == first_end_mcu_uid
        assert end_calls[1][2] == first_end_mcu_uid
        assert end_calls[1][1]["reason"] == end_calls[0][1]["reason"]
        assert store.get_command(end["commandUid"])["state"] == "COMPLETED"
        assert store.get_work_slot() is None
        permit = updater.get_job_permit({"permitUid": start["commandUid"]})
        assert permit["state"] == "COMPLETED"
        assert permit["completionOutcome"] == "CANCELLED"
    finally:
        updater.close()
        store.close()


@pytest.mark.parametrize("changed_field", ["command_uid", "reason"])
def test_unknown_end_retry_rejects_changed_identity_or_reason(
    tmp_path,
    changed_field,
):
    store, updater, uart, work, _start, end, first_end_mcu_uid = (
        _reach_unknown_end_after_late_clean_start(tmp_path)
    )
    try:
        conflicting = copy.deepcopy(end)
        if changed_field == "command_uid":
            conflicting["commandUid"] = (
                "81000000-0000-4000-8000-000000000099"
            )
        else:
            conflicting["payload"]["reason"] = "SIMULATED_DIFFERENT_REASON"

        call_count = len(uart.calls)
        result = work.end_clean_before_unlock_command(conflicting)

        assert result == {"acked": False, "error": "STATE_CONFLICT"}
        assert len(uart.calls) == call_count
        retained = store.get_work_slot()
        assert retained is not None
        assert retained["context"]["phase"] == (
            "END_BEFORE_UNLOCK_RESULT_UNKNOWN"
        )
        intent = retained["context"]["end_before_unlock"]
        assert intent["command_uid"] == end["commandUid"]
        assert intent["reason"] == end["payload"]["reason"]
        assert intent["mcu_command_uid"] == first_end_mcu_uid
    finally:
        updater.close()
        store.close()


def test_cloud_clean_resume_does_not_reset_already_recovered_window(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    deadline = (
        datetime.now(timezone.utc) + timedelta(minutes=12)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command = valid_service_command("resume-clean-operation.service-wire.json")
    operation_uid = command["payload"]["operationUid"]
    store.acquire_work_slot(
        "CLEAN",
        operation_uid,
        command["payload"]["portNo"],
        {
            "operation_uid": operation_uid,
            "port_no": command["payload"]["portNo"],
            "new_bag_uid": command["payload"]["newBagUid"],
            "config": command["payload"]["config"],
            "operation_deadline": deadline,
            "recovery_generation": 1,
            "action_sequence": 4,
            "phase": "CLEAN_RECOVERY_REQUIRED",
        },
    )
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    slot = store.get_work_slot()
    assert slot["context"]["operation_deadline"] == deadline
    assert slot["context"]["recovery_generation"] == 1
    assert uart.calls == []
    store.close()


def test_real_smoke_alarm_is_recorded_and_blocks_new_delivery(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        DeviceIdentity("SN-DEMO-0001"),
        FakePhotoManager(),
    )
    processor = CommandProcessor(store, uart, work)

    processor.process_mcu_event({
        "message_name": "SAFETY_SENSOR_EVENT",
        "message_type": 60,
        "source_tx_sequence": 16,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 8,
            "uptimeMs": 19000,
            "smokeState": "ALARM",
            "smokeSensorHealth": "OK",
            "faultCode": "NONE",
            "workType": "NONE",
            "workUid": "00000000-0000-0000-0000-000000000000",
            "portNo": 1,
        },
    })

    assert store.get_state("smoke_state") == "ALARM"
    assert store.list_active_faults() == []
    command = valid_service_command("start-delivery-session.service-wire.json")
    command["payload"]["portNo"] = 1
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    assert store.get_command(command["commandUid"])["state"] == "FAILED"
    assert store.get_command(command["commandUid"])["last_error"] == (
        "SAFETY_SMOKE_ALARM"
    )
    assert uart.calls == []
    store.close()


def test_clean_window_expiry_before_unlock_preserves_work_for_recovery(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 1_000}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(
        command["commandUid"], command["commandType"], command
    )
    processor.process_next()
    start_mcu_command_uid = store.get_command(
        command["commandUid"]
    )["mcu_command_uid"]
    ticks["milliseconds"] += command["payload"]["operationWindowMs"] + 1

    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_mcu_command_uid,
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "measurementUid": "53100000-0000-4000-8000-000000000001",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 500,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    })

    slot = store.get_work_slot()
    assert slot["work_uid"] == command["payload"]["operationUid"]
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    assert slot["context"]["recovery_error_code"] == "COMMAND_EXPIRED"
    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "COMMAND_EXPIRED"
    assert not store.clean_restart_interlock_active(
        command["payload"]["portNo"]
    )
    assert [call[0] for call in uart.calls] == ["START_CLEAN_OPERATION"]
    store.close()


def test_clean_reunlock_expiry_preserves_original_work_for_cloud_resume(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 1_000}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    operation_uid = command["payload"]["operationUid"]
    port_no = command["payload"]["portNo"]
    store.receive_command(
        command["commandUid"], command["commandType"], command
    )
    processor.process_next()
    start_mcu_command_uid = store.get_command(
        command["commandUid"]
    )["mcu_command_uid"]

    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_mcu_command_uid,
            "operationUid": operation_uid,
            "portNo": port_no,
            "measurementUid": "53100000-0000-4000-8000-000000000002",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 500,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    })
    assert [call[0] for call in uart.calls] == [
        "START_CLEAN_OPERATION",
        "UNLOCK_CLEAN_DOOR",
    ]

    ticks["milliseconds"] += command["payload"]["operationWindowMs"] + 1
    processor.process_mcu_event({
        "message_name": "CLEAN_UNLOCK_REQUESTED",
        "message_type": 54,
        "source_tx_sequence": 11,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 3,
            "uptimeMs": 1_802_001,
            "operationUid": operation_uid,
            "portNo": port_no,
            "cleanActionSequence": 1,
        },
    })

    slot = store.get_work_slot()
    assert slot["work_uid"] == operation_uid
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    assert [call[0] for call in uart.calls] == [
        "START_CLEAN_OPERATION",
        "UNLOCK_CLEAN_DOOR",
    ]

    resume = valid_service_command("resume-clean-operation.service-wire.json")
    assert resume["payload"]["operationUid"] == operation_uid
    store.receive_command(
        resume["commandUid"], resume["commandType"], resume
    )
    processor.process_next()

    assert store.get_command(resume["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    assert uart.calls[-1][0] == "RESUME_CLEAN_OPERATION"
    assert uart.calls[-1][1]["operationUid"] == operation_uid
    store.close()


def test_fixed_frame_failed_startup_self_test_blocks_new_delivery(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    store.save_fixed_frame_self_test({
        "queryStatus": "OK",
        "communicationHealthy": True,
        "portNo": 1,
        "validFlags": 2,
        "weightValid": False,
        "weightGrams": None,
        "weightMeasurementUid": None,
        "infraredValid": True,
        "infraredBlocked": False,
        "smokeCode": 0,
        "smokeState": "NORMAL",
        "smokeSensorHealth": "OK",
        "faultCode": None,
        "rawFrameHex": "f1020000000000f1",
    })
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "SAFETY_SENSOR_UNHEALTHY"
    assert store.get_work_slot() is None
    assert uart.calls == []
    store.close()


def test_fixed_frame_active_uart_fault_blocks_stale_healthy_snapshot(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    assert store.observe_fault_and_create_event(
        device_name="SN-DEMO-0001",
        component="UART",
        fault_code="UART_PROTOCOL",
        severity="BLOCK_DEVICE",
        detail={"reasonCode": "TIMEOUT"},
    ) == "ACCEPTED"
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "SAFETY_SENSOR_UNHEALTHY"
    assert store.get_work_slot() is None
    assert uart.calls == []
    store.close()


def test_fixed_frame_missing_startup_self_test_blocks_new_delivery(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    with store.transaction():
        store._conn.execute(
            """DELETE FROM device_state
               WHERE state_key='fixed_frame_latest_self_test_json'"""
        )
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "SAFETY_SENSOR_UNHEALTHY"
    assert store.get_work_slot() is None
    assert uart.calls == []
    store.close()


def test_compat_configuration_is_applied_only_to_edge(tmp_path):
    store = make_store(tmp_path)
    uart = FakeCompatUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    configuration = store.get_configuration(
        command["payload"]["applicationUid"]
    )
    assert configuration["state"] == "APPLIED"
    assert store.get_state("mcu_configuration_projection") == (
        "NOT_SUPPORTED"
    )
    assert uart.calls == []
    store.close()


def test_compat_dd_completes_delivery_and_caches_raw_fullness(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    trace = []
    uart = FakeCompatUart(trace=trace)
    photos = FakePhotoManager(trace=trace)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()
    assert store.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    assert photos.captured == [
        ("open", command["payload"]["sessionUid"])
    ]
    assert trace == [
        ("photo", "open"),
        ("uart", "START_DELIVERY_SESSION"),
    ]

    processor.process_mcu_event({
        "message_name": "COMPAT_DELIVERY_RESULT",
        "message_type": 240,
        "source_tx_sequence": 1,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "preWeightGrams": 12_300,
            "postWeightGrams": 14_800,
            "infraredBlocked": True,
            "rawFrameHex": "dd00300c0039d001dd",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    delivery_events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(delivery_events) == 1
    payload = delivery_events[0]["payload"]
    encode_event_post("DELIVERY_COMPLETE", delivery_events[0])
    assert payload["deliveryNetWeightGrams"] == 2_500
    assert payload["negativeWeightAnomaly"] is False
    assert (
        payload["firstPreOpenMeasurement"]["mcuEventSequence"]
        == 1
    )
    assert (
        payload["finalPostCloseMeasurement"]["mcuEventSequence"]
        == 2
    )
    assert payload["unitPriceTenThousandths"] == (
        command["payload"]["unitPriceTenThousandths"]
    )
    observation = json.loads(
        store.get_state("fixed_frame_latest_observation_json")
    )
    assert observation["infraredBlocked"] is True
    assert observation["postWeightGrams"] == 14_800
    assert photos.captured[-1] == (
        "close",
        command["payload"]["sessionUid"],
    )
    assert trace[-1] == ("photo", "close")
    store.close()


def test_compat_start_dispatches_when_open_photo_capture_fails(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    photos = FakePhotoManager(capture_result=False)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    assert inbox["last_error"] is None
    assert store.get_work_slot()["work_uid"] == (
        command["payload"]["sessionUid"]
    )
    assert uart.calls[0][0] == "START_DELIVERY_SESSION"
    store.close()


@pytest.mark.parametrize(
    "example_name",
    [
        "start-delivery-session.service-wire.json",
        "start-clean-operation.service-wire.json",
    ],
)
def test_compat_start_expired_at_uart_is_a_pre_start_failure(
    tmp_path,
    example_name,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    uart.command_result = {
        "acked": False,
        "error": "COMMAND_EXPIRED",
    }
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(example_name)
    if command["commandType"] == "START_CLEAN_OPERATION":
        command["payload"]["oldBaselineWeightGrams"] = 1_500
        command["payloadSha256"] = canonical_payload_sha256(
            command["payload"]
        )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "FAILED"
    assert inbox["last_error"] == "COMMAND_EXPIRED"
    assert store.get_work_slot() is None
    stages = {
        row["stage"]
        for row in store._conn.execute(
            "SELECT stage FROM command_observation WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchall()
    }
    assert "PRE_START_FAILED" in stages
    assert "MCU_ACCEPTED" not in stages
    store.close()


def test_compat_ef_completes_clean_with_protocol_guarantees(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    trace = []
    uart = FakeCompatUart(trace=trace)
    photos = FakePhotoManager(trace=trace)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-clean-operation.service-wire.json"
    )
    command["payload"]["oldBaselineWeightGrams"] = 1_500
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()
    assert trace == [
        ("photo", "clean_open"),
        ("uart", "START_CLEAN_OPERATION"),
    ]
    processor.process_mcu_event({
        "message_name": "COMPAT_CLEAN_RESULT",
        "message_type": 241,
        "source_tx_sequence": 2,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "preWeightGrams": 50_000,
            "postWeightGrams": 2_000,
            "infraredBlocked": False,
            "rawFrameHex": "ef00c3500007d000ef",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    clean_events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "CLEAN_COMPLETE"
    ]
    assert len(clean_events) == 1
    payload = clean_events[0]["payload"]
    encode_event_post("CLEAN_COMPLETE", clean_events[0])
    assert payload["removedNetWeightGrams"] == 48_000
    assert payload["newBaselineWeightGrams"] == 2_000
    assert (
        payload["preUnlockMeasurement"]["mcuEventSequence"]
        == 3
    )
    assert (
        payload["cleanerConfirmedFinalMeasurement"][
            "mcuEventSequence"
        ]
        == 4
    )
    confirmation = payload["cleanLockAndManualDoorConfirmation"]
    assert confirmation["lockPowerState"] == "DEENERGIZED"
    assert confirmation["solenoidHealth"] == "UNKNOWN"
    assert confirmation["physicalDoorStateBasis"] == (
        "CLEANER_CONFIRMATION"
    )
    assert confirmation["cleanerPhysicalCloseConfirmed"] is True
    assert photos.captured == [
        ("clean_open", command["payload"]["operationUid"]),
        ("clean_close", command["payload"]["operationUid"]),
    ]
    assert trace[-1] == ("photo", "clean_close")
    store.close()


def test_compat_result_rejects_boolean_identity_values(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    processor.process_next()

    with pytest.raises(
        ValueError,
        match="invalid fixed-frame result identity",
    ):
        processor.process_mcu_event({
            "message_name": "COMPAT_DELIVERY_RESULT",
            "message_type": 240,
            "source_tx_sequence": 1,
            "payload": {
                "mcuBootId": True,
                "mcuEventSequence": 1,
                "uptimeMs": 1000,
                "preWeightGrams": 12_300,
                "postWeightGrams": 14_800,
                "infraredBlocked": True,
                "rawFrameHex": "dd00300c0039d001dd",
            },
        })

    assert store.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    assert store.get_work_slot()["work_uid"] == (
        command["payload"]["sessionUid"]
    )
    store.close()


def test_compat_fullness_uses_latest_dd_observation_without_uart(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    store.set_state(
        "fixed_frame_latest_observation_json",
        json.dumps({
            "sourceWorkType": "DELIVERY",
            "sourceWorkUid": "65000000-0000-4000-8000-000000000001",
            "portNo": 1,
            "postWeightGrams": 21_000,
            "infraredBlocked": True,
            "mcuBootId": 42,
            "mcuEventSequence": 3,
        }),
    )
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "sample-fullness.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    fullness_events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "FULLNESS_SAMPLE_COMPLETE"
    ]
    assert len(fullness_events) == 1
    payload = fullness_events[0]["payload"]
    encode_event_post("FULLNESS_SAMPLE_COMPLETE", fullness_events[0])
    assert payload["fullnessSensorKind"] == "DIGITAL_INFRARED"
    assert payload["fullnessSensorValue"] == "BLOCKED"
    assert payload["fullnessSampleBasis"] == "NOT_SAMPLED"
    assert payload["totalWeightMeasurement"]["reportedWeightGrams"] == (
        21_000
    )
    assert payload["totalWeightMeasurement"]["mcuEventSequence"] == 3
    assert uart.calls == []
    store.close()


def test_compat_baseline_uses_fresh_f0_f1_weight_not_cached_history(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "COMPLETED"
    assert inbox["result"] == {
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1_234,
        "compatibilitySource": "FRESH_F0_F1_SNAPSHOT",
    }
    events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "BASELINE_MEASUREMENT_COMPLETE"
    ]
    assert len(events) == 1
    encode_event_post("BASELINE_MEASUREMENT_COMPLETE", events[0])
    measurement = events[0]["payload"]["totalWeightMeasurement"]
    assert measurement["status"] == "STABLE"
    assert measurement["weightValueAvailable"] is True
    assert measurement["reportedWeightGrams"] == 1_234
    assert measurement["weightValueKind"] == "STABLE_WINDOW_MEAN"
    assert measurement["sensorHealth"] == "OK"
    assert measurement["faultCode"] is None
    baseline = store.get_bag_baseline(command["payload"]["bagUid"])
    assert baseline["weight_grams"] == 1_234
    assert baseline["source_kind"] == "FIXED_FRAME_F0_F1"
    assert store.get_work_slot() is None
    assert uart.calls == []
    assert uart.self_test_calls == [3_000]
    store.close()


def test_compat_baseline_reports_fresh_f0_weight_failure(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart({
        "queryStatus": "OK",
        "communicationHealthy": True,
        "portNo": 1,
        "validFlags": 2,
        "weightValid": False,
        "weightGrams": None,
        "weightMeasurementUid": None,
        "infraredValid": True,
        "infraredBlocked": False,
        "smokeCode": 0,
        "smokeState": "NORMAL",
        "smokeSensorHealth": "OK",
        "faultCode": None,
        "rawFrameHex": "f1020000000000f1",
    })
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "COMPLETED"
    assert inbox["result"] == {
        "measurementStatus": "SENSOR_FAULT",
        "weightValuePresent": False,
        "reportedWeightGrams": None,
        "compatibilitySource": "FRESH_F0_F1_SNAPSHOT",
    }
    events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "BASELINE_MEASUREMENT_COMPLETE"
    ]
    assert len(events) == 1
    encode_event_post("BASELINE_MEASUREMENT_COMPLETE", events[0])
    measurement = events[0]["payload"]["totalWeightMeasurement"]
    assert measurement["status"] == "SENSOR_FAULT"
    assert measurement["weightValueAvailable"] is False
    assert measurement["reportedWeightGrams"] is None
    assert measurement["faultCode"] == "WEIGHT_SENSOR"
    assert store.get_bag_baseline(command["payload"]["bagUid"]) is None
    assert uart.calls == []
    assert uart.self_test_calls == [3_000]
    store.close()


def test_compat_corrupt_fullness_cache_falls_back_to_zero(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    store.set_state(
        "fixed_frame_latest_observation_json",
        json.dumps({
            "portNo": 1,
            "postWeightGrams": "not-an-integer",
            "infraredBlocked": True,
            "mcuBootId": 42,
            "mcuEventSequence": 3,
        }),
    )
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "sample-fullness.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "COMPLETED"
    assert inbox["result"]["fullnessPercent"] == 0
    assert inbox["result"]["fullnessState"] == "NOT_FULL"
    assert store.get_work_slot() is None
    assert uart.calls == []
    store.close()


def test_compat_delivery_overdue_keeps_a_slot_and_never_rebinds_dd_to_b(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )
    processor.process_next()
    calls_after_start = list(uart.calls)
    slot = store.get_work_slot()
    context = slot["context"]
    context["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    context["delivery_result_deadline_monotonic_ms"] = 1
    store.update_work_context(slot["work_uid"], context)

    assert work.expire_fixed_frame_work()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "MCU_RESULT_OVERDUE"
    retained = store.get_work_slot()
    assert retained is not None
    assert retained["work_uid"] == command["payload"]["sessionUid"]
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert uart.calls == calls_after_start

    second = copy.deepcopy(command)
    second["commandUid"] = str(uuid.uuid4())
    second["payload"]["sessionUid"] = str(uuid.uuid4())
    second["target"]["uid"] = second["payload"]["sessionUid"]
    second["payloadSha256"] = canonical_payload_sha256(
        second["payload"]
    )
    assert store.receive_command(
        second["commandUid"],
        second["commandType"],
        second,
    ) == "ACCEPTED"
    assert processor.process_next() is True
    second_inbox = store.get_command(second["commandUid"])
    assert second_inbox["state"] == "FAILED"
    assert second_inbox["last_error"] == "DEVICE_BUSY"
    assert uart.calls == calls_after_start

    processor.process_mcu_event({
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
    })

    assert store.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert store.get_command(second["commandUid"])["state"] == "FAILED"
    assert store.get_work_slot() is None
    delivery_events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(delivery_events) == 1
    assert delivery_events[0]["target"]["uid"] == (
        command["payload"]["sessionUid"]
    )
    observations = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
    ]
    first_stages = [
        event["payload"]["stage"]
        for event in observations
        if event.get("commandUid") == command["commandUid"]
    ]
    assert first_stages == ["ACCEPTED", "MCU_ACCEPTED"]
    assert all(
        uuid.UUID(event["eventUid"]).version == 4
        for event in observations
    )
    store.close()


def test_compat_clean_continues_when_all_photo_captures_fail(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    photos = FakePhotoManager(capture_result=False)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-clean-operation.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    assert uart.calls[0][0] == "START_CLEAN_OPERATION"
    assert store.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    processor.process_mcu_event({
        "message_name": "COMPAT_CLEAN_RESULT",
        "message_type": 241,
        "source_tx_sequence": 2,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "preWeightGrams": 50_000,
            "postWeightGrams": 2_000,
            "infraredBlocked": False,
            "rawFrameHex": "ef00c3500007d000ef",
        },
    })
    assert store.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert store.get_work_slot() is None
    assert photos.captured == [
        ("clean_open", command["payload"]["operationUid"]),
        ("clean_close", command["payload"]["operationUid"]),
    ]
    store.close()


def test_compat_fullness_supports_all_roles_without_history(
    tmp_path,
):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)

    for role in ("INITIAL", "CONFIRMATION", "MANUAL_RECHECK"):
        command = valid_compat_service_command(
            "sample-fullness.service-wire.json"
        )
        command["commandUid"] = str(uuid.uuid4())
        command["payload"]["detectionUid"] = str(uuid.uuid4())
        command["payload"]["sampleRole"] = role
        command["target"]["uid"] = command["payload"]["detectionUid"]
        command["payloadSha256"] = canonical_payload_sha256(
            command["payload"]
        )
        store.receive_command(
            command["commandUid"],
            command["commandType"],
            command,
        )
        processor.process_next()
        result = store.get_command(command["commandUid"])["result"]
        assert result["fullnessPercent"] == 0
        assert result["fullnessState"] == "NOT_FULL"

    events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(limit=100)
        if row["event_type"] == "FULLNESS_SAMPLE_COMPLETE"
    ]
    assert {
        event["payload"]["sampleRole"]
        for event in events
    } == {"INITIAL", "CONFIRMATION", "MANUAL_RECHECK"}
    assert store.get_work_slot() is None
    assert uart.calls == []
    store.close()


def test_compat_baseline_never_reuses_old_flow_weight_after_restart(
    tmp_path,
):
    database_path = tmp_path / "edge.db"
    store = EdgeStore(str(database_path))
    store.initialize()
    mark_configuration_applied(store)
    store.set_state(
        "fixed_frame_latest_observation_json",
        json.dumps({
            "sourceWorkType": "DELIVERY",
            "sourceWorkUid": str(uuid.uuid4()),
            "portNo": 1,
            "postWeightGrams": 7_777,
            "infraredBlocked": False,
            "mcuBootId": 42,
            "mcuEventSequence": 4,
        }),
    )
    uart = FakeCompatUart()
    processor = CommandProcessor(
        store,
        uart,
        WorkManager(store, uart, None, FakePhotoManager()),
    )
    first = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    store.receive_command(
        first["commandUid"],
        first["commandType"],
        first,
    )
    processor.process_next()
    assert store.get_command(first["commandUid"])["result"] == {
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1_234,
        "compatibilitySource": "FRESH_F0_F1_SNAPSHOT",
    }
    bag_uid = first["payload"]["bagUid"]
    assert store.get_bag_baseline(bag_uid)["weight_grams"] == 1_234
    assert uart.self_test_calls == [3_000]
    store.close()

    store = EdgeStore(str(database_path))
    store.initialize()
    store.set_state(
        "fixed_frame_latest_observation_json",
        json.dumps({
            "sourceWorkType": "CLEAN",
            "sourceWorkUid": str(uuid.uuid4()),
            "portNo": 1,
            "postWeightGrams": 9_999,
            "infraredBlocked": False,
            "mcuBootId": 42,
            "mcuEventSequence": 5,
        }),
    )
    uart = FakeCompatUart()
    uart.self_test_result.update({
        "weightGrams": 2_345,
        "weightMeasurementUid": (
            "6f000000-0000-4000-8000-000000000003"
        ),
        "rawFrameHex": "f1030009290000f1",
    })
    processor = CommandProcessor(
        store,
        uart,
        WorkManager(store, uart, None, FakePhotoManager()),
    )
    second = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    second["commandUid"] = str(uuid.uuid4())
    second["payload"]["measurementUid"] = str(uuid.uuid4())
    second["payload"]["bagUid"] = bag_uid
    second["target"]["uid"] = second["payload"]["measurementUid"]
    second["payloadSha256"] = canonical_payload_sha256(
        second["payload"]
    )
    store.receive_command(
        second["commandUid"],
        second["commandType"],
        second,
    )
    processor.process_next()

    result = store.get_command(second["commandUid"])["result"]
    assert result["reportedWeightGrams"] == 2_345
    assert result["compatibilitySource"] == "FRESH_F0_F1_SNAPSHOT"
    assert store.get_bag_baseline(bag_uid)["weight_grams"] == 2_345
    assert uart.calls == []
    assert uart.self_test_calls == [3_000]
    store.close()


def test_compat_clean_relative_window_expires_without_trusted_wall_clock(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 10_000}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeCompatUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_compat_service_command(
        "start-clean-operation.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    processor.process_next()

    slot = store.get_work_slot()
    assert slot["context"]["operation_window_ms"] == 1_800_000
    assert slot["context"]["operation_started_monotonic_ms"] == 10_000
    ticks["milliseconds"] += 1_800_001

    assert work.expire_fixed_frame_work()
    slot = store.get_work_slot()
    assert slot["work_uid"] == command["payload"]["operationUid"]
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    assert store.get_command(command["commandUid"])["last_error"] == (
        "COMMAND_EXPIRED"
    )
    assert not work.expire_fixed_frame_work()

    delivery = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        delivery["commandUid"],
        delivery["commandType"],
        delivery,
    )
    processor.process_next()

    assert store.get_command(delivery["commandUid"])["state"] == "FAILED"
    assert store.get_command(delivery["commandUid"])["last_error"] == (
        "DEVICE_BUSY"
    )
    assert [call[0] for call in uart.calls] == ["START_CLEAN_OPERATION"]

    processor.process_mcu_event({
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
    })

    assert store.get_work_slot() is None
    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    clean_events = [
        row for row in store.list_pending_events(limit=100)
        if row["event_type"] == "CLEAN_COMPLETE"
    ]
    assert len(clean_events) == 1
    store.close()


def test_clean_window_rejects_a_larger_monotonic_value_from_another_boot(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 10_000}
    boot = {"identity": "linux:boot-a"}
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: None,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: boot["identity"],
    )
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(
        command["commandUid"], command["commandType"], command
    )
    assert processor.process_next()
    start_uid = store.get_command(command["commandUid"])["mcu_command_uid"]
    slot = store.get_work_slot()
    assert slot["context"]["operation_started_boot_identity"] == (
        "linux:boot-a"
    )

    # A reboot can have a larger uptime than the old persisted sample.  The
    # boot identity, not the numeric comparison, must close the unlock path.
    boot["identity"] = "linux:boot-b"
    ticks["milliseconds"] = 20_000
    processor.process_mcu_event(
        clean_preunlock_event(
            command,
            start_uid,
            "53100000-0000-4000-8000-000000000099",
        )
    )

    slot = store.get_work_slot()
    assert slot["work_state"] == "RECOVERY_REQUIRED"
    assert slot["context"]["phase"] == "CLEAN_RECOVERY_REQUIRED"
    assert [call[0] for call in uart.calls] == ["START_CLEAN_OPERATION"]
    store.close()


def test_legacy_clean_window_uses_single_trusted_clock_sample(monkeypatch):
    references = iter(
        [
            datetime(2026, 8, 25, tzinfo=timezone.utc),
            None,
        ]
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: next(references),
    )

    remaining_ms = _remaining_operation_window_ms(
        {"operation_deadline": "2026-08-25T00:30:00.000Z"}
    )

    assert remaining_ms == 1_800_000

from __future__ import annotations

import os
import socket
import threading
from copy import deepcopy

import pytest

from job_safety import (
    DisabledJobSafety,
    JobPermit,
    JobSafetyError,
    PermanentJobSafety,
    PhysicalAction,
    action_digest,
    build_job_safety_from_environment,
    canonical_sha256,
    command_request_digest,
)
from local_control import (
    LocalControlAction,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlServer,
    LocalControlUnavailable,
)


PERMIT_UID = "6d36e92a-b63f-40da-bf65-4b20aef38a9f"
WORK_UID = "cc67cd8b-933e-4c35-9206-b2d9af2e5745"
COMMAND_UID = "9676db52-791f-4e90-b6f6-c50172459b8c"
BEGIN_UID = "e696429a-48ad-4ff6-a593-68fc66d72450"
ACTION_UID = "39cd4143-69bd-427c-a0c7-cf18d58215fb"
RECEIPT_UID = "3bb5f17f-45f8-42c9-a056-b196a57f2a8e"
COMPLETION_UID = "ce9c894b-cda5-410e-9ed0-83d18a2f91db"
DIGEST = "a" * 64
HANDOFF_UID = "8e5640b6-0365-455c-bce6-4983753d7590"
OBSERVATION_DIGEST = "b" * 64
QUIESCE_DIGEST = "c" * 64
FLASH_DIGEST = "d" * 64
TARGET_IDENTITY_DIGEST = "e" * 64
ROLLBACK_IDENTITY_DIGEST = "f" * 64
DISPATCH_TOKEN = "dispatch-attempt-token-0123456789abcdef"


def _command() -> dict:
    return {
        "commandUid": COMMAND_UID,
        "commandType": "START_DELIVERY_SESSION",
        "targetDeviceName": "device-1",
        "issuedAt": "2030-01-01T00:00:00.000Z",
        "expiresAt": "2030-01-01T00:01:00.000Z",
        "payloadSha256": "b" * 64,
        "payload": {"sessionUid": WORK_UID, "portNo": 1},
        "cosGrant": {"temporarySecret": "must-not-enter-digest"},
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, action, payload):
        self.calls.append((action, payload))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_disabled_port_has_no_socket_or_physical_side_effect() -> None:
    safety = DisabledJobSafety()
    assert safety.enabled is False
    assert (
        safety.request_job(
            _command(),
            work_type="DELIVERY",
            work_uid=WORK_UID,
        )
        is None
    )
    with pytest.raises(JobSafetyError) as maintenance:
        safety.require_mcu_maintenance(PERMIT_UID, "QUIESCE")
    assert maintenance.value.code == "MCU_MAINTENANCE_NOT_AUTHORIZED"
    assert (
        safety.prepare_physical_action(
            None,
            action=object(),
            dispatch_attempt_token=DISPATCH_TOKEN,
        )
        is None
    )
    assert safety.arm_physical_action(object()) is None
    assert (
        safety.cancel_prepared_physical_action(
            object(),
            dispatch_attempt_token=DISPATCH_TOKEN,
            evidence_sha256=DIGEST,
        )
        is None
    )


def _maintenance_status(**overrides) -> dict:
    result = {
        "candidateActivationState": "ACTIVE",
        "stage4CandidateEnabled": True,
        "jobGateMode": "ENFORCED",
        "jobPermitRpcEnabled": True,
        "jobGateState": "MAINTENANCE",
        "maintenanceState": "MAINTENANCE",
        "maintenanceOwnerUid": PERMIT_UID,
        "maintenanceType": "MCU_FIRMWARE_UPDATE",
        "maintenancePhase": "MAINTENANCE",
        "maintenanceFenceToken": 7,
        "reconciliationRequired": False,
        "activeJobPermitCount": 0,
        "unreconciledPhysicalActionCount": 0,
        "mcuUpdateCandidateEnabled": True,
        "mcuUpdateCandidate": {
            "activeUpdate": {
                "updateUid": PERMIT_UID,
                "state": "INITIAL_QUIESCE",
                "handoffUid": HANDOFF_UID,
                "maintenanceFenceToken": 7,
                "observationEvidenceSha256": OBSERVATION_DIGEST,
                "quiesceEvidenceSha256": QUIESCE_DIGEST,
                "lastFlashEvidenceSha256": FLASH_DIGEST,
                "targetIdentitySha256": TARGET_IDENTITY_DIGEST,
                "rollbackIdentitySha256": ROLLBACK_IDENTITY_DIGEST,
            }
        },
    }
    result.update(overrides)
    return result


def _require_quiesce(safety: PermanentJobSafety) -> None:
    safety.require_mcu_maintenance(
        PERMIT_UID,
        "QUIESCE",
        handoff_uid=HANDOFF_UID,
        observation_evidence_sha256=OBSERVATION_DIGEST,
    )


def _require_verify(safety: PermanentJobSafety) -> None:
    safety.require_mcu_maintenance(
        PERMIT_UID,
        "VERIFY",
        handoff_uid=HANDOFF_UID,
        quiesce_evidence_sha256=QUIESCE_DIGEST,
        expected_firmware_identity_sha256=TARGET_IDENTITY_DIGEST,
        observed_flash_evidence_sha256=FLASH_DIGEST,
    )


def test_mcu_maintenance_requires_exact_permanent_fence() -> None:
    verify_status = _maintenance_status(
        jobGateState="LOCKED",
        maintenanceState="LOCKED",
        maintenancePhase="LOCKED",
        reconciliationRequired=True,
    )
    verify_status["mcuUpdateCandidate"]["activeUpdate"][
        "state"
    ] = "VERIFYING_TARGET"
    client = FakeClient([_maintenance_status(), verify_status])
    safety = PermanentJobSafety(client)

    _require_quiesce(safety)
    _require_verify(safety)

    assert client.calls == [("GET_STATUS", {}), ("GET_STATUS", {})]


def test_mcu_maintenance_status_selects_control_only_mode_after_handoff() -> None:
    safety = PermanentJobSafety(FakeClient([_maintenance_status()]))

    assert safety.get_mcu_maintenance_status() == {
        "updateUid": PERMIT_UID,
        "maintenanceFenceToken": 7,
        "jobGateState": "MAINTENANCE",
        "maintenancePhase": "MAINTENANCE",
    }


@pytest.mark.parametrize(
    "gate_state,maintenance_state",
    [("DRAINING", "DRAINING"), ("LOCKED", "LOCKED")],
)
def test_pre_hardware_mcu_drain_keeps_normal_recovery_runtime(
    gate_state: str,
    maintenance_state: str,
) -> None:
    status = _maintenance_status(
        jobGateState=gate_state,
        maintenanceState=maintenance_state,
        maintenancePhase="DRAINING",
        reconciliationRequired=gate_state == "LOCKED",
    )
    status["mcuUpdateCandidate"]["activeUpdate"]["state"] = "DRAINING"
    safety = PermanentJobSafety(FakeClient([status]))

    assert safety.get_mcu_maintenance_status() is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda status: status["mcuUpdateCandidate"]["activeUpdate"].update(
            updateUid=WORK_UID
        ),
        lambda status: status["mcuUpdateCandidate"]["activeUpdate"].update(
            maintenanceFenceToken=8
        ),
        lambda status: status.update(mcuUpdateCandidateEnabled=False),
        lambda status: status.update(maintenancePhase="DRAINING"),
    ],
)
def test_mcu_maintenance_status_rejects_cross_generation_or_phase_mismatch(
    mutate,
) -> None:
    status = _maintenance_status()
    mutate(status)
    safety = PermanentJobSafety(FakeClient([status]))

    with pytest.raises(JobSafetyError) as raised:
        safety.get_mcu_maintenance_status()

    assert raised.value.code == "MCU_MAINTENANCE_STATE_INVALID"


@pytest.mark.parametrize(
    "override",
    [
        {"candidateActivationState": "REQUIRED"},
        {"jobGateState": "OPEN", "maintenanceState": "IDLE"},
        {"maintenanceOwnerUid": WORK_UID},
        {"maintenanceType": "BUSINESS_UPDATE"},
        {"maintenanceFenceToken": None},
        {"activeJobPermitCount": 1},
        {"unreconciledPhysicalActionCount": 1},
        {"reconciliationRequired": True},
    ],
)
def test_mcu_quiesce_rejects_incomplete_or_changed_fence(override) -> None:
    safety = PermanentJobSafety(FakeClient([_maintenance_status(**override)]))

    with pytest.raises(JobSafetyError) as raised:
        _require_quiesce(safety)

    assert raised.value.code == "MCU_MAINTENANCE_NOT_AUTHORIZED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("updateUid", WORK_UID),
        ("handoffUid", WORK_UID),
        ("maintenanceFenceToken", 8),
        ("observationEvidenceSha256", "9" * 64),
    ],
)
def test_mcu_quiesce_binds_the_exact_persistent_update_record(
    field: str,
    value: object,
) -> None:
    status = deepcopy(_maintenance_status())
    status["mcuUpdateCandidate"]["activeUpdate"][field] = value
    safety = PermanentJobSafety(FakeClient([status]))

    with pytest.raises(JobSafetyError) as raised:
        _require_quiesce(safety)

    assert raised.value.code == "MCU_MAINTENANCE_NOT_AUTHORIZED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "STARTING_TARGET_VERIFY"),
        ("handoffUid", WORK_UID),
        ("quiesceEvidenceSha256", "9" * 64),
        ("lastFlashEvidenceSha256", "9" * 64),
        ("targetIdentitySha256", "9" * 64),
    ],
)
def test_mcu_verification_binds_handoff_flash_and_expected_identity(
    field: str,
    value: object,
) -> None:
    status = deepcopy(_maintenance_status())
    status["mcuUpdateCandidate"]["activeUpdate"]["state"] = (
        "VERIFYING_TARGET"
    )
    status["mcuUpdateCandidate"]["activeUpdate"][field] = value
    safety = PermanentJobSafety(FakeClient([status]))

    with pytest.raises(JobSafetyError) as raised:
        _require_verify(safety)

    assert raised.value.code == "MCU_MAINTENANCE_NOT_AUTHORIZED"


def test_environment_requires_explicit_candidate_and_absolute_socket() -> None:
    assert isinstance(build_job_safety_from_environment({}), DisabledJobSafety)
    assert isinstance(
        build_job_safety_from_environment(
            {"ECOBIN_STAGE4_JOB_GATE_MODE": "disabled"}
        ),
        DisabledJobSafety,
    )
    with pytest.raises(ValueError, match="disabled or candidate"):
        build_job_safety_from_environment(
            {"ECOBIN_STAGE4_JOB_GATE_MODE": "enabled"}
        )
    with pytest.raises(ValueError, match="must be absolute"):
        build_job_safety_from_environment(
            {
                "ECOBIN_STAGE4_JOB_GATE_MODE": "candidate",
                "ECOBIN_UPDATER_CONTROL_SOCKET": "relative.sock",
            }
        )


def test_request_prepare_arm_confirm_and_complete_use_stable_exact_facts() -> None:
    action = PhysicalAction(
        action_uid=ACTION_UID,
        receipt_uid=RECEIPT_UID,
        action_key="DELIVERY:OPEN:0",
        action_kind="OPEN_DELIVERY_DOOR",
        action_digest_sha256=DIGEST,
    )
    client = FakeClient(
        [
            {
                "permitUid": COMMAND_UID,
                "state": "GRANTED",
                "mayStart": True,
            },
            {"state": "ACTIVE"},
            {
                "actionUid": ACTION_UID,
                "disposition": "ACCEPTED",
                "state": "PREPARED",
                "mayExecute": False,
            },
            {
                "actionUid": ACTION_UID,
                "disposition": "ACCEPTED",
                "state": "ARMED",
                "mayExecute": True,
            },
            {
                "actionUid": ACTION_UID,
                "state": "CONFIRMED",
                "confirmedOutcome": "EXECUTED",
                "confirmationBasis": "MCU_IDENTITY_BOUND_FACT",
                "evidenceDigestSha256": "c" * 64,
            },
            {"state": "COMPLETED"},
        ]
    )
    safety = PermanentJobSafety(client)

    permit = safety.request_job(
        _command(),
        work_type="DELIVERY",
        work_uid=WORK_UID,
    )
    safety.begin_job(permit, begin_uid=BEGIN_UID, digest=DIGEST)
    safety.prepare_physical_action(
        permit,
        action=action,
        dispatch_attempt_token=DISPATCH_TOKEN,
    )
    safety.arm_physical_action(
        action,
        dispatch_attempt_token=DISPATCH_TOKEN,
    )
    safety.confirm_physical_action(
        action,
        outcome="EXECUTED",
        evidence_sha256="c" * 64,
        confirmation_basis="MCU_IDENTITY_BOUND_FACT",
    )
    safety.complete_job(
        permit,
        completion_uid=COMPLETION_UID,
        outcome="SUCCEEDED",
        completion_digest_sha256="d" * 64,
    )

    assert permit.permit_uid == COMMAND_UID
    assert [call[0] for call in client.calls] == [
        "REQUEST_JOB_PERMIT",
        "BEGIN_JOB",
        "PREPARE_PHYSICAL_ACTION",
        "ARM_PHYSICAL_ACTION",
        "CONFIRM_PHYSICAL_ACTION",
        "COMPLETE_JOB",
    ]
    request = client.calls[0][1]
    assert request == {
        "permitUid": COMMAND_UID,
        "workUid": WORK_UID,
        "commandUid": COMMAND_UID,
        "workType": "DELIVERY",
        "requestDigestSha256": command_request_digest(_command()),
    }
    assert "temporarySecret" not in request["requestDigestSha256"]
    prepare = client.calls[2][1]
    assert prepare["actionKey"] == "DELIVERY:OPEN:0"
    assert prepare["dispatchAttemptToken"] == DISPATCH_TOKEN
    assert client.calls[3][1] == {
        "actionUid": ACTION_UID,
        "dispatchAttemptToken": DISPATCH_TOKEN,
    }
    assert client.calls[4][1]["confirmationBasis"] == (
        "MCU_IDENTITY_BOUND_FACT"
    )


def test_restored_database_cannot_inherit_an_existing_action_authority() -> None:
    existing_uid = "ca76ea1b-f94d-4f94-84bc-d16635d40dd8"
    client = FakeClient(
        [
            {
                "actionUid": existing_uid,
                "state": "NOT_EXECUTED",
            }
        ]
    )
    safety = PermanentJobSafety(client)

    with pytest.raises(
        JobSafetyError,
        match="PHYSICAL_ACTION_ALREADY_RECORDED",
    ):
        safety.prepare_physical_action(
            JobPermit(
                PERMIT_UID,
                WORK_UID,
                COMMAND_UID,
                "DELIVERY",
                DIGEST,
            ),
            action=PhysicalAction(
                ACTION_UID,
                RECEIPT_UID,
                "DELIVERY:OPEN:0",
                "OPEN_DELIVERY_DOOR",
                DIGEST,
            ),
            dispatch_attempt_token=DISPATCH_TOKEN,
        )
    assert [call[0] for call in client.calls] == [
        "PREPARE_PHYSICAL_ACTION"
    ]


def test_arm_retries_a_lost_response_with_the_same_dispatch_token() -> None:
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "DELIVERY:OPEN:0",
        "OPEN_DELIVERY_DOOR",
        DIGEST,
    )
    client = FakeClient(
        [
            LocalControlUnavailable("response lost after commit"),
            {
                "actionUid": ACTION_UID,
                "disposition": "DUPLICATE",
                "state": "ARMED",
                "mayExecute": True,
            },
        ]
    )

    PermanentJobSafety(client).arm_physical_action(
        action,
        dispatch_attempt_token=DISPATCH_TOKEN,
    )

    assert client.calls == [
        (
            "ARM_PHYSICAL_ACTION",
            {
                "actionUid": ACTION_UID,
                "dispatchAttemptToken": DISPATCH_TOKEN,
            },
        ),
        (
            "ARM_PHYSICAL_ACTION",
            {
                "actionUid": ACTION_UID,
                "dispatchAttemptToken": DISPATCH_TOKEN,
            },
        ),
    ]


@pytest.mark.parametrize("uncertain_code", ["RESULT_UNKNOWN", "SERVICE_STOPPING"])
def test_arm_retries_an_uncertain_remote_result_with_the_exact_same_payload(
    uncertain_code: str,
) -> None:
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "DELIVERY:OPEN:0",
        "OPEN_DELIVERY_DOOR",
        DIGEST,
    )
    client = FakeClient(
        [
            LocalControlRemoteError(
                uncertain_code,
                "handler may still commit",
                PERMIT_UID,
            ),
            {
                "actionUid": ACTION_UID,
                "disposition": "DUPLICATE",
                "state": "ARMED",
                "mayExecute": True,
            },
        ]
    )

    PermanentJobSafety(client).arm_physical_action(
        action,
        dispatch_attempt_token=DISPATCH_TOKEN,
    )

    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert client.calls[0][1] is client.calls[1][1]
    assert client.calls[0] == (
        "ARM_PHYSICAL_ACTION",
        {
            "actionUid": ACTION_UID,
            "dispatchAttemptToken": DISPATCH_TOKEN,
        },
    )


@pytest.mark.parametrize("uncertain_code", ["RESULT_UNKNOWN", "SERVICE_STOPPING"])
def test_repeated_uncertain_remote_result_fails_as_job_gate_unavailable(
    uncertain_code: str,
) -> None:
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "DELIVERY:OPEN:0",
        "OPEN_DELIVERY_DOOR",
        DIGEST,
    )
    client = FakeClient(
        [
            LocalControlRemoteError(
                uncertain_code,
                "handler may still commit",
                PERMIT_UID,
            ),
            LocalControlRemoteError(
                uncertain_code,
                "handler may still commit",
                PERMIT_UID,
            ),
        ]
    )

    with pytest.raises(JobSafetyError) as raised:
        PermanentJobSafety(client).arm_physical_action(
            action,
            dispatch_attempt_token=DISPATCH_TOKEN,
        )

    assert raised.value.code == "JOB_GATE_UNAVAILABLE"
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert client.calls[0][1] is client.calls[1][1]


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(socket, "AF_UNIX"),
    reason="real peer-authenticated Unix sockets require POSIX",
)
def test_processing_timeouts_retry_exact_payload_and_later_converge(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)
    runtime_dir.chmod(0o700)
    socket_path = runtime_dir / "updater.sock"
    caller_uid = os.getuid()

    calls_lock = threading.Lock()
    commit_lock = threading.Lock()
    first_started = threading.Event()
    first_may_commit = threading.Event()
    first_committed = threading.Event()
    second_started = threading.Event()
    second_may_return = threading.Event()
    second_finished = threading.Event()
    received_payloads: list[dict[str, str]] = []
    committed = False

    def arm_handler(payload: dict[str, str]) -> dict[str, object]:
        nonlocal committed
        with calls_lock:
            received_payloads.append(dict(payload))
            call_number = len(received_payloads)

        if call_number == 1:
            with commit_lock:
                first_started.set()
                if not first_may_commit.wait(timeout=3.0):
                    raise RuntimeError("test did not release the first commit")
                committed = True
                first_committed.set()
                return {
                    "actionUid": payload["actionUid"],
                    "disposition": "ACCEPTED",
                    "state": "ARMED",
                    "mayExecute": True,
                }

        if call_number == 2:
            second_started.set()
            with commit_lock:
                if not second_may_return.wait(timeout=3.0):
                    raise RuntimeError("test did not release the second handler")
                assert committed is True
                second_finished.set()
                return {
                    "actionUid": payload["actionUid"],
                    "disposition": "DUPLICATE",
                    "state": "ARMED",
                    "mayExecute": True,
                }

        with commit_lock:
            assert committed is True
            return {
                "actionUid": payload["actionUid"],
                "disposition": "DUPLICATE",
                "state": "ARMED",
                "mayExecute": True,
            }

    allowed_uids = frozenset({caller_uid})
    server = LocalControlServer(
        socket_path,
        protocol_name="ecobin.updater.control",
        actions={
            "ARM_PHYSICAL_ACTION": LocalControlAction(
                arm_handler,
                payload_fields=frozenset(
                    {"actionUid", "dispatchAttemptToken"}
                ),
                allowed_uids=allowed_uids,
            )
        },
        allowed_uids=allowed_uids,
        request_timeout_seconds=0.5,
        processing_timeout_seconds=0.05,
    )
    safety = PermanentJobSafety(
        LocalControlClient(
            socket_path,
            protocol_name="ecobin.updater.control",
            connect_timeout_seconds=0.5,
            response_timeout_seconds=0.5,
        )
    )
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "DELIVERY:OPEN:0",
        "OPEN_DELIVERY_DOOR",
        DIGEST,
    )
    outcome: list[BaseException | None] = []

    def invoke_arm() -> None:
        try:
            safety.arm_physical_action(
                action,
                dispatch_attempt_token=DISPATCH_TOKEN,
            )
        except BaseException as error:
            outcome.append(error)
        else:
            outcome.append(None)

    caller = threading.Thread(target=invoke_arm, daemon=True)
    server.start()
    try:
        caller.start()
        assert first_started.wait(timeout=1.0)
        # The retry cannot reach the second handler until the server has
        # returned RESULT_UNKNOWN for the still-running first handler.
        assert second_started.wait(timeout=2.0)
        first_may_commit.set()
        assert first_committed.wait(timeout=1.0)

        # Keep the idempotent retry in-flight beyond the second processing
        # deadline even though the first worker has now committed.
        caller.join(timeout=2.0)
        assert not caller.is_alive()
        assert len(outcome) == 1
        assert isinstance(outcome[0], JobSafetyError)
        assert outcome[0].code == "JOB_GATE_UNAVAILABLE"

        second_may_return.set()
        assert second_finished.wait(timeout=1.0)

        # A later durable-inbox retry uses the same action identity and
        # converges on the commit left by the timed-out first worker.
        safety.arm_physical_action(
            action,
            dispatch_attempt_token=DISPATCH_TOKEN,
        )

        expected_payload = {
            "actionUid": ACTION_UID,
            "dispatchAttemptToken": DISPATCH_TOKEN,
        }
        assert received_payloads == [
            expected_payload,
            expected_payload,
            expected_payload,
        ]
    finally:
        first_may_commit.set()
        second_may_return.set()
        caller.join(timeout=1.0)
        server.stop()


@pytest.mark.parametrize(
    ("operation", "response", "expected_action", "expected_basis"),
    [
        (
            "cancel",
            {
                "actionUid": ACTION_UID,
                "state": "CONFIRMED",
                "confirmedOutcome": "NOT_EXECUTED",
                "confirmationBasis": "PREPARED_NOT_ARMED",
                "evidenceDigestSha256": "e" * 64,
            },
            "CANCEL_PREPARED_PHYSICAL_ACTION",
            "PREPARED_NOT_ARMED",
        ),
        (
            "abort",
            {
                "actionUid": ACTION_UID,
                "state": "CONFIRMED",
                "confirmedOutcome": "NOT_EXECUTED",
                "confirmationBasis": "LIVE_DISPATCH_NOT_WRITTEN",
                "evidenceDigestSha256": "e" * 64,
            },
            "ABORT_PHYSICAL_ACTION_DISPATCH",
            "LIVE_DISPATCH_NOT_WRITTEN",
        ),
    ],
)
def test_zero_effect_closure_requires_permanent_basis(
    operation,
    response,
    expected_action,
    expected_basis,
) -> None:
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "DELIVERY:OPEN:0",
        "OPEN_DELIVERY_DOOR",
        DIGEST,
    )
    client = FakeClient([response])
    safety = PermanentJobSafety(client)

    if operation == "cancel":
        safety.cancel_prepared_physical_action(
            action,
            dispatch_attempt_token=DISPATCH_TOKEN,
            evidence_sha256="e" * 64,
        )
    else:
        safety.abort_physical_action_dispatch(
            action,
            dispatch_attempt_token=DISPATCH_TOKEN,
            evidence_sha256="e" * 64,
        )

    request_action, payload = client.calls[0]
    assert request_action == expected_action
    assert payload["actionUid"] == ACTION_UID
    assert payload["receiptUid"] == RECEIPT_UID
    assert response["confirmationBasis"] == expected_basis
    assert payload["dispatchAttemptToken"] == DISPATCH_TOKEN


@pytest.mark.parametrize("outcome", ["EXECUTED", "FAILED_SAFE"])
def test_live_fixed_frame_result_uses_token_bound_confirmation(outcome) -> None:
    action = PhysicalAction(
        ACTION_UID,
        RECEIPT_UID,
        "FULLNESS:SAMPLE:0",
        "SAMPLE_FULLNESS",
        DIGEST,
    )
    client = FakeClient(
        [
            {
                "actionUid": ACTION_UID,
                "state": "CONFIRMED",
                "confirmedOutcome": outcome,
                "confirmationBasis": "LIVE_FIXED_FRAME_RESULT",
                "evidenceDigestSha256": "f" * 64,
            }
        ]
    )

    PermanentJobSafety(client).confirm_live_physical_action_result(
        action,
        dispatch_attempt_token=DISPATCH_TOKEN,
        outcome=outcome,
        evidence_sha256="f" * 64,
    )

    assert client.calls == [
        (
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT",
            {
                "actionUid": ACTION_UID,
                "receiptUid": RECEIPT_UID,
                "dispatchAttemptToken": DISPATCH_TOKEN,
                "outcome": outcome,
                "evidenceDigestSha256": "f" * 64,
            },
        )
    ]


def test_new_request_cannot_inherit_an_already_active_permit() -> None:
    safety = PermanentJobSafety(
        FakeClient(
            [
                {
                    "permitUid": PERMIT_UID,
                    "state": "ACTIVE",
                    "mayStart": True,
                }
            ]
        )
    )

    with pytest.raises(JobSafetyError, match="JOB_PERMIT_NOT_GRANTED"):
        safety.request_job(
            _command(),
            work_type="DELIVERY",
            work_uid=WORK_UID,
            permit_uid=PERMIT_UID,
        )


def test_request_rejects_a_different_permit_identity_from_updater() -> None:
    safety = PermanentJobSafety(
        FakeClient(
            [
                {
                    "permitUid": PERMIT_UID,
                    "state": "GRANTED",
                    "mayStart": True,
                }
            ]
        )
    )

    with pytest.raises(
        JobSafetyError,
        match="JOB_PERMIT_IDENTITY_MISMATCH",
    ):
        safety.request_job(
            _command(),
            work_type="DELIVERY",
            work_uid=WORK_UID,
        )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (LocalControlUnavailable("down"), "JOB_GATE_UNAVAILABLE"),
        (
            LocalControlRemoteError(
                "JOB_GATE_LOCKED",
                "locked",
                PERMIT_UID,
            ),
            "JOB_GATE_LOCKED",
        ),
    ],
)
def test_local_failures_are_stable_and_fail_closed(
    failure: BaseException,
    expected_code: str,
) -> None:
    responses = (
        [failure, failure]
        if isinstance(failure, LocalControlUnavailable)
        else [failure]
    )
    safety = PermanentJobSafety(FakeClient(responses))
    with pytest.raises(JobSafetyError) as raised:
        safety.request_job(
            _command(),
            work_type="DELIVERY",
            work_uid=WORK_UID,
            permit_uid=PERMIT_UID,
        )
    assert raised.value.code == expected_code


def test_digests_are_canonical_and_exclude_only_ephemeral_cos_grant() -> None:
    first = _command()
    second = {key: first[key] for key in reversed(first)}
    second["cosGrant"] = {"different": "ephemeral"}
    assert command_request_digest(first) == command_request_digest(second)
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256(
        {"a": 1, "b": 2}
    )
    assert action_digest(
        work_uid=WORK_UID,
        command_uid=COMMAND_UID,
        action_key="DELIVERY:OPEN:0",
        action_kind="OPEN_DELIVERY_DOOR",
        payload={"portNo": 1},
    ) != action_digest(
        work_uid=WORK_UID,
        command_uid=COMMAND_UID,
        action_key="DELIVERY:OPEN:1",
        action_kind="OPEN_DELIVERY_DOOR",
        payload={"portNo": 1},
    )

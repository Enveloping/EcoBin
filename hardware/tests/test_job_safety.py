from __future__ import annotations

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
from local_control import LocalControlRemoteError, LocalControlUnavailable


PERMIT_UID = "6d36e92a-b63f-40da-bf65-4b20aef38a9f"
WORK_UID = "cc67cd8b-933e-4c35-9206-b2d9af2e5745"
COMMAND_UID = "9676db52-791f-4e90-b6f6-c50172459b8c"
BEGIN_UID = "e696429a-48ad-4ff6-a593-68fc66d72450"
ACTION_UID = "39cd4143-69bd-427c-a0c7-cf18d58215fb"
ARM_UID = "dd90d026-4a7a-41c5-bbba-6403bc255c99"
RECEIPT_UID = "3bb5f17f-45f8-42c9-a056-b196a57f2a8e"
COMPLETION_UID = "ce9c894b-cda5-410e-9ed0-83d18a2f91db"
DIGEST = "a" * 64


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
    assert safety.authorize_physical_action(object()) is None


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


def test_request_begin_arm_confirm_and_complete_use_stable_exact_facts() -> None:
    action = PhysicalAction(
        action_uid=ACTION_UID,
        arm_uid=ARM_UID,
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
                "state": "MAY_HAVE_EXECUTED",
                "mayExecute": True,
            },
            {"state": "CONFIRMED"},
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
    safety.authorize_physical_action(permit, action=action)
    safety.confirm_physical_action(
        action,
        outcome="EXECUTED",
        evidence_sha256="c" * 64,
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
        "AUTHORIZE_PHYSICAL_ACTION",
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
    assert client.calls[2][1]["actionKey"] == "DELIVERY:OPEN:0"


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
        safety.authorize_physical_action(
            JobPermit(
                PERMIT_UID,
                WORK_UID,
                COMMAND_UID,
                "DELIVERY",
                DIGEST,
            ),
            action=PhysicalAction(
                ACTION_UID,
                ARM_UID,
                RECEIPT_UID,
                "DELIVERY:OPEN:0",
                "OPEN_DELIVERY_DOOR",
                DIGEST,
            ),
        )
    assert [call[0] for call in client.calls] == [
        "AUTHORIZE_PHYSICAL_ACTION"
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

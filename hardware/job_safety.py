"""Business-side client for the permanent physical-job safety boundary.

The replaceable business database is deliberately not the authority for
whether a physical effect may run again.  This module gives ``WorkManager`` a
small port into the permanent updater database without exposing that database
or coupling business code to updater internals.

Stage four remains disabled unless the process is started with the explicit
candidate mode.  Even in candidate mode the updater starts LOCKED and must be
reconciled by the controlled migration procedure before it can grant work.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)


UPDATER_PROTOCOL_NAME = "ecobin.updater.control"
DEFAULT_UPDATER_SOCKET = "/run/ecobin/updater/control.sock"
STAGE4_MODE_ENVIRONMENT = "ECOBIN_STAGE4_JOB_GATE_MODE"
UPDATER_SOCKET_ENVIRONMENT = "ECOBIN_UPDATER_CONTROL_SOCKET"

_DISABLED_MODES = frozenset({"", "disabled"})
_CANDIDATE_MODE = "candidate"


class JobSafetyError(RuntimeError):
    """A stable failure which must prevent a new physical effect."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class JobPermit:
    permit_uid: str
    work_uid: str
    command_uid: str
    work_type: str
    request_digest_sha256: str


@dataclass(frozen=True)
class PhysicalAction:
    action_uid: str
    receipt_uid: str
    action_key: str
    action_kind: str
    action_digest_sha256: str


class DisabledJobSafety:
    """Truthful compatibility port used until the candidate is activated."""

    enabled = False

    def request_job(
        self,
        command: Mapping[str, Any],
        *,
        work_type: str,
        work_uid: str,
    ) -> None:
        del command, work_type, work_uid
        return None

    def begin_job(self, permit: None, *, begin_uid: str, digest: str) -> None:
        del permit, begin_uid, digest

    def abandon_job(
        self,
        permit: None,
        *,
        disposition_uid: str,
        evidence_sha256: str,
    ) -> None:
        del permit, disposition_uid, evidence_sha256

    def prepare_physical_action(
        self,
        permit: JobPermit | None,
        *,
        action: PhysicalAction,
        dispatch_attempt_token: str,
    ) -> None:
        del permit, action, dispatch_attempt_token
        return None

    def arm_physical_action(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    def cancel_prepared_physical_action(
        self,
        action: PhysicalAction,
        *,
        dispatch_attempt_token: str,
        evidence_sha256: str,
    ) -> None:
        del action, dispatch_attempt_token, evidence_sha256
        return None

    def abort_physical_action_dispatch(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        del args, kwargs
        return None

    def confirm_live_physical_action_result(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        del args, kwargs
        return None

    def confirm_physical_action(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def complete_job(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def require_mcu_maintenance(
        self,
        update_uid: str,
        stage: str,
        **bindings: str,
    ) -> None:
        del update_uid, stage, bindings
        raise JobSafetyError(
            "MCU_MAINTENANCE_NOT_AUTHORIZED",
            "permanent MCU maintenance is not enabled",
        )

    def get_mcu_maintenance_status(self) -> None:
        return None


class PermanentJobSafety:
    """Fail-closed caller for updater-owned permits and action receipts."""

    enabled = True

    def __init__(
        self,
        client: LocalControlClient,
        *,
        uuid_factory: Any = uuid.uuid4,
    ) -> None:
        self._client = client
        self._uuid_factory = uuid_factory

    def new_uid(self) -> str:
        value = self._uuid_factory()
        if not isinstance(value, uuid.UUID) or value.version != 4:
            raise ValueError("job safety identity factory must return UUIDv4")
        return str(value)

    def request_job(
        self,
        command: Mapping[str, Any],
        *,
        work_type: str,
        work_uid: str,
        permit_uid: str | None = None,
    ) -> JobPermit:
        command_uid = _require_uuid4(command.get("commandUid"), "commandUid")
        work_uid = _require_uuid4(work_uid, "workUid")
        if work_type not in {"DELIVERY", "CLEAN", "FULLNESS", "BASELINE"}:
            raise ValueError("unsupported physical work type")
        request_digest = command_request_digest(command)
        # One platform command owns exactly one physical job.  Reusing its
        # already-durable UUID means a lost local RPC response can be queried
        # or retried without inventing a second permit identity.
        requested_permit_uid = _require_uuid4(
            permit_uid or command_uid,
            "permitUid",
        )
        result = self._request(
            "REQUEST_JOB_PERMIT",
            {
                "permitUid": requested_permit_uid,
                "workUid": work_uid,
                "commandUid": command_uid,
                "workType": work_type,
                "requestDigestSha256": request_digest,
            },
        )
        canonical_uid = _require_uuid4(result.get("permitUid"), "permitUid")
        if canonical_uid != requested_permit_uid:
            raise JobSafetyError(
                "JOB_PERMIT_IDENTITY_MISMATCH",
                "permanent updater returned a different job permit identity",
            )
        if (
            result.get("state") != "GRANTED"
            or result.get("mayStart") is not True
        ):
            raise JobSafetyError(
                str(result.get("errorCode") or "JOB_PERMIT_NOT_GRANTED"),
                "permanent updater did not grant a new physical job",
            )
        return JobPermit(
            permit_uid=canonical_uid,
            work_uid=work_uid,
            command_uid=command_uid,
            work_type=work_type,
            request_digest_sha256=request_digest,
        )

    def begin_job(
        self,
        permit: JobPermit,
        *,
        begin_uid: str,
        digest: str,
    ) -> None:
        result = self._request(
            "BEGIN_JOB",
            {
                "permitUid": _require_uuid4(
                    permit.permit_uid,
                    "permitUid",
                ),
                "beginUid": _require_uuid4(begin_uid, "beginUid"),
                "permitDigestSha256": _require_sha256(
                    digest,
                    "permitDigestSha256",
                ),
            },
        )
        if result.get("state") != "ACTIVE":
            raise JobSafetyError(
                str(result.get("errorCode") or "JOB_BEGIN_NOT_CONFIRMED"),
                "permanent updater did not confirm the active job",
            )

    def abandon_job(
        self,
        permit: JobPermit,
        *,
        disposition_uid: str,
        evidence_sha256: str,
    ) -> None:
        result = self._request(
            "ABANDON_JOB_PERMIT",
            {
                "permitUid": permit.permit_uid,
                "dispositionUid": _require_uuid4(
                    disposition_uid,
                    "dispositionUid",
                ),
                "evidenceSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceSha256",
                ),
            },
        )
        if result.get("state") != "ABANDONED":
            raise JobSafetyError(
                str(result.get("errorCode") or "JOB_ABANDON_NOT_CONFIRMED"),
                "unused job permit was not safely abandoned",
            )

    def prepare_physical_action(
        self,
        permit: JobPermit,
        *,
        action: PhysicalAction,
        dispatch_attempt_token: str,
    ) -> None:
        prepared = self._request(
            "PREPARE_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "permitUid": permit.permit_uid,
                "workUid": permit.work_uid,
                "commandUid": permit.command_uid,
                "actionKey": action.action_key,
                "actionKind": action.action_kind,
                "actionDigestSha256": action.action_digest_sha256,
                "dispatchAttemptToken": dispatch_attempt_token,
            },
        )
        canonical_action_uid = _require_uuid4(
            prepared.get("actionUid"),
            "actionUid",
        )
        if canonical_action_uid != action.action_uid:
            # A restored business database generated a new action UUID for an
            # already-known logical effect.  It must never inherit authority
            # to replay the existing action.
            raise JobSafetyError(
                "PHYSICAL_ACTION_ALREADY_RECORDED",
                "logical physical action already exists in the permanent ledger",
            )
        if (
            prepared.get("state") != "PREPARED"
            or prepared.get("mayExecute") is not False
            or prepared.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
        ):
            raise JobSafetyError(
                str(
                    prepared.get("errorCode")
                    or "PHYSICAL_ACTION_NOT_PREPARED"
                ),
                "permanent ledger did not prepare the physical action",
            )

    def arm_physical_action(
        self,
        action: PhysicalAction,
        *,
        dispatch_attempt_token: str,
    ) -> None:
        armed = self._request(
            "ARM_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "dispatchAttemptToken": dispatch_attempt_token,
            },
        )
        canonical_action_uid = _require_uuid4(
            armed.get("actionUid"),
            "actionUid",
        )
        if canonical_action_uid != action.action_uid:
            raise JobSafetyError(
                "PHYSICAL_ACTION_IDENTITY_MISMATCH",
                "permanent updater returned a different physical action",
            )
        if (
            armed.get("state") != "ARMED"
            or armed.get("mayExecute") is not True
            or armed.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
        ):
            raise JobSafetyError(
                str(armed.get("errorCode") or "PHYSICAL_ACTION_NOT_ARMED"),
                "permanent ledger did not open the UART dispatch gate",
            )

    def cancel_prepared_physical_action(
        self,
        action: PhysicalAction,
        *,
        dispatch_attempt_token: str,
        evidence_sha256: str,
    ) -> None:
        result = self._request(
            "CANCEL_PREPARED_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "receiptUid": action.receipt_uid,
                "dispatchAttemptToken": dispatch_attempt_token,
                "evidenceDigestSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceDigestSha256",
                ),
            },
        )
        self._require_not_executed_confirmation(
            result,
            action,
            basis="PREPARED_NOT_ARMED",
            evidence_sha256=evidence_sha256,
        )

    def abort_physical_action_dispatch(
        self,
        action: PhysicalAction,
        *,
        dispatch_attempt_token: str,
        evidence_sha256: str,
    ) -> None:
        result = self._request(
            "ABORT_PHYSICAL_ACTION_DISPATCH",
            {
                "actionUid": action.action_uid,
                "receiptUid": action.receipt_uid,
                "dispatchAttemptToken": dispatch_attempt_token,
                "evidenceDigestSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceDigestSha256",
                ),
            },
        )
        self._require_not_executed_confirmation(
            result,
            action,
            basis="LIVE_DISPATCH_NOT_WRITTEN",
            evidence_sha256=evidence_sha256,
        )

    @staticmethod
    def _require_not_executed_confirmation(
        result: Mapping[str, Any],
        action: PhysicalAction,
        *,
        basis: str,
        evidence_sha256: str,
    ) -> None:
        canonical_action_uid = _require_uuid4(
            result.get("actionUid"),
            "actionUid",
        )
        if canonical_action_uid != action.action_uid:
            raise JobSafetyError(
                "PHYSICAL_ACTION_IDENTITY_MISMATCH",
                "permanent updater returned a different physical action",
            )
        if (
            result.get("state") != "CONFIRMED"
            or result.get("confirmedOutcome") != "NOT_EXECUTED"
            or result.get("confirmationBasis") != basis
            or result.get("evidenceDigestSha256") != evidence_sha256
        ):
            raise JobSafetyError(
                str(
                    result.get("errorCode")
                    or "PHYSICAL_ACTION_UNCONFIRMED"
                ),
                "permanent ledger did not confirm zero physical effect",
            )

    def confirm_physical_action(
        self,
        action: PhysicalAction,
        *,
        outcome: str,
        evidence_sha256: str,
        confirmation_basis: str,
    ) -> None:
        result = self._request(
            "CONFIRM_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "receiptUid": action.receipt_uid,
                "outcome": outcome,
                "confirmationBasis": confirmation_basis,
                "evidenceDigestSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceDigestSha256",
                ),
            },
        )
        if (
            result.get("actionUid") != action.action_uid
            or result.get("state") != "CONFIRMED"
            or result.get("confirmedOutcome") != outcome
            or result.get("confirmationBasis") != confirmation_basis
            or result.get("evidenceDigestSha256") != evidence_sha256
        ):
            raise JobSafetyError(
                str(result.get("errorCode") or "PHYSICAL_ACTION_UNCONFIRMED"),
                "physical action outcome was not durably confirmed",
            )

    def confirm_live_physical_action_result(
        self,
        action: PhysicalAction,
        *,
        dispatch_attempt_token: str,
        outcome: str,
        evidence_sha256: str,
    ) -> None:
        result = self._request(
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT",
            {
                "actionUid": action.action_uid,
                "receiptUid": action.receipt_uid,
                "dispatchAttemptToken": dispatch_attempt_token,
                "outcome": outcome,
                "evidenceDigestSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceDigestSha256",
                ),
            },
        )
        if (
            result.get("actionUid") != action.action_uid
            or result.get("state") != "CONFIRMED"
            or result.get("confirmedOutcome") != outcome
            or result.get("confirmationBasis")
            != "LIVE_FIXED_FRAME_RESULT"
            or result.get("evidenceDigestSha256") != evidence_sha256
        ):
            raise JobSafetyError(
                str(result.get("errorCode") or "PHYSICAL_ACTION_UNCONFIRMED"),
                "live fixed-frame result was not durably confirmed",
            )

    def get_physical_action(self, action_uid: str) -> dict[str, Any]:
        return self._request(
            "GET_PHYSICAL_ACTION",
            {"actionUid": _require_uuid4(action_uid, "actionUid")},
        )

    def get_job_permit(self, permit_uid: str) -> dict[str, Any]:
        return self._request(
            "GET_JOB_PERMIT",
            {"permitUid": _require_uuid4(permit_uid, "permitUid")},
        )

    def complete_job(
        self,
        permit: JobPermit,
        *,
        completion_uid: str,
        outcome: str,
        completion_digest_sha256: str,
    ) -> None:
        result = self._request(
            "COMPLETE_JOB",
            {
                "permitUid": permit.permit_uid,
                "completionUid": _require_uuid4(
                    completion_uid,
                    "completionUid",
                ),
                "outcome": outcome,
                "completionDigestSha256": _require_sha256(
                    completion_digest_sha256,
                    "completionDigestSha256",
                ),
            },
        )
        if result.get("state") not in {
            "COMPLETED",
        }:
            raise JobSafetyError(
                str(result.get("errorCode") or "JOB_COMPLETION_UNCONFIRMED"),
                "permanent updater did not confirm job completion",
            )

    def require_mcu_maintenance(
        self,
        update_uid: str,
        stage: str,
        *,
        handoff_uid: str,
        observation_evidence_sha256: str | None = None,
        quiesce_evidence_sha256: str | None = None,
        expected_firmware_identity_sha256: str | None = None,
        observed_flash_evidence_sha256: str | None = None,
    ) -> None:
        """Prove the updater journal authorizes this exact MCU handoff."""

        owner_uid = _require_uuid4(update_uid, "updateUid")
        expected_handoff_uid = _require_uuid4(handoff_uid, "handoffUid")
        if stage not in {"QUIESCE", "VERIFY"}:
            raise ValueError("MCU maintenance stage is invalid")
        if stage == "QUIESCE":
            expected_observation = _require_sha256(
                observation_evidence_sha256,
                "observationEvidenceSha256",
            )
            if any(
                value is not None
                for value in (
                    quiesce_evidence_sha256,
                    expected_firmware_identity_sha256,
                    observed_flash_evidence_sha256,
                )
            ):
                raise ValueError("MCU quiesce authorization bindings are invalid")
            expected_quiesce = None
            expected_identity = None
            expected_flash = None
        else:
            expected_quiesce = _require_sha256(
                quiesce_evidence_sha256,
                "quiesceEvidenceSha256",
            )
            expected_identity = _require_sha256(
                expected_firmware_identity_sha256,
                "expectedFirmwareIdentitySha256",
            )
            expected_flash = _require_sha256(
                observed_flash_evidence_sha256,
                "observedFlashEvidenceSha256",
            )
            if observation_evidence_sha256 is not None:
                raise ValueError("MCU verification authorization bindings are invalid")
            expected_observation = None
        status = self._request("GET_STATUS", {})
        candidate = status.get("mcuUpdateCandidate")
        active_update = (
            candidate.get("activeUpdate")
            if isinstance(candidate, Mapping)
            else None
        )
        common = bool(
            status.get("candidateActivationState") == "ACTIVE"
            and status.get("stage4CandidateEnabled") is True
            and status.get("jobGateMode") == "ENFORCED"
            and status.get("jobPermitRpcEnabled") is True
            and status.get("mcuUpdateCandidateEnabled") is True
            and status.get("maintenanceOwnerUid") == owner_uid
            and status.get("maintenanceType") == "MCU_FIRMWARE_UPDATE"
            and isinstance(status.get("maintenanceFenceToken"), int)
            and not isinstance(status.get("maintenanceFenceToken"), bool)
            and status["maintenanceFenceToken"] > 0
            and status.get("activeJobPermitCount") == 0
            and status.get("unreconciledPhysicalActionCount") == 0
            and isinstance(active_update, Mapping)
            and active_update.get("updateUid") == owner_uid
            and active_update.get("handoffUid") == expected_handoff_uid
            and active_update.get("maintenanceFenceToken")
            == status.get("maintenanceFenceToken")
        )
        if stage == "QUIESCE":
            authorized = bool(
                common
                and status.get("jobGateState") == "MAINTENANCE"
                and status.get("maintenanceState") == "MAINTENANCE"
                and status.get("maintenancePhase") == "MAINTENANCE"
                and status.get("reconciliationRequired") is False
                and active_update.get("state")
                in {
                    "INITIAL_QUIESCE",
                    "PREPARING_TARGET_RETRY",
                    "PREPARING_ROLLBACK",
                }
                and active_update.get("observationEvidenceSha256")
                == expected_observation
            )
        else:
            update_state = active_update.get("state") if common else None
            journal_identity = None
            if update_state == "VERIFYING_TARGET":
                journal_identity = active_update.get("targetIdentitySha256")
            elif update_state == "VERIFYING_ROLLBACK":
                journal_identity = active_update.get("rollbackIdentitySha256")
            authorized = bool(
                common
                and status.get("jobGateState") in {"MAINTENANCE", "LOCKED"}
                and status.get("maintenanceState")
                in {"MAINTENANCE", "LOCKED"}
                and status.get("maintenancePhase")
                in {"MAINTENANCE", "LOCKED"}
                and update_state in {"VERIFYING_TARGET", "VERIFYING_ROLLBACK"}
                and active_update.get("quiesceEvidenceSha256")
                == expected_quiesce
                and active_update.get("lastFlashEvidenceSha256")
                == expected_flash
                and journal_identity == expected_identity
            )
        if not authorized:
            raise JobSafetyError(
                "MCU_MAINTENANCE_NOT_AUTHORIZED",
                "permanent updater did not confirm a drained MCU maintenance fence",
            )

    def get_mcu_maintenance_status(self) -> dict[str, Any] | None:
        """Return the exact permanent MCU hold used for control-only boot."""

        status = self._request("GET_STATUS", {})
        if status.get("maintenanceType") != "MCU_FIRMWARE_UPDATE":
            return None
        candidate = status.get("mcuUpdateCandidate")
        active_update = (
            candidate.get("activeUpdate")
            if isinstance(candidate, Mapping)
            else None
        )
        owner_uid = _require_uuid4(
            status.get("maintenanceOwnerUid"),
            "maintenanceOwnerUid",
        )
        fence = status.get("maintenanceFenceToken")
        if (
            isinstance(fence, bool)
            or not isinstance(fence, int)
            or fence < 1
            or status.get("candidateActivationState") != "ACTIVE"
            or status.get("stage4CandidateEnabled") is not True
            or status.get("jobGateMode") != "ENFORCED"
            or status.get("jobPermitRpcEnabled") is not True
            or status.get("mcuUpdateCandidateEnabled") is not True
            or not isinstance(active_update, Mapping)
            or active_update.get("updateUid") != owner_uid
            or active_update.get("maintenanceFenceToken") != fence
        ):
            raise JobSafetyError(
                "MCU_MAINTENANCE_STATE_INVALID",
                "permanent MCU maintenance state is internally inconsistent",
            )
        phase = status.get("maintenancePhase")
        if phase == "DRAINING":
            # The MCU and UART have not been handed off yet.  The full
            # business runtime must be allowed to recover an interrupted job
            # and expose the observation endpoint, while the permanent gate
            # remains DRAINING/LOCKED and still rejects every new job.
            gate_pair = (
                status.get("jobGateState"),
                status.get("maintenanceState"),
            )
            if (
                gate_pair not in {
                    ("DRAINING", "DRAINING"),
                    ("LOCKED", "LOCKED"),
                }
                or active_update.get("state")
                not in {
                    "DRAINING",
                    "INITIAL_OBSERVE",
                    "RECOVERING",
                    "DEFERRED",
                    "REJECTED",
                }
            ):
                raise JobSafetyError(
                    "MCU_MAINTENANCE_STATE_INVALID",
                    "pre-hardware MCU drain state is internally inconsistent",
                )
            return None
        if (
            phase not in {"MAINTENANCE", "LOCKED"}
            or status.get("jobGateState") not in {"MAINTENANCE", "LOCKED"}
            or status.get("maintenanceState") not in {"MAINTENANCE", "LOCKED"}
            or status.get("activeJobPermitCount") != 0
            or status.get("unreconciledPhysicalActionCount") != 0
        ):
            raise JobSafetyError(
                "MCU_MAINTENANCE_STATE_INVALID",
                "permanent MCU maintenance state is internally inconsistent",
            )
        return {
            "updateUid": owner_uid,
            "maintenanceFenceToken": fence,
            "jobGateState": status["jobGateState"],
            "maintenancePhase": phase,
        }

    def _request(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        uncertain: LocalControlUnavailable | LocalControlRemoteError | None = (
            None
        )
        # Every mutating call carries a stable identity and exact digest. One
        # immediate retry can therefore recover a lost local response without
        # creating a second permit, receipt, or completion.
        for _attempt in range(2):
            try:
                return self._client.request(action, payload)
            except LocalControlRemoteError as error:
                if error.code in {"RESULT_UNKNOWN", "SERVICE_STOPPING"}:
                    # The server returns these codes while an idempotent
                    # handler thread may still commit: either its bounded
                    # response wait ended or shutdown began around it. Retry
                    # the exact payload; if it remains unknown, let the
                    # durable business inbox retry later instead of
                    # misclassifying the fact as rejected.
                    uncertain = error
                    continue
                raise JobSafetyError(error.code, error.message) from error
            except LocalControlUnavailable as error:
                uncertain = error
        assert uncertain is not None
        raise JobSafetyError(
            "JOB_GATE_UNAVAILABLE",
            "permanent updater could not be reached or confirmed",
        ) from uncertain


def build_job_safety_from_environment(
    environment: Mapping[str, str] | None = None,
) -> DisabledJobSafety | PermanentJobSafety:
    values = os.environ if environment is None else environment
    raw_mode = values.get(STAGE4_MODE_ENVIRONMENT, "disabled")
    if not isinstance(raw_mode, str):
        raise ValueError(f"{STAGE4_MODE_ENVIRONMENT} must be text")
    mode = raw_mode.strip().lower()
    if mode in _DISABLED_MODES:
        return DisabledJobSafety()
    if mode != _CANDIDATE_MODE:
        raise ValueError(
            f"{STAGE4_MODE_ENVIRONMENT} must be disabled or candidate"
        )
    socket_value = values.get(
        UPDATER_SOCKET_ENVIRONMENT,
        DEFAULT_UPDATER_SOCKET,
    )
    if not isinstance(socket_value, str) or not socket_value:
        raise ValueError(f"{UPDATER_SOCKET_ENVIRONMENT} must be non-empty")
    socket_path = Path(socket_value)
    if not socket_path.is_absolute():
        raise ValueError(f"{UPDATER_SOCKET_ENVIRONMENT} must be absolute")
    return PermanentJobSafety(
        LocalControlClient(
            socket_path,
            protocol_name=UPDATER_PROTOCOL_NAME,
        )
    )


def command_request_digest(command: Mapping[str, Any]) -> str:
    if not isinstance(command, Mapping):
        raise ValueError("physical job command must be an object")
    stable = {
        key: value
        for key, value in command.items()
        if key != "cosGrant"
    }
    return canonical_sha256(stable)


def action_digest(
    *,
    work_uid: str,
    command_uid: str,
    action_key: str,
    action_kind: str,
    payload: Mapping[str, Any],
) -> str:
    return canonical_sha256(
        {
            "workUid": work_uid,
            "commandUid": command_uid,
            "actionKey": action_key,
            "actionKind": action_kind,
            "payload": dict(payload),
        }
    )


def canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("job safety fact is not canonical JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value

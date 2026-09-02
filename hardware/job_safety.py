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
    arm_uid: str
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

    def authorize_physical_action(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    def confirm_physical_action(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def complete_job(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


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

    def authorize_physical_action(
        self,
        permit: JobPermit,
        *,
        action: PhysicalAction,
    ) -> None:
        armed = self._request(
            "AUTHORIZE_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "armUid": action.arm_uid,
                "permitUid": permit.permit_uid,
                "workUid": permit.work_uid,
                "commandUid": permit.command_uid,
                "actionKey": action.action_key,
                "actionKind": action.action_kind,
                "actionDigestSha256": action.action_digest_sha256,
            },
        )
        canonical_action_uid = _require_uuid4(
            armed.get("actionUid"),
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
            armed.get("disposition") != "ACCEPTED"
            or
            armed.get("state") != "MAY_HAVE_EXECUTED"
            or armed.get("mayExecute") is not True
        ):
            raise JobSafetyError(
                str(armed.get("errorCode") or "PHYSICAL_ACTION_NOT_ARMED"),
                "permanent ledger did not authorize the physical action",
            )

    def confirm_physical_action(
        self,
        action: PhysicalAction,
        *,
        outcome: str,
        evidence_sha256: str,
    ) -> None:
        result = self._request(
            "CONFIRM_PHYSICAL_ACTION",
            {
                "actionUid": action.action_uid,
                "receiptUid": action.receipt_uid,
                "outcome": outcome,
                "evidenceDigestSha256": _require_sha256(
                    evidence_sha256,
                    "evidenceDigestSha256",
                ),
            },
        )
        if result.get("state") != "CONFIRMED":
            raise JobSafetyError(
                str(result.get("errorCode") or "PHYSICAL_ACTION_UNCONFIRMED"),
                "physical action outcome was not durably confirmed",
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

    def _request(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        unavailable: LocalControlUnavailable | None = None
        # Every mutating call carries a stable identity and exact digest. One
        # immediate retry can therefore recover a lost local response without
        # creating a second permit, receipt, or completion.
        for _attempt in range(2):
            try:
                return self._client.request(action, payload)
            except LocalControlRemoteError as error:
                raise JobSafetyError(error.code, error.message) from error
            except LocalControlUnavailable as error:
                unavailable = error
        assert unavailable is not None
        raise JobSafetyError(
            "JOB_GATE_UNAVAILABLE",
            "permanent updater could not be reached or confirmed",
        ) from unavailable


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

"""work_manager.py -- delivery session and clean operation state machines."""
from __future__ import annotations
import json
import logging
import math
import secrets
import threading
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from device_identity import DeviceIdentity
from trusted_clock import local_deadline_reference, raw_utc_now
from edge_store import (
    EdgeStore,
    WORK_TYPE_BASELINE,
    WORK_TYPE_CLEAN,
    WORK_TYPE_DELIVERY,
    WORK_TYPE_FULLNESS,
    WORK_TYPE_NONE,
)
from job_safety import (
    DisabledJobSafety,
    JobPermit,
    JobSafetyError,
    PhysicalAction,
    action_digest,
    canonical_sha256,
    command_request_digest,
)
logger = logging.getLogger("work-manager")
_SYSTEM_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_PROCESS_BOOT_FALLBACK = f"process:{_uuid.uuid4()}"
# The fixed-frame MCU reports one final DD only after its locally managed
# delivery flow.  Keep transport/result latency distinct from the cloud
# command's first-dispatch authorization and from the MCU auto-close window.
_FIXED_FRAME_DELIVERY_RESULT_MIN_GRACE_MS = 30_000


class CleanUnlockDecisionDeferred(RuntimeError):
    """Keep the MCU fact pending while an accepted END command wins."""


def _new_uid() -> str:
    return str(_uuid.uuid4())


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _system_boot_identity() -> str:
    """Identify the monotonic-clock epoch without trusting wall time.

    Linux exposes a UUID that changes on every device boot.  The process
    fallback is deliberately stricter on unsupported systems: a service
    restart may require recovery, but it can never make two unrelated
    monotonic epochs comparable.
    """

    try:
        value = _SYSTEM_BOOT_ID_PATH.read_text(encoding="ascii").strip()
        return f"linux:{_uuid.UUID(value)}"
    except (OSError, UnicodeError, ValueError):
        return _PROCESS_BOOT_FALLBACK


def _compat_uid(work_uid: str, label: str) -> str:
    return _new_uid()


def _compat_measurement_sequence(
    source_sequence: int,
    label: str,
) -> int:
    """Give paired flow facts distinct IDs and preserve single-fact identity."""
    if label == "pre":
        return source_sequence * 2 - 1
    if label == "post":
        return source_sequence * 2
    return source_sequence


def _compat_measurement(
    work_uid: str,
    label: str,
    weight_grams: int,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "measurementUid": _compat_uid(work_uid, label),
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": weight_grams,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 0,
        "sampleCount": 1,
        "calibrationVersion": 0,
        "weightSensorHealth": "OK",
        "faultCode": "NONE",
        "mcuBootId": source["mcuBootId"],
        "mcuEventSequence": _compat_measurement_sequence(
            source["mcuEventSequence"],
            label,
        ),
    }


def _reported_weight(payload: dict[str, Any]) -> Optional[int]:
    if not payload.get("weightValuePresent"):
        return None
    value = payload.get("reportedWeightGrams")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _delivery_usable_weight(payload: dict[str, Any]) -> Optional[int]:
    if (
        payload.get("weightSensorHealth") != "OK"
        or payload.get("measurementStatus") not in ("STABLE", "UNSTABLE")
    ):
        return None
    return _reported_weight(payload)


def _measurement_fact(payload: Optional[dict[str, Any]]) -> Optional[dict]:
    if not payload:
        return None
    value_present = bool(payload.get("weightValuePresent"))
    fault_code = payload.get("faultCode")
    return {
        "measurementUid": payload.get("measurementUid"),
        "status": payload.get("measurementStatus"),
        "weightValueAvailable": value_present,
        "reportedWeightGrams": (
            _reported_weight(payload) if value_present else None
        ),
        "weightValueKind": payload.get("weightValueKind", "NONE"),
        "measurementElapsedMs": payload.get("measurementElapsedMs", 0),
        "sampleCount": payload.get("sampleCount", 0),
        "calibrationVersion": payload.get("calibrationVersion", 0),
        "sensorHealth": payload.get("weightSensorHealth", "UNKNOWN"),
        "faultCode": None if fault_code in (None, "NONE") else fault_code,
        "mcuBootId": payload.get("mcuBootId"),
        "mcuEventSequence": payload.get("mcuEventSequence"),
    }


def _frozen_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": config["version"],
        "contentSha256": config["contentSha256"],
        "mcuPayloadSha256": config["mcuPayloadSha256"],
    }


def _pending_photo_facts(slots: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "status": "UPLOAD_PENDING",
            "photoUid": None,
            "url": None,
            "sha256": None,
            "sizeBytes": None,
            "capturedAt": None,
            "missingReason": "PHOTO_METADATA_PENDING",
        }
        for slot in slots
    ]


def _remaining_execution_ms(command: dict[str, Any]) -> int:
    issued_at = datetime.fromisoformat(
        str(command["issuedAt"]).replace("Z", "+00:00")
    )
    expires_at = datetime.fromisoformat(
        str(command["expiresAt"]).replace("Z", "+00:00")
    )
    fallback_ms = int((expires_at - issued_at).total_seconds() * 1000)
    return _remaining_until(
        command["expiresAt"],
        fallback_ms=max(1, fallback_ms),
    )


def _remaining_until(
    expires_at_value: str,
    *,
    fallback_ms: int = 4_294_967_295,
) -> int:
    deadline_reference = local_deadline_reference()
    if deadline_reference is None:
        return min(max(1, int(fallback_ms)), 4_294_967_295)
    return _remaining_until_reference(expires_at_value, deadline_reference)


def _remaining_until_reference(
    expires_at_value: str,
    deadline_reference: datetime,
) -> int:
    expires_at = datetime.fromisoformat(
        str(expires_at_value).replace("Z", "+00:00")
    )
    remaining = int(
        (expires_at - deadline_reference).total_seconds() * 1000
    )
    if remaining <= 0:
        raise ValueError("command expired")
    return min(remaining, 4294967295)


def _fixed_frame_delivery_result_window_ms(
    context: dict[str, Any],
) -> int:
    """Return the frozen post-dispatch wait for the MCU's final DD fact."""

    auto_close_ms = context.get("delivery_auto_close_ms")
    continue_wait_ms = context.get("continue_delivery_wait_ms")
    if (
        not isinstance(auto_close_ms, int)
        or isinstance(auto_close_ms, bool)
        or auto_close_ms <= 0
        or not isinstance(continue_wait_ms, int)
        or isinstance(continue_wait_ms, bool)
        or continue_wait_ms <= 0
    ):
        raise ValueError("fixed-frame delivery result window is invalid")
    return auto_close_ms + max(
        continue_wait_ms,
        _FIXED_FRAME_DELIVERY_RESULT_MIN_GRACE_MS,
    )


def _operation_window_budget(
    context: dict[str, Any],
) -> tuple[int, float]:
    """Return remaining milliseconds and one non-resettable UART deadline."""

    dispatch_clock_sample = time.monotonic()
    duration_ms = context.get("operation_window_ms")
    started_ms = context.get("operation_started_monotonic_ms")
    started_boot_identity = context.get("operation_started_boot_identity")
    if (
        type(duration_ms) is int
        and type(started_ms) is int
        and duration_ms > 0
        and started_ms >= 0
        and isinstance(started_boot_identity, str)
        and started_boot_identity
    ):
        if _system_boot_identity() != started_boot_identity:
            # CLOCK_MONOTONIC starts a new, incomparable epoch on reboot.  Its
            # numeric value may already be larger than the persisted value, so
            # comparing only the numbers can incorrectly grant a new unlock.
            raise ValueError("operation window boot identity changed")
        elapsed_ms = _monotonic_ms() - started_ms
        if elapsed_ms < 0:
            # Also reject a broken or reset clock within one claimed boot.
            raise ValueError("operation window reference reset")
        remaining_ms = duration_ms - elapsed_ms
        if remaining_ms <= 0:
            raise ValueError("operation window expired")
        bounded_remaining = min(remaining_ms, 4_294_967_295)
        return (
            bounded_remaining,
            dispatch_clock_sample + bounded_remaining / 1000.0,
        )

    # A pre-upgrade context has no trustworthy relative reference.  It may be
    # evaluated only while the wall clock is trusted; an untrusted device must
    # not silently replace an old 30-minute window with a new full window.
    deadline_reference = local_deadline_reference()
    if deadline_reference is None:
        raise ValueError("operation window reference unavailable")
    remaining_ms = _remaining_until_reference(
        context["operation_deadline"], deadline_reference
    )
    return remaining_ms, dispatch_clock_sample + remaining_ms / 1000.0


def _remaining_operation_window_ms(context: dict[str, Any]) -> int:
    """Deduct a local relative clean window without trusting wall time."""

    return _operation_window_budget(context)[0]


def _deadline_after_ms(duration_ms: int) -> str:
    reference = local_deadline_reference()
    if reference is None:
        reference = datetime.fromisoformat(
            raw_utc_now().replace("Z", "+00:00")
        )
    return (
        reference + timedelta(milliseconds=duration_ms)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WorkManager:

    def __init__(
        self,
        store: EdgeStore,
        uart_link,
        device_identity: DeviceIdentity | None,
        photo_manager,
        *,
        job_safety=None,
    ):
        self._store = store
        self._uart = uart_link
        self._device_identity = device_identity
        self._photo = photo_manager
        self._job_safety = job_safety or DisabledJobSafety()
        # The fixed-frame MCU cannot echo the business action UUID.  Keep the
        # updater-issued dispatch token only for this live business process;
        # it must never enter rollbackable edge.db or logs.  A process restart
        # therefore cannot reinterpret a late DD/EF result as proof for an old
        # action unless updater.db already contains the exact confirmation.
        self._live_fixed_frame_dispatch_tokens: dict[str, str] = {}
        self._live_fixed_frame_dispatch_tokens_lock = threading.Lock()
        # PREPARE can commit in updater.db while both local-control replies
        # are lost.  Keep that one-time token only in this live process so a
        # retried business/event handler can close the exact never-dispatched
        # action.  The plaintext token must never enter rollbackable edge.db.
        self._uncertain_preparation_tokens: dict[str, str] = {}
        self._uncertain_preparation_tokens_lock = threading.Lock()
        # Serializes the two decisions that compete at the clean pre-unlock
        # boundary. Photo capture intentionally runs outside this lock; both
        # sides then use EdgeStore's atomic claims to choose END or UNLOCK.
        self._clean_unlock_decision_lock = threading.RLock()

    def _require_job_safety_mode_alignment(
        self,
        context: dict[str, Any],
    ) -> None:
        """Reject a retained protected job if the permanent gate is absent."""

        if (
            isinstance(context.get("job_safety"), dict)
            and not self._job_safety.enabled
        ):
            raise JobSafetyError(
                "JOB_GATE_MODE_MISMATCH",
                "protected work cannot continue without the permanent job gate",
            )

    def _own_device_name(self) -> str:
        identity = self._device_identity
        if not isinstance(identity, DeviceIdentity):
            raise RuntimeError(
                "immutable device identity is required for reliable events"
            )
        return identity.device_name

    def _remaining_clean_window_or_recovery(
        self,
        context: dict[str, Any],
        work_uid: str,
    ) -> Optional[int]:
        budget = self._clean_window_budget_or_recovery(context, work_uid)
        return None if budget is None else budget[0]

    def _clean_window_budget_or_recovery(
        self,
        context: dict[str, Any],
        work_uid: str,
    ) -> Optional[tuple[int, float]]:
        try:
            return _operation_window_budget(context)
        except ValueError as error:
            outcome = self._store.mark_clean_window_expired_for_recovery(
                work_uid
            )
            logger.warning(
                "clean operation window expired; preserving work for "
                "recovery: work=%s outcome=%s detail=%s",
                work_uid,
                outcome,
                error,
            )
            return None

    def _capture_photos(self, method_name: str, work_uid: str) -> bool:
        if self._photo is None:
            logger.warning(
                "photo manager unavailable: work=%s",
                work_uid,
            )
            return False
        method = getattr(self._photo, method_name, None)
        if method is None:
            logger.warning(
                "photo capture method unavailable: %s work=%s",
                method_name,
                work_uid,
            )
            return False
        try:
            captured = method(work_uid)
        except Exception as error:
            logger.warning(
                "photo capture persistence failed: work=%s error=%s",
                work_uid,
                error,
            )
            return False
        if captured is False:
            logger.warning(
                "photo capture was not persisted: work=%s",
                work_uid,
            )
            return False
        return True

    def _offer_initial_photo_grant(
        self,
        command: dict[str, Any],
        work_type: str,
        work_uid: str,
    ) -> None:
        grant = command.get("cosGrant")
        if grant is None or self._photo is None:
            return
        method = getattr(self._photo, "offer_initial_grant", None)
        if method is None:
            return
        method(work_type, work_uid, grant)

    def _completion_photo_facts(
        self,
        work_uid: str,
        work_type: str,
        slots: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if self._photo is not None:
            method = getattr(
                self._photo,
                "get_completion_photo_facts",
                None,
            )
            if method is not None:
                try:
                    return method(work_uid, work_type)
                except Exception:
                    logger.exception(
                        "completion photo snapshot failed: work=%s",
                        work_uid,
                    )
        return _pending_photo_facts(slots)

    def _reject_command(
        self,
        command: dict[str, Any],
        error_code: str,
    ) -> dict[str, Any]:
        self._store.record_command_observation(
            command,
            "REJECTED",
            error_code=error_code,
        )
        return {
            "acked": False,
            "error": error_code,
        }

    def _request_job_safety(
        self,
        command: dict[str, Any],
        *,
        work_type: str,
        work_uid: str,
    ) -> dict[str, Any] | None:
        """Obtain the permanent permit before creating a business work slot."""

        permit = self._job_safety.request_job(
            command,
            work_type=work_type,
            work_uid=work_uid,
        )
        if permit is None:
            return None
        return {
            "permit_uid": permit.permit_uid,
            "work_uid": permit.work_uid,
            "command_uid": permit.command_uid,
            "work_type": permit.work_type,
            "request_digest_sha256": permit.request_digest_sha256,
            # Reuse identifiers that already exist in the signed command and
            # business work.  A committed-but-unanswered local RPC therefore
            # remains addressable even before edge.db saves this context.
            "begin_uid": permit.work_uid,
            "completion_uid": permit.command_uid,
            "actions": {},
        }

    @staticmethod
    def _permit_from_safety_context(
        safety: dict[str, Any],
    ) -> JobPermit:
        return JobPermit(
            permit_uid=safety["permit_uid"],
            work_uid=safety["work_uid"],
            command_uid=safety["command_uid"],
            work_type=safety["work_type"],
            request_digest_sha256=safety["request_digest_sha256"],
        )

    def _begin_job_safety(self, context: dict[str, Any]) -> None:
        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            if self._job_safety.enabled:
                raise JobSafetyError(
                    "JOB_PERMIT_MISSING",
                    "active work has no permanent job permit",
                )
            return
        self._job_safety.begin_job(
            self._permit_from_safety_context(safety),
            begin_uid=safety["begin_uid"],
            digest=safety["request_digest_sha256"],
        )

    def _preserve_uncertain_job_begin(
        self,
        context: dict[str, Any],
        work_uid: str,
    ) -> None:
        """Keep the business slot when the permanent begin reply is unknown."""

        safety = context.get("job_safety")
        if safety is not None:
            safety["begin_result"] = "UNKNOWN"
        try:
            self._store.update_work_context(work_uid, context)
        except Exception:
            # acquire_work_slot already persisted the stable permit/begin
            # identities.  Never release that slot merely because annotating
            # the uncertain response failed.
            logger.critical(
                "could not annotate uncertain permanent job begin: work=%s",
                work_uid,
                exc_info=True,
            )

    def _abandon_job_safety(
        self,
        safety: dict[str, Any] | None,
        *,
        reason: str,
    ) -> None:
        if safety is None:
            return
        self._job_safety.abandon_job(
            self._permit_from_safety_context(safety),
            disposition_uid=safety["permit_uid"],
            evidence_sha256=canonical_sha256(
                {
                    "workUid": safety["work_uid"],
                    "reason": reason,
                }
            ),
        )

    def _send_physical_command(
        self,
        context: dict[str, Any],
        message_name: str,
        values: dict[str, Any],
        *,
        mcu_command_uid: str,
        action_key: str,
        action_kind: str,
        not_after: str | None = None,
        dispatch_deadline_monotonic_cap: float | None = None,
    ) -> dict[str, Any]:
        """Prepare now, but arm only inside the UART lock before its write."""

        self._require_job_safety_mode_alignment(context)
        dispatch_deadline_monotonic: float | None = None
        if not_after is not None:
            try:
                remaining_ms = _remaining_until(not_after)
            except ValueError:
                return {
                    "acked": False,
                    "error": "COMMAND_EXPIRED",
                    "fatal": False,
                    "message_name": message_name,
                    "mcu_command_uid": mcu_command_uid,
                    "physicalEffect": "NOT_EXECUTED",
                }
            dispatch_deadline_monotonic = (
                time.monotonic() + remaining_ms / 1000.0
            )
        if dispatch_deadline_monotonic_cap is not None:
            if (
                isinstance(dispatch_deadline_monotonic_cap, bool)
                or not isinstance(
                    dispatch_deadline_monotonic_cap, (int, float)
                )
                or not math.isfinite(
                    float(dispatch_deadline_monotonic_cap)
                )
                or dispatch_deadline_monotonic_cap <= 0
            ):
                raise ValueError(
                    "physical dispatch deadline cap must be a positive "
                    "finite monotonic timestamp"
                )
            relative_deadline = float(dispatch_deadline_monotonic_cap)
            dispatch_deadline_monotonic = (
                relative_deadline
                if dispatch_deadline_monotonic is None
                else min(dispatch_deadline_monotonic, relative_deadline)
            )
        dispatch_attempt_token = None
        if context.get("job_safety") is not None:
            dispatch_attempt_token = self._preparation_token_for(
                mcu_command_uid
            )
        action = self._prepare_physical_action(
            context,
            message_name=message_name,
            values=values,
            mcu_command_uid=mcu_command_uid,
            action_key=action_key,
            action_kind=action_kind,
            dispatch_attempt_token=dispatch_attempt_token,
        )
        if action is None and context.get("job_safety") is not None:
            # The PREPARE response was unavailable, but this still-live call
            # subsequently proved and durably recorded that the exact action
            # was never armed.  Do not turn that proof into an event retry:
            # callers can now terminate or recover the business operation at
            # a known-safe boundary without any UART write.
            return {
                "acked": False,
                "error": "PHYSICAL_ACTION_PREPARE_RESPONSE_UNAVAILABLE",
                "fatal": False,
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
                "physicalEffect": "NOT_EXECUTED",
            }
        if action is not None and not_after is not None:
            try:
                _remaining_until(not_after)
            except ValueError:
                self._cancel_prepared_physical_action_record(
                    context,
                    action_key=action_key,
                    record=context["job_safety"]["actions"][action_key],
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=canonical_sha256(
                        {
                            "eventType": "UART_COMMAND_EXPIRED_BEFORE_WRITE",
                            "workUid": context["job_safety"]["work_uid"],
                            "actionUid": action.action_uid,
                            "notAfter": not_after,
                        }
                    ),
                )
                return {
                    "acked": False,
                    "error": "COMMAND_EXPIRED",
                    "fatal": False,
                    "message_name": message_name,
                    "mcu_command_uid": mcu_command_uid,
                    "physicalEffect": "NOT_EXECUTED",
                }
        if (
            action is not None
            and dispatch_deadline_monotonic is not None
            and time.monotonic() >= dispatch_deadline_monotonic
        ):
            self._cancel_prepared_physical_action_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_DISPATCH_WINDOW_EXPIRED",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                        "notAfter": not_after,
                    }
                ),
            )
            return {
                "acked": False,
                "error": "COMMAND_EXPIRED",
                "fatal": False,
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
                "physicalEffect": "NOT_EXECUTED",
            }
        deadline_sender = getattr(
            self._uart,
            "send_command_before_deadline",
            None,
        )
        if action is not None and dispatch_deadline_monotonic is None:
            self._cancel_prepared_physical_action_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_DISPATCH_DEADLINE_MISSING",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                    }
                ),
            )
            raise JobSafetyError(
                "UART_DISPATCH_DEADLINE_MISSING",
                "physical action has no final UART dispatch deadline",
            )
        dispatch = {
            "gate_called": False,
            "gate_opened": False,
            "gate_aborted": False,
            "gate_returned": False,
        }

        def open_dispatch_gate() -> None:
            if action is None or dispatch_attempt_token is None:
                return
            if dispatch["gate_called"]:
                raise JobSafetyError(
                    "UART_DISPATCH_GATE_REUSED",
                    "one dispatch attempt cannot authorize a second write",
                )
            dispatch["gate_called"] = True
            self._job_safety.arm_physical_action(
                action,
                dispatch_attempt_token=dispatch_attempt_token,
            )
            dispatch["gate_opened"] = True
            if (
                dispatch_deadline_monotonic is not None
                and time.monotonic() >= dispatch_deadline_monotonic
            ):
                self._abort_physical_action_dispatch_record(
                    context,
                    action_key=action_key,
                    record=context["job_safety"]["actions"][action_key],
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=canonical_sha256(
                        {
                            "eventType": (
                                "UART_DISPATCH_EXPIRED_DURING_ARM"
                            ),
                            "workUid": context["job_safety"]["work_uid"],
                            "actionUid": action.action_uid,
                            "notAfter": not_after,
                        }
                    ),
                )
                dispatch["gate_aborted"] = True
                raise JobSafetyError(
                    "COMMAND_EXPIRED",
                    "physical command expired while opening its dispatch gate",
                )
            dispatch["gate_returned"] = True

        if action is not None and not callable(deadline_sender):
            self._cancel_prepared_physical_action_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_DISPATCH_GATE_UNSUPPORTED",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                    }
                ),
            )
            raise JobSafetyError(
                "UART_DISPATCH_GATE_UNSUPPORTED",
                "UART adapter cannot arm immediately before its first write",
            )

        try:
            if (
                dispatch_deadline_monotonic is not None
                and callable(deadline_sender)
            ):
                sender_arguments: dict[str, Any] = {
                    "mcu_command_uid": mcu_command_uid,
                    "dispatch_deadline_monotonic": (
                        dispatch_deadline_monotonic
                    ),
                }
                if action is not None:
                    sender_arguments["dispatch_gate"] = open_dispatch_gate
                result = deadline_sender(
                    message_name,
                    values,
                    **sender_arguments,
                )
            else:
                result = self._uart.send_command(
                    message_name,
                    values,
                    mcu_command_uid=mcu_command_uid,
                )
        except Exception:
            if action is not None:
                record = context["job_safety"]["actions"][action_key]
                if dispatch["gate_aborted"]:
                    # The same live call durably proved that no UART byte was
                    # written and closed the just-armed action.
                    pass
                elif dispatch["gate_opened"] and dispatch["gate_returned"]:
                    # The gate returned immediately before the UART write.
                    # A crash or exception from this point is never negative
                    # evidence: bytes may have reached the controller.
                    record["dispatch_result"] = "ARMED"
                    self._store.update_work_context(
                        context["job_safety"]["work_uid"],
                        context,
                    )
                    if getattr(self._uart, "compatibility_mode", False):
                        self._remember_live_fixed_frame_dispatch_token(
                            action.action_uid,
                            dispatch_attempt_token,
                        )
                    # Once ARM returned, an exception from write/flush is
                    # indeterminate rather than a negative dispatch fact in
                    # every UART mode. Return that fact to CommandProcessor so
                    # it keeps the command and work slot in recovery instead
                    # of publishing a false FAILED observation. Fixed-frame
                    # alone also needs the volatile token retained above for
                    # an identity-less late DD/EF result.
                    logger.warning(
                        "UART result became unknown after dispatch ARM: "
                        "message=%s command=%s action=%s",
                        message_name,
                        mcu_command_uid,
                        action.action_uid,
                        exc_info=True,
                    )
                    return {
                        "acked": False,
                        "error": "UART_WRITE_RESULT_UNKNOWN",
                        "fatal": False,
                        "message_name": message_name,
                        "mcu_command_uid": mcu_command_uid,
                        "physicalEffect": "UNKNOWN",
                    }
                elif dispatch["gate_opened"]:
                    # ARM committed, but the dispatch callback itself failed
                    # before returning to the UART writer (for example while
                    # trying to abort an action whose deadline just elapsed).
                    # Preserve the permanent lock and propagate the gate error;
                    # no serial write was authorized by this callback.
                    record["dispatch_result"] = "ARMED"
                    self._store.update_work_context(
                        context["job_safety"]["work_uid"],
                        context,
                    )
                else:
                    self._close_dispatch_that_never_opened(
                        context,
                        action_key=action_key,
                        record=record,
                        dispatch_attempt_token=dispatch_attempt_token,
                        arm_was_attempted=dispatch["gate_called"],
                    )
            raise

        if (
            action is not None
            and dispatch["gate_opened"]
            and result.get("uart_write_attempted") is False
        ):
            # The real UART adapter checked the same deadline after ARM but
            # before every write and proved that even the first byte was never
            # attempted.  Close the live authorization with its original
            # token; a failed abort remains locked and propagates.
            self._abort_physical_action_dispatch_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_DEADLINE_REACHED_BEFORE_FIRST_WRITE",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                        "notAfter": not_after,
                    }
                ),
            )
            dispatch["gate_aborted"] = True
        known_not_sent = (
            not result.get("acked")
            and result.get("error")
            in {
                "UART_CLOSED",
                "UART_NOT_READY",
                "COMMAND_EXPIRED",
                "MCU_FEATURE_NOT_SUPPORTED",
            }
            and (
                not dispatch["gate_opened"]
                or dispatch["gate_aborted"]
            )
        )
        if (
            action is not None
            and not dispatch["gate_called"]
            and not known_not_sent
        ):
            # An adapter that returns without invoking the callback has
            # violated the final-write boundary.  It may already have written
            # bytes, so never cancel the PREPARED record or accept its result.
            self._preserve_dispatch_gate_bypass_uncertainty(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
            )
            raise JobSafetyError(
                "UART_DISPATCH_GATE_BYPASSED",
                "UART adapter returned without invoking its dispatch gate",
            )
        if (
            action is not None
            and dispatch["gate_opened"]
            and not dispatch["gate_aborted"]
        ):
            record = context["job_safety"]["actions"][action_key]
            record["dispatch_result"] = "ARMED"
            self._store.update_work_context(
                context["job_safety"]["work_uid"],
                context,
            )
            if getattr(self._uart, "compatibility_mode", False):
                self._remember_live_fixed_frame_dispatch_token(
                    action.action_uid,
                    dispatch_attempt_token,
                )
        if (
            action is not None
            and known_not_sent
            and not dispatch["gate_aborted"]
        ):
            # These errors are accepted only while the dispatch callback has
            # not opened. The permanent PREPARED state is itself the trusted
            # proof that no UART write was authorized.
            self._cancel_prepared_physical_action_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_COMMAND_NOT_SENT",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                        "errorCode": result["error"],
                    }
                ),
            )
        if known_not_sent:
            result["physicalEffect"] = "NOT_EXECUTED"
        elif not result.get("acked"):
            result["physicalEffect"] = "UNKNOWN"
        return result

    def _remember_live_fixed_frame_dispatch_token(
        self,
        action_uid: str,
        dispatch_attempt_token: str,
    ) -> None:
        with self._live_fixed_frame_dispatch_tokens_lock:
            existing = self._live_fixed_frame_dispatch_tokens.get(action_uid)
            if existing is not None and existing != dispatch_attempt_token:
                raise JobSafetyError(
                    "PHYSICAL_ACTION_LIVE_TOKEN_CONFLICT",
                    "one physical action acquired two live dispatch tokens",
                )
            self._live_fixed_frame_dispatch_tokens[action_uid] = (
                dispatch_attempt_token
            )

    def _send_end_clean_control_command(
        self,
        context: dict[str, Any],
        values: dict[str, Any],
        *,
        mcu_command_uid: str,
        not_after: str,
    ) -> dict[str, Any]:
        """Send the non-actuating MCU cancellation state transition.

        END_CLEAN_BEFORE_UNLOCK only tells the MCU to abandon a clean state
        machine before any solenoid unlock has been dispatched.  It does not
        energize an actuator, so it belongs to the active job permit but not
        to the physical-action ledger.  Keeping this entry point dedicated
        prevents future actuating commands from bypassing two-phase dispatch.
        """

        self._require_job_safety_mode_alignment(context)
        if self._job_safety.enabled and not isinstance(
            context.get("job_safety"),
            dict,
        ):
            raise JobSafetyError(
                "JOB_PERMIT_MISSING",
                "clean cancellation has no permanent job permit",
            )
        try:
            remaining_ms = _remaining_until(not_after)
        except ValueError:
            return {
                "acked": False,
                "error": "COMMAND_EXPIRED",
                "fatal": False,
                "message_name": "END_CLEAN_BEFORE_UNLOCK",
                "mcu_command_uid": mcu_command_uid,
                "physicalEffect": "NOT_APPLICABLE",
            }
        deadline_sender = getattr(
            self._uart,
            "send_command_before_deadline",
            None,
        )
        if not callable(deadline_sender):
            raise JobSafetyError(
                "UART_DISPATCH_DEADLINE_UNSUPPORTED",
                "UART adapter cannot enforce the clean cancellation deadline",
            )
        result = deadline_sender(
            "END_CLEAN_BEFORE_UNLOCK",
            values,
            mcu_command_uid=mcu_command_uid,
            dispatch_deadline_monotonic=(
                time.monotonic() + remaining_ms / 1000.0
            ),
        )
        if result.get("acked"):
            result["controlAcceptance"] = "ACCEPTED"
        elif result.get("error") in {
            "COMMAND_EXPIRED",
            "UART_CLOSED",
            "UART_NOT_READY",
            "BUSY",
        }:
            # The UART layer either wrote no byte, or received an explicit
            # BUSY NACK proving that the MCU did not accept the stop command.
            # A later cloud command may safely reuse this MCU identity.
            result["controlAcceptance"] = "RETRYABLE_NOT_ACCEPTED"
        else:
            # TIMEOUT and unclassified transport failures may have reached the
            # MCU. Only the exact original cloud command may retry that case.
            result["controlAcceptance"] = "RESULT_UNKNOWN"
        result["physicalEffect"] = "NOT_APPLICABLE"
        return result

    def _forget_live_fixed_frame_dispatch_token(
        self,
        action_uid: str,
        dispatch_attempt_token: str | None,
    ) -> None:
        with self._live_fixed_frame_dispatch_tokens_lock:
            if (
                dispatch_attempt_token is not None
                and self._live_fixed_frame_dispatch_tokens.get(action_uid)
                == dispatch_attempt_token
            ):
                self._live_fixed_frame_dispatch_tokens.pop(action_uid, None)

    def _preserve_dispatch_gate_bypass_uncertainty(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        dispatch_attempt_token: str,
    ) -> None:
        """Turn a broken adapter postcondition into a permanent lock."""

        record["dispatch_result"] = "GATE_BYPASSED_MAY_HAVE_WRITTEN"
        self._store.update_work_context(
            context["job_safety"]["work_uid"],
            context,
        )
        try:
            # This late ARM never authorizes another write.  Its sole purpose
            # is to make the uncertainty non-rollbackable in updater.db.
            self._job_safety.arm_physical_action(
                self._physical_action(record),
                dispatch_attempt_token=dispatch_attempt_token,
            )
            record["dispatch_result"] = "ARMED_AFTER_GATE_BYPASS"
            self._store.update_work_context(
                context["job_safety"]["work_uid"],
                context,
            )
        except Exception:
            # Keep the local work slot and marker.  Reconciliation explicitly
            # refuses to cancel a PREPARED permanent row after this point.
            logger.critical(
                "could not preserve UART gate-bypass uncertainty: "
                "work=%s action=%s",
                context["job_safety"].get("work_uid"),
                record.get("action_uid"),
                exc_info=True,
            )

    def _adopt_exact_live_fixed_frame_confirmation(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        outcome: str,
        evidence_sha256: str,
    ) -> bool:
        """Adopt only an exact permanent fact after an RPC response loss."""

        safety = context.get("job_safety")
        confirmation = (
            safety.get("confirmations", {}).get(action_key)
            if isinstance(safety, dict)
            and isinstance(safety.get("confirmations"), dict)
            else None
        )
        remote = self._job_safety.get_physical_action(record["action_uid"])
        if not self._exact_permanent_live_fixed_frame_confirmation(
            safety,
            action_key=action_key,
            record=record,
            confirmation=confirmation,
            remote=remote,
            expected_outcome=outcome,
            expected_evidence_sha256=evidence_sha256,
        ):
            return False
        self._remember_action_confirmation(
            context,
            action_key=action_key,
            record=record,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis="LIVE_FIXED_FRAME_RESULT",
        )
        return True

    def _confirm_live_fixed_frame_action(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        event_type: str,
        payload: dict[str, Any],
        outcome: str,
        dispatch_attempt_token: str | None = None,
    ) -> None:
        """Resolve an old fixed-frame action without persisting its token."""

        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            return
        record = safety.get("actions", {}).get(action_key)
        if not isinstance(record, dict):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_MISMATCH",
                "fixed-frame result has no matching physical action",
            )
        evidence_sha256 = canonical_sha256(
            {
                "eventType": event_type,
                "workUid": safety["work_uid"],
                "actionUid": record["action_uid"],
                "payload": payload,
            }
        )
        confirmation = self._freeze_action_confirmation(
            context,
            action_key=action_key,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis="LIVE_FIXED_FRAME_RESULT",
        )
        if not isinstance(dispatch_attempt_token, str) or not (
            dispatch_attempt_token
        ):
            with self._live_fixed_frame_dispatch_tokens_lock:
                dispatch_attempt_token = (
                    self._live_fixed_frame_dispatch_tokens.get(
                        record["action_uid"]
                    )
                )
        if confirmation.get("confirmed") is True:
            self._forget_live_fixed_frame_dispatch_token(
                record["action_uid"],
                dispatch_attempt_token,
            )
            return
        if dispatch_attempt_token is None:
            if self._adopt_exact_live_fixed_frame_confirmation(
                context,
                action_key=action_key,
                record=record,
                outcome=outcome,
                evidence_sha256=evidence_sha256,
            ):
                return
            raise JobSafetyError(
                "PHYSICAL_ACTION_LIVE_TOKEN_MISSING",
                "fixed-frame result arrived after its live dispatch token was lost",
            )
        self._remember_live_fixed_frame_dispatch_token(
            record["action_uid"],
            dispatch_attempt_token,
        )
        try:
            self._job_safety.confirm_live_physical_action_result(
                self._physical_action(record),
                dispatch_attempt_token=dispatch_attempt_token,
                outcome=outcome,
                evidence_sha256=evidence_sha256,
            )
        except JobSafetyError:
            if not self._adopt_exact_live_fixed_frame_confirmation(
                context,
                action_key=action_key,
                record=record,
                outcome=outcome,
                evidence_sha256=evidence_sha256,
            ):
                raise
        else:
            self._remember_action_confirmation(
                context,
                action_key=action_key,
                record=record,
                outcome=outcome,
                evidence_sha256=evidence_sha256,
                confirmation_basis="LIVE_FIXED_FRAME_RESULT",
            )
        self._forget_live_fixed_frame_dispatch_token(
            record["action_uid"],
            dispatch_attempt_token,
        )

    def _prepare_physical_action(
        self,
        context: dict[str, Any],
        *,
        message_name: str,
        values: dict[str, Any],
        mcu_command_uid: str,
        action_key: str,
        action_kind: str,
        dispatch_attempt_token: str | None,
        persist_context: bool = True,
    ) -> PhysicalAction | None:
        """Persist one logical action without granting any UART write."""

        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            if self._job_safety.enabled:
                raise JobSafetyError(
                    "JOB_PERMIT_MISSING",
                    "physical action has no permanent job permit",
                )
            return None
        if (
            not isinstance(dispatch_attempt_token, str)
            or not dispatch_attempt_token
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_DISPATCH_TOKEN_MISSING",
                "physical action preparation has no live dispatch token",
            )

        actions = safety.setdefault("actions", {})
        record = actions.get(action_key)
        digest = action_digest(
            work_uid=safety["work_uid"],
            command_uid=safety["command_uid"],
            action_key=action_key,
            action_kind=action_kind,
            payload={
                "messageName": message_name,
                "mcuCommandUid": mcu_command_uid,
                "values": values,
            },
        )
        retained_uncertain_token = False
        if record is None:
            record = {
                "action_uid": mcu_command_uid,
                "receipt_uid": mcu_command_uid,
                "action_key": action_key,
                "action_kind": action_kind,
                "action_digest_sha256": digest,
                "preparation_result": "PENDING",
            }
            actions[action_key] = record
            # The identity must survive before updater.db is touched.  If the
            # process dies after this commit, recovery sees an unarmed action
            # and never invents a new physical command identity.
            if persist_context:
                self._store.update_work_context(
                    safety["work_uid"],
                    context,
                )
        else:
            with self._uncertain_preparation_tokens_lock:
                retained_uncertain_token = (
                    self._uncertain_preparation_tokens.get(
                        mcu_command_uid
                    )
                    == dispatch_attempt_token
                )
            if retained_uncertain_token:
                if not (
                    record.get("action_uid") == mcu_command_uid
                    and record.get("action_key") == action_key
                    and record.get("action_kind") == action_kind
                ):
                    raise JobSafetyError(
                        "PHYSICAL_ACTION_CONFLICT",
                        "uncertain physical action identity changed",
                    )
                # This retry exists only to resolve the earlier PREPARE call;
                # it will be cancelled below and can never reach UART.  Use
                # the original permanent digest even if a shrinking timeout
                # field was recomputed by the caller.
            elif record["action_digest_sha256"] != digest:
                raise JobSafetyError(
                    "PHYSICAL_ACTION_CONFLICT",
                    "persisted physical action content changed",
                )

        action = self._physical_action(record)
        try:
            self._job_safety.prepare_physical_action(
                self._permit_from_safety_context(safety),
                action=action,
                dispatch_attempt_token=dispatch_attempt_token,
            )
        except Exception as error:
            record["preparation_result"] = "UNKNOWN"
            if persist_context:
                self._store.update_work_context(
                    safety["work_uid"],
                    context,
                )
            closed_without_dispatch = False
            if (
                isinstance(error, JobSafetyError)
                and error.code == "JOB_GATE_UNAVAILABLE"
            ):
                closed_without_dispatch = (
                    self._close_uncertain_preparation_that_never_dispatched(
                        context,
                        action_key=action_key,
                        record=record,
                        dispatch_attempt_token=dispatch_attempt_token,
                    )
                )
                if closed_without_dispatch:
                    self._forget_uncertain_preparation_token(
                        record["action_uid"],
                        dispatch_attempt_token,
                    )
                    return None
                self._remember_uncertain_preparation_token(
                    record["action_uid"],
                    dispatch_attempt_token,
                )
            raise
        record["preparation_result"] = "PREPARED"
        if persist_context:
            self._store.update_work_context(
                safety["work_uid"],
                context,
            )
        if retained_uncertain_token:
            # Never continue an earlier indeterminate PREPARE directly into
            # ARM.  Re-establish the exact row with its original token, then
            # close it while this process still proves no UART write occurred.
            if self._close_uncertain_preparation_that_never_dispatched(
                    context,
                    action_key=action_key,
                    record=record,
                    dispatch_attempt_token=dispatch_attempt_token,
            ):
                self._forget_uncertain_preparation_token(
                    record["action_uid"],
                    dispatch_attempt_token,
                )
                return None
            raise JobSafetyError(
                "JOB_GATE_UNAVAILABLE",
                "uncertain prepared action could not be closed safely",
            )
        return action

    def _remember_uncertain_preparation_token(
        self,
        action_uid: str,
        dispatch_attempt_token: str,
    ) -> None:
        with self._uncertain_preparation_tokens_lock:
            existing = self._uncertain_preparation_tokens.get(action_uid)
            if existing is not None and existing != dispatch_attempt_token:
                raise JobSafetyError(
                    "PHYSICAL_ACTION_LIVE_TOKEN_CONFLICT",
                    "uncertain physical action acquired two live tokens",
                )
            self._uncertain_preparation_tokens[action_uid] = (
                dispatch_attempt_token
            )

    def _preparation_token_for(self, action_uid: str) -> str:
        with self._uncertain_preparation_tokens_lock:
            return (
                self._uncertain_preparation_tokens.get(action_uid)
                or secrets.token_urlsafe(32)
            )

    def _forget_uncertain_preparation_token(
        self,
        action_uid: str,
        dispatch_attempt_token: str,
    ) -> None:
        with self._uncertain_preparation_tokens_lock:
            if (
                self._uncertain_preparation_tokens.get(action_uid)
                == dispatch_attempt_token
            ):
                self._uncertain_preparation_tokens.pop(action_uid, None)

    @staticmethod
    def _remote_action_identity_matches(
        safety: dict[str, Any],
        record: dict[str, Any],
        remote: dict[str, Any],
    ) -> bool:
        """Match every non-secret field before adopting permanent state."""

        return all(
            (
                remote.get("actionUid") == record.get("action_uid"),
                remote.get("permitUid") == safety.get("permit_uid"),
                remote.get("workUid") == safety.get("work_uid"),
                remote.get("commandUid") == safety.get("command_uid"),
                remote.get("actionKey") == record.get("action_key"),
                remote.get("actionKind") == record.get("action_kind"),
                remote.get("actionDigestSha256")
                == record.get("action_digest_sha256"),
            )
        )

    @staticmethod
    def _is_lower_sha256(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 64
            and all(
                character in "0123456789abcdef" for character in value
            )
        )

    @classmethod
    def _exact_permanent_live_fixed_frame_confirmation(
        cls,
        safety: Any,
        *,
        action_key: str,
        record: Any,
        confirmation: Any,
        remote: Any,
        expected_outcome: str | None = None,
        expected_evidence_sha256: str | None = None,
    ) -> bool:
        """Match the frozen edge evidence to one immutable updater receipt."""

        if not (
            isinstance(safety, dict)
            and isinstance(record, dict)
            and isinstance(confirmation, dict)
            and isinstance(remote, dict)
        ):
            return False
        action_uid = record.get("action_uid")
        evidence_sha256 = confirmation.get("evidence_sha256")
        outcome = confirmation.get("outcome")
        locally_confirmed = confirmation.get("confirmed")
        local_state_is_consistent = (
            locally_confirmed is False
            and record.get("dispatch_result") == "ARMED"
        ) or (
            locally_confirmed is True
            and record.get("dispatch_result") == "CONFIRMED"
        )
        return bool(
            record.get("action_key") == action_key
            and record.get("receipt_uid") == action_uid
            and record.get("preparation_result") == "PREPARED"
            and cls._is_lower_sha256(record.get("action_digest_sha256"))
            and confirmation.get("confirmation_basis")
            == "LIVE_FIXED_FRAME_RESULT"
            and outcome in {"EXECUTED", "FAILED_SAFE"}
            and cls._is_lower_sha256(evidence_sha256)
            and (
                expected_outcome is None or outcome == expected_outcome
            )
            and (
                expected_evidence_sha256 is None
                or evidence_sha256 == expected_evidence_sha256
            )
            and local_state_is_consistent
            and cls._remote_action_identity_matches(safety, record, remote)
            and remote.get("state") == "CONFIRMED"
            and remote.get("dispatchMode") == "TWO_PHASE_V3"
            and remote.get("mayExecute") is False
            and remote.get("receiptUid") == record.get("receipt_uid")
            and remote.get("confirmedOutcome") == outcome
            and remote.get("confirmationBasis")
            == "LIVE_FIXED_FRAME_RESULT"
            and remote.get("evidenceDigestSha256") == evidence_sha256
        )

    @staticmethod
    def _fixed_frame_start_binding(work_type: str) -> dict[str, str]:
        bindings = {
            WORK_TYPE_DELIVERY: {
                "command_type": "START_DELIVERY_SESSION",
                "payload_work_uid": "sessionUid",
                "context_work_uid": "session_uid",
                "action_key": "DELIVERY:START:0",
                "action_kind": "START_DELIVERY_SESSION",
            },
            WORK_TYPE_CLEAN: {
                "command_type": "START_CLEAN_OPERATION",
                "payload_work_uid": "operationUid",
                "context_work_uid": "operation_uid",
                "action_key": "CLEAN:START:0",
                "action_kind": "START_CLEAN_OPERATION",
            },
        }
        try:
            return bindings[work_type]
        except KeyError as error:
            raise ValueError(
                "unsupported fixed-frame start work type"
            ) from error

    def _has_exact_live_fixed_frame_start_proof(
        self,
        context: dict[str, Any],
        *,
        work_type: str,
        work_uid: str,
        command_uid: str,
        command: dict[str, Any],
    ) -> bool:
        """Prove an abnormal start row still names this live ARMED AA/EE.

        This compatibility path exists for an older failure ordering where a
        post-ARM UART exception could mark the inbox FAILED while preserving
        the physical action.  A still-ARMED action requires this process's
        volatile token; after restart, only an already-CONFIRMED permanent
        receipt that exactly matches frozen edge evidence can replace it.
        """

        binding = self._fixed_frame_start_binding(work_type)
        if not self._job_safety.enabled:
            return False
        safety = context.get("job_safety")
        if not isinstance(safety, dict):
            return False
        if (
            safety.get("permit_uid") != command_uid
            or safety.get("work_uid") != work_uid
            or safety.get("command_uid") != command_uid
            or safety.get("work_type") != work_type
            or safety.get("begin_uid") != work_uid
            or safety.get("completion_uid") != command_uid
            or safety.get("request_digest_sha256")
            != command_request_digest(command)
            or context.get(binding["context_work_uid"]) != work_uid
        ):
            return False
        actions = safety.get("actions")
        action_key = binding["action_key"]
        if not isinstance(actions, dict) or set(actions) != {action_key}:
            return False
        record = actions.get(action_key)
        if not isinstance(record, dict):
            return False
        action_uid = record.get("action_uid")
        if not (
            isinstance(action_uid, str)
            and action_uid
            and action_uid == context.get("start_mcu_command_uid")
            and record.get("receipt_uid") == action_uid
            and record.get("action_key") == action_key
            and record.get("action_kind") == binding["action_kind"]
            and self._is_lower_sha256(
                record.get("action_digest_sha256")
            )
            and record.get("preparation_result") == "PREPARED"
            and record.get("dispatch_result") in {"ARMED", "CONFIRMED"}
        ):
            return False
        remote = self._job_safety.get_physical_action(action_uid)
        confirmations = safety.get("confirmations")
        confirmation = (
            confirmations.get(action_key)
            if isinstance(confirmations, dict)
            else None
        )
        if self._exact_permanent_live_fixed_frame_confirmation(
            safety,
            action_key=action_key,
            record=record,
            confirmation=confirmation,
            remote=remote,
        ):
            return True
        if record.get("dispatch_result") != "ARMED":
            return False
        with self._live_fixed_frame_dispatch_tokens_lock:
            live_token = self._live_fixed_frame_dispatch_tokens.get(action_uid)
        if not isinstance(live_token, str) or not live_token:
            return False
        return bool(
            self._remote_action_identity_matches(safety, record, remote)
            and remote.get("state") == "ARMED"
            and remote.get("dispatchMode") == "TWO_PHASE_V3"
            and remote.get("mayExecute") is False
            and remote.get("receiptUid") is None
            and remote.get("confirmedOutcome") is None
            and remote.get("confirmationBasis") is None
            and remote.get("evidenceDigestSha256") is None
        )

    def _close_uncertain_preparation_that_never_dispatched(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        dispatch_attempt_token: str,
    ) -> bool:
        """Cancel only an exact PREPARED row while this call owns its token.

        This helper runs inside the PREPARE call stack, before an action can be
        returned to the UART sender.  A missing row is not negative evidence:
        the timed-out handler could still commit, so only an exact durable
        PREPARED row may be cancelled.
        """

        safety = context["job_safety"]
        evidence_sha256 = canonical_sha256(
            {
                "eventType": "PHYSICAL_ACTION_PREPARE_RESPONSE_UNAVAILABLE",
                "workUid": safety["work_uid"],
                "commandUid": safety["command_uid"],
                "actionUid": record["action_uid"],
                "actionDigestSha256": record["action_digest_sha256"],
            }
        )
        try:
            remote = self._job_safety.get_physical_action(
                record["action_uid"]
            )
            if not self._remote_action_identity_matches(
                safety, record, remote
            ):
                return False
            if remote.get("state") == "PREPARED":
                record["preparation_result"] = "PREPARED"
                self._store.update_work_context(safety["work_uid"], context)
                try:
                    self._cancel_prepared_physical_action_record(
                        context,
                        action_key=action_key,
                        record=record,
                        dispatch_attempt_token=dispatch_attempt_token,
                        evidence_sha256=evidence_sha256,
                    )
                    return True
                except (JobSafetyError, KeyError, TypeError, ValueError):
                    # The cancellation may itself have committed before its
                    # response was lost.  Adopt only the exact receipt below.
                    remote = self._job_safety.get_physical_action(
                        record["action_uid"]
                    )
            if not (
                self._remote_action_identity_matches(safety, record, remote)
                and remote.get("state") == "CONFIRMED"
                and remote.get("receiptUid") == record.get("receipt_uid")
                and remote.get("confirmedOutcome") == "NOT_EXECUTED"
                and remote.get("confirmationBasis") == "PREPARED_NOT_ARMED"
                and remote.get("evidenceDigestSha256") == evidence_sha256
            ):
                return False
            self._remember_action_confirmation(
                context,
                action_key=action_key,
                record=record,
                outcome="NOT_EXECUTED",
                evidence_sha256=evidence_sha256,
                confirmation_basis="PREPARED_NOT_ARMED",
            )
            return True
        except (JobSafetyError, KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _physical_action(record: dict[str, Any]) -> PhysicalAction:
        return PhysicalAction(
            action_uid=record["action_uid"],
            receipt_uid=record["receipt_uid"],
            action_key=record["action_key"],
            action_kind=record["action_kind"],
            action_digest_sha256=record["action_digest_sha256"],
        )

    def _remember_action_confirmation(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        outcome: str,
        evidence_sha256: str,
        confirmation_basis: str,
    ) -> None:
        confirmation = self._freeze_action_confirmation(
            context,
            action_key=action_key,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis=confirmation_basis,
        )
        confirmation["confirmed"] = True
        record["dispatch_result"] = "CONFIRMED"
        self._store.update_work_context(
            context["job_safety"]["work_uid"], context
        )

    def _freeze_action_confirmation(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        outcome: str,
        evidence_sha256: str,
        confirmation_basis: str,
    ) -> dict[str, Any]:
        """Durably bind one expected receipt before the local RPC."""

        safety = context["job_safety"]
        confirmations = safety.setdefault("confirmations", {})
        existing = confirmations.get(action_key)
        confirmation = {
            "outcome": outcome,
            "evidence_sha256": evidence_sha256,
            "confirmation_basis": confirmation_basis,
            "confirmed": False,
        }
        if isinstance(existing, dict) and any(
            existing.get(field) != confirmation[field]
            for field in (
                "outcome",
                "evidence_sha256",
                "confirmation_basis",
            )
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "physical action confirmation evidence changed",
            )
        if isinstance(existing, dict):
            return existing
        confirmations[action_key] = confirmation
        self._store.update_work_context(safety["work_uid"], context)
        return confirmation

    def _cancel_prepared_physical_action_record(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        dispatch_attempt_token: str,
        evidence_sha256: str,
    ) -> None:
        action = self._physical_action(record)
        self._job_safety.cancel_prepared_physical_action(
            action,
            dispatch_attempt_token=dispatch_attempt_token,
            evidence_sha256=evidence_sha256,
        )
        self._remember_action_confirmation(
            context,
            action_key=action_key,
            record=record,
            outcome="NOT_EXECUTED",
            evidence_sha256=evidence_sha256,
            confirmation_basis="PREPARED_NOT_ARMED",
        )

    def _abort_physical_action_dispatch_record(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        dispatch_attempt_token: str,
        evidence_sha256: str,
    ) -> None:
        action = self._physical_action(record)
        self._job_safety.abort_physical_action_dispatch(
            action,
            dispatch_attempt_token=dispatch_attempt_token,
            evidence_sha256=evidence_sha256,
        )
        self._remember_action_confirmation(
            context,
            action_key=action_key,
            record=record,
            outcome="NOT_EXECUTED",
            evidence_sha256=evidence_sha256,
            confirmation_basis="LIVE_DISPATCH_NOT_WRITTEN",
        )

    def _close_dispatch_that_never_opened(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        dispatch_attempt_token: str,
        arm_was_attempted: bool,
    ) -> None:
        """Best-effort close only while this call has performed no write."""

        safety = context["job_safety"]
        evidence_sha256 = canonical_sha256(
            {
                "eventType": "UART_DISPATCH_GATE_DID_NOT_OPEN",
                "workUid": safety["work_uid"],
                "commandUid": safety["command_uid"],
                "actionUid": record["action_uid"],
            }
        )
        try:
            if arm_was_attempted:
                self._abort_physical_action_dispatch_record(
                    context,
                    action_key=action_key,
                    record=record,
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=evidence_sha256,
                )
            else:
                self._cancel_prepared_physical_action_record(
                    context,
                    action_key=action_key,
                    record=record,
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=evidence_sha256,
                )
            return
        except (JobSafetyError, KeyError, TypeError, ValueError):
            # If ARM did not commit, the permanent record may still be only
            # PREPARED. Its own state, unlike edge.db, is safe evidence for a
            # cancellation attempt.
            if arm_was_attempted:
                try:
                    self._cancel_prepared_physical_action_record(
                        context,
                        action_key=action_key,
                        record=record,
                        dispatch_attempt_token=dispatch_attempt_token,
                        evidence_sha256=evidence_sha256,
                    )
                    return
                except (JobSafetyError, KeyError, TypeError, ValueError):
                    pass
        record["dispatch_result"] = "UNKNOWN"
        self._store.update_work_context(safety["work_uid"], context)

    def _query_fixed_frame_self_test_with_gate(
        self,
        context: dict[str, Any],
        *,
        action: PhysicalAction | None,
        action_key: str,
        timeout_ms: int,
        dispatch_attempt_token: str | None,
        dispatch_deadline_monotonic_cap: float | None = None,
    ) -> dict[str, Any]:
        if action is None:
            return self._uart.query_self_test(
                timeout_ms=timeout_ms,
                on_result=self._store.save_fixed_frame_self_test,
            )

        if (
            not isinstance(dispatch_attempt_token, str)
            or not dispatch_attempt_token
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_DISPATCH_TOKEN_MISSING",
                "fixed-frame query has no live dispatch token",
            )
        if (
            isinstance(dispatch_deadline_monotonic_cap, bool)
            or not isinstance(
                dispatch_deadline_monotonic_cap, (int, float)
            )
            or not math.isfinite(float(dispatch_deadline_monotonic_cap))
            or dispatch_deadline_monotonic_cap <= 0
        ):
            raise JobSafetyError(
                "UART_DISPATCH_DEADLINE_MISSING",
                "fixed-frame query has no valid final dispatch deadline",
            )
        dispatch_deadline = min(
            time.monotonic() + timeout_ms / 1000.0,
            float(dispatch_deadline_monotonic_cap),
        )
        dispatch = {
            "gate_called": False,
            "gate_opened": False,
            "gate_aborted": False,
            "gate_returned": False,
        }

        def open_dispatch_gate() -> None:
            if dispatch["gate_called"]:
                raise JobSafetyError(
                    "UART_DISPATCH_GATE_REUSED",
                    "one dispatch attempt cannot authorize a second write",
                )
            dispatch["gate_called"] = True
            if time.monotonic() >= dispatch_deadline:
                raise JobSafetyError(
                    "COMMAND_EXPIRED",
                    "fixed-frame query expired while waiting for UART",
                )
            self._job_safety.arm_physical_action(
                action,
                dispatch_attempt_token=dispatch_attempt_token,
            )
            dispatch["gate_opened"] = True
            if time.monotonic() >= dispatch_deadline:
                self._abort_physical_action_dispatch_record(
                    context,
                    action_key=action_key,
                    record=context["job_safety"]["actions"][action_key],
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=canonical_sha256(
                        {
                            "eventType": (
                                "UART_QUERY_EXPIRED_DURING_ARM"
                            ),
                            "workUid": context["job_safety"]["work_uid"],
                            "actionUid": action.action_uid,
                        }
                    ),
                )
                dispatch["gate_aborted"] = True
                raise JobSafetyError(
                    "COMMAND_EXPIRED",
                    "fixed-frame query expired while opening its dispatch gate",
                )
            dispatch["gate_returned"] = True

        try:
            result = self._uart.query_self_test(
                timeout_ms=timeout_ms,
                on_result=self._store.save_fixed_frame_self_test,
                dispatch_gate=open_dispatch_gate,
            )
        except Exception:
            record = context["job_safety"]["actions"][action_key]
            if dispatch["gate_aborted"]:
                pass
            elif dispatch["gate_opened"] and dispatch["gate_returned"]:
                record["dispatch_result"] = "ARMED"
                context["phase"] = "QUERY_RESULT_UNKNOWN"
                self._store.update_work_context(
                    context["job_safety"]["work_uid"],
                    context,
                )
                logger.warning(
                    "fixed-frame query result became unknown after dispatch "
                    "ARM: work=%s action=%s",
                    context["job_safety"].get("work_uid"),
                    record.get("action_uid"),
                    exc_info=True,
                )
                return {
                    "acked": False,
                    "error": "UART_QUERY_RESULT_UNKNOWN",
                    "fatal": False,
                    "message_name": "QUERY_FIXED_FRAME_SELF_TEST",
                    "mcu_command_uid": record.get("action_uid"),
                    "physicalEffect": "UNKNOWN",
                    "queryStatus": "RESULT_UNKNOWN",
                    "compatibility_mode": True,
                }
            elif dispatch["gate_opened"]:
                record["dispatch_result"] = "ARMED"
                self._store.update_work_context(
                    context["job_safety"]["work_uid"],
                    context,
                )
            else:
                self._close_dispatch_that_never_opened(
                    context,
                    action_key=action_key,
                    record=record,
                    dispatch_attempt_token=dispatch_attempt_token,
                    arm_was_attempted=dispatch["gate_called"],
                )
            raise

        record = context["job_safety"]["actions"][action_key]
        if dispatch["gate_opened"]:
            record["dispatch_result"] = "ARMED"
            query_outcome = (
                "EXECUTED"
                if result.get("queryStatus") == "OK"
                else "FAILED_SAFE"
            )
            pending_query_result = {
                "action_key": action_key,
                "event_type": "FIXED_FRAME_F0_F1_QUERY_RESULT",
                "outcome": query_outcome,
                "result": dict(result),
            }
            existing_query_result = context.get(
                "pending_fixed_frame_query_result"
            )
            if (
                existing_query_result is not None
                and existing_query_result != pending_query_result
            ):
                raise JobSafetyError(
                    "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                    "fixed-frame query result changed before completion",
                )
            context["pending_fixed_frame_query_result"] = (
                pending_query_result
            )
            self._store.update_work_context(
                context["job_safety"]["work_uid"],
                context,
            )
            self._confirm_live_fixed_frame_action(
                context,
                action_key=action_key,
                event_type="FIXED_FRAME_F0_F1_QUERY_RESULT",
                payload=result,
                outcome=query_outcome,
                dispatch_attempt_token=dispatch_attempt_token,
            )
        elif result.get("queryStatus") == "UART_CLOSED":
            self._cancel_prepared_physical_action_record(
                context,
                action_key=action_key,
                record=record,
                dispatch_attempt_token=dispatch_attempt_token,
                evidence_sha256=canonical_sha256(
                    {
                        "eventType": "UART_QUERY_NOT_SENT",
                        "workUid": context["job_safety"]["work_uid"],
                        "actionUid": action.action_uid,
                        "errorCode": "UART_CLOSED",
                    }
                ),
            )
        else:
            self._preserve_dispatch_gate_bypass_uncertainty(
                context,
                action_key=action_key,
                record=record,
                dispatch_attempt_token=dispatch_attempt_token,
            )
            raise JobSafetyError(
                "UART_DISPATCH_GATE_BYPASSED",
                "fixed-frame query returned without invoking its dispatch gate",
            )
        return result

    def _confirm_action_from_mcu_event(
        self,
        context: dict[str, Any],
        *,
        expected_action_key: str,
        expected_action_kind: str,
        event_type: str,
        payload: dict[str, Any],
        outcome: str = "EXECUTED",
        allow_followup_fact: bool = False,
    ) -> None:
        """Resolve one action from an already-durable, identity-bound MCU fact."""

        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            return
        action_uid = payload.get("mcuCommandUid")
        actions = safety.get("actions", {})
        record = actions.get(expected_action_key)
        if not (
            isinstance(record, dict)
            and record.get("action_uid") == action_uid
            and record.get("action_key") == expected_action_key
            and record.get("action_kind") == expected_action_kind
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_MISMATCH",
                "MCU result does not identify the expected physical action",
            )
        confirmations = safety.setdefault("confirmations", {})
        existing = confirmations.get(expected_action_key)
        evidence_sha256 = canonical_sha256(
            {
                "eventType": event_type,
                "workUid": safety["work_uid"],
                "actionUid": action_uid,
                "payload": payload,
            }
        )
        if isinstance(existing, dict) and (
            existing.get("outcome") != outcome
            or existing.get("evidence_sha256") != evidence_sha256
        ):
            if (
                allow_followup_fact
                and existing.get("confirmed") is True
                and existing.get("outcome") == "EXECUTED"
                and existing.get("confirmation_basis")
                == "MCU_IDENTITY_BOUND_FACT"
            ):
                # Some MCU actions intentionally produce a lifecycle: one
                # clean unlock reports ENERGIZED and later DEENERGIZED; one
                # delivery authorization can report locally driven OPEN/CLOSE
                # transitions.  The first fact permanently confirms the edge
                # action.  Later facts may advance business state after the
                # caller validates their operation, port and transition, but
                # must never rewrite that immutable action receipt.
                return
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "physical action confirmation evidence changed",
            )
        self._confirm_physical_action_record(
            context,
            action_key=expected_action_key,
            record=record,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis="MCU_IDENTITY_BOUND_FACT",
        )

    def _confirm_physical_action_record(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        outcome: str,
        evidence_sha256: str,
        confirmation_basis: str,
    ) -> None:
        """Persist an exact receipt intent before its retry-safe local RPC."""

        confirmation = self._freeze_action_confirmation(
            context,
            action_key=action_key,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis=confirmation_basis,
        )
        if confirmation.get("confirmed") is True:
            return
        self._job_safety.confirm_physical_action(
            self._physical_action(record),
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            confirmation_basis=confirmation_basis,
        )
        confirmation["confirmed"] = True
        record["dispatch_result"] = "CONFIRMED"
        self._store.update_work_context(
            context["job_safety"]["work_uid"], context
        )

    def _complete_job_safety(
        self,
        context: dict[str, Any],
        *,
        outcome: str,
        evidence: dict[str, Any],
        physical_outcome: str | None = None,
    ) -> bool:
        """Finish the permanent permit only after business facts are durable."""

        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            return not self._job_safety.enabled
        pending = self._prepare_job_safety_completion(
            context,
            outcome=outcome,
            evidence=evidence,
            physical_outcome=physical_outcome,
        )
        if pending is None:
            return not self._job_safety.enabled
        work_uid = safety.get("work_uid")
        if isinstance(work_uid, str):
            self._store.update_work_context(work_uid, context)
        return self._finish_job_safety(safety, pending)

    def _prepare_job_safety_completion(
        self,
        context: dict[str, Any],
        *,
        outcome: str,
        evidence: dict[str, Any],
        physical_outcome: str | None = None,
    ) -> dict[str, Any] | None:
        """Freeze a retry identity before the business terminal transaction."""

        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if safety is None:
            return None
        existing = safety.get("pending_completion")
        if isinstance(existing, dict):
            if (
                existing.get("outcome") != outcome
                or existing.get("physical_outcome") != physical_outcome
            ):
                logger.critical(
                    "permanent job completion retry changed terminal facts: "
                    "work=%s",
                    safety.get("work_uid"),
                )
                raise JobSafetyError(
                    "JOB_COMPLETION_FACT_CONFLICT",
                    "terminal job facts changed after being frozen",
                )
            # The first attempt freezes the digest.  Never hash a context
            # that now contains its own pending record or overwrite a receipt
            # already accepted by the permanent ledger.
            return existing
        evidence_sha256 = canonical_sha256(evidence)
        pending = {
            "outcome": outcome,
            "evidence_sha256": evidence_sha256,
            "physical_outcome": physical_outcome,
        }
        safety["pending_completion"] = pending
        return pending

    def _finish_job_safety(
        self,
        safety: dict[str, Any],
        pending: dict[str, Any],
    ) -> bool:
        """Retry-safe permanent receipts using only persisted exact facts."""

        if not self._job_safety.enabled:
            raise JobSafetyError(
                "JOB_GATE_MODE_MISMATCH",
                "protected work cannot finish without the permanent job gate",
            )
        try:
            # Never manufacture an action result from a terminal business
            # record. edge.db can be rolled back; only the permanent ledger
            # may prove that every prepared/armed action was resolved.
            for record in safety.get("actions", {}).values():
                remote = self._job_safety.get_physical_action(
                    record["action_uid"]
                )
                if remote.get("state") == "CONFIRMED":
                    continue
                local_resolution = record.get(
                    "unknown_effect_resolution"
                )
                remote_resolution = remote.get(
                    "unknownEffectResolution"
                )
                unknown_effect_is_exact = bool(
                    pending.get("outcome") == "CANCELLED"
                    and pending.get("physical_outcome")
                    == "UNKNOWN_EFFECT_QUARANTINED"
                    and self._remote_action_identity_matches(
                        safety, record, remote
                    )
                    and remote.get("state") == "ARMED"
                    and isinstance(local_resolution, dict)
                    and local_resolution.get("confirmed") is True
                    and isinstance(remote_resolution, dict)
                    and remote_resolution.get("resolutionState")
                    == "UNKNOWN_EFFECT_QUARANTINED"
                    and remote_resolution.get("resolutionUid")
                    == local_resolution.get("resolution_uid")
                    and remote_resolution.get("actionUid")
                    == record.get("action_uid")
                    and remote_resolution.get("permitUid")
                    == safety.get("permit_uid")
                    and remote_resolution.get("workUid")
                    == safety.get("work_uid")
                    and remote_resolution.get("commandUid")
                    == safety.get("command_uid")
                    and remote_resolution.get("actionKey")
                    == record.get("action_key")
                    and remote_resolution.get("actionKind")
                    == record.get("action_kind")
                    and remote_resolution.get("actionDigestSha256")
                    == record.get("action_digest_sha256")
                    and remote_resolution.get("expectedLedgerSequence")
                    == local_resolution.get("expected_ledger_sequence")
                    and remote_resolution.get("evidenceDigestSha256")
                    == local_resolution.get("evidence_sha256")
                )
                if not unknown_effect_is_exact:
                    raise JobSafetyError(
                        "PHYSICAL_ACTION_UNCONFIRMED",
                        "permanent action remains prepared or armed",
                    )
            self._job_safety.complete_job(
                self._permit_from_safety_context(safety),
                completion_uid=safety["completion_uid"],
                outcome=pending["outcome"],
                completion_digest_sha256=pending["evidence_sha256"],
            )
            safety.pop("pending_completion", None)
            safety["completion_confirmed"] = True
            return True
        except (JobSafetyError, KeyError, TypeError, ValueError):
            # The business result has already committed and must still reach
            # the platform.  Leaving the permanent permit unresolved blocks a
            # new physical job until reconciliation, which is the safe result.
            logger.critical(
                "permanent job completion could not be confirmed: work=%s",
                safety.get("work_uid"),
                exc_info=True,
            )
            return False

    def reconcile_pending_job_safety_completion(self) -> bool:
        """Retry a terminal permanent receipt before releasing its work slot."""

        slot = self._store.get_work_slot()
        if slot is None:
            return False
        context = slot["context"]
        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        pending = (
            safety.get("pending_completion")
            if isinstance(safety, dict)
            else None
        )
        if not isinstance(pending, dict):
            return False
        if not self._finish_job_safety(safety, pending):
            return False
        self._store.release_work_slot(slot["work_uid"])
        return True

    def reconcile_pending_physical_action_confirmations(self) -> bool:
        """Retry frozen action receipts independently of the MCU inbox row."""

        slot = self._store.get_work_slot()
        if slot is None:
            return False
        context = slot["context"]
        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if not isinstance(safety, dict):
            return False
        confirmations = safety.setdefault("confirmations", {})
        progressed = False
        for action_key, confirmation in list(confirmations.items()):
            if (
                not isinstance(confirmation, dict)
                or confirmation.get("confirmed") is True
            ):
                continue
            record = safety.get("actions", {}).get(action_key)
            if not isinstance(record, dict):
                logger.critical(
                    "physical action confirmation lost its action record: "
                    "work=%s action=%s",
                    safety.get("work_uid"),
                    action_key,
                )
                return progressed
            try:
                if (
                    confirmation.get("confirmation_basis")
                    == "LIVE_FIXED_FRAME_RESULT"
                ):
                    if not self._reconcile_live_fixed_frame_confirmation(
                        context,
                        action_key=action_key,
                        record=record,
                        confirmation=confirmation,
                    ):
                        return progressed
                else:
                    self._confirm_physical_action_record(
                        context,
                        action_key=action_key,
                        record=record,
                        outcome=confirmation["outcome"],
                        evidence_sha256=confirmation["evidence_sha256"],
                        confirmation_basis=confirmation[
                            "confirmation_basis"
                        ],
                    )
            except (JobSafetyError, KeyError, TypeError, ValueError):
                return progressed
            progressed = True
        try:
            if self._resume_pending_fixed_frame_query_result(context):
                progressed = True
        except (JobSafetyError, KeyError, TypeError, ValueError):
            return progressed
        return progressed

    def _reconcile_live_fixed_frame_confirmation(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        confirmation: dict[str, Any],
    ) -> bool:
        """Adopt an exact late receipt or retry with this process's token."""

        action_uid = record["action_uid"]
        remote = self._job_safety.get_physical_action(action_uid)
        safety = context.get("job_safety")
        exact_confirmation = (
            self._exact_permanent_live_fixed_frame_confirmation(
                safety,
                action_key=action_key,
                record=record,
                confirmation=confirmation,
                remote=remote,
            )
        )
        with self._live_fixed_frame_dispatch_tokens_lock:
            dispatch_attempt_token = (
                self._live_fixed_frame_dispatch_tokens.get(action_uid)
            )
        if exact_confirmation:
            self._remember_action_confirmation(
                context,
                action_key=action_key,
                record=record,
                outcome=confirmation["outcome"],
                evidence_sha256=confirmation["evidence_sha256"],
                confirmation_basis="LIVE_FIXED_FRAME_RESULT",
            )
            self._forget_live_fixed_frame_dispatch_token(
                action_uid,
                dispatch_attempt_token,
            )
            return True
        if remote.get("state") == "CONFIRMED":
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "permanent fixed-frame receipt conflicts with local evidence",
            )
        if not isinstance(dispatch_attempt_token, str) or not (
            dispatch_attempt_token
        ):
            return False
        self._job_safety.confirm_live_physical_action_result(
            self._physical_action(record),
            dispatch_attempt_token=dispatch_attempt_token,
            outcome=confirmation["outcome"],
            evidence_sha256=confirmation["evidence_sha256"],
        )
        self._remember_action_confirmation(
            context,
            action_key=action_key,
            record=record,
            outcome=confirmation["outcome"],
            evidence_sha256=confirmation["evidence_sha256"],
            confirmation_basis="LIVE_FIXED_FRAME_RESULT",
        )
        self._forget_live_fixed_frame_dispatch_token(
            action_uid,
            dispatch_attempt_token,
        )
        return True

    def _resume_pending_fixed_frame_query_result(
        self,
        context: dict[str, Any],
    ) -> bool:
        """Finish a protected baseline from its exact persisted F0/F1 fact."""

        pending = context.get("pending_fixed_frame_query_result")
        if pending is None:
            return False
        if not isinstance(pending, dict) or not isinstance(
            pending.get("result"), dict
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "pending fixed-frame query result is invalid",
            )
        if (
            pending.get("action_key")
            != "BASELINE:FIXED_FRAME_QUERY:0"
            or pending.get("event_type")
            != "FIXED_FRAME_F0_F1_QUERY_RESULT"
            or pending.get("outcome") not in {"EXECUTED", "FAILED_SAFE"}
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "pending fixed-frame query identity is invalid",
            )
        safety = context.get("job_safety")
        if not isinstance(safety, dict):
            raise JobSafetyError(
                "JOB_PERMIT_MISSING",
                "pending fixed-frame query has no permanent job permit",
            )
        confirmation = safety.get("confirmations", {}).get(
            pending["action_key"]
        )
        if not (
            isinstance(confirmation, dict)
            and confirmation.get("confirmed") is True
        ):
            return False
        command_uid = safety.get("command_uid")
        command_row = (
            self._store.get_command(command_uid)
            if isinstance(command_uid, str)
            else None
        )
        command = command_row.get("payload") if command_row else None
        if not (
            isinstance(command, dict)
            and command.get("commandUid") == command_uid
            and command.get("commandType") == "MEASURE_EMPTY_BAG_BASELINE"
            and isinstance(command.get("payload"), dict)
            and command["payload"].get("measurementUid")
            == safety.get("work_uid")
            and command["payload"].get("measurementUid")
            == context.get("measurement_uid")
            and command["payload"].get("portNo") == context.get("port_no")
        ):
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "pending fixed-frame query command identity changed",
            )
        self._complete_compat_baseline_query_result(
            command,
            context,
            dict(pending["result"]),
            job_safety=safety,
        )
        return True

    def reconcile_pre_action_job_safety_failure(self) -> bool:
        """Close an interrupted job only when no physical action was armed."""

        slot = self._store.get_work_slot()
        if slot is None:
            return False
        context = slot["context"]
        self._require_job_safety_mode_alignment(context)
        safety = context.get("job_safety")
        if not isinstance(safety, dict):
            return False
        if safety.get("pending_completion"):
            return False
        actions = safety.get("actions", {})
        confirmations = safety.setdefault("confirmations", {})
        if actions:
            if not self._reconcile_never_dispatched_actions(
                context,
                safety,
            ):
                return False
            for action_key in safety.get("actions", {}):
                confirmation = confirmations.get(action_key)
                if not (
                    isinstance(confirmation, dict)
                    and confirmation.get("confirmed") is True
                    and confirmation.get("outcome") == "NOT_EXECUTED"
                ):
                    return False
        command_uid = safety.get("command_uid")
        command_row = (
            self._store.get_command(command_uid)
            if isinstance(command_uid, str)
            else None
        )
        if not command_row or command_row["state"] not in {
            "FAILED",
            "RECOVERY_REQUIRED",
        }:
            return False
        try:
            # The stable begin identity handles both possible outcomes of the
            # interrupted call: GRANTED becomes ACTIVE; ACTIVE is a duplicate.
            self._begin_job_safety(context)
        except (JobSafetyError, KeyError, TypeError, ValueError):
            return False
        reason = str(command_row.get("last_error") or "EDGE_RESTARTED")
        command = command_row.get("payload")
        evidence = {
            "eventType": "PHYSICAL_JOB_NOT_STARTED",
            "workUid": slot["work_uid"],
            "commandUid": command_uid,
            "reason": reason,
        }
        self._prepare_job_safety_completion(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome="NOT_EXECUTED",
        )
        if not isinstance(command, dict) or not self._store.fail_command_and_observe(
            command,
            reason,
            stage="FAILED",
            work_uid=slot["work_uid"],
            work_context=context,
        ):
            return False
        completed = self._complete_job_safety(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome="NOT_EXECUTED",
        )
        if not completed:
            return False
        self._store.release_work_slot(slot["work_uid"])
        return True

    def reconcile_orphan_granted_job_permits(self) -> int:
        """Abandon a grant committed before its business slot was created."""

        if not self._job_safety.enabled:
            return 0
        progressed = 0
        work_fields = {
            "START_DELIVERY_SESSION": ("DELIVERY", "sessionUid"),
            "START_CLEAN_OPERATION": ("CLEAN", "operationUid"),
            "SAMPLE_FULLNESS": ("FULLNESS", "detectionUid"),
            "MEASURE_EMPTY_BAG_BASELINE": ("BASELINE", "measurementUid"),
        }
        for row in self._store.list_orphan_permit_reconciliation_commands():
            command = row.get("payload")
            if not isinstance(command, dict):
                continue
            command_uid = command.get("commandUid")
            mapping = work_fields.get(command.get("commandType"))
            if not isinstance(command_uid, str) or mapping is None:
                continue
            work_type, work_field = mapping
            work_uid = (command.get("payload") or {}).get(work_field)
            try:
                remote = self._job_safety.get_job_permit(command_uid)
            except JobSafetyError as error:
                if error.code == "JOB_PERMIT_NOT_FOUND":
                    if self._store.mark_orphan_permit_reconciled(
                        command_uid
                    ):
                        progressed += 1
                continue
            expected_digest = command_request_digest(command)
            exact_identity = (
                remote.get("permitUid") == command_uid
                and remote.get("commandUid") == command_uid
                and remote.get("workUid") == work_uid
                and remote.get("workType") == work_type
                and remote.get("requestDigestSha256") == expected_digest
            )
            if not exact_identity:
                logger.critical(
                    "orphan permanent permit identity conflicts: command=%s",
                    command_uid,
                )
                continue
            if remote.get("state") == "GRANTED":
                permit = JobPermit(
                    permit_uid=command_uid,
                    work_uid=work_uid,
                    command_uid=command_uid,
                    work_type=work_type,
                    request_digest_sha256=expected_digest,
                )
                try:
                    self._job_safety.abandon_job(
                        permit,
                        disposition_uid=command_uid,
                        evidence_sha256=canonical_sha256(
                            {
                                "eventType": "EDGE_RESTARTED_BEFORE_SLOT",
                                "commandUid": command_uid,
                                "workUid": work_uid,
                            }
                        ),
                    )
                except JobSafetyError:
                    continue
            elif remote.get("state") not in {"ABANDONED", "COMPLETED"}:
                # ACTIVE without a business slot can follow a database
                # restore. It is not proof of zero physical effect.
                continue
            if self._store.mark_orphan_permit_reconciled(command_uid):
                progressed += 1
        return progressed

    def _reconcile_never_dispatched_actions(
        self,
        context: dict[str, Any],
        safety: dict[str, Any],
    ) -> bool:
        """Resolve only from the non-rollbackable permanent action state."""

        confirmations = safety.setdefault("confirmations", {})
        for action_key, record in list(
            safety.get("actions", {}).items()
        ):
            try:
                remote = self._job_safety.get_physical_action(
                    record["action_uid"]
                )
            except JobSafetyError as error:
                if error.code == "PHYSICAL_ACTION_NOT_FOUND":
                    safety["actions"].pop(action_key, None)
                    confirmations.pop(action_key, None)
                    continue
                return False
            if not self._remote_action_identity_matches(
                safety,
                record,
                remote,
            ):
                return False
            if remote.get("state") == "PREPARED":
                with self._uncertain_preparation_tokens_lock:
                    dispatch_attempt_token = (
                        self._uncertain_preparation_tokens.get(
                            record["action_uid"]
                        )
                    )
                if isinstance(dispatch_attempt_token, str) and (
                    dispatch_attempt_token
                ):
                    evidence_sha256 = canonical_sha256(
                        {
                            "eventType": (
                                "LIVE_PREPARE_RECOVERY_WITHOUT_DISPATCH"
                            ),
                            "workUid": safety["work_uid"],
                            "commandUid": safety["command_uid"],
                            "actionUid": record["action_uid"],
                            "actionDigestSha256": record[
                                "action_digest_sha256"
                            ],
                        }
                    )
                    try:
                        self._cancel_prepared_physical_action_record(
                            context,
                            action_key=action_key,
                            record=record,
                            dispatch_attempt_token=(
                                dispatch_attempt_token
                            ),
                            evidence_sha256=evidence_sha256,
                        )
                    except (
                        JobSafetyError,
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        return False
                    self._forget_uncertain_preparation_token(
                        record["action_uid"],
                        dispatch_attempt_token,
                    )
                    continue
                # Only the still-live dispatch call can prove that it has not
                # written bytes and cancel PREPARED.  After a process restart,
                # edge.db may itself be an older snapshot and cannot prove an
                # adapter did not bypass the callback before that snapshot.
                # Prefer a manual availability recovery over a repeated door
                # or solenoid action.
                return False
            if remote.get("state") != "CONFIRMED":
                return False
            if remote.get("confirmedOutcome") != "NOT_EXECUTED":
                return False
            basis = remote.get("confirmationBasis")
            remote_evidence = remote.get("evidenceDigestSha256")
            if basis not in {
                "PREPARED_NOT_ARMED",
                "LIVE_DISPATCH_NOT_WRITTEN",
            } or not isinstance(remote_evidence, str):
                return False
            try:
                self._remember_action_confirmation(
                    context,
                    action_key=action_key,
                    record=record,
                    outcome="NOT_EXECUTED",
                    evidence_sha256=remote_evidence,
                    confirmation_basis=basis,
                )
            except (JobSafetyError, KeyError, TypeError, ValueError):
                return False
        self._store.update_work_context(safety["work_uid"], context)
        return True

    def _fail_work_before_physical_action(
        self,
        *,
        context: dict[str, Any],
        command: dict[str, Any],
        work_uid: str,
        mcu_command_uid: str | None,
        error_code: str,
    ) -> dict[str, Any]:
        """Close a known-zero-effect job after its business failure is durable."""

        evidence = {
            "eventType": "PHYSICAL_JOB_NOT_STARTED",
            "workUid": work_uid,
            "commandUid": command["commandUid"],
            "reason": error_code,
        }
        self._prepare_job_safety_completion(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome="NOT_EXECUTED",
        )
        persisted = self._store.fail_fixed_frame_work(
            work_uid=work_uid,
            command=command,
            error_code=error_code,
            mcu_command_uid=mcu_command_uid,
            stage="PRE_START_FAILED",
            release_work_slot=not self._job_safety.enabled,
            work_context=context,
        )
        if not persisted:
            # Without the business-side terminal fact the permanent permit
            # must remain unresolved, so never try to release it here.
            raise ValueError("pre-action business failure was not persisted")
        completed = self._complete_job_safety(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome="NOT_EXECUTED",
        )
        if completed:
            self._store.release_work_slot(work_uid)
        return {
            "acked": False,
            "error": error_code,
            "mcu_command_uid": mcu_command_uid,
        }

    def _fail_active_job_at_safe_boundary(
        self,
        *,
        context: dict[str, Any],
        error_code: str,
        evidence: dict[str, Any],
    ) -> bool:
        """End work after prior actions are proven but the next one never ran."""

        safety = context.get("job_safety")
        work_uid = (
            safety.get("work_uid")
            if isinstance(safety, dict)
            else context.get("session_uid")
            or context.get("operation_uid")
            or context.get("detection_uid")
            or context.get("measurement_uid")
        )
        command_uid = (
            safety.get("command_uid")
            if isinstance(safety, dict)
            else context.get("start_command_uid")
            or context.get("command_uid")
        )
        command_row = (
            self._store.get_command(command_uid)
            if isinstance(command_uid, str)
            else None
        )
        command = command_row.get("payload") if command_row else None
        if not isinstance(work_uid, str) or not isinstance(command, dict):
            return False
        context["phase"] = "FAILED_SAFE"
        self._prepare_job_safety_completion(
            context,
            outcome="FAILED",
            evidence=evidence,
        )
        if not self._store.fail_command_and_observe(
            command,
            error_code,
            stage="FAILED",
            work_uid=work_uid if isinstance(safety, dict) else None,
            work_context=context if isinstance(safety, dict) else None,
        ):
            return False
        completed = self._complete_job_safety(
            context,
            outcome="FAILED",
            evidence=evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)
        return completed

    def _fail_unstored_job_before_physical_action(
        self,
        *,
        context: dict[str, Any],
        command: dict[str, Any],
        work_uid: str,
        error_code: str,
        physical_outcome: str = "NOT_EXECUTED",
    ) -> dict[str, Any]:
        """Close a fixed-frame local job that never acquired a work slot."""

        evidence = {
            "eventType": "PHYSICAL_JOB_NOT_STARTED",
            "workUid": work_uid,
            "commandUid": command["commandUid"],
            "reason": error_code,
        }
        self._prepare_job_safety_completion(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome=physical_outcome,
        )
        persisted = self._store.fail_command_and_observe(
            command,
            error_code,
            stage="PRE_START_FAILED",
            work_uid=work_uid if self._job_safety.enabled else None,
            work_context=context if self._job_safety.enabled else None,
        )
        if not persisted:
            raise ValueError("pre-action command failure was not persisted")
        completed = self._complete_job_safety(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome=physical_outcome,
        )
        if completed and self._job_safety.enabled:
            self._store.release_work_slot(work_uid)
        return {
            "acked": False,
            "error": error_code,
            "mcu_command_uid": None,
        }

    def _safety_rejection(self, port_no: int) -> Optional[str]:
        if getattr(self._uart, "compatibility_mode", False):
            if (
                not getattr(self._uart, "is_open", True)
                or self._store.get_active_edge_fault(
                    "UART",
                    "UART_PROTOCOL",
                )
                is not None
            ):
                return "SAFETY_SENSOR_UNHEALTHY"
            try:
                fixed_self_test = json.loads(
                    self._store.get_state(
                        "fixed_frame_latest_self_test_json",
                        "null",
                    )
                )
            except (TypeError, ValueError):
                fixed_self_test = None
            weight = (
                fixed_self_test.get("weightGrams")
                if isinstance(fixed_self_test, dict)
                else None
            )
            infrared = (
                fixed_self_test.get("infraredBlocked")
                if isinstance(fixed_self_test, dict)
                else None
            )
            if not (
                isinstance(fixed_self_test, dict)
                and fixed_self_test.get("portNo") == 1
                and fixed_self_test.get("queryStatus") == "OK"
                and fixed_self_test.get("communicationHealthy") is True
                and fixed_self_test.get("validFlags") == 3
                and fixed_self_test.get("weightValid") is True
                and isinstance(weight, int)
                and not isinstance(weight, bool)
                and 0 <= weight <= 350_000
                and fixed_self_test.get("infraredValid") is True
                and isinstance(infrared, bool)
            ):
                return "SAFETY_SENSOR_UNHEALTHY"
        smoke_state = self._store.get_state(
            f"port_{port_no}_smoke_state",
            self._store.get_state("smoke_state", ""),
        )
        smoke_health = self._store.get_state(
            f"port_{port_no}_smoke_sensor_health",
            self._store.get_state("smoke_sensor_health", ""),
        )
        if smoke_state == "ALARM":
            return "SAFETY_SMOKE_ALARM"
        if smoke_health and smoke_health != "OK":
            return "SAFETY_SENSOR_UNHEALTHY"
        return None

    def accept_photo_upload_grant(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        if self._photo is None:
            raise RuntimeError("photo manager is required")
        method = getattr(self._photo, "offer_upload_grant", None)
        if method is None:
            raise RuntimeError("photo upload is not supported")
        return method(command)

    @property
    def active_delivery_session(self) -> Optional[dict]:
        slot = self._store.get_work_slot()
        if slot and slot["work_type"] == WORK_TYPE_DELIVERY:
            return slot
        return None

    def _mark_fixed_frame_state_corrupt(
        self,
        slot: dict[str, Any],
        context: dict[str, Any],
        error: str,
    ) -> bool:
        """Retain fixed-frame work whose binding/deadline is untrusted."""

        context["phase"] = "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
        context["fixed_frame_recovery_error"] = error
        context["fixed_frame_recovery_monotonic_ms"] = _monotonic_ms()
        context["fixed_frame_recovery_boot_identity"] = (
            _system_boot_identity()
        )
        command_uid = context.get("start_command_uid")
        return (
            self._store.mark_fixed_frame_state_corrupt_for_recovery(
                work_type=slot["work_type"],
                work_uid=slot["work_uid"],
                command_type=self._fixed_frame_start_binding(
                    slot["work_type"]
                )["command_type"],
                command_uid=(
                    command_uid if isinstance(command_uid, str) else None
                ),
                work_context=context,
            )
        )

    def expire_fixed_frame_work(self) -> bool:
        """Move an overdue DD/EF wait into a non-replayable recovery lock.

        Elapsed edge time is not an authoritative delivery terminal because
        the fixed-frame MCU can extend its own operation.  Both delivery and
        clean therefore retain their original slot until a uniquely bound
        live result or an explicit recovery path resolves the physical fact.
        """
        if not getattr(self._uart, "compatibility_mode", False):
            return False
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] not in {
            WORK_TYPE_DELIVERY,
            WORK_TYPE_CLEAN,
        }:
            return False
        context = slot["context"]
        safety = context.get("job_safety")
        pending_completion = (
            safety.get("pending_completion")
            if isinstance(safety, dict)
            else None
        )
        if (
            context.get("phase") == "COMPLETING"
            or isinstance(pending_completion, dict)
        ):
            # The business terminal fact is already durable.  Only the exact
            # permanent COMPLETE_JOB receipt remains, so the elapsed-result
            # diagnostic must not rewrite the context into recovery and make
            # a replay bypass the fixed-frame terminal idempotence guard.
            return False
        if (
            context.get("phase")
            in {
                "CLEAN_RECOVERY_REQUIRED",
                "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED",
                "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED",
            }
        ):
            return False
        deadline = context.get("operation_deadline")
        if slot["work_type"] == WORK_TYPE_CLEAN and not deadline:
            return False
        try:
            if slot["work_type"] == WORK_TYPE_CLEAN:
                remaining_ms = _remaining_operation_window_ms(context)
            else:
                deadline_reference = local_deadline_reference()
                monotonic_deadline = context.get(
                    "delivery_result_deadline_monotonic_ms"
                )
                deadline_boot_identity = context.get(
                    "delivery_result_deadline_boot_identity"
                )
                has_frozen_monotonic_deadline = (
                    monotonic_deadline is not None
                    or deadline_boot_identity is not None
                )
                if has_frozen_monotonic_deadline:
                    if not (
                        isinstance(monotonic_deadline, int)
                        and not isinstance(monotonic_deadline, bool)
                        and monotonic_deadline > 0
                        and isinstance(deadline_boot_identity, str)
                        and deadline_boot_identity
                    ):
                        raise ValueError(
                            "fixed-frame delivery deadline is invalid"
                        )
                    if deadline_boot_identity != _system_boot_identity():
                        remaining_ms = 0
                    else:
                        remaining_ms = (
                            monotonic_deadline - _monotonic_ms()
                        )
                else:
                    # Legacy fixed-frame work predates the durable monotonic
                    # result deadline.  With trusted UTC, expiresAt is still a
                    # safe upper-bound anchor only after adding the complete
                    # post-dispatch MCU result window.  It must never be used
                    # alone: expiresAt authorizes the first dispatch, not DD.
                    expires_at = context.get("expires_at")
                    if not isinstance(expires_at, str) or not expires_at:
                        raise ValueError(
                            "fixed-frame delivery authorization deadline "
                            "is missing"
                        )
                    authorization_deadline = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                    if (
                        authorization_deadline.tzinfo is None
                        or authorization_deadline.utcoffset() is None
                    ):
                        raise ValueError(
                            "fixed-frame delivery deadline has no timezone"
                        )
                    result_window_ms = (
                        _fixed_frame_delivery_result_window_ms(context)
                    )
                    if deadline_reference is None:
                        # A legacy context has no comparable monotonic epoch.
                        # Losing trusted UTC must fail closed, never fall back
                        # to the generic ~49-day transport allowance.
                        remaining_ms = 0
                    else:
                        result_deadline = (
                            authorization_deadline
                            + timedelta(milliseconds=result_window_ms)
                        )
                        remaining_ms = int(
                            (
                                result_deadline - deadline_reference
                            ).total_seconds()
                            * 1_000
                        )
            if remaining_ms > 0:
                return False
        except (KeyError, TypeError, ValueError):
            if slot["work_type"] == WORK_TYPE_DELIVERY:
                return self._mark_fixed_frame_state_corrupt(
                    slot,
                    context,
                    "DELIVERY_DEADLINE_CONTEXT_INVALID",
                )
            # The clean-window helper intentionally raises both when its hard
            # window elapsed and when a reboot/reset made the old monotonic
            # epoch incomparable.  Either case closes further actions and
            # enters recovery; it is not proof that the command binding itself
            # is corrupt.
            remaining_ms = 0
        binding = self._fixed_frame_start_binding(slot["work_type"])
        command_uid = context.get("start_command_uid")
        command_row = (
            self._store.get_command(command_uid)
            if isinstance(command_uid, str) and command_uid
            else None
        )
        command = (
            command_row.get("payload")
            if command_row
            else None
        )
        command_payload = (
            command.get("payload")
            if isinstance(command, dict)
            else None
        )
        command_target = (
            command.get("target")
            if isinstance(command, dict)
            else None
        )
        immutable_command_binding_valid = (
            command_row is not None
            and command_row.get("command_type") == binding["command_type"]
            and isinstance(command, dict)
            and command.get("commandUid") == command_uid
            and command.get("commandType") == binding["command_type"]
            and isinstance(command_payload, dict)
            and command_payload.get(binding["payload_work_uid"])
            == slot["work_uid"]
            and isinstance(command_target, dict)
            and command_target.get("uid") == slot["work_uid"]
        )
        if not immutable_command_binding_valid:
            # AA may already have reached the MCU.  Losing its inbox row (or
            # finding an incompatible row behind the context link) is local
            # state corruption, regardless of whether this process currently
            # has permanent job safety enabled. Releasing the slot could let
            # B claim A's anonymous DD.
            return self._mark_fixed_frame_state_corrupt(
                slot,
                context,
                (
                    "START_COMMAND_NOT_FOUND"
                    if command_row is None
                    else "START_COMMAND_BINDING_INVALID"
                ),
            )
        # A fixed-frame DD/EF can be durably queued and confirmed in the
        # permanent updater before the edge terminal transaction commits.  On
        # restart the volatile ARM token is intentionally gone, but the exact
        # frozen evidence plus the immutable updater receipt is already enough
        # to let the original inbox row converge.  Do not rewrite that window
        # as corrupt merely because expiry runs before MCU inbox replay.
        safety = context.get("job_safety")
        actions = (
            safety.get("actions") if isinstance(safety, dict) else None
        )
        confirmations = (
            safety.get("confirmations")
            if isinstance(safety, dict)
            else None
        )
        action_key = binding["action_key"]
        frozen_confirmation = (
            confirmations.get(action_key)
            if isinstance(confirmations, dict)
            else None
        )
        if frozen_confirmation is not None:
            record = (
                actions.get(action_key)
                if isinstance(actions, dict)
                else None
            )
            if not isinstance(record, dict) or not isinstance(
                record.get("action_uid"), str
            ):
                return self._mark_fixed_frame_state_corrupt(
                    slot,
                    context,
                    "START_ACTION_CONFIRMED_EVIDENCE_INVALID",
                )
            try:
                remote = self._job_safety.get_physical_action(
                    record["action_uid"]
                )
            except JobSafetyError as error:
                if error.code == "JOB_GATE_UNAVAILABLE":
                    return False
                return self._mark_fixed_frame_state_corrupt(
                    slot,
                    context,
                    "START_ACTION_CONFIRMED_EVIDENCE_INVALID",
                )
            if self._exact_permanent_live_fixed_frame_confirmation(
                safety,
                action_key=action_key,
                record=record,
                confirmation=frozen_confirmation,
                remote=remote,
            ):
                return False
            if (
                remote.get("state") == "CONFIRMED"
                or not self._remote_action_identity_matches(
                    safety,
                    record,
                    remote,
                )
            ):
                return self._mark_fixed_frame_state_corrupt(
                    slot,
                    context,
                    "START_ACTION_CONFIRMED_EVIDENCE_INVALID",
                )
        command_state = command_row.get("state")
        if command_state != "WAITING_MCU_RESULT":
            abnormal_start_is_eligible = (
                command_state in {"FAILED", "RECOVERY_REQUIRED"}
                and context.get("phase")
                in {"STARTING", "START_RESULT_UNKNOWN"}
            )
            if abnormal_start_is_eligible:
                try:
                    exact_live_start = (
                        self._has_exact_live_fixed_frame_start_proof(
                            context,
                            work_type=slot["work_type"],
                            work_uid=slot["work_uid"],
                            command_uid=command_uid,
                            command=command,
                        )
                    )
                except JobSafetyError as error:
                    if error.code == "JOB_GATE_UNAVAILABLE":
                        # The permanent ARMED fact cannot currently be read.
                        # Preserve both databases unchanged and try later.
                        return False
                    exact_live_start = False
                except (KeyError, TypeError, ValueError):
                    exact_live_start = False
            else:
                exact_live_start = False
            if not exact_live_start:
                return self._mark_fixed_frame_state_corrupt(
                    slot,
                    context,
                    "START_ACTION_LIVE_PROOF_INVALID",
                )
        if (
            slot["work_type"] == WORK_TYPE_CLEAN
            and command_state == "WAITING_MCU_RESULT"
        ):
            # A normally dispatched clean uses the hard operation window and
            # its existing recovery workflow.  Only the post-ARM transport
            # uncertainty above needs the live-token compatibility bridge.
            return (
                self._store.mark_clean_window_expired_for_recovery(
                    slot["work_uid"]
                )
                == "RECOVERY_REQUIRED"
            )
        # Retain the original slot without replay. For delivery this is only a
        # diagnostic window because the MCU can extend its local flow. For the
        # exceptional clean branch the hard operation window has elapsed, but
        # a uniquely attributable terminal EF may still report what already
        # happened before the deadline.
        context["phase"] = (
            "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
        )
        overdue_prefix = (
            "delivery"
            if slot["work_type"] == WORK_TYPE_DELIVERY
            else "clean"
        )
        context[f"{overdue_prefix}_result_overdue"] = True
        context[f"{overdue_prefix}_result_overdue_monotonic_ms"] = (
            _monotonic_ms()
        )
        context[f"{overdue_prefix}_result_overdue_boot_identity"] = (
            _system_boot_identity()
        )
        return self._store.mark_fixed_frame_result_overdue_for_recovery(
            work_type=slot["work_type"],
            work_uid=slot["work_uid"],
            command_type=binding["command_type"],
            command_uid=command_uid,
            expected_command_state=command_state,
            work_context=context,
        )

    def quarantine_delivery_recovery(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        """Cancel one uncertain fixed-frame delivery as issue-only evidence.

        This is deliberately not a delivery completion.  It requires a new
        device boot, a fresh idle-safe MCU status, a fresh healthy sensor
        snapshot and explicit operator confirmations before it appends an
        unknown-effect resolution to the permanent ledger.
        """

        if command.get("commandType") != "QUARANTINE_DELIVERY_RECOVERY":
            raise ValueError("delivery recovery command type is invalid")
        if not getattr(self._uart, "compatibility_mode", False):
            raise JobSafetyError(
                "RECOVERY_MODE_NOT_SUPPORTED",
                "delivery quarantine currently requires fixed-frame MCU mode",
            )
        if not self._job_safety.enabled:
            raise JobSafetyError(
                "JOB_PERMIT_MISSING",
                "delivery quarantine requires the permanent job ledger",
            )
        payload = command.get("payload")
        target = command.get("target")
        if not isinstance(payload, dict) or not isinstance(target, dict):
            raise ValueError("delivery recovery command is incomplete")
        required_confirmations = (
            "physicalOutcomeUnknownConfirmed",
            "causeFixedConfirmed",
            "devicePowerCycledConfirmed",
            "motionAreaClearConfirmed",
            "deliveryDoorClosedConfirmed",
            "mechanismClearConfirmed",
        )
        if any(payload.get(field) is not True for field in required_confirmations):
            raise JobSafetyError(
                "RECOVERY_OPERATOR_CONFIRMATION_REQUIRED",
                "every delivery recovery safety confirmation must be true",
            )
        reason = payload.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
        ):
            raise ValueError("delivery recovery reason is invalid")
        recovery_uid = payload.get("recoveryUid")
        session_uid = payload.get("sessionUid")
        original_command_uid = payload.get("originalCommandUid")
        recovery_command_uid = command.get("commandUid")
        if not all(
            isinstance(value, str) and value
            for value in (
                recovery_uid,
                session_uid,
                original_command_uid,
                recovery_command_uid,
            )
        ):
            raise ValueError("delivery recovery identity is invalid")
        if target != {"type": "DELIVERY_SESSION", "uid": session_uid}:
            raise JobSafetyError(
                "RECOVERY_TARGET_MISMATCH",
                "delivery recovery target differs from its session",
            )

        slot = self._store.get_work_slot()
        if not (
            slot is not None
            and slot.get("work_type") == WORK_TYPE_DELIVERY
            and slot.get("work_uid") == session_uid
            and slot.get("work_state") == "RECOVERY_REQUIRED"
        ):
            raise JobSafetyError(
                "RECOVERY_WORK_NOT_ELIGIBLE",
                "the exact delivery is not locked for recovery",
            )
        context = slot.get("context")
        if not isinstance(context, dict):
            raise JobSafetyError(
                "RECOVERY_WORK_CONTEXT_INVALID",
                "delivery recovery context is unavailable",
            )
        self._require_job_safety_mode_alignment(context)
        original_phase = context.get("phase")
        if original_phase not in {
            "START_RESULT_UNKNOWN",
            "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED",
        }:
            raise JobSafetyError(
                "RECOVERY_WORK_NOT_ELIGIBLE",
                "delivery is not in an unknown-result recovery phase",
            )
        if (
            context.get("session_uid") != session_uid
            or context.get("start_command_uid") != original_command_uid
            or context.get("device_name") != command.get("targetDeviceName")
        ):
            raise JobSafetyError(
                "RECOVERY_WORK_IDENTITY_MISMATCH",
                "delivery recovery does not match the frozen work identity",
            )
        safety = context.get("job_safety")
        if not isinstance(safety, dict) or safety.get("pending_completion"):
            raise JobSafetyError(
                "RECOVERY_WORK_NOT_ELIGIBLE",
                "delivery already has a terminal completion in progress",
            )
        actions = safety.get("actions")
        if not isinstance(actions, dict) or set(actions) != {
            "DELIVERY:START:0"
        }:
            raise JobSafetyError(
                "RECOVERY_ACTION_IDENTITY_MISMATCH",
                "delivery does not have exactly one recoverable start action",
            )
        action_record = actions["DELIVERY:START:0"]
        if not (
            isinstance(action_record, dict)
            and action_record.get("action_uid")
            == context.get("start_mcu_command_uid")
            and action_record.get("action_key") == "DELIVERY:START:0"
            and action_record.get("action_kind")
            == "START_DELIVERY_SESSION"
            and action_record.get("preparation_result") == "PREPARED"
            and action_record.get("dispatch_result") == "ARMED"
            and self._is_lower_sha256(
                action_record.get("action_digest_sha256")
            )
        ):
            raise JobSafetyError(
                "RECOVERY_ACTION_IDENTITY_MISMATCH",
                "delivery start action is not an exact armed action",
            )
        confirmations = safety.get("confirmations")
        if isinstance(confirmations, dict) and confirmations.get(
            "DELIVERY:START:0"
        ) is not None:
            raise JobSafetyError(
                "RECOVERY_NORMAL_RESULT_ALREADY_RECORDED",
                "a normal delivery result already owns the start action",
            )

        original_row = self._store.get_command(original_command_uid)
        recovery_row = self._store.get_command(recovery_command_uid)
        original_envelope = (
            original_row.get("payload")
            if isinstance(original_row, dict)
            else None
        )
        if not (
            isinstance(original_row, dict)
            and original_row.get("command_type")
            == "START_DELIVERY_SESSION"
            and original_row.get("state") == "RECOVERY_REQUIRED"
            and isinstance(original_envelope, dict)
            and original_envelope.get("commandUid")
            == original_command_uid
            and original_envelope.get("target")
            == {"type": "DELIVERY_SESSION", "uid": session_uid}
            and isinstance(original_envelope.get("payload"), dict)
            and original_envelope["payload"].get("sessionUid")
            == session_uid
            and isinstance(recovery_row, dict)
            and recovery_row.get("command_type")
            == "QUARANTINE_DELIVERY_RECOVERY"
            and recovery_row.get("state") == "PROCESSING"
        ):
            raise JobSafetyError(
                "RECOVERY_COMMAND_IDENTITY_MISMATCH",
                "delivery recovery command bindings changed",
            )

        prior_boot_identity = context.get(
            "delivery_result_deadline_boot_identity"
        )
        current_boot_identity = _system_boot_identity()
        if not (
            isinstance(prior_boot_identity, str)
            and prior_boot_identity
            and isinstance(current_boot_identity, str)
            and current_boot_identity
            and current_boot_identity != prior_boot_identity
        ):
            raise JobSafetyError(
                "RECOVERY_POWER_CYCLE_NOT_PROVEN",
                "a device reboot after the uncertain action is not proven",
            )

        remote_action = self._job_safety.get_physical_action(
            action_record["action_uid"]
        )
        if not (
            self._remote_action_identity_matches(
                safety, action_record, remote_action
            )
            and remote_action.get("state") == "ARMED"
            and remote_action.get("dispatchMode") == "TWO_PHASE_V3"
            and remote_action.get("confirmedOutcome") is None
            and remote_action.get("confirmationBasis") is None
            and remote_action.get("receiptUid") is None
            and isinstance(remote_action.get("ledgerSequence"), int)
            and not isinstance(remote_action.get("ledgerSequence"), bool)
            and remote_action["ledgerSequence"] > 0
        ):
            raise JobSafetyError(
                "RECOVERY_PERMANENT_ACTION_MISMATCH",
                "permanent action is not the exact unresolved delivery start",
            )

        frozen = context.get("pending_recovery_quarantine")
        if frozen is None:
            firmware_status = self._uart.query_firmware_status(
                1, timeout_ms=3_000
            )
            if not (
                isinstance(firmware_status, dict)
                and firmware_status.get("queryStatus") == "OK"
                and firmware_status.get("mode") == 1
                and firmware_status.get("statusCode") == 0
                and firmware_status.get("protocolRevision") == 2
                and firmware_status.get("safeFlags") == 0x0F
                and isinstance(
                    firmware_status.get("rawFrameHex"), str
                )
                and firmware_status["rawFrameHex"]
            ):
                raise JobSafetyError(
                    "RECOVERY_MCU_NOT_IDLE_SAFE",
                    "fresh MCU status does not prove idle safe outputs",
                )
            self_test = self._uart.query_self_test(timeout_ms=3_000)
            if not (
                isinstance(self_test, dict)
                and self_test.get("queryStatus") == "OK"
                and self_test.get("communicationHealthy") is True
                and self_test.get("portNo") == context.get("port_no") == 1
                and self_test.get("validFlags") == 3
                and self_test.get("weightValid") is True
                and isinstance(self_test.get("weightGrams"), int)
                and not isinstance(self_test.get("weightGrams"), bool)
                and self_test.get("infraredValid") is True
                and isinstance(self_test.get("infraredBlocked"), bool)
                and self_test.get("smokeCode") == 0
                and self_test.get("smokeState") == "NORMAL"
                and self_test.get("smokeSensorHealth") == "OK"
                and self_test.get("faultCode") is None
                and isinstance(self_test.get("rawFrameHex"), str)
                and self_test["rawFrameHex"]
            ):
                raise JobSafetyError(
                    "RECOVERY_SENSOR_EVIDENCE_UNHEALTHY",
                    "fresh MCU self-test is incomplete or unsafe",
                )
            pending_result = getattr(
                self._uart, "has_pending_business_result", None
            )
            if not callable(pending_result) or pending_result():
                raise JobSafetyError(
                    "RECOVERY_NORMAL_RESULT_PENDING",
                    "a normal fixed-frame result must be processed first",
                )
            # Re-read rollbackable facts after the serial probes.  If a DD
            # won concurrently, its normal terminal path must take priority.
            current_slot = self._store.get_work_slot()
            current_original = self._store.get_command(original_command_uid)
            if not (
                current_slot is not None
                and current_slot.get("work_type") == WORK_TYPE_DELIVERY
                and current_slot.get("work_uid") == session_uid
                and current_slot.get("work_state") == "RECOVERY_REQUIRED"
                and current_original is not None
                and current_original.get("state") == "RECOVERY_REQUIRED"
            ):
                raise JobSafetyError(
                    "RECOVERY_NORMAL_RESULT_WON",
                    "delivery state changed while collecting recovery evidence",
                )
            evidence = {
                "eventType": "DELIVERY_RECOVERY_QUARANTINE_EVIDENCE",
                "recoveryUid": recovery_uid,
                "recoveryCommandUid": recovery_command_uid,
                "sessionUid": session_uid,
                "originalCommandUid": original_command_uid,
                "originalPhase": original_phase,
                "originalCommandState": original_row["state"],
                "previousBootIdentity": prior_boot_identity,
                "currentBootIdentity": current_boot_identity,
                "operatorConfirmations": {
                    field: payload[field]
                    for field in required_confirmations
                },
                "firmwareStatus": firmware_status,
                "selfTest": self_test,
                "permanentAction": {
                    "actionUid": remote_action["actionUid"],
                    "permitUid": remote_action["permitUid"],
                    "workUid": remote_action["workUid"],
                    "commandUid": remote_action["commandUid"],
                    "actionKey": remote_action["actionKey"],
                    "actionKind": remote_action["actionKind"],
                    "actionDigestSha256": remote_action[
                        "actionDigestSha256"
                    ],
                    "ledgerSequence": remote_action["ledgerSequence"],
                    "state": "ARMED",
                },
            }
            frozen = {
                "recovery_uid": recovery_uid,
                "recovery_command_uid": recovery_command_uid,
                "session_uid": session_uid,
                "original_command_uid": original_command_uid,
                "evidence": evidence,
                "evidence_sha256": canonical_sha256(evidence),
            }
            context["pending_recovery_quarantine"] = frozen
            action_record["unknown_effect_resolution"] = {
                "resolution_uid": recovery_uid,
                "expected_ledger_sequence": remote_action[
                    "ledgerSequence"
                ],
                "evidence_sha256": frozen["evidence_sha256"],
                "confirmed": False,
            }
            if not self._store.update_work_context(session_uid, context):
                raise JobSafetyError(
                    "RECOVERY_WORK_IDENTITY_MISMATCH",
                    "delivery work changed before evidence was frozen",
                )
        elif not (
            isinstance(frozen, dict)
            and frozen.get("recovery_uid") == recovery_uid
            and frozen.get("recovery_command_uid")
            == recovery_command_uid
            and frozen.get("session_uid") == session_uid
            and frozen.get("original_command_uid")
            == original_command_uid
            and isinstance(frozen.get("evidence"), dict)
            and frozen.get("evidence_sha256")
            == canonical_sha256(frozen["evidence"])
        ):
            raise JobSafetyError(
                "RECOVERY_EVIDENCE_CONFLICT",
                "frozen delivery recovery evidence changed",
            )

        resolution = action_record.get("unknown_effect_resolution")
        if not (
            isinstance(resolution, dict)
            and resolution.get("resolution_uid") == recovery_uid
            and resolution.get("expected_ledger_sequence")
            == remote_action["ledgerSequence"]
            and resolution.get("evidence_sha256")
            == frozen["evidence_sha256"]
        ):
            raise JobSafetyError(
                "RECOVERY_EVIDENCE_CONFLICT",
                "delivery action resolution evidence changed",
            )
        self._job_safety.quarantine_unknown_physical_action(
            self._permit_from_safety_context(safety),
            action=self._physical_action(action_record),
            resolution_uid=recovery_uid,
            expected_ledger_sequence=remote_action["ledgerSequence"],
            evidence_sha256=frozen["evidence_sha256"],
        )
        resolution["confirmed"] = True
        context["phase"] = "RECOVERY_QUARANTINE_COMPLETING"

        event_payload = {
            "recoveryUid": recovery_uid,
            "sessionUid": session_uid,
            "originalCommandUid": original_command_uid,
            "portNo": context["port_no"],
            "reason": reason.strip(),
            "businessValue": "NONE",
            "operatorConfirmations": frozen["evidence"][
                "operatorConfirmations"
            ],
            "deviceEvidence": {
                "previousBootIdentity": frozen["evidence"][
                    "previousBootIdentity"
                ],
                "currentBootIdentity": frozen["evidence"][
                    "currentBootIdentity"
                ],
                "firmwareIdentityHex": frozen["evidence"][
                    "firmwareStatus"
                ]["firmwareIdentityHex"],
                "safeFlags": frozen["evidence"]["firmwareStatus"][
                    "safeFlags"
                ],
                "firmwareStatusRawFrameHex": frozen["evidence"][
                    "firmwareStatus"
                ]["rawFrameHex"],
                "selfTestWeightGrams": frozen["evidence"]["selfTest"][
                    "weightGrams"
                ],
                "selfTestWeightMeasurementUid": frozen["evidence"][
                    "selfTest"
                ]["weightMeasurementUid"],
                "selfTestInfraredBlocked": frozen["evidence"][
                    "selfTest"
                ]["infraredBlocked"],
                "selfTestRawFrameHex": frozen["evidence"]["selfTest"][
                    "rawFrameHex"
                ],
                "actionUid": frozen["evidence"]["permanentAction"][
                    "actionUid"
                ],
                "permitUid": frozen["evidence"]["permanentAction"][
                    "permitUid"
                ],
                "workUid": frozen["evidence"]["permanentAction"][
                    "workUid"
                ],
                "commandUid": frozen["evidence"]["permanentAction"][
                    "commandUid"
                ],
                "actionKey": frozen["evidence"]["permanentAction"][
                    "actionKey"
                ],
                "actionKind": frozen["evidence"]["permanentAction"][
                    "actionKind"
                ],
                "actionDigestSha256": frozen["evidence"][
                    "permanentAction"
                ]["actionDigestSha256"],
                "ledgerSequence": frozen["evidence"]["permanentAction"][
                    "ledgerSequence"
                ],
                "actionState": "ARMED",
                "resolutionState": "UNKNOWN_EFFECT_QUARANTINED",
                "resolutionEvidenceSha256": frozen["evidence_sha256"],
            },
            "firstPreOpenMeasurement": _measurement_fact(
                context.get("first_measurement")
            ),
            "finalPostCloseMeasurement": _measurement_fact(
                context.get("final_measurement")
            ),
            "photos": self._completion_photo_facts(
                session_uid,
                "DELIVERY_SESSION",
                (
                    "BEFORE_INNER",
                    "BEFORE_OUTER",
                    "AFTER_INNER",
                    "AFTER_OUTER",
                ),
            ),
        }
        completion_evidence = {
            "eventType": "DELIVERY_RECOVERY_QUARANTINED",
            "recoveryUid": recovery_uid,
            "sessionUid": session_uid,
            "originalCommandUid": original_command_uid,
            "resolutionEvidenceSha256": frozen["evidence_sha256"],
            "businessValue": "NONE",
        }
        self._prepare_job_safety_completion(
            context,
            outcome="CANCELLED",
            evidence=completion_evidence,
            physical_outcome="UNKNOWN_EFFECT_QUARANTINED",
        )
        persisted = self._store.quarantine_delivery_recovery(
            work_uid=session_uid,
            original_command_uid=original_command_uid,
            recovery_command_uid=recovery_command_uid,
            recovery_uid=recovery_uid,
            context=context,
            event_payload=event_payload,
            device_name=command["targetDeviceName"],
            release_work_slot=False,
        )
        if persisted not in {"ACCEPTED", "DUPLICATE"}:
            raise JobSafetyError(
                "RECOVERY_WORK_IDENTITY_MISMATCH",
                "delivery recovery could not close the exact work slot",
            )
        completed = self._complete_job_safety(
            context,
            outcome="CANCELLED",
            evidence=completion_evidence,
            physical_outcome="UNKNOWN_EFFECT_QUARANTINED",
        )
        if completed:
            self._store.release_work_slot(session_uid)
        return {
            "disposition": "QUARANTINED",
            "recoveryUid": recovery_uid,
            "sessionUid": session_uid,
            "originalCommandUid": original_command_uid,
            "businessValue": "NONE",
            "jobCompletionConfirmed": completed,
        }

    def start_delivery_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """持久化并派发一次云端已授权的投递会话。"""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        # 后端只判断它能权威确认的身份、归属、OneNet 在线和整机占位。
        # 重启清运锁等业务安全事实必须在香橙派写串口前判断。超声波测距
        # 与烟雾状态一样只作为辅助设备事实上报，不参与投递准入。
        if self._store.clean_restart_interlock_active(
            payload["portNo"]
        ):
            return self._reject_command(
                command,
                "CLEAN_RESTARTED_CLEAN_REQUIRED",
            )
        safety_error = self._safety_rejection(payload["portNo"])
        if safety_error:
            return self._reject_command(command, safety_error)
        if (
            getattr(self._uart, "compatibility_mode", False)
            and payload["portNo"] != 1
        ):
            return self._reject_command(
                command,
                "MCU_FEATURE_NOT_SUPPORTED",
            )
        start_window_ms = _remaining_execution_ms(command)
        session_uid = payload["sessionUid"]
        mcu_command_uid = _new_uid()
        job_safety = self._request_job_safety(
            command,
            work_type=WORK_TYPE_DELIVERY,
            work_uid=session_uid,
        )
        ctx = {
            "session_uid": session_uid,
            "port_no": payload["portNo"],
            "bag_uid": payload["bagUid"],
            "unit_price_ten_thousandths": payload["unitPriceTenThousandths"],
            "continue_delivery_wait_ms": payload["continueDeliveryWaitMs"],
            "negative_weight_threshold_grams": payload[
                "negativeWeightThresholdGrams"
            ],
            "delivery_auto_close_ms": payload["deliveryAutoCloseMs"],
            "config": config,
            "start_command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "start_mcu_command_uid": mcu_command_uid,
            "expires_at": command["expiresAt"],
            "phase": "STARTING",
            # UART v1 defines the first delivery round as 1.  Keeping this
            # authoritative value in the work context binds the pre-open
            # measurement and every later door result to the same round.
            "round_index": 1,
            "negative_weight_anomaly": False,
            "first_weight_grams": None,
            "first_measurement_uid": None,
            "final_weight_grams": None,
            "final_measurement_uid": None,
        }
        if job_safety is not None:
            ctx["job_safety"] = job_safety
        # 单作业槽和命令 ACCEPTED 观察在 SQLite 中一起落盘，成功后才允许写串口。
        # 若设备进程在随后崩溃，启动恢复会明确结束未决作业，绝不自动重放旧开门。
        if not self._store.acquire_work_slot(
            WORK_TYPE_DELIVERY,
            session_uid,
            payload["portNo"],
            ctx,
            observed_command=command,
        ):
            self._abandon_job_safety(
                job_safety,
                reason="BUSINESS_WORK_SLOT_BUSY",
            )
            return self._reject_command(command, "DEVICE_BUSY")
        try:
            self._begin_job_safety(ctx)
        except Exception:
            self._preserve_uncertain_job_begin(ctx, session_uid)
            raise
        self._offer_initial_photo_grant(
            command,
            "DELIVERY_SESSION",
            session_uid,
        )
        compatibility_mode = getattr(
            self._uart,
            "compatibility_mode",
            False,
        )
        if compatibility_mode:
            self._capture_photos(
                "capture_open_photos",
                session_uid,
            )
        # Permit acquisition, begin reconciliation and photo persistence may
        # consume the whole command window.  Recompute at the last safe point
        # for every UART protocol mode; the earlier value is validation only.
        try:
            start_window_ms = _remaining_execution_ms(command)
        except ValueError:
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=session_uid,
                mcu_command_uid=mcu_command_uid,
                error_code="COMMAND_EXPIRED",
            )
        result = self._send_physical_command(
            ctx,
            "START_DELIVERY_SESSION",
            {
                "sessionUid": session_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "unitPriceTenThousandths": payload[
                    "unitPriceTenThousandths"
                ],
                "continueDeliveryWaitMs": payload["continueDeliveryWaitMs"],
                "negativeWeightThresholdGrams": payload[
                    "negativeWeightThresholdGrams"
                ],
                "startExecutionWindowMs": start_window_ms,
                "deliveryAutoCloseMs": payload["deliveryAutoCloseMs"],
            },
            mcu_command_uid=mcu_command_uid,
            action_key="DELIVERY:START:0",
            action_kind="START_DELIVERY_SESSION",
            not_after=command["expiresAt"],
        )
        fixed_frame_may_have_started = (
            compatibility_mode
            and (
                result["acked"]
                or result.get("physicalEffect") == "UNKNOWN"
            )
        )
        if fixed_frame_may_have_started:
            # The AA/BB fixed-frame path has no protocol ACK.  A successful
            # return means the first OPEN command bytes were written locally,
            # which is the earliest truthful anchor for the MCU-managed
            # delivery/result window.  The cloud expiresAt was consumed only
            # as the first-dispatch authorization above.
            result_window_ms = _fixed_frame_delivery_result_window_ms(ctx)
            ctx["delivery_result_window_ms"] = result_window_ms
            ctx["delivery_result_deadline_monotonic_ms"] = (
                _monotonic_ms() + result_window_ms
            )
            ctx["delivery_result_deadline_boot_identity"] = (
                _system_boot_identity()
            )
        if compatibility_mode:
            if result["acked"]:
                ctx["phase"] = "WAITING_COMPAT_DELIVERY_RESULT"
            elif result.get("physicalEffect") == "UNKNOWN":
                ctx["phase"] = "START_RESULT_UNKNOWN"
            else:
                ctx["phase"] = "START_FAILED"
        else:
            ctx["phase"] = (
                "WAITING_PREOPEN_WEIGHT"
                if result["acked"]
                else "START_RESULT_UNKNOWN"
            )
        self._store.update_work_context(session_uid, ctx)
        if result.get("physicalEffect") == "NOT_EXECUTED":
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=session_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=str(result.get("error") or "UART_WRITE_FAILED"),
            )
        if result["acked"]:
            # uart-v1 的 acked 是协议 ACK；fixed-frame 没有 ACK，兼容适配器只能表示
            # “串口字节已在本机写出”。MCU_ACCEPTED 是当前云端契约投影，不能当成
            # 门已实际打开的真机证据。
            self._store.record_command_observation(
                command,
                "MCU_ACCEPTED",
                mcu_command_uid=mcu_command_uid,
            )
        return result

    def start_clean_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Persist and dispatch one cloud-authorized clean operation."""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        safety_error = self._safety_rejection(payload["portNo"])
        if safety_error:
            return self._reject_command(command, safety_error)
        if (
            getattr(self._uart, "compatibility_mode", False)
            and payload["portNo"] != 1
        ):
            return self._reject_command(
                command,
                "MCU_FEATURE_NOT_SUPPORTED",
            )
        operation_uid = payload["operationUid"]
        mcu_command_uid = _new_uid()
        operation_window_ms = int(payload["operationWindowMs"])
        job_safety = self._request_job_safety(
            command,
            work_type=WORK_TYPE_CLEAN,
            work_uid=operation_uid,
        )
        ctx = {
            "operation_uid": operation_uid,
            "port_no": payload["portNo"],
            "old_bag_uid": payload["oldBagUid"],
            "old_baseline_weight_grams": payload[
                "oldBaselineWeightGrams"
            ],
            "new_bag_uid": payload["newBagUid"],
            "config": config,
            "start_command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "start_mcu_command_uid": mcu_command_uid,
            "recovery_generation": 0,
            "action_sequence": 0,
            "operation_deadline": _deadline_after_ms(
                operation_window_ms
            ),
            "operation_window_ms": operation_window_ms,
            "operation_started_monotonic_ms": _monotonic_ms(),
            "operation_started_boot_identity": _system_boot_identity(),
            "phase": "STARTING",
            "preunlock_weight_grams": None,
            "preunlock_measurement_uid": None,
            "final_weight_grams": None,
            "final_measurement_uid": None,
            "completion_confirmed": False,
        }
        if job_safety is not None:
            ctx["job_safety"] = job_safety
        if not self._store.acquire_work_slot(
            WORK_TYPE_CLEAN,
            operation_uid,
            payload["portNo"],
            ctx,
            observed_command=command,
        ):
            self._abandon_job_safety(
                job_safety,
                reason="BUSINESS_WORK_SLOT_BUSY",
            )
            return self._reject_command(command, "DEVICE_BUSY")
        try:
            self._begin_job_safety(ctx)
        except Exception:
            self._preserve_uncertain_job_begin(ctx, operation_uid)
            raise
        self._offer_initial_photo_grant(
            command,
            "CLEAN_OPERATION",
            operation_uid,
        )
        compatibility_mode = getattr(
            self._uart,
            "compatibility_mode",
            False,
        )
        if compatibility_mode:
            self._capture_photos(
                "capture_clean_open_photos",
                operation_uid,
            )
            try:
                start_window_ms = _remaining_execution_ms(command)
            except ValueError:
                return self._fail_work_before_physical_action(
                    context=ctx,
                    command=command,
                    work_uid=operation_uid,
                    mcu_command_uid=mcu_command_uid,
                    error_code="COMMAND_EXPIRED",
                )
        else:
            try:
                start_window_ms = _remaining_execution_ms(command)
            except ValueError:
                return self._fail_work_before_physical_action(
                    context=ctx,
                    command=command,
                    work_uid=operation_uid,
                    mcu_command_uid=mcu_command_uid,
                    error_code="COMMAND_EXPIRED",
                )
        result = self._send_physical_command(
            ctx,
            "START_CLEAN_OPERATION",
            {
                "operationUid": operation_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": start_window_ms,
                "operationWindowMs": payload["operationWindowMs"],
            },
            mcu_command_uid=mcu_command_uid,
            action_key="CLEAN:START:0",
            action_kind="START_CLEAN_OPERATION",
            not_after=command["expiresAt"],
        )
        if compatibility_mode:
            if result["acked"]:
                ctx["phase"] = "WAITING_COMPAT_CLEAN_RESULT"
            elif result.get("physicalEffect") == "UNKNOWN":
                ctx["phase"] = "START_RESULT_UNKNOWN"
            else:
                ctx["phase"] = "START_FAILED"
        else:
            ctx["phase"] = (
                "WAITING_PREUNLOCK_WEIGHT"
                if result["acked"]
                else "START_RESULT_UNKNOWN"
            )
        self._store.update_work_context(operation_uid, ctx)
        if result.get("physicalEffect") == "NOT_EXECUTED":
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=operation_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=str(result.get("error") or "UART_WRITE_FAILED"),
            )
        if result["acked"]:
            self._store.record_command_observation(
                command,
                "MCU_ACCEPTED",
                mcu_command_uid=mcu_command_uid,
            )
        return result

    def start_fullness_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        detection_uid = payload["detectionUid"]
        job_safety = self._request_job_safety(
            command,
            work_type=WORK_TYPE_FULLNESS,
            work_uid=detection_uid,
        )
        if getattr(self._uart, "compatibility_mode", False):
            return self._start_compat_fullness_command(
                command,
                job_safety=job_safety,
            )
        mcu_command_uid = _new_uid()
        ctx = {
            "detection_uid": detection_uid,
            "port_no": payload["portNo"],
            "command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "mcu_command_uid": mcu_command_uid,
            "payload": payload,
            "phase": "SAMPLING",
        }
        if job_safety is not None:
            ctx["job_safety"] = job_safety
        if not self._store.acquire_work_slot(
            WORK_TYPE_FULLNESS,
            detection_uid,
            payload["portNo"],
            ctx,
        ):
            self._abandon_job_safety(
                job_safety,
                reason="BUSINESS_WORK_SLOT_BUSY",
            )
            return {"acked": False, "error": "DEVICE_BUSY"}
        try:
            self._begin_job_safety(ctx)
        except Exception:
            self._preserve_uncertain_job_begin(ctx, detection_uid)
            raise
        try:
            start_window_ms = _remaining_execution_ms(command)
        except ValueError:
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=detection_uid,
                mcu_command_uid=mcu_command_uid,
                error_code="COMMAND_EXPIRED",
            )
        result = self._send_physical_command(
            ctx,
            "SAMPLE_FULLNESS",
            {
                "detectionUid": detection_uid,
                "portNo": payload["portNo"],
                "sampleRole": payload["sampleRole"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": start_window_ms,
                "settleWaitMs": payload["settleWaitMs"],
                "measurementTimeoutMs": payload["measurementTimeoutMs"],
            },
            mcu_command_uid=mcu_command_uid,
            action_key="FULLNESS:SAMPLE:0",
            action_kind="SAMPLE_FULLNESS",
            not_after=command["expiresAt"],
        )
        ctx["phase"] = (
            "WAITING_RESULT" if result["acked"] else "RESULT_UNKNOWN"
        )
        self._store.update_work_context(detection_uid, ctx)
        if result.get("physicalEffect") == "NOT_EXECUTED":
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=detection_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=str(result.get("error") or "UART_WRITE_FAILED"),
            )
        return result

    def _start_compat_fullness_command(
        self,
        command: dict[str, Any],
        *,
        job_safety: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = command["payload"]
        detection_uid = payload["detectionUid"]
        context: dict[str, Any] = {
            "detection_uid": detection_uid,
            "port_no": payload["portNo"],
            "command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "phase": "COMPAT_LOCAL_RESULT",
        }
        if job_safety is not None:
            context["job_safety"] = job_safety
            if not self._store.acquire_work_slot(
                WORK_TYPE_FULLNESS,
                detection_uid,
                payload["portNo"],
                context,
            ):
                self._abandon_job_safety(
                    job_safety,
                    reason="BUSINESS_WORK_SLOT_BUSY",
                )
                return {"acked": False, "error": "DEVICE_BUSY"}
        try:
            self._begin_job_safety(context)
        except Exception:
            if job_safety is not None:
                self._preserve_uncertain_job_begin(
                    context,
                    detection_uid,
                )
            raise
        try:
            observation = json.loads(
                self._store.get_state(
                    "fixed_frame_latest_observation_json",
                    "",
                )
            )
        except (TypeError, ValueError):
            observation = None
        has_history = (
            isinstance(observation, dict)
            and observation.get("portNo") == payload["portNo"]
            and self._valid_compat_fullness_observation(observation)
        )
        if not has_history:
            observation = {
                "postWeightGrams": 0,
                "infraredBlocked": False,
                "mcuBootId": max(
                    1,
                    int(self._store.get_edge_boot_id() or 1),
                ),
                "mcuEventSequence": (
                    self._store.reserve_compat_mcu_event_sequence()
                ),
            }
        measurement = _compat_measurement(
            detection_uid,
            "total-weight",
            observation["postWeightGrams"],
            observation,
        )
        event_payload = {
            "detectionUid": detection_uid,
            "portNo": payload["portNo"],
            "sampleRole": payload["sampleRole"],
            "triggerType": payload["triggerType"],
            "fullnessMode": payload["fullnessMode"],
            "fullnessSensorKind": "DIGITAL_INFRARED",
            "fullnessSensorValue": (
                "BLOCKED"
                if observation["infraredBlocked"]
                else "CLEAR"
            ),
            "fullnessSampleBasis": "NOT_SAMPLED",
            "representativeDistanceMm": None,
            "requestedSampleCount": 1 if has_history else 0,
            "validSampleCount": 1 if has_history else 0,
            "totalWeightMeasurement": _measurement_fact(measurement),
            "frozenConfig": _frozen_config(payload["config"]),
        }
        full_weight = payload["configuredFullWeightGrams"]
        fullness_percent = (
            observation["postWeightGrams"] * 100.0 / full_weight
        )
        local_result = {
            "fullnessSensorValue": event_payload[
                "fullnessSensorValue"
            ],
            "fullnessSampleBasis": "NOT_SAMPLED",
            "fullnessPercent": fullness_percent,
            "fullnessState": (
                "FULL"
                if fullness_percent >= 100.0
                else "NOT_FULL"
            ),
            "resultSource": (
                "LATEST_FLOW_POST"
                if has_history
                else "NO_HISTORY_ZERO"
            ),
        }
        completion_evidence = {
            "detectionUid": detection_uid,
            "result": local_result,
        }
        self._prepare_job_safety_completion(
            context,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        completed = self._store.complete_fixed_frame_local_result(
            result_type="FULLNESS",
            result_key=(
                f"{detection_uid}:{payload['sampleRole']}"
            ),
            command=command,
            event_uid=_new_uid(),
            event_type="FULLNESS_SAMPLE_COMPLETE",
            target_type="FULLNESS_DETECTION",
            target_uid=detection_uid,
            event_payload=event_payload,
            result=local_result,
            work_uid=detection_uid if job_safety is not None else None,
            work_context=context if job_safety is not None else None,
        )
        if completed not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame fullness persistence {completed.lower()}"
            )
        safety_completed = self._complete_job_safety(
            context,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if safety_completed and job_safety is not None:
            self._store.release_work_slot(detection_uid)
        return {
            "acked": True,
            "completed_locally": True,
            "mcu_command_uid": None,
            "disposition": local_result["resultSource"],
        }

    @staticmethod
    def _valid_compat_fullness_observation(
        observation: dict[str, Any],
    ) -> bool:
        post_weight = observation.get("postWeightGrams")
        mcu_boot_id = observation.get("mcuBootId")
        event_sequence = observation.get("mcuEventSequence")
        return (
            isinstance(post_weight, int)
            and not isinstance(post_weight, bool)
            and 0 <= post_weight <= 350_000
            and isinstance(observation.get("infraredBlocked"), bool)
            and isinstance(mcu_boot_id, int)
            and not isinstance(mcu_boot_id, bool)
            and mcu_boot_id > 0
            and isinstance(event_sequence, int)
            and not isinstance(event_sequence, bool)
            and event_sequence > 0
        )

    def start_baseline_command(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        if (
            getattr(self._uart, "compatibility_mode", False)
            and payload.get("emptyBagConfirmed") is not True
        ):
            return self._reject_command(
                command,
                "EMPTY_BAG_NOT_CONFIRMED",
            )
        measurement_uid = payload["measurementUid"]
        job_safety = self._request_job_safety(
            command,
            work_type=WORK_TYPE_BASELINE,
            work_uid=measurement_uid,
        )
        if getattr(self._uart, "compatibility_mode", False):
            return self._start_compat_baseline_command(
                command,
                job_safety=job_safety,
            )
        mcu_command_uid = _new_uid()
        ctx = {
            "measurement_uid": measurement_uid,
            "port_no": payload["portNo"],
            "bag_uid": payload["bagUid"],
            "empty_bag_confirmed": payload["emptyBagConfirmed"],
            "command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "mcu_command_uid": mcu_command_uid,
            "config": config,
            "phase": "MEASURING",
        }
        if job_safety is not None:
            ctx["job_safety"] = job_safety
        if not self._store.acquire_work_slot(
            WORK_TYPE_BASELINE,
            measurement_uid,
            payload["portNo"],
            ctx,
        ):
            self._abandon_job_safety(
                job_safety,
                reason="BUSINESS_WORK_SLOT_BUSY",
            )
            return {"acked": False, "error": "DEVICE_BUSY"}
        try:
            self._begin_job_safety(ctx)
        except Exception:
            self._preserve_uncertain_job_begin(ctx, measurement_uid)
            raise
        try:
            start_window_ms = _remaining_execution_ms(command)
        except ValueError:
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=measurement_uid,
                mcu_command_uid=mcu_command_uid,
                error_code="COMMAND_EXPIRED",
            )
        result = self._send_physical_command(
            ctx,
            "MEASURE_BASELINE",
            {
                "measurementUid": measurement_uid,
                "portNo": payload["portNo"],
                "configVersion": config["version"],
                "configContentSha256": config["contentSha256"],
                "startExecutionWindowMs": start_window_ms,
                "measurementTimeoutMs": payload["measurementTimeoutMs"],
            },
            mcu_command_uid=mcu_command_uid,
            action_key="BASELINE:MEASURE:0",
            action_kind="MEASURE_EMPTY_BAG_BASELINE",
            not_after=command["expiresAt"],
        )
        ctx["phase"] = (
            "WAITING_RESULT" if result["acked"] else "RESULT_UNKNOWN"
        )
        self._store.update_work_context(measurement_uid, ctx)
        if result.get("physicalEffect") == "NOT_EXECUTED":
            return self._fail_work_before_physical_action(
                context=ctx,
                command=command,
                work_uid=measurement_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=str(result.get("error") or "UART_WRITE_FAILED"),
            )
        return result

    def _start_compat_baseline_command(
        self,
        command: dict[str, Any],
        *,
        job_safety: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = command["payload"]
        measurement_uid = payload["measurementUid"]
        bag_uid = payload["bagUid"]
        context: dict[str, Any] = {
            "measurement_uid": measurement_uid,
            "port_no": payload["portNo"],
            "bag_uid": bag_uid,
            "command_uid": command["commandUid"],
            "device_name": command["targetDeviceName"],
            "phase": "COMPAT_QUERY_STARTING",
        }
        if job_safety is not None:
            context["job_safety"] = job_safety
            if not self._store.acquire_work_slot(
                WORK_TYPE_BASELINE,
                measurement_uid,
                payload["portNo"],
                context,
            ):
                self._abandon_job_safety(
                    job_safety,
                    reason="BUSINESS_WORK_SLOT_BUSY",
                )
                return {"acked": False, "error": "DEVICE_BUSY"}
        try:
            self._begin_job_safety(context)
        except Exception:
            if job_safety is not None:
                self._preserve_uncertain_job_begin(
                    context,
                    measurement_uid,
                )
            raise
        query_dispatch_clock_sample = time.monotonic()
        try:
            query_timeout_ms = min(
                3_000,
                payload["measurementTimeoutMs"],
                _remaining_execution_ms(command),
            )
        except ValueError:
            return self._fail_unstored_job_before_physical_action(
                context=context,
                command=command,
                work_uid=measurement_uid,
                error_code="COMMAND_EXPIRED",
            )
        query_dispatch_deadline = (
            query_dispatch_clock_sample + query_timeout_ms / 1000.0
        )
        # Reuse the command's durable measurement UUID as the permanent
        # physical-action identity. The actual arm still happens inside the
        # fixed-frame foreground UART lock immediately before its F0 write.
        action_uid = measurement_uid
        dispatch_attempt_token = (
            self._preparation_token_for(action_uid)
            if job_safety is not None
            else None
        )
        try:
            action = self._prepare_physical_action(
                context,
                message_name="QUERY_FIXED_FRAME_SELF_TEST",
                values={
                    "measurementUid": measurement_uid,
                    "portNo": payload["portNo"],
                    "bagUid": bag_uid,
                },
                mcu_command_uid=action_uid,
                action_key="BASELINE:FIXED_FRAME_QUERY:0",
                action_kind="MEASURE_EMPTY_BAG_BASELINE",
                dispatch_attempt_token=dispatch_attempt_token,
                persist_context=job_safety is not None,
            )
        except Exception:
            # No UART query has happened yet.  The permanent record remains
            # authoritative if the local response itself was uncertain.
            raise
        if action is None and job_safety is not None:
            return self._fail_unstored_job_before_physical_action(
                context=context,
                command=command,
                work_uid=measurement_uid,
                error_code=(
                    "PHYSICAL_ACTION_PREPARE_RESPONSE_UNAVAILABLE"
                ),
            )
        dispatch_clock_sample = time.monotonic()
        try:
            command_remaining_ms = _remaining_execution_ms(command)
            query_timeout_ms = min(query_timeout_ms, command_remaining_ms)
            query_dispatch_deadline = min(
                query_dispatch_deadline,
                dispatch_clock_sample + command_remaining_ms / 1000.0,
            )
        except ValueError:
            if action is not None:
                self._cancel_prepared_physical_action_record(
                    context,
                    action_key="BASELINE:FIXED_FRAME_QUERY:0",
                    record=context["job_safety"]["actions"][
                        "BASELINE:FIXED_FRAME_QUERY:0"
                    ],
                    dispatch_attempt_token=dispatch_attempt_token,
                    evidence_sha256=canonical_sha256(
                        {
                            "eventType": "UART_COMMAND_EXPIRED_BEFORE_WRITE",
                            "workUid": measurement_uid,
                            "actionUid": action.action_uid,
                            "notAfter": command["expiresAt"],
                        }
                    ),
                )
            return self._fail_unstored_job_before_physical_action(
                context=context,
                command=command,
                work_uid=measurement_uid,
                error_code="COMMAND_EXPIRED",
            )
        snapshot = self._query_fixed_frame_self_test_with_gate(
            context,
            action=action,
            action_key="BASELINE:FIXED_FRAME_QUERY:0",
            timeout_ms=query_timeout_ms,
            dispatch_attempt_token=dispatch_attempt_token,
            dispatch_deadline_monotonic_cap=query_dispatch_deadline,
        )
        if snapshot.get("physicalEffect") == "UNKNOWN":
            return snapshot
        return self._complete_compat_baseline_query_result(
            command,
            context,
            snapshot,
            job_safety=job_safety,
        )

    def _complete_compat_baseline_query_result(
        self,
        command: dict[str, Any],
        context: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        job_safety: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Commit one already-confirmed F0/F1 result without another query."""

        payload = command["payload"]
        measurement_uid = payload["measurementUid"]
        bag_uid = payload["bagUid"]
        if job_safety is not None:
            pending_query_result = context.get(
                "pending_fixed_frame_query_result"
            )
            expected_pending_result = {
                "action_key": "BASELINE:FIXED_FRAME_QUERY:0",
                "event_type": "FIXED_FRAME_F0_F1_QUERY_RESULT",
                "outcome": (
                    "EXECUTED"
                    if snapshot.get("queryStatus") == "OK"
                    else "FAILED_SAFE"
                ),
                "result": dict(snapshot),
            }
            if pending_query_result != expected_pending_result:
                raise JobSafetyError(
                    "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                    "pending fixed-frame query result changed",
                )
            action_record = job_safety.get("actions", {}).get(
                "BASELINE:FIXED_FRAME_QUERY:0"
            )
            confirmation = job_safety.get("confirmations", {}).get(
                "BASELINE:FIXED_FRAME_QUERY:0"
            )
            if not isinstance(action_record, dict) or not isinstance(
                confirmation, dict
            ):
                raise JobSafetyError(
                    "PHYSICAL_ACTION_UNCONFIRMED",
                    "fixed-frame query has no durable action receipt",
                )
            expected_evidence = canonical_sha256(
                {
                    "eventType": expected_pending_result["event_type"],
                    "workUid": job_safety["work_uid"],
                    "actionUid": action_record["action_uid"],
                    "payload": snapshot,
                }
            )
            if not (
                confirmation.get("confirmed") is True
                and confirmation.get("outcome")
                == expected_pending_result["outcome"]
                and confirmation.get("confirmation_basis")
                == "LIVE_FIXED_FRAME_RESULT"
                and confirmation.get("evidence_sha256")
                == expected_evidence
            ):
                raise JobSafetyError(
                    "PHYSICAL_ACTION_UNCONFIRMED",
                    "fixed-frame query receipt is not durably confirmed",
                )
        weight_grams = snapshot.get("weightGrams")
        stable = (
            snapshot.get("queryStatus") == "OK"
            and snapshot.get("communicationHealthy") is True
            and snapshot.get("portNo") == payload["portNo"]
            and snapshot.get("weightValid") is True
            and isinstance(weight_grams, int)
            and not isinstance(weight_grams, bool)
            and 0 <= weight_grams <= 350_000
        )
        query_status = str(
            snapshot.get("queryStatus") or "PROTOCOL_ERROR"
        )
        if stable:
            measurement_status = "STABLE"
            sensor_health = "OK"
            fault_code = "NONE"
        elif query_status == "TIMEOUT":
            measurement_status = "TIMEOUT"
            sensor_health = "TIMEOUT"
            fault_code = "WEIGHT_TIMEOUT"
        elif query_status == "UART_CLOSED":
            measurement_status = "DISCONNECTED"
            sensor_health = "DISCONNECTED"
            fault_code = "WEIGHT_DISCONNECTED"
        elif query_status == "OK":
            measurement_status = "SENSOR_FAULT"
            sensor_health = "UNKNOWN"
            fault_code = "WEIGHT_SENSOR"
        else:
            measurement_status = "PROTOCOL_ERROR"
            sensor_health = "PROTOCOL_ERROR"
            fault_code = "WEIGHT_PROTOCOL"

        mcu_boot_id = max(
            1,
            int(self._store.get_edge_boot_id() or 1),
        )
        mcu_event_sequence = (
            self._store.reserve_compat_mcu_event_sequence()
        )
        measurement = {
            "measurementUid": _compat_uid(
                measurement_uid,
                "baseline-f0-f1",
            ),
            "measurementStatus": measurement_status,
            "weightValuePresent": stable,
            "reportedWeightGrams": weight_grams if stable else None,
            "weightValueKind": (
                "STABLE_WINDOW_MEAN" if stable else "NONE"
            ),
            "measurementElapsedMs": 0,
            "sampleCount": 1 if stable else 0,
            "calibrationVersion": 0,
            "weightSensorHealth": sensor_health,
            "faultCode": fault_code,
            "mcuBootId": mcu_boot_id,
            "mcuEventSequence": mcu_event_sequence,
        }
        event_payload = {
            "measurementUid": measurement_uid,
            "portNo": payload["portNo"],
            "bagUid": bag_uid,
            "emptyBagConfirmed": True,
            "totalWeightMeasurement": _measurement_fact(measurement),
            "frozenConfig": _frozen_config(payload["config"]),
        }
        result = {
            "measurementStatus": measurement_status,
            "weightValuePresent": stable,
            "reportedWeightGrams": weight_grams if stable else None,
            "compatibilitySource": "FRESH_F0_F1_SNAPSHOT",
        }
        completion_evidence = {
            "measurementUid": measurement_uid,
            "queryStatus": query_status,
            "measurementStatus": measurement_status,
            "weightGrams": weight_grams if stable else None,
        }
        self._prepare_job_safety_completion(
            context,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        completion_context = context
        if job_safety is not None:
            completion_context = dict(context)
            completion_context.pop(
                "pending_fixed_frame_query_result", None
            )
        now = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        completed = self._store.complete_fixed_frame_local_result(
            result_type="BASELINE",
            result_key=measurement_uid,
            command=command,
            event_uid=_new_uid(),
            event_type="BASELINE_MEASUREMENT_COMPLETE",
            target_type="BASELINE_MEASUREMENT",
            target_uid=measurement_uid,
            event_payload=event_payload,
            result=result,
            bag_baseline=(
                {
                    "bag_uid": bag_uid,
                    "weight_grams": weight_grams,
                    "source_kind": "FIXED_FRAME_F0_F1",
                    "source_work_type": WORK_TYPE_BASELINE,
                    "source_work_uid": measurement_uid,
                    "source_mcu_boot_id": mcu_boot_id,
                    "source_mcu_event_sequence": mcu_event_sequence,
                    "source_observed_at": now,
                    "measurement_uid": measurement["measurementUid"],
                    "updated_at": now,
                }
                if stable
                else None
            ),
            work_uid=measurement_uid if job_safety is not None else None,
            work_context=(
                completion_context if job_safety is not None else None
            ),
        )
        if completed not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame baseline persistence {completed.lower()}"
            )
        if job_safety is not None:
            context.pop("pending_fixed_frame_query_result", None)
        safety_completed = self._complete_job_safety(
            context,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if safety_completed and job_safety is not None:
            self._store.release_work_slot(measurement_uid)
        return {
            "acked": True,
            "completed_locally": True,
            "mcu_command_uid": None,
            "disposition": (
                "FRESH_F0_F1_STABLE"
                if stable
                else "FRESH_F0_F1_FAILED"
            ),
        }

    def end_clean_before_unlock_command(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        # This command races with the last pre-unlock measurement handler.
        # Keep every in-process read/modify/write on that boundary in one
        # decision section; EdgeStore's claim below is the durable arbiter.
        with self._clean_unlock_decision_lock:
            return self._end_clean_before_unlock_command_locked(command)

    def _end_clean_before_unlock_command_locked(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        if getattr(self._uart, "compatibility_mode", False):
            return {
                "acked": False,
                "error": "MCU_FEATURE_NOT_SUPPORTED",
            }
        payload = command["payload"]
        slot = self._store.get_work_slot()
        if (
            not slot
            or slot["work_type"] != WORK_TYPE_CLEAN
            or slot["work_uid"] != payload["operationUid"]
            or slot["port_no"] != payload["portNo"]
        ):
            return {"acked": False, "error": "UNKNOWN_WORK"}
        ctx = slot["context"]
        if ctx.get("phase") not in {
            "STARTING",
            "WAITING_PREUNLOCK_WEIGHT",
            "PREUNLOCK_MEASURED",
            "PREUNLOCK_PHOTO_BLOCKED",
            "ENDING_BEFORE_UNLOCK",
            "END_BEFORE_UNLOCK_RESULT_UNKNOWN",
            "END_BEFORE_UNLOCK_RETRYABLE",
        }:
            return {"acked": False, "error": "STATE_CONFLICT"}
        try:
            execution_deadline_ms = _remaining_execution_ms(command)
        except ValueError:
            return {
                "acked": False,
                "error": "COMMAND_EXPIRED",
                "physicalEffect": "NOT_APPLICABLE",
            }
        existing_intent = ctx.get("end_before_unlock")
        if isinstance(existing_intent, dict):
            exact_retry = (
                existing_intent.get("command_uid")
                == command["commandUid"]
            )
            safe_takeover = (
                existing_intent.get("command_uid")
                != command["commandUid"]
                and existing_intent.get("state")
                == "RETRYABLE_NOT_ACCEPTED"
                and existing_intent.get("reason") == payload["reason"]
                and ctx.get("phase") == "END_BEFORE_UNLOCK_RETRYABLE"
            )
            if not exact_retry and not safe_takeover:
                return {"acked": False, "error": "STATE_CONFLICT"}
            mcu_command_uid = existing_intent["mcu_command_uid"]
            end_intent = existing_intent
        else:
            mcu_command_uid = _new_uid()
        # Persist the operator's cancellation intent before talking to the
        # MCU. The transaction is also the durable arbitration point with the
        # competing unlock claim: whichever claim commits first prevents the
        # other physical-control path from starting.
        claimed_context = self._store.claim_clean_end_before_unlock(
            work_uid=slot["work_uid"],
            port_no=slot["port_no"],
            command_uid=command["commandUid"],
            mcu_command_uid=mcu_command_uid,
            reason=payload["reason"],
        )
        if claimed_context is None:
            return {"acked": False, "error": "STATE_CONFLICT"}
        ctx = claimed_context
        end_intent = ctx["end_before_unlock"]
        mcu_command_uid = end_intent["mcu_command_uid"]
        result = self._send_end_clean_control_command(
            ctx,
            {
                "operationUid": slot["work_uid"],
                "portNo": slot["port_no"],
                "parentCommandUid": ctx["start_mcu_command_uid"],
                "recoveryGeneration": ctx["recovery_generation"],
                "executionDeadlineMs": execution_deadline_ms,
                "reason": payload["reason"],
            },
            mcu_command_uid=mcu_command_uid,
            not_after=command["expiresAt"],
        )
        if result["acked"]:
            end_intent["state"] = "ACKED"
            ctx["phase"] = "END_BEFORE_UNLOCK_ACKED"
            start_command_uid = ctx.get("start_command_uid")
            if start_command_uid:
                start_row = self._store.get_command(start_command_uid)
                if start_row and start_row["state"] not in {
                    "COMPLETED",
                    "FAILED",
                }:
                    self._store.fail_command(
                        start_command_uid,
                        payload["reason"],
                    )
            completion_evidence = {
                "operationUid": slot["work_uid"],
                "terminalAction": "END_CLEAN_BEFORE_UNLOCK",
                "reason": payload["reason"],
            }
            self._prepare_job_safety_completion(
                ctx,
                outcome="CANCELLED",
                evidence=completion_evidence,
            )
            self._store.complete_command_with_work_context(
                command["commandUid"],
                {"reason": payload["reason"]},
                work_uid=slot["work_uid"],
                work_context=ctx,
            )
            completed = self._complete_job_safety(
                ctx,
                outcome="CANCELLED",
                evidence=completion_evidence,
            )
            if completed:
                self._store.release_work_slot(slot["work_uid"])
        elif (
            result.get("controlAcceptance")
            == "RETRYABLE_NOT_ACCEPTED"
        ):
            end_intent["state"] = "RETRYABLE_NOT_ACCEPTED"
            ctx["phase"] = "END_BEFORE_UNLOCK_RETRYABLE"
            self._store.update_work_context(slot["work_uid"], ctx)
        else:
            end_intent["state"] = "RESULT_UNKNOWN"
            self._store.update_work_context(slot["work_uid"], ctx)
        return result

    def resume_clean_command(self, command: dict[str, Any]) -> dict[str, Any]:
        if getattr(self._uart, "compatibility_mode", False):
            return {
                "acked": False,
                "error": "MCU_FEATURE_NOT_SUPPORTED",
            }
        payload = command["payload"]
        self._require_applied_config(payload["config"])
        slot = self._store.get_work_slot()
        if (
            not slot
            or slot["work_type"] != WORK_TYPE_CLEAN
            or slot["work_uid"] != payload["operationUid"]
            or slot["port_no"] != payload["portNo"]
        ):
            return {"acked": False, "error": "UNKNOWN_WORK"}
        ctx = slot["context"]
        if ctx.get("new_bag_uid") not in (None, payload["newBagUid"]):
            return {"acked": False, "error": "IDEMPOTENCY_CONFLICT"}
        self._offer_initial_photo_grant(
            command,
            "CLEAN_OPERATION",
            payload["operationUid"],
        )
        current_generation = int(ctx.get("recovery_generation", 0))
        requested_generation = payload["recoveryGeneration"]
        if current_generation > requested_generation:
            return {
                "acked": False,
                "error": "RECOVERY_GENERATION_OUTDATED",
            }
        if (
            current_generation == requested_generation
            and ctx.get("phase") == "CLEAN_RECOVERY_REQUIRED"
            and ctx.get("resume_reconciliation_confirmed") is not False
        ):
            self._store.complete_command(
                command["commandUid"],
                {
                    "recoveryGeneration": current_generation,
                    "state": "CLEAN_RECOVERY_REQUIRED",
                },
            )
            return {
                "acked": True,
                "already_recovered": True,
                "mcu_command_uid": ctx.get("resume_mcu_command_uid"),
            }
        exact_inflight_retry = (
            current_generation == requested_generation
            and ctx.get("phase") == "RESUMING_CLEAN"
            and ctx.get("resume_command_uid") == command["commandUid"]
            and isinstance(ctx.get("resume_mcu_command_uid"), str)
            and isinstance(ctx.get("resume_action_key"), str)
            and isinstance(ctx.get("resume_next_action_sequence"), int)
        )
        if current_generation == requested_generation and not (
            exact_inflight_retry
        ):
            return {
                "acked": False,
                "error": "RECOVERY_GENERATION_NOT_ADVANCED",
            }
        if exact_inflight_retry:
            next_action_sequence = ctx["resume_next_action_sequence"]
            resume_uid = ctx["resume_mcu_command_uid"]
            resume_action_key = ctx["resume_action_key"]
        else:
            next_action_sequence = int(ctx.get("action_sequence", 0)) + 1
            resume_uid = _new_uid()
            resume_action_key = f"CLEAN:RESUME:{requested_generation}"
            ctx["recovery_generation"] = requested_generation
            ctx["resume_mcu_command_uid"] = resume_uid
            ctx["resume_action_key"] = resume_action_key
            ctx["resume_next_action_sequence"] = next_action_sequence
            ctx["resume_command_uid"] = command["commandUid"]
            ctx["resume_reconciliation_confirmed"] = False
            ctx["phase"] = "RESUMING_CLEAN"
            self._store.update_work_context(slot["work_uid"], ctx)
        result = self._send_physical_command(
            ctx,
            "RESUME_CLEAN_OPERATION",
            {
                "operationUid": slot["work_uid"],
                "portNo": slot["port_no"],
                "recoveryGeneration": requested_generation,
                "nextCleanActionSequence": next_action_sequence,
                "configVersion": payload["config"]["version"],
                "configContentSha256": payload["config"][
                    "contentSha256"
                ],
            },
            mcu_command_uid=resume_uid,
            action_key=resume_action_key,
            action_kind="RESUME_CLEAN_OPERATION",
            not_after=command["expiresAt"],
        )
        if result.get("physicalEffect") == "NOT_EXECUTED":
            ctx["phase"] = "CLEAN_RECOVERY_REQUIRED"
            ctx["resume_reconciliation_confirmed"] = False
            ctx["last_recovery_reason"] = str(
                result.get("error") or "CLEAN_RESUME_NOT_EXECUTED"
            )
            self._store.update_work_context(slot["work_uid"], ctx)
        return result

    def _require_applied_config(self, config: dict[str, Any]) -> None:
        version = self._store.get_state("applied_config_version")
        content_sha256 = self._store.get_state(
            "applied_config_content_sha256"
        )
        if (
            version != str(config.get("version"))
            or content_sha256 != config.get("contentSha256")
        ):
            raise ValueError("command config is not applied locally")

    def _create_reliable_event(
        self,
        *,
        event_type: str,
        target_type: str,
        work_uid: str,
        command_uid: Optional[str],
        device_name: Optional[str],
        payload: dict[str, Any],
        work_state_update: Optional[dict] = None,
        event_uid: Optional[str] = None,
        fullness_transition: Optional[dict] = None,
        mcu_receive_generation: Optional[int] = None,
        mcu_boot_id: Optional[int] = None,
        mcu_event_sequence: Optional[int] = None,
    ) -> str:
        event_uid = event_uid or _new_uid()
        return self._store.create_edge_event(
            event_uid=event_uid,
            event_type=event_type,
            payload=payload,
            work_uid=work_uid,
            work_state_update=work_state_update,
            device_name=device_name or "UNKNOWN_DEVICE",
            target_type=target_type,
            command_uid=command_uid,
            fullness_transition=fullness_transition,
            mcu_receive_generation=mcu_receive_generation,
            mcu_boot_id=mcu_boot_id,
            mcu_event_sequence=mcu_event_sequence,
        )

    def _fullness_transition(
        self,
        *,
        port_no: int,
        bag_uid: Optional[str],
        source_work_type: str,
        source_work_uid: str,
        device_name: str,
        measurement: dict[str, Any],
        infrared_blocked: Optional[bool],
        fixed_frame: bool,
        baseline_weight_grams: Optional[int] = None,
        reset_for_new_bag: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Build a reliable event only for a locally confirmed state change.

        A missing or failed observation never changes the current bag. A bag
        replacement deliberately starts at NOT_FULL; this mirrors the cloud
        rule that only an explicit current-bag FULL fact blocks delivery.
        """
        if not bag_uid:
            return None
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            return self._new_bag_default_transition(
                port_no,
                bag_uid,
                device_name,
            ) if reset_for_new_bag else None
        port_config = next(
            (
                port
                for port in applied["payload"].get("ports", [])
                if port.get("portNo") == port_no
            ),
            None,
        )
        if not port_config or port_config.get("enabled") is not True:
            return self._new_bag_default_transition(
                port_no,
                bag_uid,
                device_name,
            ) if reset_for_new_bag else None

        total_weight = _delivery_usable_weight(measurement)
        if baseline_weight_grams is None:
            baseline = self._store.get_bag_baseline(bag_uid)
            if baseline is not None:
                baseline_weight_grams = baseline.get("weight_grams")
        configured_full_weight = port_config.get(
            "configuredFullWeightGrams"
        )
        weight_available = (
            isinstance(total_weight, int)
            and not isinstance(total_weight, bool)
            and isinstance(baseline_weight_grams, int)
            and not isinstance(baseline_weight_grams, bool)
            and isinstance(configured_full_weight, int)
            and not isinstance(configured_full_weight, bool)
            and configured_full_weight > 0
        )
        weight_full = None
        fullness_percent_hundredths = None
        if weight_available:
            net_weight = max(0, total_weight - baseline_weight_grams)
            weight_full = net_weight >= configured_full_weight
            fullness_percent_hundredths = (
                net_weight * 10_000 // configured_full_weight
            )
        sensor_available = isinstance(infrared_blocked, bool)
        mode = port_config.get("fullnessMode")
        decided_state: Optional[str] = None
        if mode == "SENSOR_ONLY" and sensor_available:
            decided_state = "FULL" if infrared_blocked else "NOT_FULL"
        elif mode == "WEIGHT_ONLY" and weight_available:
            decided_state = "FULL" if weight_full else "NOT_FULL"
        elif mode == "SENSOR_OR_WEIGHT":
            if (sensor_available and infrared_blocked) or weight_full is True:
                decided_state = "FULL"
            elif sensor_available and weight_available:
                decided_state = "NOT_FULL"

        if decided_state is None:
            return self._new_bag_default_transition(
                port_no,
                bag_uid,
                device_name,
            ) if reset_for_new_bag else None

        state_change_uid = _new_uid()
        event_uid = _new_uid()
        measurement_fact = _measurement_fact(measurement)
        payload = {
            "stateChangeUid": state_change_uid,
            "portNo": port_no,
            "bagUid": bag_uid,
            "state": decided_state,
            "sourceWorkType": source_work_type,
            "sourceWorkUid": source_work_uid,
            "fullnessMode": mode,
            "fullnessSensorKind": (
                "DIGITAL_INFRARED"
                if fixed_frame
                else port_config.get("fullnessSensorKind")
            ),
            "fullnessSensorValue": (
                "NOT_SAMPLED"
                if not sensor_available
                else ("BLOCKED" if infrared_blocked else "CLEAR")
            ),
            "confirmationBasis": (
                "FIXED_FRAME_CACHED_FINAL_OBSERVATION"
                if fixed_frame
                else "MCU_INDEPENDENT_RECHECK"
            ),
            "totalWeightMeasurement": measurement_fact,
            "baselineWeightGrams": baseline_weight_grams,
            "configuredFullWeightGrams": configured_full_weight,
            "fullnessPercentHundredths": fullness_percent_hundredths,
            "weightFull": weight_full,
            "frozenConfig": _frozen_config(applied["payload"]["config"]),
        }
        return {
            "port_no": port_no,
            "bag_uid": bag_uid,
            "state": decided_state,
            "state_change_uid": state_change_uid,
            "event_uid": event_uid,
            "device_name": device_name,
            "payload": payload,
        }

    @staticmethod
    def _new_bag_default_transition(
        port_no: int,
        bag_uid: str,
        device_name: str,
    ) -> dict[str, Any]:
        return {
            "port_no": port_no,
            "bag_uid": bag_uid,
            "state": "NOT_FULL",
            "state_change_uid": _new_uid(),
            "event_uid": _new_uid(),
            "device_name": device_name,
            "payload": {},
        }

    def start_delivery_session(self, session_uid, port_no, unit_price_ten_thousandths,
                               bag_qr_code, negative_weight_threshold_grams=500):
        if self._job_safety.enabled:
            # This legacy debug entry point has no signed cloud command from
            # which to derive a permanent permit.  It must not become a side
            # door around the stage-four job gate.
            return {"success": False, "reason": "JOB_PERMIT_REQUIRED"}
        ctx = {"session_uid": session_uid, "port_no": port_no,
               "unit_price_ten_thousandths": unit_price_ten_thousandths,
               "bag_qr_code": bag_qr_code,
               "negative_weight_threshold_grams": negative_weight_threshold_grams,
               "phase": "STARTED", "round_index": 1,
               "negative_weight_anomaly": False, "first_weight_grams": None,
               "first_measurement_uid": None, "final_weight_grams": None,
               "final_measurement_uid": None}
        ok = self._store.acquire_work_slot(WORK_TYPE_DELIVERY, session_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("delivery session started: %s port=%d", session_uid, port_no)
        return {"success": True, "session_uid": session_uid}

    def authorize_first_open(self, session_uid):
        if self._job_safety.enabled:
            return {"success": False, "reason": "LEGACY_ENTRY_DISABLED"}
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != session_uid:
            return {"success": False, "reason": "SESSION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        preopen_uid = ctx.get("first_measurement_uid") or _new_uid()
        if not ctx.get("first_measurement_uid"):
            ctx["first_measurement_uid"] = preopen_uid
            self._store.update_work_context(session_uid, ctx)
        parent_cmd_uid = _new_uid()
        result = self._uart.send_authorize_delivery_first_open(
            session_uid=session_uid, port_no=port_no,
            preopen_measurement_uid=preopen_uid,
            parent_start_command_uid=parent_cmd_uid, remaining_ms=45000)
        if result["acked"]:
            ctx["phase"] = "WAITING_PREOPEN_WEIGHT"
            self._store.update_work_context(session_uid, ctx)
            return {"success": True, "session_uid": session_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def handle_mcu_event(self, frame):
        msg_name = frame.get("message_name", "")
        payload = frame.get("payload", {})
        mcu_receive_generation = int(
            frame.get("mcu_receive_generation") or 0
        )
        if msg_name == "COMPAT_DELIVERY_RESULT":
            self._on_compat_delivery_result(payload)
            return
        if msg_name == "COMPAT_CLEAN_RESULT":
            self._on_compat_clean_result(payload)
            return
        if msg_name == "SAFETY_SENSOR_EVENT":
            self._on_safety_sensor_event(
                payload,
                int(frame.get("mcu_receive_generation") or 0),
            )
            return
        if msg_name == "FAULT_OBSERVED":
            self._on_fault_observed(
                payload,
                int(frame.get("mcu_receive_generation") or 0),
            )
            return
        if msg_name == "SAFE_CLOSE_RESULT":
            self._on_safe_close_result(payload)
            return
        slot = self._store.get_work_slot()
        if not slot:
            return
        ctx = slot.get("context", {})
        work_uid = slot["work_uid"]
        work_type = slot["work_type"]
        if msg_name == "WORK_PREOPEN_WEIGHT_READY":
            self._on_preopen_weight(ctx, payload, work_uid)
        elif msg_name == "DELIVERY_DOOR_COMMAND_RESULT" and work_type == WORK_TYPE_DELIVERY:
            self._on_delivery_door_command_result(ctx, payload, work_uid)
        elif msg_name == "WORK_POSTCLOSE_WEIGHT_READY":
            self._on_postclose_weight(ctx, payload, work_uid)
        elif msg_name == "DELIVERY_SELECTION":
            self._on_delivery_selection(
                ctx,
                payload,
                work_uid,
                mcu_receive_generation=mcu_receive_generation,
            )
        elif msg_name == "WORK_PREUNLOCK_WEIGHT_READY" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_preunlock_weight(ctx, payload, work_uid)
        elif msg_name == "CLEAN_UNLOCK_REQUESTED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_unlock_requested(ctx, payload, work_uid)
        elif msg_name == "CLEAN_FINISH_REQUESTED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_finish_requested(ctx, payload, work_uid)
        elif msg_name == "CLEAN_LOCK_POWER_CHANGED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_lock_power_changed(ctx, payload, work_uid)
        elif msg_name == "CLEAN_FINAL_WEIGHT_READY" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_final_weight(ctx, payload, work_uid)
        elif msg_name == "CLEAN_COMPLETION_CONFIRMED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_completion(
                ctx,
                payload,
                work_uid,
                mcu_receive_generation=mcu_receive_generation,
            )
        elif msg_name == "FULLNESS_SAMPLE_RESULT" and work_type == WORK_TYPE_FULLNESS:
            self._on_fullness_sample_result(
                ctx,
                payload,
                work_uid,
                mcu_receive_generation=mcu_receive_generation,
            )
        elif msg_name == "BASELINE_MEASUREMENT_RESULT" and work_type == WORK_TYPE_BASELINE:
            self._on_baseline_measurement_result(
                ctx,
                payload,
                work_uid,
                mcu_receive_generation=mcu_receive_generation,
            )
        elif msg_name == "BOOT_RECONCILIATION_RESULT" and work_type == WORK_TYPE_CLEAN:
            self._on_boot_reconciliation_result(ctx, payload, work_uid)

    def _resume_committed_fixed_frame_work(
        self,
        context: dict[str, Any],
        work_uid: str,
    ) -> bool:
        """Finish a retained permanent permit without emitting a second event."""

        if context.get("phase") != "COMPLETING":
            return False
        safety = context.get("job_safety")
        command_uid = context.get("start_command_uid")
        command = (
            self._store.get_command(command_uid)
            if isinstance(command_uid, str)
            else None
        )
        pending = (
            safety.get("pending_completion")
            if isinstance(safety, dict)
            else None
        )
        if not (
            isinstance(safety, dict)
            and isinstance(pending, dict)
            and command is not None
            and command.get("state") == "COMPLETED"
        ):
            raise JobSafetyError(
                "JOB_COMPLETION_FACT_CONFLICT",
                "fixed-frame completion state is internally inconsistent",
            )
        if self._finish_job_safety(safety, pending):
            self._store.release_work_slot(work_uid)
        return True

    def _on_compat_delivery_result(self, payload: dict[str, Any]) -> None:
        """把固定帧 DD 结果收敛为唯一 DELIVERY_COMPLETE 事件。"""
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] != WORK_TYPE_DELIVERY:
            logger.warning(
                "ignoring DD result without active delivery work"
            )
            return
        pre_weight, post_weight = self._compat_result_weights(payload)
        work_uid = slot["work_uid"]
        ctx = slot["context"]
        if isinstance(ctx.get("fixed_frame_recovery_error"), str):
            raise JobSafetyError(
                "FIXED_FRAME_COMMAND_BINDING_CORRUPT",
                "fixed-frame DD cannot be bound to its original command",
            )
        result_was_overdue = ctx.get("delivery_result_overdue") is True
        self._confirm_live_fixed_frame_action(
            ctx,
            action_key="DELIVERY:START:0",
            event_type="FIXED_FRAME_DD_RESULT",
            payload=payload,
            outcome="EXECUTED",
        )
        if self._resume_committed_fixed_frame_work(ctx, work_uid):
            return
        first_measurement = _compat_measurement(
            work_uid,
            "pre",
            pre_weight,
            payload,
        )
        final_measurement = _compat_measurement(
            work_uid,
            "post",
            post_weight,
            payload,
        )
        ctx.update({
            "phase": "COMPLETING",
            "round_index": 1,
            "first_weight_grams": pre_weight,
            "first_measurement_uid": first_measurement["measurementUid"],
            "first_measurement": first_measurement,
            "final_weight_grams": post_weight,
            "final_measurement_uid": final_measurement["measurementUid"],
            "final_measurement": final_measurement,
            "negative_weight_anomaly": False,
            "last_delivery_door_command": "CLOSE",
            "last_delivery_door_output_status": "COMMAND_DISPATCHED",
            "delivery_door_physical_state_basis": "NOT_OBSERVABLE",
        })
        self._capture_photos(
            "capture_close_photos",
            work_uid,
        )
        event_payload = {
            "sessionUid": ctx.get("session_uid", work_uid),
            "portNo": ctx["port_no"],
            "firstPreOpenMeasurement": _measurement_fact(first_measurement),
            "finalPostCloseMeasurement": _measurement_fact(final_measurement),
            "deliveryNetWeightGrams": post_weight - pre_weight,
            "finalDoorCommand": {
                "command": "CLOSE",
                "outputStatus": "COMMAND_DISPATCHED",
                "physicalStateBasis": "NOT_OBSERVABLE",
            },
            "completionReason": "USER_ENDED",
            "manualReviewRequired": False,
            "negativeWeightAnomaly": False,
            "frozenConfig": _frozen_config(ctx["config"]),
            "unitPriceTenThousandths": ctx[
                "unit_price_ten_thousandths"
            ],
            "photos": self._completion_photo_facts(
                work_uid,
                "DELIVERY_SESSION",
                (
                    "BEFORE_INNER",
                    "BEFORE_OUTER",
                    "AFTER_INNER",
                    "AFTER_OUTER",
                ),
            ),
        }
        observation = {
            "sourceWorkType": WORK_TYPE_DELIVERY,
            "sourceWorkUid": work_uid,
            "portNo": ctx["port_no"],
            "postWeightGrams": post_weight,
            "infraredBlocked": payload["infraredBlocked"],
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
            # Local diagnostic only.  DELIVERY_COMPLETE remains the normal
            # contract because the predicted edge window was not authoritative
            # and cannot justify a backend manual-review outcome.
            "resultOverdue": result_was_overdue,
        }
        fullness_transition = self._fullness_transition(
            port_no=ctx["port_no"],
            bag_uid=ctx.get("bag_uid"),
            source_work_type="DELIVERY_SESSION",
            source_work_uid=ctx.get("session_uid", work_uid),
            device_name=ctx.get("device_name") or "UNKNOWN_DEVICE",
            measurement=final_measurement,
            infrared_blocked=payload["infraredBlocked"],
            fixed_frame=True,
        )
        completion_evidence = {
            "eventType": "DELIVERY_COMPLETE",
            "workUid": work_uid,
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
            "resultOverdue": result_was_overdue,
        }
        self._prepare_job_safety_completion(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        # 事件发件箱、命令完成、最终观测、满溢状态变化和作业槽释放由一个
        # SQLite 事务提交。这样强杀发生在任意时刻，都不会丢结果或重复生成第二单。
        created = self._store.complete_fixed_frame_work(
            work_type=WORK_TYPE_DELIVERY,
            work_uid=work_uid,
            command_uid=ctx["start_command_uid"],
            command_result={
                "preWeightGrams": pre_weight,
                "postWeightGrams": post_weight,
                "resultSource": "FIXED_FRAME_DD",
                "resultOverdue": result_was_overdue,
            },
            context=ctx,
            observation=observation,
            event_uid=_new_uid(),
            event_type="DELIVERY_COMPLETE",
            event_payload=event_payload,
            device_name=ctx.get("device_name") or "UNKNOWN_DEVICE",
            target_type="DELIVERY_SESSION",
            fullness_transition=fullness_transition,
            release_work_slot=not self._job_safety.enabled,
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame delivery persistence {created.lower()}"
            )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)
        logger.info(
            "fixed-frame delivery complete: %s net=%d",
            work_uid,
            post_weight - pre_weight,
        )

    def _on_compat_clean_result(self, payload: dict[str, Any]) -> None:
        slot = self._store.get_work_slot()
        if not slot or slot["work_type"] != WORK_TYPE_CLEAN:
            logger.warning("ignoring EF result without active clean work")
            return
        pre_weight, post_weight = self._compat_result_weights(payload)
        work_uid = slot["work_uid"]
        ctx = slot["context"]
        if isinstance(ctx.get("fixed_frame_recovery_error"), str):
            raise JobSafetyError(
                "FIXED_FRAME_COMMAND_BINDING_CORRUPT",
                "fixed-frame EF cannot be bound to its original command",
            )
        self._confirm_live_fixed_frame_action(
            ctx,
            action_key="CLEAN:START:0",
            event_type="FIXED_FRAME_EF_RESULT",
            payload=payload,
            outcome="EXECUTED",
        )
        if self._resume_committed_fixed_frame_work(ctx, work_uid):
            return
        pre_measurement = _compat_measurement(
            work_uid,
            "pre",
            pre_weight,
            payload,
        )
        final_measurement = _compat_measurement(
            work_uid,
            "post",
            post_weight,
            payload,
        )
        ctx.update({
            "phase": "COMPLETING",
            "action_sequence": 1,
            "preunlock_weight_grams": pre_weight,
            "preunlock_measurement_uid": pre_measurement["measurementUid"],
            "preunlock_measurement": pre_measurement,
            "final_weight_grams": post_weight,
            "final_measurement_uid": final_measurement["measurementUid"],
            "final_measurement": final_measurement,
            "completion_confirmed": True,
            "clean_lock_power_state": "DEENERGIZED",
            "clean_solenoid_health": "UNKNOWN",
            "clean_door_state_basis": "CLEANER_CONFIRMATION",
            "cleaner_physical_close_confirmed": True,
        })
        self._capture_photos(
            "capture_clean_close_photos",
            work_uid,
        )
        removed_weight = pre_weight - post_weight
        event_payload = {
            "operationUid": ctx.get("operation_uid", work_uid),
            "portNo": ctx["port_no"],
            "oldBagUid": ctx.get("old_bag_uid"),
            "newBagUid": ctx.get("new_bag_uid"),
            "preUnlockMeasurement": _measurement_fact(pre_measurement),
            "cleanerConfirmedFinalMeasurement": _measurement_fact(
                final_measurement
            ),
            "removedNetWeightGrams": removed_weight,
            "newBaselineWeightGrams": post_weight,
            "cleanerCompletionConfirmed": True,
            "cleanActionSequence": 1,
            "cleanLockAndManualDoorConfirmation": {
                "lockPowerState": "DEENERGIZED",
                "solenoidHealth": "UNKNOWN",
                "physicalDoorStateBasis": "CLEANER_CONFIRMATION",
                "cleanerPhysicalCloseConfirmed": True,
            },
            "frozenConfig": _frozen_config(ctx["config"]),
            "photos": self._completion_photo_facts(
                work_uid,
                "CLEAN_OPERATION",
                (
                    "FIRST_OPEN_INNER",
                    "FIRST_OPEN_OUTER",
                    "FINAL_CLOSE_INNER",
                    "FINAL_CLOSE_OUTER",
                ),
            ),
        }
        observation = {
            "sourceWorkType": WORK_TYPE_CLEAN,
            "sourceWorkUid": work_uid,
            "portNo": ctx["port_no"],
            "postWeightGrams": post_weight,
            "infraredBlocked": payload["infraredBlocked"],
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
        }
        now = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        completion_evidence = {
            "eventType": "CLEAN_COMPLETE",
            "workUid": work_uid,
            "mcuBootId": payload["mcuBootId"],
            "mcuEventSequence": payload["mcuEventSequence"],
            "measurementUid": final_measurement["measurementUid"],
        }
        self._prepare_job_safety_completion(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        created = self._store.complete_fixed_frame_work(
            work_type=WORK_TYPE_CLEAN,
            work_uid=work_uid,
            command_uid=ctx["start_command_uid"],
            command_result={
                "preWeightGrams": pre_weight,
                "postWeightGrams": post_weight,
                "resultSource": "FIXED_FRAME_EF",
            },
            context=ctx,
            observation=observation,
            event_uid=_new_uid(),
            event_type="CLEAN_COMPLETE",
            event_payload=event_payload,
            device_name=ctx.get("device_name") or "UNKNOWN_DEVICE",
            target_type="CLEAN_OPERATION",
            bag_baseline={
                "bag_uid": ctx["new_bag_uid"],
                "weight_grams": post_weight,
                "source_kind": "SAME_BAG_CLEAN_POST",
                "source_work_type": WORK_TYPE_CLEAN,
                "source_work_uid": work_uid,
                "source_mcu_boot_id": payload["mcuBootId"],
                "source_mcu_event_sequence": payload[
                    "mcuEventSequence"
                ],
                "source_observed_at": now,
                "measurement_uid": final_measurement[
                    "measurementUid"
                ],
                "updated_at": now,
            },
            fullness_transition=self._fullness_transition(
                port_no=ctx["port_no"],
                bag_uid=ctx["new_bag_uid"],
                source_work_type="CLEAN_OPERATION",
                source_work_uid=ctx.get("operation_uid", work_uid),
                device_name=(
                    ctx.get("device_name") or "UNKNOWN_DEVICE"
                ),
                measurement=final_measurement,
                infrared_blocked=payload["infraredBlocked"],
                fixed_frame=True,
                baseline_weight_grams=post_weight,
                reset_for_new_bag=True,
            ),
            release_work_slot=not self._job_safety.enabled,
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame clean persistence {created.lower()}"
            )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)
        logger.info(
            "fixed-frame clean complete: %s removed=%d",
            work_uid,
            removed_weight or 0,
        )

    @staticmethod
    def _compat_result_weights(
        payload: dict[str, Any],
    ) -> tuple[int, int]:
        values = (
            payload.get("preWeightGrams"),
            payload.get("postWeightGrams"),
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 350_000
            for value in values
        ):
            raise ValueError("invalid fixed-frame weight result")
        if (
            not isinstance(payload.get("mcuBootId"), int)
            or isinstance(payload.get("mcuBootId"), bool)
            or payload["mcuBootId"] <= 0
            or not isinstance(payload.get("mcuEventSequence"), int)
            or isinstance(payload.get("mcuEventSequence"), bool)
            or payload["mcuEventSequence"] <= 0
            or not isinstance(payload.get("infraredBlocked"), bool)
        ):
            raise ValueError("invalid fixed-frame result identity")
        return values

    def _on_safety_sensor_event(
        self,
        payload,
        mcu_receive_generation: int,
    ):
        result = self._store.record_safety_state_and_event(
            device_name=self._own_device_name(),
            mcu_receive_generation=mcu_receive_generation,
            payload=payload,
        )
        if result not in ("ACCEPTED", "DUPLICATE", "UNCHANGED"):
            raise ValueError(
                f"safety sensor event {result.lower()}"
            )

    def _on_fault_observed(
        self,
        payload,
        mcu_receive_generation: int,
    ):
        severity = str(payload.get("severity") or "WARNING")
        lifecycle = str(payload.get("lifecycle") or "OBSERVED")
        fault_uid = str(payload.get("faultUid") or _new_uid())
        device_name = self._own_device_name()
        component = str(
            payload.get("component") or "MCU_INTERNAL"
        )
        fault_code = str(
            payload.get("faultCode") or "MCU_INTERNAL"
        )
        port_no = (
            payload.get("portNo")
            if isinstance(payload.get("portNo"), int)
            and payload.get("portNo") > 0
            else None
        )
        if lifecycle == "RECOVERED":
            result = self._store.recover_fault_and_create_event(
                device_name=device_name,
                fault_uid=fault_uid,
                component=component,
                fault_code=fault_code,
                port_no=port_no,
                recovery_evidence="MCU_RECOVERY_EVENT",
                mcu_boot_id=payload.get("mcuBootId"),
                mcu_event_sequence=payload.get(
                    "mcuEventSequence"
                ),
                mcu_receive_generation=mcu_receive_generation,
            )
        else:
            result = self._store.observe_fault_and_create_event(
                device_name=device_name,
                component=component,
                fault_code=fault_code,
                severity=severity,
                port_no=port_no,
                fault_uid=fault_uid,
                mcu_boot_id=payload.get("mcuBootId"),
                mcu_event_sequence=payload.get(
                    "mcuEventSequence"
                ),
                mcu_receive_generation=mcu_receive_generation,
                detail=dict(payload),
            )
        if result not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(f"fault lifecycle result {result.lower()}")

    def _on_boot_reconciliation_result(self, ctx, payload, work_uid):
        if (
            payload.get("decision") != "RESUME_CLEAN_OPERATION"
            or payload.get("activeWorkType") != "CLEAN_OPERATION"
            or payload.get("mcuCommandUid")
            != ctx.get("resume_mcu_command_uid")
            or payload.get("activeWorkUid") != work_uid
            or payload.get("activePortNo") != ctx.get("port_no")
            or payload.get("recoveryGeneration")
            != ctx.get("recovery_generation")
            or payload.get("nextCleanActionSequence")
            != ctx.get("resume_next_action_sequence")
        ):
            raise ValueError("boot reconciliation result does not match clean")
        if payload.get("status") != "ACCEPTED":
            self._confirm_action_from_mcu_event(
                ctx,
                expected_action_key=ctx.get("resume_action_key")
                or f"CLEAN:RESUME:{ctx['recovery_generation']}",
                expected_action_kind="RESUME_CLEAN_OPERATION",
                event_type="BOOT_RECONCILIATION_RESULT",
                payload=payload,
                outcome="FAILED_SAFE",
            )
            ctx["phase"] = "CLEAN_RECOVERY_FAILED"
            self._store.update_work_context(work_uid, ctx)
            raise ValueError(
                "clean resume rejected: "
                + str(payload.get("faultCode") or "MCU_INTERNAL")
            )
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key=ctx.get("resume_action_key")
            or f"CLEAN:RESUME:{ctx['recovery_generation']}",
            expected_action_kind="RESUME_CLEAN_OPERATION",
            event_type="BOOT_RECONCILIATION_RESULT",
            payload=payload,
        )
        ctx["resume_reconciliation_confirmed"] = True
        ctx["phase"] = "CLEAN_RECOVERY_REQUIRED"
        self._store.update_work_context(work_uid, ctx)
        command_uid = ctx.get("resume_command_uid")
        if command_uid:
            self._store.complete_command(
                command_uid,
                {
                    "recoveryGeneration": ctx["recovery_generation"],
                    "state": "CLEAN_RECOVERY_REQUIRED",
                },
            )

    def _on_safe_close_result(self, payload):
        self._store.set_state(
            "last_safe_close_output_status",
            str(payload.get("outputStatus") or "UNKNOWN"),
        )
        self._store.set_state(
            "last_safe_close_physical_state_basis",
            str(
                payload.get("physicalDoorStateBasis")
                or "NOT_OBSERVABLE"
            ),
        )

    def _on_delivery_door_command_result(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("mcuCommandUid")
            != ctx.get("authorize_mcu_command_uid")
        ):
            raise ValueError("door command result does not match delivery")
        door_command = payload.get("command")
        phase = ctx.get("phase")
        if payload.get("roundIndex") != ctx.get("round_index"):
            raise ValueError("door command result has an unexpected round")
        if door_command == "OPEN":
            if phase not in {
                "AUTHORIZING_FIRST_OPEN",
                "WAITING_OPEN_COMMAND_RESULT",
                "FIRST_OPEN_RESULT_UNKNOWN",
                "CONTINUING",
            }:
                raise ValueError("door open result is out of sequence")
        elif door_command == "CLOSE":
            if phase != "DELIVERY_WINDOW":
                raise ValueError("door close result is out of sequence")
        else:
            raise ValueError("door command result has an invalid command")
        output_status = payload.get("outputStatus")
        output_executed = (
            door_command == "OPEN"
            and output_status == "COMMAND_DISPATCHED"
        ) or (
            door_command == "CLOSE"
            and output_status
            in {
                "COMMAND_DISPATCHED",
                "COALESCED_WITH_EXISTING_CLOSE",
            }
        )
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key="DELIVERY:AUTHORIZE_OPEN:0",
            expected_action_kind="OPEN_DELIVERY_DOOR",
            event_type="DELIVERY_DOOR_COMMAND_RESULT",
            payload=payload,
            outcome=(
                "EXECUTED" if output_executed
                # This is an identity-bound MCU result after the UART action
                # was armed.  A controller-side rejection resolves the action
                # safely, but it is not proof that the edge never dispatched
                # it and therefore must not use the PREPARED-only outcome.
                else "FAILED_SAFE"
            ),
            allow_followup_fact=True,
        )
        ctx["last_delivery_door_command"] = payload.get("command")
        ctx["last_delivery_door_output_status"] = payload.get(
            "outputStatus"
        )
        ctx["delivery_door_physical_state_basis"] = payload.get(
            "physicalDoorStateBasis"
        )
        if payload.get("command") == "OPEN":
            if output_status != "COMMAND_DISPATCHED":
                self._fail_active_job_at_safe_boundary(
                    context=ctx,
                    error_code="DELIVERY_OPEN_FAILED_SAFE",
                    evidence={
                        "eventType": "DELIVERY_OPEN_FAILED_SAFE",
                        "workUid": work_uid,
                        "mcuCommandUid": payload.get("mcuCommandUid"),
                        "roundIndex": payload.get("roundIndex"),
                        "outputStatus": output_status,
                        "faultCode": payload.get("faultCode"),
                    },
                )
                return
            ctx["phase"] = "DELIVERY_WINDOW"
        elif payload.get("command") == "CLOSE":
            if not output_executed:
                # The earlier OPEN output may still be held and the device
                # cannot observe the mechanical door position.  Preserve the
                # active work/permit as a recovery lock instead of admitting
                # another job or accepting a post-close weight as if closure
                # had succeeded.
                ctx["phase"] = "DELIVERY_CLOSE_RECOVERY_REQUIRED"
                self._store.update_work_context(work_uid, ctx)
                return
            ctx["phase"] = "WAITING_POSTCLOSE_WEIGHT"
        self._store.update_work_context(work_uid, ctx)

    def _on_preopen_weight(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("start_mcu_command_uid")
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("roundIndex") != ctx.get("round_index")
        ):
            raise ValueError("preopen measurement does not match active delivery")
        measurement_uid = payload.get("measurementUid", "")
        reported_weight = _reported_weight(payload)
        weight_grams = _delivery_usable_weight(payload)
        status = payload.get("measurementStatus", "STABLE")
        ctx["phase"] = "PREOPEN_MEASURED"
        ctx["first_reported_weight_grams"] = reported_weight
        ctx["first_measurement_status"] = status
        ctx["first_measurement_uid"] = measurement_uid
        ctx["first_measurement"] = dict(payload)
        if weight_grams is not None:
            ctx["first_weight_grams"] = weight_grams
        self._store.update_work_context(work_uid, ctx)
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key="DELIVERY:START:0",
            expected_action_kind="START_DELIVERY_SESSION",
            event_type="WORK_PREOPEN_WEIGHT_READY",
            payload=payload,
        )
        start_command_uid = ctx.get("start_command_uid")
        if start_command_uid:
            self._store.complete_command(
                start_command_uid,
                {
                    "measurementUid": measurement_uid,
                    "measurementStatus": status,
                },
            )
        if weight_grams is None:
            ctx["phase"] = "PREOPEN_WEIGHT_BLOCKED"
            self._store.update_work_context(work_uid, ctx)
            return
        if not self._capture_photos(
            "capture_open_photos",
            work_uid,
        ):
            ctx["phase"] = "PREOPEN_PHOTO_BLOCKED"
            self._store.update_work_context(work_uid, ctx)
            return
        authorize_uid = ctx.get("authorize_mcu_command_uid") or _new_uid()
        ctx["authorize_mcu_command_uid"] = authorize_uid
        ctx["phase"] = "AUTHORIZING_FIRST_OPEN"
        self._store.update_work_context(work_uid, ctx)
        try:
            remaining_authorization_ms = _remaining_until(ctx["expires_at"])
        except ValueError:
            self._fail_active_job_at_safe_boundary(
                context=ctx,
                error_code="COMMAND_EXPIRED",
                evidence={
                    "eventType": "DELIVERY_OPEN_NOT_EXECUTED",
                    "workUid": work_uid,
                    "preopenMeasurementUid": measurement_uid,
                    "reason": "COMMAND_EXPIRED",
                },
            )
            return
        result = self._send_physical_command(
            ctx,
            "AUTHORIZE_DELIVERY_FIRST_OPEN",
            {
                "sessionUid": work_uid,
                "portNo": ctx["port_no"],
                "firstPreOpenMeasurementUid": measurement_uid,
                "parentStartCommandUid": ctx["start_mcu_command_uid"],
                "remainingStartAuthorizationMs": remaining_authorization_ms,
            },
            mcu_command_uid=authorize_uid,
            action_key="DELIVERY:AUTHORIZE_OPEN:0",
            action_kind="OPEN_DELIVERY_DOOR",
            not_after=ctx["expires_at"],
        )
        if result.get("physicalEffect") == "NOT_EXECUTED":
            self._fail_active_job_at_safe_boundary(
                context=ctx,
                error_code=str(
                    result.get("error") or "DELIVERY_OPEN_NOT_EXECUTED"
                ),
                evidence={
                    "eventType": "DELIVERY_OPEN_NOT_EXECUTED",
                    "workUid": work_uid,
                    "preopenMeasurementUid": measurement_uid,
                    "reason": str(
                        result.get("error")
                        or "DELIVERY_OPEN_NOT_EXECUTED"
                    ),
                },
            )
            return
        ctx["phase"] = (
            "WAITING_OPEN_COMMAND_RESULT"
            if result["acked"]
            else "FIRST_OPEN_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)
        logger.info("preopen weight: %d g", weight_grams or 0)

    def _on_postclose_weight(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("mcuCommandUid")
            != ctx.get("authorize_mcu_command_uid")
            or payload.get("roundIndex") != ctx.get("round_index")
        ):
            raise ValueError("postclose measurement does not match delivery")
        reported_weight = _reported_weight(payload)
        weight_grams = _delivery_usable_weight(payload)
        status = payload.get("measurementStatus", "STABLE")
        round_idx = payload.get("roundIndex", 0)
        measurement_uid = payload.get("measurementUid", "")
        ctx["round_index"] = round_idx
        ctx[f"round_{round_idx}_reported_weight_grams"] = reported_weight
        ctx[f"round_{round_idx}_measurement_status"] = status
        ctx[f"round_{round_idx}_measurement_uid"] = measurement_uid
        ctx["final_measurement_uid"] = measurement_uid
        if weight_grams is not None:
            prev_key = "first_weight_grams" if round_idx == 1 else f"round_{round_idx-1}_open_weight"
            prev_weight = ctx.get(prev_key)
            threshold = ctx.get("negative_weight_threshold_grams", 500)
            if prev_weight is not None and (weight_grams - prev_weight) < -threshold:
                ctx["negative_weight_anomaly"] = True
            ctx[f"round_{round_idx}_close_weight"] = weight_grams
            ctx["final_weight_grams"] = weight_grams
        else:
            ctx["final_weight_grams"] = None
        ctx["final_measurement"] = dict(payload)
        ctx["phase"] = "WAITING_SELECTION"
        self._store.update_work_context(work_uid, ctx)
        logger.info("postclose weight round=%d wt=%d", round_idx, weight_grams or 0)

    def _on_delivery_selection(
        self,
        ctx,
        payload,
        work_uid,
        *,
        mcu_receive_generation: int = 0,
    ):
        round_index = payload.get("roundIndex")
        measurement_uid = payload.get("postCloseMeasurementUid")
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or round_index != ctx.get("round_index")
            or measurement_uid
            != ctx.get(f"round_{round_index}_measurement_uid")
        ):
            raise ValueError("selection does not bind persisted postclose weight")
        selection = payload.get("selection", "END")
        session_uid = ctx.get("session_uid", work_uid)
        if selection in ("END", "WINDOW_EXPIRED"):
            ctx["phase"] = "FINALIZING"
            self._store.update_work_context(work_uid, ctx)
            self._capture_photos(
                "capture_close_photos",
                work_uid,
            )
            first_wt = ctx.get("first_weight_grams")
            final_wt = ctx.get("final_weight_grams")
            net = None
            if first_wt is not None and final_wt is not None:
                net = final_wt - first_wt
            event_payload = {
                "sessionUid": session_uid,
                "portNo": ctx["port_no"],
                "firstPreOpenMeasurement": _measurement_fact(
                    ctx.get("first_measurement")
                ),
                "finalPostCloseMeasurement": _measurement_fact(
                    ctx.get("final_measurement")
                ),
                "deliveryNetWeightGrams": net,
                "finalDoorCommand": {
                    "command": ctx.get(
                        "last_delivery_door_command",
                        "CLOSE",
                    ),
                    "outputStatus": ctx.get(
                        "last_delivery_door_output_status",
                        "COMMAND_DISPATCHED",
                    ),
                    "physicalStateBasis": ctx.get(
                        "delivery_door_physical_state_basis",
                        "NOT_OBSERVABLE",
                    ),
                },
                "completionReason": (
                    "USER_ENDED"
                    if selection == "END"
                    else "SELECTION_WINDOW_EXPIRED"
                ),
                "manualReviewRequired": False,
                "negativeWeightAnomaly": ctx.get(
                    "negative_weight_anomaly",
                    False,
                ),
                "frozenConfig": _frozen_config(ctx["config"]),
                "unitPriceTenThousandths": ctx[
                    "unit_price_ten_thousandths"
                ],
                "photos": self._completion_photo_facts(
                    work_uid,
                    "DELIVERY_SESSION",
                    (
                        "BEFORE_INNER",
                        "BEFORE_OUTER",
                        "AFTER_INNER",
                        "AFTER_OUTER",
                    ),
                ),
            }
            completion_evidence = {
                "eventType": "DELIVERY_COMPLETE",
                "workUid": work_uid,
                "startCommandUid": ctx.get("start_command_uid"),
                "finalMeasurementUid": ctx.get("final_measurement_uid"),
            }
            self._prepare_job_safety_completion(
                ctx,
                outcome="SUCCEEDED",
                evidence=completion_evidence,
            )
            ctx["phase"] = "COMPLETING"
            self._create_reliable_event(
                event_type="DELIVERY_COMPLETE",
                target_type="DELIVERY_SESSION",
                work_uid=work_uid,
                command_uid=ctx.get("start_command_uid"),
                device_name=ctx.get("device_name"),
                payload=event_payload,
                work_state_update={
                    "state": "COMPLETING",
                    "context": ctx,
                },
                fullness_transition=self._fullness_transition(
                    port_no=ctx["port_no"],
                    bag_uid=ctx.get("bag_uid"),
                    source_work_type="DELIVERY_SESSION",
                    source_work_uid=session_uid,
                    device_name=(
                        ctx.get("device_name") or "UNKNOWN_DEVICE"
                    ),
                    measurement=ctx.get("final_measurement") or {},
                    infrared_blocked=ctx.get("infrared_blocked"),
                    fixed_frame=False,
                ),
                mcu_receive_generation=mcu_receive_generation,
                mcu_boot_id=payload.get("mcuBootId"),
                mcu_event_sequence=payload.get("mcuEventSequence"),
            )
            logger.info("delivery complete: %s net=%d", session_uid, net or 0)
        else:
            round_idx = ctx.get("round_index", 0) + 1
            ctx["round_index"] = round_idx
            ctx[f"round_{round_idx}_open_weight"] = ctx.get(f"round_{round_idx-1}_close_weight")
            ctx["phase"] = "CONTINUING"
            self._store.update_work_context(work_uid, ctx)
            logger.info("continue delivery round=%d", round_idx)

    def finalize_delivery(self, work_uid):
        slot = self._store.get_work_slot()
        context = (
            slot["context"]
            if slot and slot["work_uid"] == work_uid
            else None
        )
        if context is not None:
            completed = self._complete_job_safety(
                context,
                outcome="SUCCEEDED",
                evidence={
                    "eventType": "DELIVERY_COMPLETE",
                    "workUid": work_uid,
                    "startCommandUid": context.get("start_command_uid"),
                    "finalMeasurementUid": context.get(
                        "final_measurement_uid"
                    ),
                },
            )
            if completed:
                self._store.release_work_slot(work_uid)
        else:
            self._store.release_work_slot(work_uid)
        logger.info("delivery session ended: %s", work_uid)

    def start_clean_operation(self, operation_uid, port_no, old_bag_qr, new_bag_qr):
        if self._job_safety.enabled:
            return {"success": False, "reason": "JOB_PERMIT_REQUIRED"}
        ctx = {"operation_uid": operation_uid, "port_no": port_no,
               "old_bag_qr": old_bag_qr, "new_bag_qr": new_bag_qr,
               "phase": "STARTED", "action_sequence": 0,
               "preunlock_weight_grams": None, "preunlock_measurement_uid": None,
               "final_weight_grams": None, "final_measurement_uid": None,
               "completion_confirmed": False}
        ok = self._store.acquire_work_slot(WORK_TYPE_CLEAN, operation_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("clean operation started: %s port=%d", operation_uid, port_no)
        return {"success": True, "operation_uid": operation_uid}

    def _on_clean_preunlock_weight(self, ctx, payload, work_uid):
        # The event may have been queued with a context snapshot taken before
        # an operator's END command. Reload the authoritative slot while the
        # in-process decision lock is held so that no stale context can erase
        # a committed cancellation intent.
        with self._clean_unlock_decision_lock:
            current_slot = self._store.get_work_slot()
            if (
                not current_slot
                or current_slot["work_type"] != WORK_TYPE_CLEAN
                or current_slot["work_uid"] != work_uid
            ):
                return
            ctx = current_slot["context"]
            if (
                payload.get("operationUid") != work_uid
                or payload.get("mcuCommandUid")
                != ctx.get("start_mcu_command_uid")
                or payload.get("portNo") != ctx.get("port_no")
            ):
                raise ValueError(
                    "preunlock measurement does not match active clean"
                )
            # This identity-bound MCU fact proves that the START action reached
            # the controller. Resolve the permanent action ledger before
            # applying the business operation-window policy; otherwise a fact
            # arriving just after expiry would be consumed while leaving the
            # job permanently blocked by an unreconciled action.
            self._confirm_action_from_mcu_event(
                ctx,
                expected_action_key="CLEAN:START:0",
                expected_action_kind="START_CLEAN_OPERATION",
                event_type="WORK_PREUNLOCK_WEIGHT_READY",
                payload=payload,
            )
            end_intent = ctx.get("end_before_unlock")
            if isinstance(end_intent, dict):
                # END was durably requested before this late START result.
                # Keep the measurement as audit evidence, but never continue
                # into the photo/unlock branch after the operator stopped it.
                measurement_uid = payload.get("measurementUid", "")
                ctx["preunlock_measurement_uid"] = measurement_uid
                ctx["preunlock_weight_grams"] = _reported_weight(payload)
                ctx["preunlock_measurement"] = dict(payload)
                ctx["phase"] = (
                    "END_BEFORE_UNLOCK_CONFIRMED"
                    if end_intent.get("state") == "ACKED"
                    else "END_BEFORE_UNLOCK_RESULT_UNKNOWN"
                )
                self._store.update_work_context(work_uid, ctx)
                if end_intent.get("state") == "ACKED":
                    completion_evidence = {
                        "operationUid": work_uid,
                        "terminalAction": "END_CLEAN_BEFORE_UNLOCK",
                        "reason": end_intent["reason"],
                    }
                    completed = self._complete_job_safety(
                        ctx,
                        outcome="CANCELLED",
                        evidence=completion_evidence,
                    )
                    if completed:
                        self._store.release_work_slot(work_uid)
                return
            if (
                self._remaining_clean_window_or_recovery(ctx, work_uid)
                is None
            ):
                return
            measurement_uid = payload.get("measurementUid", "")
            ctx["preunlock_measurement_uid"] = measurement_uid
            ctx["preunlock_weight_grams"] = _reported_weight(payload)
            ctx["preunlock_measurement"] = dict(payload)
            ctx["phase"] = "PREUNLOCK_MEASURED"
            self._store.update_work_context(work_uid, ctx)
            start_command_uid = ctx.get("start_command_uid")
            if start_command_uid:
                self._store.complete_command(
                    start_command_uid,
                    {
                        "measurementUid": measurement_uid,
                        "measurementStatus": payload.get(
                            "measurementStatus"
                        ),
                    },
                )
            applied = self._store.get_latest_applied_configuration()
            if not applied:
                raise ValueError("no applied configuration for clean unlock")

        # Camera I/O may be slow. It must not prevent an operator from
        # committing END while no unlock has yet been claimed.
        if not self._capture_photos(
            "capture_clean_open_photos",
            work_uid,
        ):
            with self._clean_unlock_decision_lock:
                current_slot = self._store.get_work_slot()
                if (
                    not current_slot
                    or current_slot["work_type"] != WORK_TYPE_CLEAN
                    or current_slot["work_uid"] != work_uid
                ):
                    return
                current = current_slot["context"]
                if (
                    current.get("phase") == "PREUNLOCK_MEASURED"
                    and not isinstance(
                        current.get("end_before_unlock"), dict
                    )
                    and current.get("preunlock_measurement_uid")
                    == measurement_uid
                ):
                    current["phase"] = "PREUNLOCK_PHOTO_BLOCKED"
                    self._store.update_work_context(work_uid, current)
            return

        with self._clean_unlock_decision_lock:
            current_slot = self._store.get_work_slot()
            if (
                not current_slot
                or current_slot["work_type"] != WORK_TYPE_CLEAN
                or current_slot["work_uid"] != work_uid
            ):
                return
            current = current_slot["context"]
            if isinstance(current.get("end_before_unlock"), dict):
                return
            remaining_window_ms = self._remaining_clean_window_or_recovery(
                current, work_uid
            )
            if remaining_window_ms is None:
                return
            unlock_uid = (
                current.get("unlock_mcu_command_uid") or _new_uid()
            )
            unlock_action_key = current.get("unlock_action_key") or (
                "CLEAN:UNLOCK:"
                f"{current['recovery_generation']}:"
                f"{current['action_sequence']}"
            )
            claimed_context = self._store.claim_clean_unlock_dispatch(
                work_uid=work_uid,
                start_mcu_command_uid=current["start_mcu_command_uid"],
                preunlock_measurement_uid=measurement_uid,
                recovery_generation=current["recovery_generation"],
                action_sequence=current["action_sequence"],
                unlock_mcu_command_uid=unlock_uid,
                unlock_action_key=unlock_action_key,
            )
            if claimed_context == "DEFERRED_BY_PENDING_END":
                raise CleanUnlockDecisionDeferred(
                    "clean unlock is waiting for a pending END command"
                )
            if claimed_context is None:
                # END_CLEAN_BEFORE_UNLOCK or another terminal/recovery
                # transition won while this handler was taking photos. Never
                # prepare an unlock from the earlier in-memory context.
                logger.info(
                    "clean unlock dispatch was superseded before claim: "
                    "work=%s",
                    work_uid,
                )
                return
            ctx = claimed_context
            # The SQLite claim may have waited behind OneNet ingress. Sample
            # the relative operation window again after that wait so the
            # deadline passed into PREPARE/ARM cannot regain elapsed time.
            operation_window_budget = self._clean_window_budget_or_recovery(
                ctx, work_uid
            )
            if operation_window_budget is None:
                return
            (
                remaining_window_ms,
                operation_dispatch_deadline,
            ) = operation_window_budget

        result = self._send_physical_command(
            ctx,
            "UNLOCK_CLEAN_DOOR",
            {
                "operationUid": work_uid,
                "portNo": ctx["port_no"],
                "cleanActionSequence": ctx["action_sequence"],
                "recoveryGeneration": ctx["recovery_generation"],
                "unlockPulseMs": applied["payload"]["deviceConfig"][
                    "cleanSolenoidPulseMs"
                ],
                "remainingOperationWindowMs": remaining_window_ms,
                "parentCommandUid": ctx["start_mcu_command_uid"],
            },
            mcu_command_uid=unlock_uid,
            action_key=ctx["unlock_action_key"],
            action_kind="UNLOCK_CLEAN_DOOR",
            not_after=ctx["operation_deadline"],
            dispatch_deadline_monotonic_cap=(
                operation_dispatch_deadline
            ),
        )
        if result.get("physicalEffect") == "NOT_EXECUTED":
            self._fail_active_job_at_safe_boundary(
                context=ctx,
                error_code=str(
                    result.get("error") or "CLEAN_UNLOCK_NOT_EXECUTED"
                ),
                evidence={
                    "eventType": "CLEAN_UNLOCK_NOT_EXECUTED",
                    "workUid": work_uid,
                    "preunlockMeasurementUid": measurement_uid,
                    "reason": str(
                        result.get("error")
                        or "CLEAN_UNLOCK_NOT_EXECUTED"
                    ),
                },
            )
            return
        ctx["phase"] = (
            "WAITING_LOCK_OUTPUT"
            if result["acked"]
            else "UNLOCK_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_unlock_requested(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
        ):
            raise ValueError("unlock request does not match active clean")
        action_sequence = payload.get("cleanActionSequence")
        exact_inflight_retry = (
            isinstance(action_sequence, int)
            and action_sequence == ctx.get("action_sequence", 0)
            and ctx.get("phase") == "REUNLOCKING"
            and ctx.get("unlock_request_mcu_boot_id")
            == payload.get("mcuBootId")
            and ctx.get("unlock_request_mcu_event_sequence")
            == payload.get("mcuEventSequence")
            and isinstance(ctx.get("unlock_mcu_command_uid"), str)
            and isinstance(ctx.get("unlock_action_key"), str)
        )
        if not isinstance(action_sequence, int) or (
            action_sequence <= ctx.get("action_sequence", 0)
            and not exact_inflight_retry
        ):
            raise ValueError("clean action sequence did not advance")
        remaining_window_ms = self._remaining_clean_window_or_recovery(
            ctx, work_uid
        )
        if remaining_window_ms is None:
            return
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            raise ValueError("no applied configuration for clean unlock")
        if exact_inflight_retry:
            unlock_uid = ctx["unlock_mcu_command_uid"]
            unlock_action_key = ctx["unlock_action_key"]
        else:
            unlock_uid = _new_uid()
            unlock_action_key = (
                "CLEAN:UNLOCK:"
                f"{ctx['recovery_generation']}:"
                f"{action_sequence}"
            )
            ctx["action_sequence"] = action_sequence
            ctx["final_weight_grams"] = None
            ctx["final_measurement_uid"] = None
            ctx["final_measurement"] = None
            ctx["unlock_mcu_command_uid"] = unlock_uid
            ctx["unlock_action_key"] = unlock_action_key
            ctx["unlock_request_mcu_boot_id"] = payload.get("mcuBootId")
            ctx["unlock_request_mcu_event_sequence"] = payload.get(
                "mcuEventSequence"
            )
            ctx["phase"] = "REUNLOCKING"
            self._store.update_work_context(work_uid, ctx)
        operation_window_budget = self._clean_window_budget_or_recovery(
            ctx, work_uid
        )
        if operation_window_budget is None:
            return
        (
            remaining_window_ms,
            operation_dispatch_deadline,
        ) = operation_window_budget
        parent_uid = (
            ctx.get("resume_mcu_command_uid")
            or ctx["start_mcu_command_uid"]
        )
        result = self._send_physical_command(
            ctx,
            "UNLOCK_CLEAN_DOOR",
            {
                "operationUid": work_uid,
                "portNo": ctx["port_no"],
                "cleanActionSequence": action_sequence,
                "recoveryGeneration": ctx["recovery_generation"],
                "unlockPulseMs": applied["payload"]["deviceConfig"][
                    "cleanSolenoidPulseMs"
                ],
                "remainingOperationWindowMs": remaining_window_ms,
                "parentCommandUid": parent_uid,
            },
            mcu_command_uid=unlock_uid,
            action_key=unlock_action_key,
            action_kind="UNLOCK_CLEAN_DOOR",
            not_after=ctx["operation_deadline"],
            dispatch_deadline_monotonic_cap=(
                operation_dispatch_deadline
            ),
        )
        if result.get("physicalEffect") == "NOT_EXECUTED":
            ctx["phase"] = "CLEAN_RECOVERY_REQUIRED"
            ctx["last_recovery_reason"] = str(
                result.get("error") or "CLEAN_REUNLOCK_NOT_EXECUTED"
            )
            self._store.update_work_context(work_uid, ctx)
            return
        ctx["phase"] = (
            "WAITING_LOCK_OUTPUT"
            if result["acked"]
            else "UNLOCK_RESULT_UNKNOWN"
        )
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_finish_requested(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
        ):
            raise ValueError("finish request does not match active clean")
        action_sequence = payload.get("cleanActionSequence")
        if (
            not isinstance(action_sequence, int)
            or action_sequence <= ctx.get("action_sequence", 0)
        ):
            raise ValueError("clean action sequence did not advance")
        ctx["action_sequence"] = action_sequence
        ctx["final_weight_grams"] = None
        ctx["final_measurement_uid"] = None
        ctx["final_measurement"] = None
        ctx["phase"] = "WAITING_FINAL_WEIGHT"
        self._store.update_work_context(work_uid, ctx)

    def _on_clean_lock_power_changed(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("mcuCommandUid")
            != ctx.get("unlock_mcu_command_uid")
        ):
            raise ValueError("lock event does not match active clean")
        if (
            ctx.get("clean_lock_power_state") == "DEENERGIZED"
            and payload.get("lockPowerState") == "ENERGIZED"
        ):
            raise ValueError("clean lock power event regressed")
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key=ctx.get("unlock_action_key")
            or (
                "CLEAN:UNLOCK:"
                f"{ctx.get('recovery_generation', 0)}:"
                f"{ctx.get('action_sequence', 0)}"
            ),
            expected_action_kind="UNLOCK_CLEAN_DOOR",
            event_type="CLEAN_LOCK_POWER_CHANGED",
            payload=payload,
            outcome=(
                "EXECUTED"
                if payload.get("lockPowerState")
                in {"ENERGIZED", "DEENERGIZED"}
                else "FAILED_SAFE"
            ),
            allow_followup_fact=True,
        )
        ctx["clean_lock_power_state"] = payload.get("lockPowerState")
        ctx["clean_solenoid_health"] = payload.get("solenoidHealth")
        if payload.get("lockPowerState") == "DEENERGIZED":
            ctx["phase"] = "ACTIVE"
        self._store.update_work_context(work_uid, ctx)

    def authorize_clean_unlock(self, operation_uid):
        if self._job_safety.enabled:
            return {"success": False, "reason": "LEGACY_ENTRY_DISABLED"}
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != operation_uid:
            return {"success": False, "reason": "OPERATION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        if not ctx.get("preunlock_measurement_uid"):
            ctx["preunlock_measurement_uid"] = _new_uid()
            self._store.update_work_context(operation_uid, ctx)
        ctx["action_sequence"] = ctx.get("action_sequence", 0) + 1
        action_seq = ctx["action_sequence"]
        ctx["phase"] = "UNLOCKING"
        self._store.update_work_context(operation_uid, ctx)
        ledger_action_uid = _new_uid()
        self._prepare_physical_action(
            ctx,
            message_name="UNLOCK_CLEAN_DOOR_LEGACY",
            values={
                "operationUid": operation_uid,
                "portNo": port_no,
                "actionSequence": action_seq,
                "preunlockMeasurementUid": ctx[
                    "preunlock_measurement_uid"
                ],
            },
            mcu_command_uid=ledger_action_uid,
            action_key=(
                "CLEAN:LEGACY_UNLOCK:"
                f"{ctx.get('recovery_generation', 0)}:{action_seq}"
            ),
            action_kind="UNLOCK_CLEAN_DOOR",
            dispatch_attempt_token=None,
        )
        result = self._uart.send_unlock_clean_door(
            operation_uid=operation_uid, port_no=port_no,
            action_sequence=action_seq,
            preunlock_measurement_uid=ctx["preunlock_measurement_uid"])
        if result["acked"]:
            ctx["phase"] = "ACTIVE"
            self._store.update_work_context(operation_uid, ctx)
            return {"success": True, "operation_uid": operation_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def _on_clean_final_weight(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("cleanActionSequence")
            != ctx.get("action_sequence")
        ):
            raise ValueError("final measurement does not match active clean")
        weight_grams = _reported_weight(payload)
        ctx["final_weight_grams"] = weight_grams
        ctx["final_measurement_status"] = payload.get("measurementStatus")
        ctx["final_weight_value_kind"] = payload.get("weightValueKind")
        ctx["final_measurement_uid"] = payload.get("measurementUid", "")
        ctx["final_measurement"] = dict(payload)
        ctx["phase"] = "FINAL_WEIGHT_READY"
        self._store.update_work_context(work_uid, ctx)
        logger.info("clean final weight: %d g", weight_grams or 0)

    def _on_clean_completion(
        self,
        ctx,
        payload,
        work_uid,
        *,
        mcu_receive_generation: int = 0,
    ):
        if (
            payload.get("operationUid") != work_uid
            or payload.get("portNo") != ctx.get("port_no")
            or payload.get("cleanActionSequence")
            != ctx.get("action_sequence")
            or payload.get("finalMeasurementUid")
            != ctx.get("final_measurement_uid")
            or payload.get("lockPowerState") != "DEENERGIZED"
            or payload.get("cleanDoorStateBasis")
            != "CLEANER_CONFIRMATION"
            or payload.get("cleanerPhysicalCloseConfirmed") is not True
        ):
            raise ValueError("clean completion lacks matching manual confirmation")
        ctx["phase"] = "COMPLETION_CONFIRMED"
        ctx["completion_confirmed"] = True
        ctx["clean_lock_power_state"] = payload.get("lockPowerState")
        ctx["clean_solenoid_health"] = payload.get("solenoidHealth")
        ctx["clean_door_state_basis"] = payload.get(
            "cleanDoorStateBasis"
        )
        ctx["cleaner_physical_close_confirmed"] = True
        self._store.update_work_context(work_uid, ctx)
        self._capture_photos(
            "capture_clean_close_photos",
            work_uid,
        )
        final_usable = _delivery_usable_weight(
            ctx.get("final_measurement") or {}
        )
        preunlock_weight = ctx.get("preunlock_weight_grams")
        removed_weight = (
            preunlock_weight - final_usable
            if preunlock_weight is not None
            and final_usable is not None
            else None
        )
        event_payload = {
            "operationUid": ctx["operation_uid"],
            "portNo": ctx["port_no"],
            "oldBagUid": ctx.get("old_bag_uid"),
            "newBagUid": ctx.get("new_bag_uid"),
            "preUnlockMeasurement": _measurement_fact(
                ctx.get("preunlock_measurement")
            ),
            "cleanerConfirmedFinalMeasurement": _measurement_fact(
                ctx.get("final_measurement")
            ),
            "removedNetWeightGrams": removed_weight,
            "newBaselineWeightGrams": final_usable,
            "cleanerCompletionConfirmed": True,
            "cleanActionSequence": ctx["action_sequence"],
            "cleanLockAndManualDoorConfirmation": {
                "lockPowerState": payload.get("lockPowerState"),
                "solenoidHealth": payload.get("solenoidHealth"),
                "physicalDoorStateBasis": payload.get(
                    "cleanDoorStateBasis"
                ),
                "cleanerPhysicalCloseConfirmed": True,
            },
            "frozenConfig": _frozen_config(ctx["config"]),
            "photos": self._completion_photo_facts(
                work_uid,
                "CLEAN_OPERATION",
                (
                    "FIRST_OPEN_INNER",
                    "FIRST_OPEN_OUTER",
                    "FINAL_CLOSE_INNER",
                    "FINAL_CLOSE_OUTER",
                ),
            ),
        }
        completion_evidence = {
            "eventType": "CLEAN_COMPLETE",
            "workUid": work_uid,
            "startCommandUid": ctx.get("start_command_uid"),
            "finalMeasurementUid": ctx.get("final_measurement_uid"),
        }
        self._prepare_job_safety_completion(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        ctx["phase"] = "COMPLETING"
        self._create_reliable_event(
            event_type="CLEAN_COMPLETE",
            target_type="CLEAN_OPERATION",
            work_uid=work_uid,
            command_uid=ctx.get("start_command_uid"),
            device_name=ctx.get("device_name"),
            payload=event_payload,
            work_state_update={
                "state": "COMPLETING",
                "context": ctx,
            },
            fullness_transition=self._fullness_transition(
                port_no=ctx["port_no"],
                bag_uid=ctx["new_bag_uid"],
                source_work_type="CLEAN_OPERATION",
                source_work_uid=ctx["operation_uid"],
                device_name=(
                    ctx.get("device_name") or "UNKNOWN_DEVICE"
                ),
                measurement=ctx.get("final_measurement") or {},
                infrared_blocked=ctx.get("infrared_blocked"),
                fixed_frame=False,
                baseline_weight_grams=final_usable,
                reset_for_new_bag=True,
            ),
            mcu_receive_generation=mcu_receive_generation,
            mcu_boot_id=payload.get("mcuBootId"),
            mcu_event_sequence=payload.get("mcuEventSequence"),
        )
        logger.info("clean complete: %s", ctx["operation_uid"])

    def finalize_clean(self, work_uid):
        slot = self._store.get_work_slot()
        context = (
            slot["context"]
            if slot and slot["work_uid"] == work_uid
            else None
        )
        if context is not None:
            completed = self._complete_job_safety(
                context,
                outcome="SUCCEEDED",
                evidence={
                    "eventType": "CLEAN_COMPLETE",
                    "workUid": work_uid,
                    "startCommandUid": context.get("start_command_uid"),
                    "finalMeasurementUid": context.get(
                        "final_measurement_uid"
                    ),
                },
            )
            if completed:
                self._store.release_work_slot(work_uid)
        else:
            self._store.release_work_slot(work_uid)
        logger.info("clean operation ended: %s", work_uid)

    def _on_fullness_sample_result(
        self,
        ctx,
        payload,
        work_uid,
        *,
        mcu_receive_generation: int = 0,
    ):
        if (
            payload.get("detectionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
            or payload.get("portNo") != ctx.get("port_no")
        ):
            raise ValueError("fullness result does not match active detection")
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key="FULLNESS:SAMPLE:0",
            expected_action_kind="SAMPLE_FULLNESS",
            event_type="FULLNESS_SAMPLE_RESULT",
            payload=payload,
        )
        distance = (
            payload.get("representativeDistanceMm")
            if payload.get("representativeDistancePresent")
            else None
        )
        event_payload = {
            "detectionUid": work_uid,
            "portNo": ctx["port_no"],
            "sampleRole": payload.get("sampleRole"),
            "triggerType": ctx["payload"].get("triggerType"),
            "fullnessMode": ctx["payload"].get("fullnessMode"),
            "fullnessSensorKind": payload.get("fullnessSensorKind"),
            "fullnessSensorValue": payload.get("fullnessSensorValue"),
            "fullnessSampleBasis": payload.get("fullnessSampleBasis"),
            "representativeDistanceMm": distance,
            "requestedSampleCount": payload.get("requestedSampleCount"),
            "validSampleCount": payload.get("validSampleCount"),
            "totalWeightMeasurement": _measurement_fact(payload),
            "frozenConfig": _frozen_config(ctx["payload"]["config"]),
        }
        completion_evidence = {
            "eventType": "FULLNESS_SAMPLE_COMPLETE",
            "workUid": work_uid,
            "mcuBootId": payload.get("mcuBootId"),
            "mcuEventSequence": payload.get("mcuEventSequence"),
        }
        self._prepare_job_safety_completion(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        self._store.complete_command(
            ctx["command_uid"],
            {
                "fullnessSensorValue": payload.get(
                    "fullnessSensorValue"
                ),
                "fullnessSampleBasis": payload.get(
                    "fullnessSampleBasis"
                ),
            },
        )
        self._create_reliable_event(
            event_type="FULLNESS_SAMPLE_COMPLETE",
            target_type="FULLNESS_DETECTION",
            command_uid=ctx["command_uid"],
            device_name=ctx.get("device_name"),
            payload=event_payload,
            work_uid=work_uid,
            work_state_update={"state": "COMPLETED", "context": ctx},
            mcu_receive_generation=mcu_receive_generation,
            mcu_boot_id=payload.get("mcuBootId"),
            mcu_event_sequence=payload.get("mcuEventSequence"),
        )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)

    def _on_baseline_measurement_result(
        self,
        ctx,
        payload,
        work_uid,
        *,
        mcu_receive_generation: int = 0,
    ):
        if (
            payload.get("measurementUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
            or payload.get("portNo") != ctx.get("port_no")
        ):
            raise ValueError("baseline result does not match active measurement")
        self._confirm_action_from_mcu_event(
            ctx,
            expected_action_key="BASELINE:MEASURE:0",
            expected_action_kind="MEASURE_EMPTY_BAG_BASELINE",
            event_type="BASELINE_MEASUREMENT_RESULT",
            payload=payload,
        )
        event_payload = {
            "measurementUid": work_uid,
            "portNo": ctx["port_no"],
            "bagUid": ctx["bag_uid"],
            "emptyBagConfirmed": ctx["empty_bag_confirmed"],
            "totalWeightMeasurement": _measurement_fact(payload),
            "frozenConfig": _frozen_config(ctx["config"]),
        }
        completion_evidence = {
            "eventType": "BASELINE_MEASUREMENT_COMPLETE",
            "workUid": work_uid,
            "mcuBootId": payload.get("mcuBootId"),
            "mcuEventSequence": payload.get("mcuEventSequence"),
        }
        self._prepare_job_safety_completion(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        self._store.complete_command(
            ctx["command_uid"],
            {
                "measurementStatus": payload.get("measurementStatus"),
                "weightValuePresent": payload.get("weightValuePresent"),
            },
        )
        self._create_reliable_event(
            event_type="BASELINE_MEASUREMENT_COMPLETE",
            target_type="BASELINE_MEASUREMENT",
            command_uid=ctx["command_uid"],
            device_name=ctx.get("device_name"),
            payload=event_payload,
            work_uid=work_uid,
            work_state_update={"state": "COMPLETED", "context": ctx},
            mcu_receive_generation=mcu_receive_generation,
            mcu_boot_id=payload.get("mcuBootId"),
            mcu_event_sequence=payload.get("mcuEventSequence"),
        )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)

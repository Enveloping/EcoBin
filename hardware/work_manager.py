"""work_manager.py -- delivery session and clean operation state machines."""
from __future__ import annotations
import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
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
def _new_uid() -> str:
    return str(_uuid.uuid4())


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


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


def _remaining_operation_window_ms(context: dict[str, Any]) -> int:
    """Deduct a local relative clean window without trusting wall time."""

    duration_ms = context.get("operation_window_ms")
    started_ms = context.get("operation_started_monotonic_ms")
    if (
        type(duration_ms) is int
        and type(started_ms) is int
        and duration_ms > 0
        and started_ms >= 0
    ):
        elapsed_ms = _monotonic_ms() - started_ms
        if elapsed_ms < 0:
            # CLOCK_MONOTONIC reset implies a device reboot.  In-flight work
            # is not recoverable across that boundary, so never grant a fresh
            # window from an incomparable reference.
            raise ValueError("operation window reference reset")
        remaining_ms = duration_ms - elapsed_ms
        if remaining_ms <= 0:
            raise ValueError("operation window expired")
        return min(remaining_ms, 4_294_967_295)

    # A pre-upgrade context has no trustworthy relative reference.  It may be
    # evaluated only while the wall clock is trusted; an untrusted device must
    # not silently replace an old 30-minute window with a new full window.
    deadline_reference = local_deadline_reference()
    if deadline_reference is None:
        raise ValueError("operation window reference unavailable")
    return _remaining_until_reference(
        context["operation_deadline"], deadline_reference
    )


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
        try:
            return _remaining_operation_window_ms(context)
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
    ) -> dict[str, Any]:
        """Arm the permanent ledger before the first UART write can occur."""

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
        action = self._arm_physical_action(
            context,
            message_name=message_name,
            values=values,
            mcu_command_uid=mcu_command_uid,
            action_key=action_key,
            action_kind=action_kind,
        )
        if action is not None and not_after is not None:
            try:
                _remaining_until(not_after)
            except ValueError:
                self._confirm_physical_action_record(
                    context,
                    action_key=action_key,
                    record=context["job_safety"]["actions"][action_key],
                    outcome="NOT_EXECUTED",
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
        deadline_sender = getattr(
            self._uart,
            "send_command_before_deadline",
            None,
        )
        if (
            dispatch_deadline_monotonic is not None
            and callable(deadline_sender)
        ):
            result = deadline_sender(
                message_name,
                values,
                mcu_command_uid=mcu_command_uid,
                dispatch_deadline_monotonic=dispatch_deadline_monotonic,
            )
        else:
            result = self._uart.send_command(
                message_name,
                values,
                mcu_command_uid=mcu_command_uid,
            )
        known_not_sent = (
            not result.get("acked")
            and result.get("error")
            in {
                "UART_CLOSED",
                "UART_NOT_READY",
                "COMMAND_EXPIRED",
                "MCU_FEATURE_NOT_SUPPORTED",
            }
        )
        if action is not None and known_not_sent:
            # UartLink returns these two errors before its first serial write.
            # Freeze that exact no-effect fact so the permanent MAY record can
            # be closed immediately or retried after an updater outage.
            self._confirm_physical_action_record(
                context,
                action_key=action_key,
                record=context["job_safety"]["actions"][action_key],
                outcome="NOT_EXECUTED",
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

    def _arm_physical_action(
        self,
        context: dict[str, Any],
        *,
        message_name: str,
        values: dict[str, Any],
        mcu_command_uid: str,
        action_key: str,
        action_kind: str,
        persist_context: bool = True,
    ) -> PhysicalAction | None:
        """Persist one logical action and move it across the may-run fence."""

        safety = context.get("job_safety")
        if safety is None:
            if self._job_safety.enabled:
                raise JobSafetyError(
                    "JOB_PERMIT_MISSING",
                    "physical action has no permanent job permit",
                )
            return None

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
        if record is None:
            record = {
                "action_uid": mcu_command_uid,
                "arm_uid": mcu_command_uid,
                "receipt_uid": mcu_command_uid,
                "action_key": action_key,
                "action_kind": action_kind,
                "action_digest_sha256": digest,
                "authorization_result": "PENDING",
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
        elif record["action_digest_sha256"] != digest:
            raise JobSafetyError(
                "PHYSICAL_ACTION_CONFLICT",
                "persisted physical action content changed",
            )

        action = self._physical_action(record)
        try:
            self._job_safety.authorize_physical_action(
                self._permit_from_safety_context(safety),
                action=action,
            )
        except Exception:
            record["authorization_result"] = "UNKNOWN"
            if persist_context:
                self._store.update_work_context(
                    safety["work_uid"],
                    context,
                )
            raise
        record["authorization_result"] = "ACCEPTED"
        if persist_context:
            # From this durable point onward a crash is conservatively treated
            # as possibly having reached UART.
            self._store.update_work_context(
                safety["work_uid"],
                context,
            )
        return action

    @staticmethod
    def _physical_action(record: dict[str, Any]) -> PhysicalAction:
        return PhysicalAction(
            action_uid=record["action_uid"],
            arm_uid=record["arm_uid"],
            receipt_uid=record["receipt_uid"],
            action_key=record["action_key"],
            action_kind=record["action_kind"],
            action_digest_sha256=record["action_digest_sha256"],
        )

    def _confirm_action_from_mcu_event(
        self,
        context: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        outcome: str = "EXECUTED",
    ) -> None:
        """Resolve one action from an already-durable, identity-bound MCU fact."""

        safety = context.get("job_safety")
        if safety is None:
            return
        action_uid = payload.get("mcuCommandUid")
        actions = safety.get("actions", {})
        match = next(
            (
                (key, record)
                for key, record in actions.items()
                if record.get("action_uid") == action_uid
            ),
            None,
        )
        if match is None:
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_MISMATCH",
                "MCU result does not identify a persisted physical action",
            )
        action_key, record = match
        confirmations = safety.setdefault("confirmations", {})
        existing = confirmations.get(action_key)
        if isinstance(existing, dict) and existing.get("confirmed") is True:
            return
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
            raise JobSafetyError(
                "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                "physical action confirmation evidence changed",
            )
        self._confirm_physical_action_record(
            context,
            action_key=action_key,
            record=record,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
        )

    def _confirm_physical_action_record(
        self,
        context: dict[str, Any],
        *,
        action_key: str,
        record: dict[str, Any],
        outcome: str,
        evidence_sha256: str,
    ) -> None:
        """Persist an exact receipt intent before its retry-safe local RPC."""

        safety = context["job_safety"]
        confirmations = safety.setdefault("confirmations", {})
        confirmation = confirmations.get(action_key)
        if isinstance(confirmation, dict):
            if (
                confirmation.get("outcome") != outcome
                or confirmation.get("evidence_sha256") != evidence_sha256
            ):
                raise JobSafetyError(
                    "PHYSICAL_ACTION_EVIDENCE_CONFLICT",
                    "physical action confirmation evidence changed",
                )
            if confirmation.get("confirmed") is True:
                return
        else:
            confirmation = {
                "outcome": outcome,
                "evidence_sha256": evidence_sha256,
                "confirmed": False,
            }
            confirmations[action_key] = confirmation
        self._store.update_work_context(safety["work_uid"], context)
        self._job_safety.confirm_physical_action(
            self._physical_action(record),
            outcome=outcome,
            evidence_sha256=evidence_sha256,
        )
        confirmation["confirmed"] = True
        self._store.update_work_context(safety["work_uid"], context)

    def _complete_job_safety(
        self,
        context: dict[str, Any],
        *,
        outcome: str,
        evidence: dict[str, Any],
        physical_outcome: str | None = None,
    ) -> bool:
        """Finish the permanent permit only after business facts are durable."""

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

        try:
            confirmations = safety.get("confirmations", {})
            for action_key, record in safety.get("actions", {}).items():
                confirmation = confirmations.get(action_key, {})
                self._job_safety.confirm_physical_action(
                    self._physical_action(record),
                    outcome=(
                        confirmation.get("outcome")
                        or pending.get("physical_outcome")
                        or (
                            "EXECUTED"
                            if pending["outcome"]
                            in {"SUCCEEDED", "CANCELLED"}
                            else "FAILED_SAFE"
                        )
                    ),
                    evidence_sha256=(
                        confirmation.get("evidence_sha256")
                        or pending["evidence_sha256"]
                    ),
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
                self._confirm_physical_action_record(
                    context,
                    action_key=action_key,
                    record=record,
                    outcome=confirmation["outcome"],
                    evidence_sha256=confirmation["evidence_sha256"],
                )
            except (JobSafetyError, KeyError, TypeError, ValueError):
                return progressed
            progressed = True
        return progressed

    def reconcile_pre_action_job_safety_failure(self) -> bool:
        """Close an interrupted job only when no physical action was armed."""

        slot = self._store.get_work_slot()
        if slot is None:
            return False
        context = slot["context"]
        safety = context.get("job_safety")
        if not isinstance(safety, dict):
            return False
        if safety.get("pending_completion"):
            return False
        actions = safety.get("actions", {})
        confirmations = safety.setdefault("confirmations", {})
        if actions:
            pending_authorizations = {
                key: record
                for key, record in actions.items()
                if record.get("authorization_result")
                in {"PENDING", "UNKNOWN"}
            }
            if pending_authorizations and not (
                self._reconcile_never_dispatched_actions(
                    context,
                    safety,
                )
            ):
                return False
            for action_key, record in safety.get("actions", {}).items():
                if record.get("authorization_result") in {
                    "PENDING",
                    "UNKNOWN",
                }:
                    return False
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
        """Resolve authorization attempts that provably never reached UART."""

        confirmations = safety.setdefault("confirmations", {})
        for action_key, record in list(
            safety.get("actions", {}).items()
        ):
            if record.get("authorization_result") not in {
                "PENDING",
                "UNKNOWN",
            }:
                continue
            evidence_sha256 = canonical_sha256(
                {
                    "eventType": "LOCAL_ACTION_NOT_DISPATCHED",
                    "workUid": safety["work_uid"],
                    "commandUid": safety["command_uid"],
                    "actionUid": record["action_uid"],
                }
            )
            try:
                remote = self._job_safety.get_physical_action(
                    record["action_uid"]
                )
            except JobSafetyError as error:
                if error.code == "PHYSICAL_ACTION_NOT_FOUND":
                    safety["actions"].pop(action_key, None)
                    continue
                return False
            if remote.get("state") == "MAY_HAVE_EXECUTED":
                try:
                    self._confirm_physical_action_record(
                        context,
                        action_key=action_key,
                        record=record,
                        outcome="NOT_EXECUTED",
                        evidence_sha256=evidence_sha256,
                    )
                except JobSafetyError:
                    return False
                record["authorization_result"] = "ACCEPTED"
            elif (
                remote.get("state") != "CONFIRMED"
                or remote.get("confirmedOutcome") != "NOT_EXECUTED"
                or remote.get("evidenceDigestSha256") != evidence_sha256
            ):
                return False
            else:
                confirmation = {
                    "outcome": "NOT_EXECUTED",
                    "evidence_sha256": evidence_sha256,
                    "confirmed": True,
                }
                confirmations[action_key] = confirmation
                record["authorization_result"] = "ACCEPTED"
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

    def _record_fixed_frame_start_failure(
        self,
        *,
        context: dict[str, Any],
        command: dict[str, Any],
        work_uid: str,
        mcu_command_uid: str,
        error_code: str,
    ) -> None:
        known_no_effect = error_code in {
            "COMMAND_EXPIRED",
            "UART_CLOSED",
            "MCU_FEATURE_NOT_SUPPORTED",
        }
        evidence = {
            "eventType": "PHYSICAL_JOB_NOT_STARTED",
            "workUid": work_uid,
            "commandUid": command["commandUid"],
            "reason": error_code,
        }
        if known_no_effect:
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
            stage="PRE_START_FAILED" if known_no_effect else "FAILED",
            release_work_slot=not self._job_safety.enabled,
            work_context=context if known_no_effect else None,
        )
        if not self._job_safety.enabled or not persisted or not known_no_effect:
            return
        completed = self._complete_job_safety(
            context,
            outcome="FAILED",
            evidence=evidence,
            physical_outcome="NOT_EXECUTED",
        )
        if completed:
            self._store.release_work_slot(work_uid)

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

    def expire_fixed_frame_work(self) -> bool:
        """Resolve an expired DD/EF wait without replaying its physical command.

        Delivery remains a terminal timeout.  Clean work is retained in the
        recovery state because the legacy EE frame may already have unlocked
        the physical door and a late EF result can still close the operation.
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
        if (
            context.get("phase")
            in {
                "CLEAN_RECOVERY_REQUIRED",
                "FIXED_FRAME_RESULT_TIMEOUT_RECOVERY_REQUIRED",
            }
        ):
            return False
        deadline = (
            context.get("expires_at")
            if slot["work_type"] == WORK_TYPE_DELIVERY
            else context.get("operation_deadline")
        )
        if not deadline:
            return False
        try:
            remaining_ms = (
                _remaining_operation_window_ms(context)
                if slot["work_type"] == WORK_TYPE_CLEAN
                else _remaining_until(deadline)
            )
            if remaining_ms > 0:
                return False
        except ValueError:
            pass
        if slot["work_type"] == WORK_TYPE_CLEAN:
            return (
                self._store.mark_clean_window_expired_for_recovery(
                    slot["work_uid"]
                )
                == "RECOVERY_REQUIRED"
            )
        command_uid = context.get("start_command_uid")
        command_row = (
            self._store.get_command(command_uid)
            if command_uid
            else None
        )
        command = (
            command_row.get("payload")
            if command_row
            else None
        )
        if command is None:
            if self._job_safety.enabled:
                return False
            self._store.release_work_slot(slot["work_uid"])
            return True
        if self._job_safety.enabled:
            context["phase"] = (
                "FIXED_FRAME_RESULT_TIMEOUT_RECOVERY_REQUIRED"
            )
        return self._store.fail_fixed_frame_work(
            work_uid=slot["work_uid"],
            command=command,
            error_code="MCU_RESULT_TIMEOUT",
            mcu_command_uid=context.get("start_mcu_command_uid"),
            stage="FAILED",
            release_work_slot=not self._job_safety.enabled,
            work_context=context if self._job_safety.enabled else None,
        )

    def start_delivery_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """持久化并派发一次云端已授权的投递会话。"""
        payload = command["payload"]
        config = payload["config"]
        self._require_applied_config(config)
        # 后端只判断它能权威确认的身份、归属、OneNet 在线和整机占位。
        # 满溢、安全传感器、重启清运锁等现场事实必须在香橙派写串口前判断。
        if self._store.clean_restart_interlock_active(
            payload["portNo"]
        ):
            return self._reject_command(
                command,
                "CLEAN_RESTARTED_CLEAN_REQUIRED",
            )
        if self._store.get_port_fullness_state(
            payload["portNo"],
            payload["bagUid"],
        ) == "FULL":
            return self._reject_command(command, "PORT_FULL")
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
            "round_index": 0,
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
        if compatibility_mode:
            ctx["phase"] = (
                "WAITING_COMPAT_DELIVERY_RESULT"
                if result["acked"]
                else "START_FAILED"
            )
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
        elif compatibility_mode:
            error_code = str(
                result.get("error") or "UART_WRITE_FAILED"
            )
            self._record_fixed_frame_start_failure(
                context=ctx,
                command=command,
                work_uid=session_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
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
            ctx["phase"] = (
                "WAITING_COMPAT_CLEAN_RESULT"
                if result["acked"]
                else "START_FAILED"
            )
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
        elif compatibility_mode:
            error_code = str(
                result.get("error") or "UART_WRITE_FAILED"
            )
            self._record_fixed_frame_start_failure(
                context=ctx,
                command=command,
                work_uid=operation_uid,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
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
        # The synchronous fixed-frame path has no business work slot yet, so
        # use the command's already-durable measurement UUID as its permanent
        # physical-action identity.
        action_uid = measurement_uid
        try:
            self._arm_physical_action(
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
                persist_context=job_safety is not None,
            )
        except Exception:
            # No UART query has happened yet.  The permanent record remains
            # authoritative if the local response itself was uncertain.
            raise
        try:
            query_timeout_ms = min(
                query_timeout_ms,
                _remaining_execution_ms(command),
            )
        except ValueError:
            return self._fail_unstored_job_before_physical_action(
                context=context,
                command=command,
                work_uid=measurement_uid,
                error_code="COMMAND_EXPIRED",
            )
        snapshot = self._uart.query_self_test(
            timeout_ms=query_timeout_ms,
            on_result=self._store.save_fixed_frame_self_test,
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
            work_context=context if job_safety is not None else None,
        )
        if completed not in ("ACCEPTED", "DUPLICATE"):
            raise ValueError(
                f"fixed-frame baseline persistence {completed.lower()}"
            )
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
        }:
            return {"acked": False, "error": "STATE_CONFLICT"}
        mcu_command_uid = _new_uid()
        result = self._send_physical_command(
            ctx,
            "END_CLEAN_BEFORE_UNLOCK",
            {
                "operationUid": slot["work_uid"],
                "portNo": slot["port_no"],
                "parentCommandUid": ctx["start_mcu_command_uid"],
                "recoveryGeneration": ctx["recovery_generation"],
                "executionDeadlineMs": _remaining_execution_ms(command),
                "reason": payload["reason"],
            },
            mcu_command_uid=mcu_command_uid,
            action_key=(
                "CLEAN:END_BEFORE_UNLOCK:"
                f"{ctx['recovery_generation']}"
            ),
            action_kind="END_CLEAN_BEFORE_UNLOCK",
            not_after=command["expiresAt"],
        )
        if result["acked"]:
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
        next_action_sequence = int(ctx.get("action_sequence", 0)) + 1
        resume_uid = _new_uid()
        ctx["recovery_generation"] = requested_generation
        ctx["resume_mcu_command_uid"] = resume_uid
        ctx["resume_command_uid"] = command["commandUid"]
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
            action_key=f"CLEAN:RESUME:{requested_generation}",
            action_kind="RESUME_CLEAN_OPERATION",
            not_after=command["expiresAt"],
        )
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
               "phase": "STARTED", "round_index": 0,
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
            self._on_delivery_selection(ctx, payload, work_uid)
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
            self._on_clean_completion(ctx, payload, work_uid)
        elif msg_name == "FULLNESS_SAMPLE_RESULT" and work_type == WORK_TYPE_FULLNESS:
            self._on_fullness_sample_result(ctx, payload, work_uid)
        elif msg_name == "BASELINE_MEASUREMENT_RESULT" and work_type == WORK_TYPE_BASELINE:
            self._on_baseline_measurement_result(ctx, payload, work_uid)
        elif msg_name == "BOOT_RECONCILIATION_RESULT" and work_type == WORK_TYPE_CLEAN:
            self._on_boot_reconciliation_result(ctx, payload, work_uid)

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
        old_baseline = ctx.get("old_baseline_weight_grams")
        removed_weight = (
            pre_weight - old_baseline
            if isinstance(old_baseline, int)
            and not isinstance(old_baseline, bool)
            else None
        )
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
            or payload.get("mcuCommandUid")
            != ctx.get("resume_mcu_command_uid")
            or payload.get("activeWorkUid") != work_uid
            or payload.get("recoveryGeneration")
            != ctx.get("recovery_generation")
        ):
            raise ValueError("boot reconciliation result does not match clean")
        if payload.get("status") != "ACCEPTED":
            self._confirm_action_from_mcu_event(
                ctx,
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
            event_type="BOOT_RECONCILIATION_RESULT",
            payload=payload,
        )
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
        if payload.get("sessionUid") != work_uid:
            raise ValueError("door command result does not match delivery")
        output_status = payload.get("outputStatus")
        self._confirm_action_from_mcu_event(
            ctx,
            event_type="DELIVERY_DOOR_COMMAND_RESULT",
            payload=payload,
            outcome=(
                "EXECUTED"
                if output_status in {
                    "COMMAND_DISPATCHED",
                    "COALESCED_WITH_EXISTING_CLOSE",
                }
                else "NOT_EXECUTED"
            ),
        )
        ctx["last_delivery_door_command"] = payload.get("command")
        ctx["last_delivery_door_output_status"] = payload.get(
            "outputStatus"
        )
        ctx["delivery_door_physical_state_basis"] = payload.get(
            "physicalDoorStateBasis"
        )
        if payload.get("command") == "OPEN":
            ctx["phase"] = "DELIVERY_WINDOW"
        elif payload.get("command") == "CLOSE":
            ctx["phase"] = "WAITING_POSTCLOSE_WEIGHT"
        self._store.update_work_context(work_uid, ctx)

    def _on_preopen_weight(self, ctx, payload, work_uid):
        if (
            payload.get("sessionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("start_mcu_command_uid")
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

    def _on_delivery_selection(self, ctx, payload, work_uid):
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
        if (
            payload.get("operationUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("start_mcu_command_uid")
        ):
            raise ValueError("preunlock measurement does not match active clean")
        # This identity-bound MCU fact proves that the START action reached
        # the controller.  Resolve the permanent action ledger before applying
        # the business operation-window policy; otherwise a fact arriving just
        # after expiry would be consumed while leaving the job permanently
        # blocked by an action that can no longer be reconciled.
        self._confirm_action_from_mcu_event(
            ctx,
            event_type="WORK_PREUNLOCK_WEIGHT_READY",
            payload=payload,
        )
        if self._remaining_clean_window_or_recovery(ctx, work_uid) is None:
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
                    "measurementStatus": payload.get("measurementStatus"),
                },
            )
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            raise ValueError("no applied configuration for clean unlock")
        if not self._capture_photos(
            "capture_clean_open_photos",
            work_uid,
        ):
            ctx["phase"] = "PREUNLOCK_PHOTO_BLOCKED"
            self._store.update_work_context(work_uid, ctx)
            return
        remaining_window_ms = self._remaining_clean_window_or_recovery(
            ctx, work_uid
        )
        if remaining_window_ms is None:
            return
        unlock_uid = ctx.get("unlock_mcu_command_uid") or _new_uid()
        ctx["unlock_mcu_command_uid"] = unlock_uid
        ctx["phase"] = "UNLOCKING"
        self._store.update_work_context(work_uid, ctx)
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
            action_key=(
                "CLEAN:UNLOCK:"
                f"{ctx['recovery_generation']}:"
                f"{ctx['action_sequence']}"
            ),
            action_kind="UNLOCK_CLEAN_DOOR",
            not_after=ctx["operation_deadline"],
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
        if payload.get("operationUid") != work_uid:
            raise ValueError("unlock request does not match active clean")
        action_sequence = payload.get("cleanActionSequence")
        if (
            not isinstance(action_sequence, int)
            or action_sequence <= ctx.get("action_sequence", 0)
        ):
            raise ValueError("clean action sequence did not advance")
        remaining_window_ms = self._remaining_clean_window_or_recovery(
            ctx, work_uid
        )
        if remaining_window_ms is None:
            return
        ctx["action_sequence"] = action_sequence
        ctx["final_weight_grams"] = None
        ctx["final_measurement_uid"] = None
        ctx["final_measurement"] = None
        applied = self._store.get_latest_applied_configuration()
        if not applied:
            raise ValueError("no applied configuration for clean unlock")
        unlock_uid = _new_uid()
        ctx["unlock_mcu_command_uid"] = unlock_uid
        ctx["phase"] = "REUNLOCKING"
        self._store.update_work_context(work_uid, ctx)
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
            action_key=(
                "CLEAN:UNLOCK:"
                f"{ctx['recovery_generation']}:"
                f"{action_sequence}"
            ),
            action_kind="UNLOCK_CLEAN_DOOR",
            not_after=ctx["operation_deadline"],
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
        if payload.get("operationUid") != work_uid:
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
        if payload.get("operationUid") != work_uid:
            raise ValueError("lock event does not match active clean")
        self._confirm_action_from_mcu_event(
            ctx,
            event_type="CLEAN_LOCK_POWER_CHANGED",
            payload=payload,
            outcome=(
                "EXECUTED"
                if payload.get("lockPowerState")
                in {"ENERGIZED", "DEENERGIZED"}
                else "FAILED_SAFE"
            ),
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
        self._arm_physical_action(
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

    def _on_clean_completion(self, ctx, payload, work_uid):
        if (
            payload.get("operationUid") != work_uid
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
        old_baseline = ctx.get("old_baseline_weight_grams")
        removed_weight = (
            preunlock_weight - old_baseline
            if preunlock_weight is not None
            and old_baseline is not None
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

    def _on_fullness_sample_result(self, ctx, payload, work_uid):
        if (
            payload.get("detectionUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
        ):
            raise ValueError("fullness result does not match active detection")
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
        )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)

    def _on_baseline_measurement_result(self, ctx, payload, work_uid):
        if (
            payload.get("measurementUid") != work_uid
            or payload.get("mcuCommandUid") != ctx.get("mcu_command_uid")
        ):
            raise ValueError("baseline result does not match active measurement")
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
        )
        completed = self._complete_job_safety(
            ctx,
            outcome="SUCCEEDED",
            evidence=completion_evidence,
        )
        if completed:
            self._store.release_work_slot(work_uid)

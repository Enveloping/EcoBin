"""Reliable business-runtime update progress through the communication agent."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from business_update_store import BusinessUpdateStore, BusinessUpdateStoreError
from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)
from trusted_clock import event_clock_fields


logger = logging.getLogger("business-update-reporter")

EVENT_TYPE = "BUSINESS_RUNTIME_UPDATE_PROGRESS"
CANCEL_EVENT_TYPE = "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT"
TARGET_TYPE = "BUSINESS_RUNTIME_DEPLOYMENT"
COMMUNICATION_PROTOCOL_NAME = "ecobin.communication.control"
DEFAULT_COMMUNICATION_SOCKET = "/run/ecobin/communication/control.sock"

_FAILURE_STAGES = frozenset(
    {
        "DEFERRED",
        "REJECTED",
        "FAILED_LOCKED",
        "DOWNLOAD_AUTHORIZATION_REQUIRED",
    }
)


class BusinessUpdateProgressReporter:
    """Freeze each observed journal stage before handing it to communication."""

    def __init__(
        self,
        *,
        journal: BusinessUpdateStore,
        safety_store: Any,
        communication_client: LocalControlClient,
        retry_seconds: float = 0.5,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        if retry_seconds <= 0:
            raise ValueError("business progress retry interval must be positive")
        self.journal = journal
        self.safety_store = safety_store
        self.communication_client = communication_client
        self._retry_seconds = float(retry_seconds)
        self._uuid_factory = uuid_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._failure = None
        self._thread = threading.Thread(
            target=self._run,
            name="business-update-progress",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(
                    "business update progress reporter did not stop in time"
                )
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def process_once(self) -> bool:
        pending_cancellations = (
            self.journal.list_pending_cancellation_result_deliveries(limit=1)
        )
        if pending_cancellations:
            return self._submit_cancellation(pending_cancellations[0])

        cancellation_candidates = (
            self.journal.list_cancellation_results_requiring_delivery()
        )
        if cancellation_candidates:
            cancellation = cancellation_candidates[0]
            payload = _cancellation_result_payload(
                cancellation,
                self.safety_store.get_status(),
            )
            clock = event_clock_fields()
            event_uid = self._new_event_uid("cancellation result")
            delivery = self.journal.reserve_cancellation_result_delivery(
                cancellation["cancelCommandUid"],
                event_uid,
                clock["occurredAt"],
                clock["clockQuality"],
                payload,
            )
            return self._submit_cancellation(delivery)

        pending = self.journal.list_pending_progress_deliveries(limit=1)
        if pending:
            return self._submit(pending[0])

        candidates = self.journal.list_remote_updates_requiring_progress()
        if not candidates:
            return False
        snapshot = candidates[0]
        payload = _progress_payload(
            snapshot,
            self.safety_store.get_status(),
        )
        clock = event_clock_fields()
        event_uid = self._new_event_uid("progress")
        try:
            delivery = self.journal.reserve_progress_delivery(
                snapshot["updateUid"],
                snapshot["stageSequence"],
                event_uid,
                clock["occurredAt"],
                clock["clockQuality"],
                payload,
            )
        except BusinessUpdateStoreError as error:
            if error.code == "BUSINESS_PROGRESS_STAGE_SUPERSEDED":
                return True
            raise
        return self._submit(
            {
                **delivery,
                "deploymentUid": snapshot["deploymentUid"],
                "commandUid": snapshot["commandUid"],
            }
        )

    def _new_event_uid(self, label: str) -> str:
        event_uid = self._uuid_factory()
        if not isinstance(event_uid, uuid.UUID) or event_uid.version != 4:
            raise RuntimeError(
                f"{label} reporter UUID factory must return UUIDv4"
            )
        return str(event_uid)

    def _submit(self, delivery: Mapping[str, Any]) -> bool:
        try:
            result = self.communication_client.request(
                "SUBMIT_UPDATER_EVENT",
                {
                    "eventUid": delivery["eventUid"],
                    "eventType": EVENT_TYPE,
                    "targetType": TARGET_TYPE,
                    "targetUid": delivery["deploymentUid"],
                    "commandUid": delivery["commandUid"],
                    "occurredAt": delivery["occurredAt"],
                    "clockQuality": delivery["clockQuality"],
                    "payload": delivery["payload"],
                },
            )
        except LocalControlUnavailable:
            return False
        except LocalControlRemoteError as error:
            if error.code in {"SERVICE_STOPPING", "FEATURE_DISABLED"}:
                return False
            raise RuntimeError(
                "communication rejected immutable updater progress"
            ) from error
        if (
            not isinstance(result, dict)
            or result.get("eventUid") != delivery["eventUid"]
            or result.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
            or result.get("durableAccepted") is not True
        ):
            raise RuntimeError(
                "communication returned a malformed updater event receipt"
            )
        self.journal.mark_progress_delivery_accepted(
            delivery["updateUid"],
            delivery["stageSequence"],
            delivery["eventUid"],
        )
        return True

    def _submit_cancellation(self, delivery: Mapping[str, Any]) -> bool:
        try:
            result = self.communication_client.request(
                "SUBMIT_UPDATER_EVENT",
                {
                    "eventUid": delivery["eventUid"],
                    "eventType": CANCEL_EVENT_TYPE,
                    "targetType": TARGET_TYPE,
                    "targetUid": delivery["deploymentUid"],
                    "commandUid": delivery["cancelCommandUid"],
                    "occurredAt": delivery["occurredAt"],
                    "clockQuality": delivery["clockQuality"],
                    "payload": delivery["payload"],
                },
            )
        except LocalControlUnavailable:
            return False
        except LocalControlRemoteError as error:
            if error.code in {"SERVICE_STOPPING", "FEATURE_DISABLED"}:
                return False
            raise RuntimeError(
                "communication rejected immutable updater cancellation result"
            ) from error
        if (
            not isinstance(result, dict)
            or result.get("eventUid") != delivery["eventUid"]
            or result.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
            or result.get("durableAccepted") is not True
        ):
            raise RuntimeError(
                "communication returned a malformed cancellation result receipt"
            )
        self.journal.mark_cancellation_result_delivery_accepted(
            delivery["cancelCommandUid"],
            delivery["eventUid"],
        )
        return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self.process_once():
                    continue
                self._wake.wait(self._retry_seconds)
                self._wake.clear()
        except BaseException as error:
            self._failure = error
            self._stop.set()
            logger.exception("business update progress reporter failed")


def _progress_payload(
    snapshot: Mapping[str, Any],
    safety: Mapping[str, Any],
) -> dict[str, Any]:
    state = snapshot["state"]
    remote_state = snapshot["downloadState"]
    if (
        state == "DEFERRED"
        and snapshot.get("errorCode") == "BUSINESS_UPDATE_CANCELLED"
    ):
        stage = "CANCELLED"
    elif state == "RECEIVED":
        stage = {
            "RECEIVED": "RECEIVED",
            "DOWNLOADING": "DOWNLOADING",
            "DOWNLOADED": "VERIFYING_PACKAGE",
            "WAITING_AUTHORIZATION": "DOWNLOAD_AUTHORIZATION_REQUIRED",
            "DEFERRED": "DEFERRED",
            "REJECTED": "REJECTED",
        }[remote_state]
    else:
        stage = state

    gate = safety.get("jobGateState")
    admission = gate if gate in {
        "OPEN", "DRAINING", "MAINTENANCE", "LOCKED"
    } else "LOCKED"
    error_code = None
    if stage in _FAILURE_STAGES:
        error_code = (
            snapshot.get("downloadErrorCode")
            if stage == "DOWNLOAD_AUTHORIZATION_REQUIRED"
            else snapshot.get("errorCode")
        )
        if not isinstance(error_code, str) or not error_code:
            raise RuntimeError("failure progress has no durable error code")

    installed_values = (
        snapshot.get("installedReleaseId"),
        snapshot.get("installedVersionName"),
        snapshot.get("installedReleaseSequence"),
        snapshot.get("installedPackageSha256"),
    )
    installed_present = tuple(value is not None for value in installed_values)
    if any(installed_present) and not all(installed_present):
        raise RuntimeError("installed business identity is incomplete")
    installed_complete = all(installed_present)
    return {
        "deploymentUid": snapshot["deploymentUid"],
        "updateUid": snapshot["updateUid"],
        "releaseUid": snapshot["releaseId"],
        "versionName": snapshot["versionName"],
        "releaseSequence": snapshot["releaseSequence"],
        "packageSha256": snapshot["packageSha256"],
        "stage": stage,
        "stageSequence": snapshot["stageSequence"],
        "businessAdmissionState": admission,
        "downloadAttemptCount": snapshot["downloadAttemptCount"],
        "targetAttemptCount": snapshot["targetAttemptCount"],
        "rollbackAttemptCount": snapshot["rollbackAttemptCount"],
        "databaseRestored": bool(snapshot["databaseRestored"]),
        "installedReleaseUid": installed_values[0] if installed_complete else None,
        "installedVersionName": installed_values[1] if installed_complete else None,
        "installedReleaseSequence": installed_values[2] if installed_complete else None,
        "installedPackageSha256": installed_values[3] if installed_complete else None,
        "errorCode": error_code,
    }


def _cancellation_result_payload(
    cancellation: Mapping[str, Any],
    safety: Mapping[str, Any],
) -> dict[str, Any]:
    outcome = cancellation["outcome"]
    if outcome not in {"CANCELLED", "TOO_LATE"}:
        raise RuntimeError("business cancellation result is not final")
    gate = safety.get("jobGateState")
    admission = gate if gate in {
        "OPEN", "DRAINING", "MAINTENANCE", "LOCKED"
    } else "LOCKED"
    return {
        "deploymentUid": cancellation["deploymentUid"],
        "updateUid": cancellation["updateUid"],
        "controlSequence": cancellation["controlSequence"],
        "result": outcome,
        "observedStage": cancellation["observedState"],
        "businessAdmissionState": admission,
        "errorCode": (
            None
            if outcome == "CANCELLED"
            else "BUSINESS_UPDATE_CANCEL_TOO_LATE"
        ),
    }


__all__ = [
    "BusinessUpdateProgressReporter",
    "DEFAULT_COMMUNICATION_SOCKET",
    "CANCEL_EVENT_TYPE",
    "EVENT_TYPE",
    "TARGET_TYPE",
]

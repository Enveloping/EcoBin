"""Restart-safe local candidate orchestration for business-runtime updates."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from business_update_package import (
    BusinessPackageStageError,
    BusinessReleasePackageStager,
)
from business_update_store import (
    BUSINESS_UPDATE_ARTIFACT_CLEANUP_STATES,
    BUSINESS_UPDATE_TERMINAL_STATES,
    BusinessUpdateStore,
    BusinessUpdateStoreError,
)
from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
    canonical_local_payload_sha256,
)
from updater_store import UpdaterStore, UpdaterStoreError


logger = logging.getLogger("business-update-coordinator")

BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
BUSINESS_HELPER_PROTOCOL_NAME = (
    "ecobin.business-release-activation-helper.control"
)
DEFAULT_BUSINESS_SOCKET = "/run/ecobin/business/control.sock"
DEFAULT_BUSINESS_HELPER_SOCKET = (
    "/run/ecobin/privileged/business-release-activation-candidate.sock"
)
MAINTENANCE_TYPE = "BUSINESS_RUNTIME_UPDATE"
BASELINE_BUSINESS_RELEASE = "BUSINESS_RELEASE"
BASELINE_IMAGE_BRIDGE = "IMAGE_BRIDGE"
DRAIN_TIMEOUT_SECONDS = 30 * 60
OBSERVATION_SECONDS = 30 * 60
RESTART_BUSINESS_HEALTH_GRACE_SECONDS = 3 * 60 + 30
_RESTART_HEALTH_STATES = {
    "VERIFYING_TARGET",
    "OBSERVING",
    "VERIFYING_ROLLBACK",
}


class BusinessUpdateCoordinatorError(RuntimeError):
    def __init__(self, code: str, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class BusinessUpdateCoordinator:
    """Advance one local signed business update by idempotent durable steps."""

    def __init__(
        self,
        *,
        safety_store: UpdaterStore,
        journal: BusinessUpdateStore,
        package_stager: BusinessReleasePackageStager,
        remote_downloader: Any | None = None,
        business_client: Any | None = None,
        helper_client: Any | None = None,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        utc_now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        poll_seconds: float = 5.0,
        drain_timeout_seconds: int = DRAIN_TIMEOUT_SECONDS,
        observation_seconds: int = OBSERVATION_SECONDS,
        restart_business_health_grace_seconds: int = (
            RESTART_BUSINESS_HEALTH_GRACE_SECONDS
        ),
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("business update poll interval must be positive")
        for value, label in (
            (drain_timeout_seconds, "drain timeout"),
            (observation_seconds, "observation timeout"),
            (
                restart_business_health_grace_seconds,
                "restart business health grace",
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"business update {label} must be positive")
        self.safety_store = safety_store
        self.journal = journal
        self.package_stager = package_stager
        self.remote_downloader = remote_downloader
        self.business_client = business_client or LocalControlClient(
            DEFAULT_BUSINESS_SOCKET,
            protocol_name=BUSINESS_PROTOCOL_NAME,
            response_timeout_seconds=15.0,
        )
        self.helper_client = helper_client or LocalControlClient(
            DEFAULT_BUSINESS_HELPER_SOCKET,
            protocol_name=BUSINESS_HELPER_PROTOCOL_NAME,
            response_timeout_seconds=910.0,
        )
        self._uuid_factory = uuid_factory
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._poll_seconds = float(poll_seconds)
        self._drain_timeout_seconds = drain_timeout_seconds
        self._observation_seconds = observation_seconds
        self._restart_health_update_uid: str | None = None
        self._restart_health_deadline: float | None = None
        self._restart_health_wait_logged = False
        active_update = self.journal.get_active_update()
        if (
            active_update is not None
            and active_update["state"] in _RESTART_HEALTH_STATES
        ):
            self._restart_health_update_uid = active_update["updateUid"]
            self._restart_health_deadline = (
                self._monotonic() + restart_business_health_grace_seconds
            )
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._terminal_cleanup_attempted: set[str] = set()
        self.failure: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.set()
        self.failure = None
        self._thread = threading.Thread(
            target=self._run,
            name="business-update-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_seconds)
        self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def queue_local(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._queue_lock:
            try:
                duplicate = self.journal.preflight_update_request(payload)
                if duplicate is not None:
                    return duplicate
                safety = self.safety_store.get_status()
                if (
                    safety.get("candidateActivationState") != "ACTIVE"
                    or safety.get("jobGateState") != "OPEN"
                    or safety.get("maintenanceOwnerUid") is not None
                ):
                    raise BusinessUpdateCoordinatorError(
                        "BUSINESS_UPDATE_BUSY",
                        "device is not open for a new local business update",
                    )
                self._require_proxy_business_health(expected_version=None)
                result = self.journal.create_update(payload)
            except BusinessUpdateStoreError as error:
                raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        self.wake()
        return result

    def queue_remote(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Accept a validated cloud update while keeping its URL in memory."""

        downloader = self.remote_downloader
        if downloader is None:
            raise BusinessUpdateCoordinatorError(
                "FEATURE_DISABLED",
                "remote business runtime update is disabled",
            )
        payload = command.get("payload")
        grant = command.get("downloadGrant")
        if not isinstance(payload, Mapping) or not isinstance(grant, Mapping):
            raise BusinessUpdateCoordinatorError(
                "REQUEST_INVALID", "remote business update command is invalid"
            )
        request = {
            "updateUid": payload.get("updateUid"),
            "deploymentUid": payload.get("deploymentUid"),
            "commandUid": command.get("commandUid"),
            "releaseId": payload.get("releaseUid"),
            "versionName": payload.get("versionName"),
            "releaseSequence": payload.get("releaseSequence"),
            "packageSha256": payload.get("packageSha256"),
            "packageSize": payload.get("packageSize"),
            "signingKeyId": payload.get("signingKeyId"),
            "stablePayloadSha256": command.get("payloadSha256"),
            "controlSequence": payload.get("controlSequence"),
            "objectKey": payload.get("objectKey"),
            "signatureSha256": payload.get("signatureSha256"),
            "packageSignatureBase64": payload.get("packageSignatureBase64"),
            "observationWindowSeconds": payload.get(
                "observationWindowSeconds"
            ),
            "downloadTimeoutSeconds": payload.get("downloadTimeoutSeconds"),
            "drainTimeoutSeconds": payload.get("drainTimeoutSeconds"),
            "maximumRetryCount": payload.get("maximumRetryCount"),
        }
        with self._queue_lock:
            try:
                existing = self.journal.get_update(request["updateUid"])
                if existing is None:
                    safety = self.safety_store.get_status()
                    if (
                        safety.get("candidateActivationState") != "ACTIVE"
                        or safety.get("jobGateState") != "OPEN"
                        or safety.get("maintenanceOwnerUid") is not None
                    ):
                        raise BusinessUpdateCoordinatorError(
                            "BUSINESS_UPDATE_BUSY",
                            "device is not open for a new business update",
                        )
                    self._require_proxy_business_health(expected_version=None)
                result = self.journal.create_or_refresh_remote_update(
                    request,
                    authorization_sequence=grant.get(
                        "authorizationSequence"
                    ),
                )
            except BusinessUpdateStoreError as error:
                raise BusinessUpdateCoordinatorError(
                    error.code, str(error)
                ) from error
        if result["authorizationDisposition"] in {"ACCEPTED", "REFRESHED"}:
            downloader.accept_authorization(
                update_uid=request["updateUid"],
                authorization_sequence=grant["authorizationSequence"],
                url=grant.get("url"),
                expires_at=_parse_utc(grant.get("expiresAt")),
            )
        self.wake()
        return result

    def cancel_remote(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Accept one monotonic cloud cancellation at the last safe point.

        Cancellation and update advancement share ``_process_lock``.  The
        durable state observed while this lock is held therefore decides
        whether cancellation won before the first mutation, or arrived too
        late and must leave the running update untouched.
        """

        downloader = self.remote_downloader
        if downloader is None:
            raise BusinessUpdateCoordinatorError(
                "FEATURE_DISABLED",
                "remote business runtime update is disabled",
            )
        payload = command.get("payload")
        if not isinstance(payload, Mapping):
            raise BusinessUpdateCoordinatorError(
                "REQUEST_INVALID",
                "remote business update cancellation is invalid",
            )
        request = {
            "cancelCommandUid": command.get("commandUid"),
            "updateUid": payload.get("updateUid"),
            "deploymentUid": payload.get("deploymentUid"),
            "controlSequence": payload.get("controlSequence"),
            "reason": payload.get("reason"),
        }
        with self._process_lock:
            with self._queue_lock:
                try:
                    self._reconcile_cancellation_boundary(
                        request["updateUid"]
                    )
                    result = self.journal.request_remote_cancellation(
                        request
                    )
                    if result["outcome"] == "ACCEPTED":
                        downloader.cancel(request["updateUid"])
                except (BusinessUpdateStoreError, UpdaterStoreError) as error:
                    raise BusinessUpdateCoordinatorError(
                        error.code, str(error)
                    ) from error
        self.wake()
        return result

    def get_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"updateUid"}:
            raise BusinessUpdateCoordinatorError(
                "REQUEST_INVALID", "business update query fields are invalid"
            )
        try:
            result = self.journal.get_update(payload["updateUid"])
        except BusinessUpdateStoreError as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        if result is None:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_UPDATE_NOT_FOUND", "business update was not found"
            )
        return result

    def get_status(self) -> dict[str, Any]:
        return self.journal.get_status()

    def authorize_privileged_action(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            update = self.journal.get_update(payload.get("updateUid"))
            if update is None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_NOT_FOUND", "business update was not found"
                )
            fence = update.get("maintenanceFenceToken")
            self.safety_store.require_update_maintenance(
                update["updateUid"],
                fence,
                allow_recovery_lock=True,
                maintenance_type=MAINTENANCE_TYPE,
            )
            return self.journal.authorize_action(payload)
        except (BusinessUpdateStoreError, UpdaterStoreError) as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error

    def process_once(self) -> bool:
        if not self._process_lock.acquire(blocking=False):
            return False
        try:
            cleanup_progress = self._reconcile_terminal_artifact_cleanup()
            if cleanup_progress is not None:
                return cleanup_progress
            update = self.journal.get_active_update()
            if update is None:
                return False
            before = (update["state"], update["step"], update["stageSequence"])
            try:
                self._advance(update)
            except BusinessUpdateCoordinatorError as error:
                self._record_step_error(update, error)
            except (BusinessUpdateStoreError, UpdaterStoreError) as error:
                self._record_step_error(
                    update,
                    BusinessUpdateCoordinatorError(error.code, str(error)),
                )
            current = self.journal.get_update(update["updateUid"])
            if current is None:
                return True
            after = (current["state"], current["step"], current["stageSequence"])
            return after != before
        finally:
            self._process_lock.release()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self.process_once():
                    if self._stop_event.wait(0.02):
                        return
                    continue
                self._wake_event.wait(self._poll_seconds)
                self._wake_event.clear()
        except BaseException as error:  # noqa: BLE001 - worker death must fail service
            self.failure = error
            logger.exception("business update coordinator stopped unexpectedly")

    def _advance(self, update: dict[str, Any]) -> None:
        cancellation = self.journal.get_pending_cancellation(
            update["updateUid"]
        )
        if cancellation is not None:
            self._complete_pending_cancellation(update, cancellation)
            return
        state = update["state"]
        if state == "RECEIVED":
            remote = self.journal.get_remote_update(update["updateUid"])
            if remote is not None and remote["downloadState"] != "DOWNLOADED":
                return
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_PACKAGE",
                expected_states={"RECEIVED"},
                step="VERIFY_PACKAGE",
            )
            return
        if state == "VERIFYING_PACKAGE":
            self._stage_package(update)
            return
        if state == "PACKAGE_READY":
            self._begin_target_drain(update)
            return
        if state == "WAITING_FOR_IDLE":
            self._finish_target_drain(update)
            return
        if state == "MIGRATING_DATA":
            self._migrate_data(update)
            return
        if state == "ACTIVATING":
            self._activate_target(update)
            return
        if state == "VERIFYING_TARGET":
            self._verify_target(update)
            return
        if state == "OBSERVING":
            self._observe_target(update)
            return
        if state == "ROLLING_BACK":
            self._rollback(update)
            return
        if state == "VERIFYING_ROLLBACK":
            self._verify_rollback(update)
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STATE_INVALID", "business update state cannot advance"
        )

    def _reconcile_cancellation_boundary(self, update_uid: object) -> None:
        """Close the power-loss gap after drain became maintenance.

        A live coordinator changes the safety gate and journal while holding
        ``_process_lock``.  A power loss can nevertheless leave the gate in
        maintenance while the journal still says WAITING_FOR_IDLE.  That gate
        transition is treated as the irreversible boundary on recovery, so a
        later cancellation is recorded as TOO_LATE instead of erasing an
        already-established maintenance fence.
        """

        update = self.journal.get_update(update_uid)
        if update is None or update["state"] != "WAITING_FOR_IDLE":
            return
        status = self.safety_store.get_status()
        if (
            status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken")
            == update.get("maintenanceFenceToken")
            and status.get("maintenancePhase") == "MAINTENANCE"
            and status.get("jobGateState") in {"MAINTENANCE", "LOCKED"}
        ):
            self.journal.transition(
                update["updateUid"],
                "MIGRATING_DATA",
                expected_states={"WAITING_FOR_IDLE"},
                step="INSPECT_BASELINE",
            )

    def _complete_pending_cancellation(
        self,
        update: Mapping[str, Any],
        cancellation: Mapping[str, Any],
    ) -> None:
        downloader = self.remote_downloader
        if downloader is None:
            raise BusinessUpdateCoordinatorError(
                "FEATURE_DISABLED",
                "remote business runtime update is disabled",
            )
        downloader.cancel(update["updateUid"])
        if not downloader.cleanup_cancelled(update["updateUid"]):
            return
        try:
            self.package_stager.cleanup(update["updateUid"])
        except BusinessPackageStageError as error:
            raise BusinessUpdateCoordinatorError(
                error.code, str(error)
            ) from error

        evidence = canonical_local_payload_sha256(
            {
                "cancelCommandUid": cancellation["cancelCommandUid"],
                "updateUid": update["updateUid"],
                "outcome": "CANCELLED",
            }
        )
        fence = update.get("maintenanceFenceToken")
        status = self.safety_store.get_status()
        if isinstance(fence, int) and not isinstance(fence, bool):
            status = self._resume_drain_if_needed(update)
            if (
                status.get("jobGateState") != "OPEN"
                or status.get("maintenanceOwnerUid") is not None
            ):
                try:
                    self.safety_store.abort_update_drain(
                        update["updateUid"],
                        fence,
                        evidence_sha256=evidence,
                        maintenance_type=MAINTENANCE_TYPE,
                    )
                except UpdaterStoreError as error:
                    raise BusinessUpdateCoordinatorError(
                        error.code, str(error)
                    ) from error
        elif (
            status.get("jobGateState") != "OPEN"
            or status.get("maintenanceOwnerUid") is not None
        ):
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_CANCEL_SAFETY_CONFLICT",
                "another safety operation owns the device",
            )
        self.journal.complete_remote_cancellation(
            update["updateUid"], evidence_sha256=evidence
        )

    def _stage_package(self, update: Mapping[str, Any]) -> None:
        try:
            health = self._require_proxy_business_health(expected_version=None)
            database_size = health.get("businessDatabaseSize")
            if (
                isinstance(database_size, bool)
                or not isinstance(database_size, int)
                or database_size < 0
            ):
                raise BusinessPackageStageError(
                    "BUSINESS_DATABASE_SIZE_INVALID",
                    "business database size could not be confirmed",
                )
            staged = self.package_stager.stage(
                update_uid=update["updateUid"],
                release_id=update["releaseId"],
                version_name=update["versionName"],
                release_sequence=update["releaseSequence"],
                expected_package_sha256=update["packageSha256"],
                expected_package_size=update["packageSize"],
                signing_key_id=update["signingKeyId"],
                business_database_size=database_size,
            )
        except BusinessUpdateCoordinatorError as error:
            self._finish_before_drain(
                update,
                outcome="DEFERRED",
                error_code=error.code,
                error_message=str(error),
            )
            return
        except BusinessPackageStageError as error:
            outcome = (
                "DEFERRED"
                if error.code
                in {"BUSINESS_PACKAGE_NOT_READY", "DEVICE_STORAGE_UNKNOWN"}
                else "REJECTED"
            )
            self._finish_before_drain(
                update,
                outcome=outcome,
                error_code=error.code,
                error_message=str(error),
            )
            return
        self.journal.transition(
            update["updateUid"],
            "PACKAGE_READY",
            expected_states={"VERIFYING_PACKAGE"},
            step="BEGIN_DRAIN",
            fields={
                "manifest_json": json.dumps(
                    dict(staged.manifest), sort_keys=True, separators=(",", ":")
                ),
                "last_error_code": None,
                "last_error_message": None,
            },
        )

    def _finish_before_drain(
        self,
        update: Mapping[str, Any],
        *,
        outcome: str,
        error_code: str,
        error_message: str,
    ) -> None:
        if outcome not in {"DEFERRED", "REJECTED"}:
            raise AssertionError("pre-drain business update outcome is invalid")
        stable_code = _stable_error_code(error_code)
        evidence = canonical_local_payload_sha256(
            {
                "updateUid": update["updateUid"],
                "outcome": outcome,
                "errorCode": stable_code,
            }
        )
        self.journal.transition(
            update["updateUid"],
            outcome,
            expected_states={"VERIFYING_PACKAGE"},
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": stable_code,
                "last_error_message": error_message,
            },
        )

    def _begin_target_drain(self, update: Mapping[str, Any]) -> None:
        status = self.safety_store.transition_job_gate(
            "DRAINING",
            owner_update_uid=update["updateUid"],
            maintenance_type=MAINTENANCE_TYPE,
        )
        self.journal.transition(
            update["updateUid"],
            "WAITING_FOR_IDLE",
            expected_states={"PACKAGE_READY"},
            step="WAIT_FOR_IDLE",
            fields={
                "maintenance_fence_token": status["maintenanceFenceToken"],
                "drain_started_at": _format_utc(self._utc_now()),
            },
        )

    def _finish_target_drain(self, update: Mapping[str, Any]) -> None:
        status = self._resume_drain_if_needed(update)
        status = self._resume_owned_maintenance_if_needed(update, status)
        if (
            status.get("jobGateState") == "MAINTENANCE"
            and status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken")
            == update.get("maintenanceFenceToken")
        ):
            self.journal.transition(
                update["updateUid"],
                "MIGRATING_DATA",
                expected_states={"WAITING_FOR_IDLE"},
                step="INSPECT_BASELINE",
            )
            return
        if self._elapsed(update.get("drainStartedAt")) >= self._policy_seconds(
            update,
            "drainTimeoutSeconds",
            self._drain_timeout_seconds,
        ):
            self._defer_before_mutation(update, "BUSINESS_DRAIN_TIMEOUT")
            return
        if (
            status.get("jobGateState") != "DRAINING"
            or status.get("activeJobPermitCount") != 0
            or status.get("unreconciledPhysicalActionCount") != 0
        ):
            return
        try:
            self.safety_store.transition_job_gate("MAINTENANCE")
        except UpdaterStoreError as error:
            if error.code == "JOB_DRAIN_INCOMPLETE":
                return
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        self.journal.transition(
            update["updateUid"],
            "MIGRATING_DATA",
            expected_states={"WAITING_FOR_IDLE"},
            step="INSPECT_BASELINE",
        )

    def _migrate_data(self, update: Mapping[str, Any]) -> None:
        step = update["step"]
        if step == "INSPECT_BASELINE":
            status = self._dispatch_helper(update, "INSPECT_BASELINE", {})
            marker = status.get("currentRelease")
            current = status.get("currentReleaseUid")
            bridge_state = status.get("bridgeBusinessRuntimeState")
            installed_release_count = status.get("installedReleaseCount")
            if (
                not isinstance(current, str)
                or not isinstance(marker, Mapping)
                or marker.get("releaseUid") != current
                or marker.get("schemaVersion") != 2
                or not isinstance(marker.get("packageSha256"), str)
                or not isinstance(marker.get("versionName"), str)
                or isinstance(marker.get("releaseSequence"), bool)
                or not isinstance(marker.get("releaseSequence"), int)
                or marker["releaseSequence"] < 1
            ):
                if not (
                    current is None
                    and marker is None
                    and status.get("previousReleaseUid") is None
                    and not isinstance(installed_release_count, bool)
                    and isinstance(installed_release_count, int)
                    and installed_release_count >= 0
                    and status.get("businessRuntimeState") == "INACTIVE"
                    and bridge_state == "ACTIVE"
                ):
                    self._reject_after_drain(
                        update,
                        "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                    )
                    return
                health = self._require_proxy_business_health(expected_version=None)
                previous_version = health.get("releaseVersion")
                if not isinstance(previous_version, str) or not previous_version:
                    self._reject_after_drain(
                        update,
                        "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                    )
                    return
                self.journal.transition(
                    update["updateUid"],
                    "MIGRATING_DATA",
                    expected_states={"MIGRATING_DATA"},
                    step="STOP_BASELINE",
                    fields={
                        "baseline_kind": BASELINE_IMAGE_BRIDGE,
                        "previous_version_name": previous_version,
                    },
                )
                return
            if (
                status.get("businessRuntimeState") != "ACTIVE"
                or bridge_state != "INACTIVE"
            ):
                self._reject_after_drain(
                    update,
                    "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                )
                return
            if current == update["releaseId"]:
                self._reject_after_drain(update, "BUSINESS_TARGET_ALREADY_INSTALLED")
                return
            if update["releaseSequence"] <= marker["releaseSequence"]:
                self._reject_after_drain(
                    update,
                    "BUSINESS_RELEASE_SEQUENCE_NOT_NEWER",
                )
                return
            if update["versionName"] == marker["versionName"]:
                self._reject_after_drain(
                    update,
                    "BUSINESS_VERSION_IDENTITY_CONFLICT",
                )
                return
            self.journal.transition(
                update["updateUid"],
                "MIGRATING_DATA",
                expected_states={"MIGRATING_DATA"},
                step="STOP_BASELINE",
                    fields={
                        "baseline_kind": BASELINE_BUSINESS_RELEASE,
                        "previous_release_id": current,
                        "previous_version_name": marker["versionName"],
                        "previous_release_sequence": marker["releaseSequence"],
                        "previous_package_sha256": marker["packageSha256"],
                    },
            )
            return
        if step == "STOP_BASELINE":
            action_kind = (
                "STOP_BRIDGE_FOR_TARGET"
                if update.get("baselineKind") == BASELINE_IMAGE_BRIDGE
                else "STOP_FOR_TARGET"
            )
            self._dispatch_helper(update, action_kind, {})
            self.journal.transition(
                update["updateUid"], "MIGRATING_DATA", step="SNAPSHOT_DATABASE"
            )
            return
        if step == "SNAPSHOT_DATABASE":
            result = self._dispatch_helper(update, "SNAPSHOT_DATABASE", {})
            if result.get("disposition") not in {"CREATED", "ALREADY_CREATED"}:
                raise BusinessUpdateCoordinatorError(
                    "BUSINESS_SNAPSHOT_RECEIPT_INVALID",
                    "business database snapshot receipt is invalid",
                )
            self.journal.transition(
                update["updateUid"],
                "MIGRATING_DATA",
                step="INSTALL_TARGET",
                fields={"snapshot_created_at": _format_utc(self._utc_now())},
            )
            return
        if step == "INSTALL_TARGET":
            self._dispatch_helper(
                update,
                "INSTALL_TARGET",
                {
                    "releaseUid": update["releaseId"],
                    "packageSha256": update["packageSha256"],
                    "versionName": update["versionName"],
                    "releaseSequence": update["releaseSequence"],
                },
            )
            self.journal.transition(
                update["updateUid"],
                "ACTIVATING",
                expected_states={"MIGRATING_DATA"},
                step="ACTIVATE_TARGET",
            )
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STEP_INVALID", "business migration step is invalid"
        )

    def _activate_target(self, update: Mapping[str, Any]) -> None:
        if update["step"] == "ACTIVATE_TARGET":
            result = self._dispatch_helper(
                update,
                "ACTIVATE_TARGET",
                {"releaseUid": update["releaseId"]},
            )
            previous = result.get("previousReleaseUid")
            if previous != update["previousReleaseId"]:
                raise BusinessUpdateCoordinatorError(
                    "BUSINESS_RELEASE_BASELINE_CHANGED",
                    "business release baseline changed during activation",
                )
            self.journal.transition(
                update["updateUid"], "ACTIVATING", step="START_TARGET"
            )
            return
        if update["step"] == "START_TARGET":
            self._dispatch_helper(update, "START_TARGET", {})
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_TARGET",
                expected_states={"ACTIVATING"},
                step="VERIFY_TARGET",
            )
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STEP_INVALID", "business activation step is invalid"
        )

    def _verify_target(self, update: Mapping[str, Any]) -> None:
        if update["step"] == "VERIFY_TARGET":
            health = self._require_proxy_business_health_with_restart_grace(
                update,
                expected_version=update["versionName"],
            )
            if health is None:
                return
            status = self._dispatch_helper(update, "INSPECT_TARGET", {})
            marker = status.get("currentRelease")
            if (
                status.get("currentReleaseUid") != update["releaseId"]
                or status.get("bridgeBusinessRuntimeState") != "INACTIVE"
                or not isinstance(marker, Mapping)
                or marker.get("packageSha256") != update["packageSha256"]
                or marker.get("versionName") != update["versionName"]
                or marker.get("releaseSequence") != update["releaseSequence"]
            ):
                raise BusinessUpdateCoordinatorError(
                    "BUSINESS_TARGET_IDENTITY_MISMATCH",
                    "running business release identity differs from the signed target",
                )
            evidence = canonical_local_payload_sha256(
                {
                    "updateUid": update["updateUid"],
                    "releaseId": update["releaseId"],
                    "packageSha256": update["packageSha256"],
                    "runtimeInstanceUid": health["runtimeInstanceUid"],
                    "status": "READY",
                }
            )
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_TARGET",
                step="RELEASE_TARGET",
                fields={
                    "installed_release_id": update["releaseId"],
                    "installed_version_name": update["versionName"],
                    "installed_release_sequence": update["releaseSequence"],
                    "installed_package_sha256": update["packageSha256"],
                    "result_evidence_sha256": evidence,
                    "last_error_code": None,
                    "last_error_message": None,
                },
            )
            return
        if update["step"] == "RELEASE_TARGET":
            self._release_or_confirm_open(update, outcome="SUCCEEDED")
            self.journal.transition(
                update["updateUid"],
                "OBSERVING",
                expected_states={"VERIFYING_TARGET"},
                step="OBSERVE_TARGET",
                fields={"observation_started_at": _format_utc(self._utc_now())},
            )
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STEP_INVALID", "business target verification step is invalid"
        )

    def _observe_target(self, update: Mapping[str, Any]) -> None:
        try:
            health = self._require_proxy_business_health_with_restart_grace(
                update,
                expected_version=update["versionName"],
            )
        except BusinessUpdateCoordinatorError as error:
            self._begin_rollback(update, error.code, str(error))
            return
        if health is None:
            return
        if self._elapsed(update.get("observationStartedAt")) < self._policy_seconds(
            update,
            "observationWindowSeconds",
            self._observation_seconds,
        ):
            return
        evidence = canonical_local_payload_sha256(
            {
                "updateUid": update["updateUid"],
                "outcome": "SUCCEEDED",
                "releaseId": update["releaseId"],
                "packageSha256": update["packageSha256"],
                "observationStartedAt": update["observationStartedAt"],
                "completedAt": _format_utc(self._utc_now()),
            }
        )
        self.journal.transition(
            update["updateUid"],
            "SUCCEEDED",
            expected_states={"OBSERVING"},
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": None,
                "last_error_message": None,
            },
        )
        self._attempt_terminal_artifact_cleanup(update["updateUid"])

    def _begin_rollback(
        self, update: Mapping[str, Any], error_code: str, error_message: str
    ) -> None:
        baseline_kind = update.get("baselineKind")
        previous = update.get("previousReleaseId")
        previous_version = update.get("previousVersionName")
        if baseline_kind == BASELINE_BUSINESS_RELEASE and not isinstance(
            previous, str
        ):
            self._fail_locked(update, "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE", error_message)
            return
        if baseline_kind == BASELINE_IMAGE_BRIDGE and (
            not isinstance(previous_version, str) or not previous_version
        ):
            self._fail_locked(
                update,
                "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                error_message,
            )
            return
        if baseline_kind not in {
            BASELINE_BUSINESS_RELEASE,
            BASELINE_IMAGE_BRIDGE,
        }:
            self._fail_locked(
                update,
                "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                error_message,
            )
            return
        status = self.safety_store.get_status()
        next_step = "WAIT_ROLLBACK_IDLE"
        if status.get("jobGateState") == "OPEN":
            status = self.safety_store.transition_job_gate(
                "DRAINING",
                owner_update_uid=update["updateUid"],
                maintenance_type=MAINTENANCE_TYPE,
            )
        elif (
            status.get("jobGateState") == "MAINTENANCE"
            and status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken")
            == update.get("maintenanceFenceToken")
        ):
            next_step = "STOP_TARGET"
        elif (
            status.get("jobGateState") == "LOCKED"
            and status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken")
            == update.get("maintenanceFenceToken")
        ):
            if status.get("maintenancePhase") == "DRAINING":
                next_step = "WAIT_ROLLBACK_IDLE"
            else:
                try:
                    status = self.safety_store.resume_update_maintenance(
                        update["updateUid"],
                        update["maintenanceFenceToken"],
                        maintenance_type=MAINTENANCE_TYPE,
                    )
                except UpdaterStoreError as error:
                    self._fail_locked(
                        update,
                        "BUSINESS_MAINTENANCE_RECOVERY_FAILED",
                        str(error),
                    )
                    return
                next_step = "STOP_TARGET"
        elif status.get("jobGateState") != "DRAINING" or (
            status.get("maintenanceOwnerUid") != update["updateUid"]
            or status.get("maintenanceFenceToken")
            != update.get("maintenanceFenceToken")
        ):
            self._fail_locked(
                update,
                "BUSINESS_MAINTENANCE_OWNERSHIP_LOST",
                "business update no longer owns its maintenance fence",
            )
            return
        fence = status.get("maintenanceFenceToken")
        self.journal.transition(
            update["updateUid"],
            "ROLLING_BACK",
            step=next_step,
            fields={
                "maintenance_fence_token": fence,
                "drain_started_at": _format_utc(self._utc_now()),
                "last_error_code": _stable_error_code(error_code),
                "last_error_message": error_message,
            },
        )

    def _rollback(self, update: Mapping[str, Any]) -> None:
        step = update["step"]
        if step == "WAIT_ROLLBACK_IDLE":
            status = self._resume_drain_if_needed(update)
            if (
                status.get("jobGateState") == "MAINTENANCE"
                and status.get("maintenanceOwnerUid") == update["updateUid"]
                and status.get("maintenanceFenceToken")
                == update.get("maintenanceFenceToken")
            ):
                self.journal.transition(
                    update["updateUid"], "ROLLING_BACK", step="STOP_TARGET"
                )
                return
            if self._elapsed(update.get("drainStartedAt")) >= self._policy_seconds(
                update,
                "drainTimeoutSeconds",
                self._drain_timeout_seconds,
            ):
                self._fail_locked(
                    update,
                    "BUSINESS_ROLLBACK_DRAIN_TIMEOUT",
                    "business rollback could not obtain an idle device",
                )
                return
            if (
                status.get("jobGateState") != "DRAINING"
                or status.get("activeJobPermitCount") != 0
                or status.get("unreconciledPhysicalActionCount") != 0
            ):
                return
            try:
                self.safety_store.transition_job_gate("MAINTENANCE")
            except UpdaterStoreError as error:
                if error.code == "JOB_DRAIN_INCOMPLETE":
                    return
                raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
            self.journal.transition(
                update["updateUid"], "ROLLING_BACK", step="STOP_TARGET"
            )
            return
        if step == "STOP_TARGET":
            self._dispatch_helper(update, "STOP_FOR_ROLLBACK", {})
            next_step = (
                "RESTORE_DATABASE"
                if update.get("snapshotCreatedAt") is not None
                else "ACTIVATE_ROLLBACK"
            )
            self.journal.transition(update["updateUid"], "ROLLING_BACK", step=next_step)
            return
        if step == "RESTORE_DATABASE":
            self._dispatch_helper(update, "RESTORE_DATABASE", {})
            self.journal.transition(
                update["updateUid"],
                "ROLLING_BACK",
                step="ACTIVATE_ROLLBACK",
                fields={"database_restored": 1},
            )
            return
        if step == "ACTIVATE_ROLLBACK":
            if update.get("baselineKind") == BASELINE_IMAGE_BRIDGE:
                self._dispatch_helper(
                    update,
                    "DEACTIVATE_TARGET_FOR_BRIDGE",
                    {"releaseUid": update["releaseId"]},
                )
            else:
                self._dispatch_helper(
                    update,
                    "ACTIVATE_ROLLBACK",
                    {"releaseUid": update["previousReleaseId"]},
                )
            self.journal.transition(
                update["updateUid"], "ROLLING_BACK", step="START_ROLLBACK"
            )
            return
        if step == "START_ROLLBACK":
            action_kind = (
                "START_BRIDGE_ROLLBACK"
                if update.get("baselineKind") == BASELINE_IMAGE_BRIDGE
                else "START_ROLLBACK"
            )
            self._dispatch_helper(update, action_kind, {})
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_ROLLBACK",
                expected_states={"ROLLING_BACK"},
                step="VERIFY_ROLLBACK",
            )
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STEP_INVALID", "business rollback step is invalid"
        )

    def _verify_rollback(self, update: Mapping[str, Any]) -> None:
        if update["step"] == "VERIFY_ROLLBACK":
            health = None
            if self._restart_health_update_uid == update["updateUid"]:
                previous_version = update.get("previousVersionName")
                if not isinstance(previous_version, str) or not previous_version:
                    raise BusinessUpdateCoordinatorError(
                        "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                        "business rollback version is unavailable",
                    )
                health = self._require_proxy_business_health_with_restart_grace(
                    update,
                    expected_version=previous_version,
                )
                if health is None:
                    return
            status = self._dispatch_helper(update, "INSPECT_ROLLBACK", {})
            if update.get("baselineKind") == BASELINE_IMAGE_BRIDGE:
                previous_version = update.get("previousVersionName")
                if not isinstance(previous_version, str) or not previous_version:
                    raise BusinessUpdateCoordinatorError(
                        "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE",
                        "image bridge rollback version is unavailable",
                    )
                if (
                    status.get("currentReleaseUid") is not None
                    or status.get("businessRuntimeState") != "INACTIVE"
                    or status.get("bridgeBusinessRuntimeState") != "ACTIVE"
                ):
                    raise BusinessUpdateCoordinatorError(
                        "BUSINESS_ROLLBACK_IDENTITY_MISMATCH",
                        "image bridge rollback identity could not be confirmed",
                    )
                if health is None:
                    health = self._require_proxy_business_health(
                        expected_version=previous_version
                    )
                evidence = canonical_local_payload_sha256(
                    {
                        "updateUid": update["updateUid"],
                        "outcome": "ROLLED_BACK",
                        "baselineKind": BASELINE_IMAGE_BRIDGE,
                        "runtimeInstanceUid": health["runtimeInstanceUid"],
                        "databaseRestored": bool(update.get("databaseRestored")),
                    }
                )
                self.journal.transition(
                    update["updateUid"],
                    "VERIFYING_ROLLBACK",
                    step="RELEASE_ROLLBACK",
                    fields={
                        "installed_release_id": None,
                        "installed_version_name": None,
                        "installed_release_sequence": None,
                        "installed_package_sha256": None,
                        "result_evidence_sha256": evidence,
                    },
                )
                return
            marker = status.get("currentRelease")
            if (
                status.get("currentReleaseUid") != update["previousReleaseId"]
                or not isinstance(marker, Mapping)
                or marker.get("releaseUid") != update["previousReleaseId"]
                or marker.get("versionName") != update.get("previousVersionName")
                or marker.get("releaseSequence")
                != update.get("previousReleaseSequence")
                or marker.get("packageSha256")
                != update.get("previousPackageSha256")
            ):
                raise BusinessUpdateCoordinatorError(
                    "BUSINESS_ROLLBACK_IDENTITY_MISMATCH",
                    "restored business release identity could not be confirmed",
                )
            if health is None:
                health = self._require_proxy_business_health(
                    expected_version=marker["versionName"]
                )
            evidence = canonical_local_payload_sha256(
                {
                    "updateUid": update["updateUid"],
                    "outcome": "ROLLED_BACK",
                    "releaseId": update["previousReleaseId"],
                    "runtimeInstanceUid": health["runtimeInstanceUid"],
                    "databaseRestored": bool(update.get("databaseRestored")),
                }
            )
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_ROLLBACK",
                step="RELEASE_ROLLBACK",
                fields={
                    "installed_release_id": update["previousReleaseId"],
                    "installed_version_name": marker["versionName"],
                    "installed_release_sequence": marker["releaseSequence"],
                    "installed_package_sha256": marker["packageSha256"],
                    "result_evidence_sha256": evidence,
                },
            )
            return
        if update["step"] == "RELEASE_ROLLBACK":
            self._release_or_confirm_open(update, outcome="ROLLED_BACK")
            self.journal.transition(
                update["updateUid"],
                "ROLLED_BACK",
                expected_states={"VERIFYING_ROLLBACK"},
                step="COMPLETE",
                fields={"reconciliation_required": 0},
            )
            self._attempt_terminal_artifact_cleanup(update["updateUid"])
            return
        raise BusinessUpdateCoordinatorError(
            "BUSINESS_UPDATE_STEP_INVALID", "business rollback verification is invalid"
        )

    def _dispatch_helper(
        self,
        update: Mapping[str, Any],
        action_kind: str,
        extra_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        from business_update_store import BUSINESS_ACTION_POLICIES

        action_uid = self._new_uid()
        helper_action = BUSINESS_ACTION_POLICIES[action_kind][1]
        payload = {
            "updateUid": update["updateUid"],
            "actionUid": action_uid,
            **dict(extra_payload),
        }
        try:
            self.journal.prepare_action(
                update_uid=update["updateUid"],
                action_uid=action_uid,
                action_kind=action_kind,
                payload=payload,
            )
            self.journal.begin_action(action_uid)
            result = self.helper_client.request(helper_action, payload)
        except LocalControlRemoteError as error:
            self.journal.finish_action(
                action_uid,
                "FAILED",
                error_code=_stable_error_code(error.code),
                error_message=error.message,
            )
            raise BusinessUpdateCoordinatorError(error.code, error.message) from error
        except LocalControlUnavailable as error:
            self.journal.finish_action(
                action_uid,
                "UNKNOWN",
                error_code="PRIVILEGED_ACTION_RESULT_UNKNOWN",
                error_message="business helper result could not be confirmed",
            )
            raise BusinessUpdateCoordinatorError(
                "PRIVILEGED_ACTION_RESULT_UNKNOWN",
                "business helper result could not be confirmed",
                uncertain=True,
            ) from error
        except BusinessUpdateStoreError as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        finished = self.journal.finish_action(
            action_uid, "SUCCEEDED", response=result
        )
        if not isinstance(finished.get("responseDigestSha256"), str):
            raise BusinessUpdateCoordinatorError(
                "PRIVILEGED_ACTION_RECEIPT_INVALID",
                "business helper result has no durable digest",
            )
        return result

    def _require_proxy_business_health(
        self, *, expected_version: str | None
    ) -> dict[str, Any]:
        try:
            health = self.business_client.request("GET_STATUS", {})
        except (LocalControlUnavailable, LocalControlRemoteError, ValueError) as error:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_RUNTIME_NOT_READY",
                "business runtime health could not be confirmed",
            ) from error
        required = {
            "component": "BUSINESS_RUNTIME",
            "status": "READY",
            "managementArchitectureGeneration": "LOCAL_PROXY",
            "cloudConnectionOwner": "COMMUNICATION_AGENT",
            "jobPermitEnforced": True,
            "cloudProxyIngressEnabled": True,
        }
        if any(health.get(name) != value for name, value in required.items()):
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_RUNTIME_NOT_READY",
                "business runtime has not reached the permanent proxy posture",
            )
        if expected_version is not None and health.get("releaseVersion") != expected_version:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_RUNTIME_VERSION_MISMATCH",
                "business runtime reported a different release version",
            )
        instance = health.get("runtimeInstanceUid")
        if not isinstance(instance, str):
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_RUNTIME_NOT_READY",
                "business runtime instance identity is missing",
            )
        return health

    def _require_proxy_business_health_with_restart_grace(
        self,
        update: Mapping[str, Any],
        *,
        expected_version: str,
    ) -> dict[str, Any] | None:
        try:
            health = self._require_proxy_business_health(
                expected_version=expected_version
            )
        except BusinessUpdateCoordinatorError as error:
            if (
                error.code == "BUSINESS_RUNTIME_NOT_READY"
                and self._restart_health_update_uid == update["updateUid"]
                and self._restart_health_deadline is not None
                and self._monotonic() < self._restart_health_deadline
            ):
                if not self._restart_health_wait_logged:
                    logger.info(
                        "waiting for business runtime after updater restart: "
                        "updateUid=%s state=%s step=%s",
                        update["updateUid"],
                        update["state"],
                        update["step"],
                    )
                    self._restart_health_wait_logged = True
                return None
            self._clear_restart_health_grace(update["updateUid"])
            raise
        self._clear_restart_health_grace(update["updateUid"])
        return health

    def _clear_restart_health_grace(self, update_uid: str) -> None:
        if self._restart_health_update_uid != update_uid:
            return
        self._restart_health_update_uid = None
        self._restart_health_deadline = None
        self._restart_health_wait_logged = False

    def _resume_drain_if_needed(self, update: Mapping[str, Any]) -> dict[str, Any]:
        status = self.safety_store.get_status()
        if (
            status.get("jobGateState") == "LOCKED"
            and status.get("maintenancePhase") == "DRAINING"
            and status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken") == update["maintenanceFenceToken"]
            and status.get("activeJobPermitCount") == 0
            and status.get("unreconciledPhysicalActionCount") == 0
        ):
            try:
                return self.safety_store.resume_update_drain(
                    update["updateUid"],
                    update["maintenanceFenceToken"],
                    maintenance_type=MAINTENANCE_TYPE,
                )
            except UpdaterStoreError as error:
                raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        return status

    def _resume_owned_maintenance_if_needed(
        self,
        update: Mapping[str, Any],
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (
            status.get("jobGateState") == "LOCKED"
            and status.get("maintenancePhase") == "MAINTENANCE"
            and status.get("maintenanceOwnerUid") == update["updateUid"]
            and status.get("maintenanceFenceToken")
            == update.get("maintenanceFenceToken")
        ):
            try:
                return self.safety_store.resume_update_maintenance(
                    update["updateUid"],
                    update["maintenanceFenceToken"],
                    maintenance_type=MAINTENANCE_TYPE,
                )
            except UpdaterStoreError as error:
                raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        return dict(status)

    def _defer_before_mutation(self, update: Mapping[str, Any], code: str) -> None:
        evidence = canonical_local_payload_sha256(
            {"updateUid": update["updateUid"], "outcome": "DEFERRED", "errorCode": code}
        )
        try:
            self.safety_store.abort_update_drain(
                update["updateUid"],
                update["maintenanceFenceToken"],
                evidence_sha256=evidence,
                maintenance_type=MAINTENANCE_TYPE,
            )
        except UpdaterStoreError as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        self.journal.transition(
            update["updateUid"],
            "DEFERRED",
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": code,
                "last_error_message": "device did not become idle within 30 minutes",
            },
        )

    def _defer_without_gate(self, update: Mapping[str, Any], code: str) -> None:
        evidence = canonical_local_payload_sha256(
            {"updateUid": update["updateUid"], "outcome": "DEFERRED", "errorCode": code}
        )
        self.journal.transition(
            update["updateUid"],
            "DEFERRED",
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": _stable_error_code(code),
                "last_error_message": "another maintenance action owns the device",
            },
        )

    def _reject_after_drain(self, update: Mapping[str, Any], code: str) -> None:
        evidence = canonical_local_payload_sha256(
            {"updateUid": update["updateUid"], "outcome": "REJECTED", "errorCode": code}
        )
        try:
            self.safety_store.release_update_maintenance(
                update["updateUid"],
                update["maintenanceFenceToken"],
                outcome="ROLLED_BACK",
                evidence_sha256=evidence,
                maintenance_type=MAINTENANCE_TYPE,
            )
        except UpdaterStoreError as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error
        self.journal.transition(
            update["updateUid"],
            "REJECTED",
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": code,
                "last_error_message": "business rollback baseline is not eligible",
            },
        )

    def _release_or_confirm_open(
        self, update: Mapping[str, Any], *, outcome: str
    ) -> None:
        status = self.safety_store.get_status()
        if status.get("jobGateState") == "OPEN" and status.get("maintenanceOwnerUid") is None:
            return
        try:
            self.safety_store.release_update_maintenance(
                update["updateUid"],
                update["maintenanceFenceToken"],
                outcome=outcome,
                evidence_sha256=update["resultEvidenceSha256"],
                maintenance_type=MAINTENANCE_TYPE,
            )
        except UpdaterStoreError as error:
            raise BusinessUpdateCoordinatorError(error.code, str(error)) from error

    def _reconcile_terminal_artifact_cleanup(self) -> bool | None:
        for update_uid in self.journal.list_updates_eligible_for_artifact_cleanup():
            if update_uid in self._terminal_cleanup_attempted:
                continue
            outcome = self._attempt_terminal_artifact_cleanup(update_uid)
            # The downloader can still be unwinding its final I/O after it has
            # persisted a terminal rejection. Sleep before retrying that case.
            return False if outcome == "DEFERRED" else True
        return None

    def _attempt_terminal_artifact_cleanup(self, update_uid: str) -> str:
        update = self.journal.get_update(update_uid)
        if (
            update is None
            or update.get("state") not in BUSINESS_UPDATE_ARTIFACT_CLEANUP_STATES
        ):
            self._terminal_cleanup_attempted.add(update_uid)
            return "SKIPPED"

        outcome = "COMPLETE"
        try:
            self.package_stager.cleanup(update_uid)
        except (BusinessPackageStageError, OSError) as error:
            code = getattr(error, "code", "BUSINESS_STAGING_CLEANUP_FAILED")
            logger.warning(
                "business update staging cleanup will retry after updater restart: "
                "updateUid=%s code=%s",
                update_uid,
                code,
            )
            outcome = "FAILED"

        downloader = self.remote_downloader
        if downloader is not None:
            try:
                if not downloader.cleanup_terminal(update_uid):
                    return "DEFERRED"
            except (BusinessUpdateStoreError, OSError, ValueError) as error:
                code = getattr(error, "code", "BUSINESS_DOWNLOAD_CLEANUP_FAILED")
                logger.warning(
                    "business download cleanup will retry after updater restart: "
                    "updateUid=%s code=%s",
                    update_uid,
                    code,
                )
                outcome = "FAILED"

        # Unsafe or unreadable paths remain visible to the strict readiness
        # audit. Retry them after process restart without emitting a warning on
        # every poll cycle.
        self._terminal_cleanup_attempted.add(update_uid)
        return outcome

    def _fail_locked(
        self, update: Mapping[str, Any], code: str, message: str
    ) -> None:
        evidence = canonical_local_payload_sha256(
            {"updateUid": update["updateUid"], "outcome": "FAILED_LOCKED", "errorCode": code}
        )
        failed = self.journal.transition(
            update["updateUid"],
            "FAILED_LOCKED",
            step="COMPLETE",
            fields={
                "result_evidence_sha256": evidence,
                "last_error_code": _stable_error_code(code),
                "last_error_message": message,
                "reconciliation_required": 1,
            },
        )
        fence = failed.get("maintenanceFenceToken")
        if isinstance(fence, int) and not isinstance(fence, bool):
            try:
                self.safety_store.lock_update_maintenance(
                    failed["updateUid"],
                    fence,
                    reason_code="BUSINESS_UPDATE_FAILED",
                    maintenance_type=MAINTENANCE_TYPE,
                )
            except UpdaterStoreError:
                logger.exception("failed to retain business update safety lock")

    def _record_step_error(
        self, update: dict[str, Any], error: BusinessUpdateCoordinatorError
    ) -> None:
        current = self.journal.get_update(update["updateUid"])
        if current is None or current["state"] in BUSINESS_UPDATE_TERMINAL_STATES:
            return
        if self.journal.get_pending_cancellation(current["updateUid"]) is not None:
            logger.warning(
                "business cancellation cleanup will retry: updateUid=%s code=%s",
                current["updateUid"],
                _stable_error_code(error.code),
            )
            return
        retryable = {
            "PRIVILEGED_ACTION_RESULT_UNKNOWN",
            "HELPER_AUTHORIZATION_UNAVAILABLE",
            "HELPER_BUSY",
            "SERVICE_STOPPING",
        }
        if error.code in retryable or error.uncertain:
            if (
                current["state"] not in {"ROLLING_BACK", "VERIFYING_ROLLBACK"}
                or current.get("errorCode") is None
            ):
                self.journal.transition(
                    current["updateUid"],
                    current["state"],
                    step=current["step"],
                    fields={
                        "last_error_code": _stable_error_code(error.code),
                        "last_error_message": str(error),
                    },
                )
            return
        if current["state"] == "PACKAGE_READY":
            self._defer_without_gate(current, error.code)
            return
        if (
            current["state"] == "MIGRATING_DATA"
            and current["step"] == "INSPECT_BASELINE"
            and current.get("previousReleaseId") is None
        ):
            self._reject_after_drain(current, _stable_error_code(error.code))
            return
        if current["state"] in {"MIGRATING_DATA", "ACTIVATING", "VERIFYING_TARGET"}:
            self._begin_rollback(current, error.code, str(error))
            return
        if current["state"] in {"ROLLING_BACK", "VERIFYING_ROLLBACK"}:
            self._fail_locked(current, error.code, str(error))
            return
        self._fail_locked(current, error.code, str(error))

    def _elapsed(self, value: object) -> float:
        if not isinstance(value, str):
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_UPDATE_DEADLINE_INVALID", "business update timestamp is missing"
            )
        try:
            started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_UPDATE_DEADLINE_INVALID", "business update timestamp is invalid"
            ) from error
        now = self._utc_now()
        if started.tzinfo is None or now.tzinfo is None:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_UPDATE_DEADLINE_INVALID", "business update clock is not aware"
            )
        return max(0.0, (now - started).total_seconds())

    def _policy_seconds(
        self,
        update: Mapping[str, Any],
        field: str,
        default: int,
    ) -> int:
        remote = self.journal.get_remote_update(update["updateUid"])
        if remote is None:
            return default
        value = remote.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise BusinessUpdateCoordinatorError(
                "BUSINESS_UPDATE_POLICY_INVALID",
                "business update frozen policy is invalid",
            )
        return value

    def _new_uid(self) -> str:
        value = self._uuid_factory()
        if not isinstance(value, uuid.UUID) or value.version != 4:
            raise RuntimeError("business update UUID factory must return UUIDv4")
        return str(value)


def _stable_error_code(value: object) -> str:
    if isinstance(value, str) and 1 <= len(value) <= 64 and all(
        character.isupper() or character.isdigit() or character == "_"
        for character in value
    ):
        return value
    return "BUSINESS_UPDATE_STEP_FAILED"


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("business update clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BusinessUpdateCoordinatorError(
            "REQUEST_INVALID", "download authorization expiry is invalid"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BusinessUpdateCoordinatorError(
            "REQUEST_INVALID", "download authorization expiry is invalid"
        ) from error
    return parsed


__all__ = [
    "BusinessUpdateCoordinator",
    "BusinessUpdateCoordinatorError",
    "MAINTENANCE_TYPE",
]

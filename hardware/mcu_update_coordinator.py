"""Permanent, restart-safe orchestration for candidate MCU firmware updates."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from datetime import datetime, timezone

from local_control import (
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
    canonical_local_payload_sha256,
)
from mcu_update_package import McuFirmwarePackageStager, McuPackageStageError
from mcu_update_store import (
    MCU_UPDATE_TERMINAL_STATES,
    McuUpdateStore,
    McuUpdateStoreError,
)
from updater_store import UpdaterStore, UpdaterStoreError


logger = logging.getLogger("mcu-update-coordinator")

BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
BUSINESS_HELPER_PROTOCOL_NAME = "ecobin.business-activation-helper.control"
MCU_HELPER_PROTOCOL_NAME = "ecobin.mcu-flash-helper.control"
DEFAULT_BUSINESS_SOCKET = "/run/ecobin/business/control.sock"
DEFAULT_BUSINESS_HELPER_SOCKET = (
    "/run/ecobin/privileged/business-activation-candidate.sock"
)
DEFAULT_MCU_HELPER_SOCKET = "/run/ecobin/privileged/mcu-flash-candidate.sock"
TARGET_ATTEMPT_LIMIT = 3
ROLLBACK_ATTEMPT_LIMIT = 3
DRAIN_TIMEOUT_SECONDS = 30 * 60


class McuUpdateCoordinatorError(RuntimeError):
    def __init__(self, code: str, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class McuUpdateCoordinator:
    """Advance one durable MCU update by one safely repeatable step at a time."""

    def __init__(
        self,
        *,
        safety_store: UpdaterStore,
        journal: McuUpdateStore,
        package_stager: McuFirmwarePackageStager,
        business_client: Any | None = None,
        business_helper_client: Any | None = None,
        mcu_helper_client: Any | None = None,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        utc_now: Callable[[], datetime] | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("MCU update poll interval must be positive")
        self.safety_store = safety_store
        self.journal = journal
        self.package_stager = package_stager
        self.business_client = business_client or LocalControlClient(
            DEFAULT_BUSINESS_SOCKET,
            protocol_name=BUSINESS_PROTOCOL_NAME,
            response_timeout_seconds=15.0,
        )
        self.business_helper_client = business_helper_client or LocalControlClient(
            DEFAULT_BUSINESS_HELPER_SOCKET,
            protocol_name=BUSINESS_HELPER_PROTOCOL_NAME,
            response_timeout_seconds=220.0,
        )
        self.mcu_helper_client = mcu_helper_client or LocalControlClient(
            DEFAULT_MCU_HELPER_SOCKET,
            protocol_name=MCU_HELPER_PROTOCOL_NAME,
            response_timeout_seconds=370.0,
        )
        self._uuid_factory = uuid_factory
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self.failure: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.set()
        self.failure = None
        self._thread = threading.Thread(
            target=self._run,
            name="mcu-update-coordinator",
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
        expected = {
            "updateUid",
            "commandUid",
            "targetPackageSha256",
            "rollbackPackageSha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise McuUpdateCoordinatorError(
                "REQUEST_INVALID",
                "local MCU candidate request fields are invalid",
            )
        with self._queue_lock:
            try:
                duplicate = self.journal.preflight_update_request(payload)
                if duplicate is not None:
                    return duplicate
                safety_status = self.safety_store.get_status()
                if (
                    safety_status.get("candidateActivationState") != "ACTIVE"
                    or safety_status.get("jobGateState") != "OPEN"
                    or safety_status.get("maintenanceOwnerUid") is not None
                ):
                    raise McuUpdateCoordinatorError(
                        "MCU_UPDATE_BUSY",
                        "device is not open for a new local MCU update",
                    )
                staged = self.package_stager.stage_pair(
                    update_uid=payload["updateUid"],
                    expected_target_package_sha256=payload[
                        "targetPackageSha256"
                    ],
                    expected_rollback_package_sha256=payload[
                        "rollbackPackageSha256"
                    ],
                )
                result = self.journal.create_update(
                    update_uid=payload["updateUid"],
                    command_uid=payload["commandUid"],
                    request_payload=dict(payload),
                    target_package_sha256=staged.target_package_sha256,
                    target_manifest=staged.target_manifest,
                    rollback_package_sha256=staged.rollback_package_sha256,
                    rollback_manifest=staged.rollback_manifest,
                    handoff_uid=self._new_uid(),
                )
            except ValueError as error:
                raise McuUpdateCoordinatorError(
                    "REQUEST_INVALID",
                    "local MCU candidate request values are invalid",
                ) from error
            except (McuPackageStageError, McuUpdateStoreError) as error:
                raise McuUpdateCoordinatorError(error.code, str(error)) from error
        self.wake()
        return result

    def get_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"updateUid"}:
            raise McuUpdateCoordinatorError(
                "REQUEST_INVALID",
                "MCU update query fields are invalid",
            )
        try:
            result = self.journal.get_update(payload["updateUid"])
        except McuUpdateStoreError as error:
            raise McuUpdateCoordinatorError(error.code, str(error)) from error
        if result is None:
            raise McuUpdateCoordinatorError(
                "MCU_UPDATE_NOT_FOUND",
                "MCU update was not found",
            )
        return result

    def get_status(self) -> dict[str, Any]:
        return self.journal.get_status()

    def authorize_privileged_action(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            update = self.journal.get_update(payload.get("updateUid"))
            if update is None:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_NOT_FOUND",
                    "MCU update was not found",
                )
            fence_token = update.get("maintenanceFenceToken")
            self.safety_store.require_update_maintenance(
                update["updateUid"],
                fence_token,
                allow_recovery_lock=True,
            )
            return self.journal.authorize_action(payload)
        except (McuUpdateStoreError, UpdaterStoreError) as error:
            raise McuUpdateCoordinatorError(error.code, str(error)) from error

    def process_once(self) -> bool:
        if not self._process_lock.acquire(blocking=False):
            return False
        try:
            pending_release = self.journal.get_pending_release()
            if pending_release is not None:
                try:
                    self._release_completed_update(pending_release)
                except (
                    McuUpdateCoordinatorError,
                    McuUpdateStoreError,
                    UpdaterStoreError,
                ) as error:
                    logger.warning(
                        "MCU update %s maintenance release failed: %s",
                        pending_release["updateUid"],
                        error.code,
                    )
                    return False
                return True
            update = self.journal.get_active_update()
            if update is None:
                return False
            before_state = update["state"]
            before_release = update["releaseState"]
            try:
                self._advance(update)
            except McuUpdateCoordinatorError as error:
                logger.warning(
                    "MCU update %s step %s failed: %s",
                    update["updateUid"],
                    update["state"],
                    error.code,
                )
                self._record_step_error(update, error)
                current = self.journal.get_update(update["updateUid"])
                return bool(
                    current is not None
                    and (
                        current["state"] != before_state
                        or current["releaseState"] != before_release
                    )
                )
            except (McuUpdateStoreError, UpdaterStoreError) as error:
                wrapped = McuUpdateCoordinatorError(error.code, str(error))
                self._record_step_error(update, wrapped)
                current = self.journal.get_update(update["updateUid"])
                return bool(
                    current is not None
                    and (
                        current["state"] != before_state
                        or current["releaseState"] != before_release
                    )
                )
            current = self.journal.get_update(update["updateUid"])
            return bool(
                current is not None
                and (
                    current["state"] != before_state
                    or current["releaseState"] != before_release
                )
            )
        finally:
            self._process_lock.release()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                progressed = self.process_once()
                if progressed:
                    if self._stop_event.wait(0.02):
                        return
                    continue
                self._wake_event.wait(self._poll_seconds)
                self._wake_event.clear()
        except BaseException as error:  # noqa: BLE001 - agent must surface worker death
            self.failure = error
            logger.exception("MCU update coordinator stopped unexpectedly")

    def _advance(self, update: dict[str, Any]) -> None:
        state = update["state"]
        if state == "QUEUED":
            status = self.safety_store.transition_job_gate(
                "DRAINING",
                owner_update_uid=update["updateUid"],
                maintenance_type="MCU_FIRMWARE_UPDATE",
            )
            self.journal.transition(
                update["updateUid"],
                "DRAINING",
                expected_states={"QUEUED"},
                fields={
                    "maintenance_fence_token": status[
                        "maintenanceFenceToken"
                    ]
                },
            )
            return
        if state == "DRAINING":
            status = self.safety_store.get_status()
            if (
                status.get("jobGateState") == "LOCKED"
                and status.get("maintenancePhase") == "DRAINING"
                and status.get("maintenanceOwnerUid") == update["updateUid"]
                and status.get("maintenanceFenceToken")
                == update["maintenanceFenceToken"]
                and status.get("activeJobPermitCount") == 0
                and status.get("unreconciledPhysicalActionCount") == 0
            ):
                self.safety_store.resume_update_drain(
                    update["updateUid"],
                    update["maintenanceFenceToken"],
                )
                return
            if self._drain_expired(update):
                self._finish_pre_hardware(
                    update,
                    outcome="DEFERRED",
                    error_code="BUSINESS_DRAIN_TIMEOUT",
                    error_message="current business work did not drain within 30 minutes",
                )
                return
            if (
                status.get("jobGateState") != "DRAINING"
                or status.get("activeJobPermitCount") != 0
                or status.get("unreconciledPhysicalActionCount") != 0
            ):
                return
            self.journal.transition(
                update["updateUid"],
                "INITIAL_OBSERVE",
                expected_states={"DRAINING"},
            )
            return
        if state == "INITIAL_OBSERVE":
            if self._drain_expired(update):
                self._finish_pre_hardware(
                    update,
                    outcome="DEFERRED",
                    error_code="BUSINESS_DRAIN_TIMEOUT",
                    error_message="pre-update MCU observation did not finish within 30 minutes",
                )
                return
            observation = self._observe_application()
            if not observation["healthy"]:
                self._finish_pre_hardware(
                    update,
                    outcome="REJECTED",
                    error_code="MCU_INITIAL_HEALTH_CHECK_FAILED",
                    error_message="current MCU application is not healthy enough for an update",
                )
                return
            if observation["identitySha256"] != update[
                "rollbackIdentitySha256"
            ]:
                self._finish_pre_hardware(
                    update,
                    outcome="REJECTED",
                    error_code="MCU_ROLLBACK_IDENTITY_MISMATCH",
                    error_message="rollback firmware does not match the installed MCU identity",
                )
                return
            self.journal.transition(
                update["updateUid"],
                "INITIAL_OBSERVE",
                fields={
                    "observation_evidence_sha256": observation[
                        "evidenceSha256"
                    ]
                },
            )
            try:
                self.safety_store.transition_job_gate("MAINTENANCE")
            except UpdaterStoreError as error:
                if error.code == "JOB_DRAIN_INCOMPLETE":
                    return
                raise
            self.journal.transition(
                update["updateUid"],
                "INITIAL_QUIESCE",
                expected_states={"INITIAL_OBSERVE"},
                fields={
                    "observation_evidence_sha256": observation[
                        "evidenceSha256"
                    ]
                },
            )
            return
        if state == "INITIAL_QUIESCE":
            self._quiesce(update, recovery=False)
            self.journal.transition(
                update["updateUid"],
                "STOPPING_TARGET",
                expected_states={"INITIAL_QUIESCE"},
            )
            return
        if state == "STOPPING_TARGET":
            self._dispatch_business_service(update, operation="STOP", source="TARGET")
            self.journal.transition(
                update["updateUid"],
                "FLASHING_TARGET",
                expected_states={"STOPPING_TARGET"},
            )
            return
        if state == "FLASHING_TARGET":
            self._flash(update, source="TARGET")
            self.journal.transition(
                update["updateUid"],
                "STARTING_TARGET_VERIFY",
                expected_states={"FLASHING_TARGET"},
            )
            return
        if state == "STARTING_TARGET_VERIFY":
            self._dispatch_business_service(update, operation="START", source="TARGET")
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_TARGET",
                expected_states={"STARTING_TARGET_VERIFY"},
            )
            return
        if state == "VERIFYING_TARGET":
            self._verify(update, source="TARGET")
            return
        if state == "PREPARING_TARGET_RETRY":
            self._prepare_retry(update, source="TARGET")
            return
        if state == "PREPARING_ROLLBACK":
            self._prepare_retry(update, source="ROLLBACK")
            return
        if state == "STOPPING_ROLLBACK":
            self._dispatch_business_service(
                update,
                operation="STOP",
                source="ROLLBACK",
            )
            self.journal.transition(
                update["updateUid"],
                "FLASHING_ROLLBACK",
                expected_states={"STOPPING_ROLLBACK"},
            )
            return
        if state == "FLASHING_ROLLBACK":
            self._flash(update, source="ROLLBACK")
            self.journal.transition(
                update["updateUid"],
                "STARTING_ROLLBACK_VERIFY",
                expected_states={"FLASHING_ROLLBACK"},
            )
            return
        if state == "STARTING_ROLLBACK_VERIFY":
            self._dispatch_business_service(
                update,
                operation="START",
                source="ROLLBACK",
            )
            self.journal.transition(
                update["updateUid"],
                "VERIFYING_ROLLBACK",
                expected_states={"STARTING_ROLLBACK_VERIFY"},
            )
            return
        if state == "VERIFYING_ROLLBACK":
            self._verify(update, source="ROLLBACK")
            return
        if state == "RECOVERING":
            self._recover_interrupted(update)
            return
        raise McuUpdateCoordinatorError(
            "MCU_UPDATE_STATE_UNSUPPORTED",
            f"MCU update state cannot be processed: {state}",
        )

    def _observe_application(self) -> dict[str, Any]:
        try:
            response = self.business_client.request(
                "OBSERVE_MCU_MAINTENANCE_STATE",
                {},
            )
        except LocalControlRemoteError as error:
            raise McuUpdateCoordinatorError(error.code, error.message) from error
        except LocalControlUnavailable as error:
            raise McuUpdateCoordinatorError(
                "BUSINESS_MAINTENANCE_UNAVAILABLE",
                "business MCU maintenance endpoint is unavailable",
                uncertain=True,
            ) from error
        return _parse_application_evidence(response)

    def _quiesce(self, update: dict[str, Any], *, recovery: bool) -> None:
        action = (
            "QUIESCE_MCU_FOR_RECOVERY"
            if recovery
            else "QUIESCE_MCU_FOR_UPDATE"
        )
        payload = {
            "updateUid": update["updateUid"],
            "handoffUid": update["handoffUid"],
            "expectedObservationSha256": update[
                "observationEvidenceSha256"
            ],
        }
        try:
            response = self.business_client.request(action, payload)
        except LocalControlRemoteError as error:
            uncertain = error.code not in {
                "BUSINESS_RUNTIME_NOT_READY",
                "MCU_MAINTENANCE_BUSY",
                "MCU_MAINTENANCE_NOT_AUTHORIZED",
                "FEATURE_DISABLED",
            }
            raise McuUpdateCoordinatorError(
                error.code,
                error.message,
                uncertain=uncertain,
            ) from error
        except LocalControlUnavailable as error:
            raise McuUpdateCoordinatorError(
                "MCU_QUIESCE_RESULT_UNKNOWN",
                "MCU quiesce result is unknown and requires observed recovery",
                uncertain=True,
            ) from error
        digest = _require_evidence_digest(response, "QUIESCE")
        self.journal.transition(
            update["updateUid"],
            update["state"],
            fields={"quiesce_evidence_sha256": digest},
        )

    def _dispatch_business_service(
        self,
        update: dict[str, Any],
        *,
        operation: str,
        source: str,
    ) -> None:
        action_kind = f"{operation}_BUSINESS_{source}"
        helper_action = (
            "STOP_BUSINESS_RUNTIME"
            if operation == "STOP"
            else "START_BUSINESS_RUNTIME"
        )
        self._dispatch_privileged(
            update,
            action_kind=action_kind,
            helper_action=helper_action,
            client=self.business_helper_client,
            extra_payload={},
        )

    def _flash(self, update: dict[str, Any], *, source: str) -> None:
        attempt = self.journal.record_attempt(update["updateUid"], source)
        try:
            response_digest = self._dispatch_privileged(
                self.journal.get_update(update["updateUid"]) or update,
                action_kind=f"FLASH_{source}",
                helper_action="FLASH_MCU_FIRMWARE",
                client=self.mcu_helper_client,
                extra_payload={"source": source},
            )
        except McuUpdateCoordinatorError as error:
            try:
                self._dispatch_privileged(
                    self.journal.get_update(update["updateUid"]) or update,
                    action_kind="RECOVER_APPLICATION",
                    helper_action="RECOVER_MCU_APPLICATION",
                    client=self.mcu_helper_client,
                    extra_payload={},
                )
            except McuUpdateCoordinatorError as recovery_error:
                raise McuUpdateCoordinatorError(
                    "MCU_APPLICATION_RECOVERY_FAILED",
                    f"{error.code}; {recovery_error.code}",
                    uncertain=True,
                ) from recovery_error
            if error.uncertain:
                raise McuUpdateCoordinatorError(
                    "MCU_FLASH_RESULT_UNKNOWN",
                    "MCU flash result is unknown; actual firmware identity must be observed",
                    uncertain=True,
                ) from error
            response_digest = canonical_local_payload_sha256(
                {
                    "source": source,
                    "attempt": attempt,
                    "result": "FLASH_FAILED_APPLICATION_RECOVERED",
                    "errorCode": error.code,
                }
            )
        self.journal.transition(
            update["updateUid"],
            update["state"],
            fields={"last_flash_evidence_sha256": response_digest},
        )

    def _verify(self, update: dict[str, Any], *, source: str) -> None:
        identity_digest = update[
            "targetIdentitySha256"
            if source == "TARGET"
            else "rollbackIdentitySha256"
        ]
        payload = {
            "updateUid": update["updateUid"],
            "handoffUid": update["handoffUid"],
            "quiesceEvidenceSha256": update["quiesceEvidenceSha256"],
            "expectedFirmwareIdentitySha256": identity_digest,
            "observedFlashEvidenceSha256": update[
                "lastFlashEvidenceSha256"
            ],
        }
        try:
            response = self.business_client.request(
                "VERIFY_MCU_AFTER_UPDATE",
                payload,
            )
            evidence_digest = _require_evidence_digest(response, "VERIFY")
        except LocalControlUnavailable as error:
            raise McuUpdateCoordinatorError(
                "MCU_VERIFICATION_RESULT_UNKNOWN",
                "MCU post-flash verification result is unknown",
            ) from error
        except LocalControlRemoteError as error:
            if error.code in {
                "RESULT_UNKNOWN",
                "SERVICE_STOPPING",
                "BUSINESS_RUNTIME_NOT_READY",
                "MCU_MAINTENANCE_BUSY",
                "MCU_MAINTENANCE_IN_PROGRESS",
            }:
                raise McuUpdateCoordinatorError(
                    error.code,
                    error.message,
                ) from error
            code = error.code
            message = error.message
            current = self.journal.get_update(update["updateUid"]) or update
            if source == "TARGET":
                next_state = (
                    "PREPARING_TARGET_RETRY"
                    if current["targetAttemptCount"] < TARGET_ATTEMPT_LIMIT
                    else "PREPARING_ROLLBACK"
                )
            else:
                if current["rollbackAttemptCount"] >= ROLLBACK_ATTEMPT_LIMIT:
                    raise McuUpdateCoordinatorError(code, message) from error
                next_state = "PREPARING_ROLLBACK"
            self.journal.transition(
                update["updateUid"],
                next_state,
                expected_states={update["state"]},
                fields={
                    "last_error_code": _stable_error_code(code),
                    "last_error_message": str(message),
                },
            )
            return
        completion_digest = canonical_local_payload_sha256(
            {
                "source": source,
                "flashEvidenceSha256": update["lastFlashEvidenceSha256"],
                "verificationEvidenceSha256": evidence_digest,
            }
        )
        outcome = "SUCCEEDED" if source == "TARGET" else "ROLLED_BACK"
        completed = self.journal.complete_update(
            update["updateUid"],
            outcome,
            completion_digest,
        )
        self._release_completed_update(completed)

    def _prepare_retry(self, update: dict[str, Any], *, source: str) -> None:
        observation = self._observe_application()
        observed_identity = observation["identitySha256"]
        if observed_identity is None:
            raise McuUpdateCoordinatorError(
                "MCU_RECOVERY_IDENTITY_UNAVAILABLE",
                "running MCU identity is unavailable before another flash",
            )
        if observed_identity not in {
            update["targetIdentitySha256"],
            update["rollbackIdentitySha256"],
        }:
            raise McuUpdateCoordinatorError(
                "MCU_RECOVERY_IDENTITY_UNEXPECTED",
                "running MCU identity is neither the signed target nor rollback identity",
            )
        status = self.safety_store.get_status()
        if status["jobGateState"] == "LOCKED":
            self.safety_store.resume_update_maintenance(
                update["updateUid"],
                update["maintenanceFenceToken"],
            )
        handoff_uid = self._new_uid()
        updated = self.journal.rotate_handoff(update["updateUid"], handoff_uid)
        updated = self.journal.transition(
            update["updateUid"],
            update["state"],
            fields={
                "observation_evidence_sha256": observation[
                    "evidenceSha256"
                ]
            },
        )
        self._quiesce(updated, recovery=True)
        next_state = (
            "STOPPING_TARGET" if source == "TARGET" else "STOPPING_ROLLBACK"
        )
        self.journal.transition(
            update["updateUid"],
            next_state,
            expected_states={update["state"]},
        )

    def _recover_interrupted(self, update: dict[str, Any]) -> None:
        try:
            self._dispatch_business_service(
                update,
                operation="START",
                source="TARGET",
            )
        except McuUpdateCoordinatorError:
            # An already-active control-only service is reported as success by
            # the fixed systemctl primitive.  Any other failure is retryable.
            raise
        observation = self._observe_application()
        observed_identity = observation["identitySha256"]
        recovery_digest = canonical_local_payload_sha256(
            {
                "stage": "RESTART_RECOVERY",
                "observationEvidenceSha256": observation["evidenceSha256"],
                "lastFlashEvidenceSha256": update[
                    "lastFlashEvidenceSha256"
                ],
            }
        )
        if observation["healthy"] and observed_identity == update[
            "targetIdentitySha256"
        ]:
            self.journal.reconcile_unknown_actions(
                update["updateUid"],
                recovery_digest,
            )
            completed = self.journal.complete_update(
                update["updateUid"],
                "SUCCEEDED",
                recovery_digest,
            )
            self._release_completed_update(completed)
            return
        if observation["healthy"] and observed_identity == update[
            "rollbackIdentitySha256"
        ]:
            self.journal.reconcile_unknown_actions(
                update["updateUid"],
                recovery_digest,
            )
            completed = self.journal.complete_update(
                update["updateUid"],
                "ROLLED_BACK",
                recovery_digest,
            )
            self._release_completed_update(completed)
            return
        if observed_identity is None:
            raise McuUpdateCoordinatorError(
                "MCU_RECOVERY_IDENTITY_UNAVAILABLE",
                "interrupted MCU update cannot observe a running application",
            )
        self.journal.reconcile_unknown_actions(
            update["updateUid"],
            recovery_digest,
        )
        if observed_identity not in {
            update["targetIdentitySha256"],
            update["rollbackIdentitySha256"],
        }:
            raise McuUpdateCoordinatorError(
                "MCU_RECOVERY_IDENTITY_UNEXPECTED",
                "interrupted MCU update observed an unexpected firmware identity",
            )
        next_state = (
            "PREPARING_TARGET_RETRY"
            if update["targetAttemptCount"] < TARGET_ATTEMPT_LIMIT
            else "PREPARING_ROLLBACK"
        )
        self.journal.transition(
            update["updateUid"],
            next_state,
            expected_states={"RECOVERING"},
            fields={
                "observation_evidence_sha256": observation[
                    "evidenceSha256"
                ]
            },
        )

    def _dispatch_privileged(
        self,
        update: dict[str, Any],
        *,
        action_kind: str,
        helper_action: str,
        client: Any,
        extra_payload: Mapping[str, Any],
    ) -> str:
        action_uid = self._new_uid()
        payload = {
            "updateUid": update["updateUid"],
            "actionUid": action_uid,
            **dict(extra_payload),
        }
        self.journal.prepare_action(
            update_uid=update["updateUid"],
            action_uid=action_uid,
            action_kind=action_kind,
            payload=payload,
        )
        self.journal.begin_action(action_uid)
        try:
            result = client.request(helper_action, payload)
        except LocalControlRemoteError as error:
            self.journal.finish_action(
                action_uid,
                "FAILED",
                error_code=_stable_error_code(error.code),
                error_message=error.message,
            )
            raise McuUpdateCoordinatorError(error.code, error.message) from error
        except LocalControlUnavailable as error:
            self.journal.finish_action(
                action_uid,
                "UNKNOWN",
                error_code="PRIVILEGED_ACTION_RESULT_UNKNOWN",
                error_message="privileged helper result could not be confirmed",
            )
            raise McuUpdateCoordinatorError(
                "PRIVILEGED_ACTION_RESULT_UNKNOWN",
                "privileged helper result could not be confirmed",
                uncertain=True,
            ) from error
        finished = self.journal.finish_action(
            action_uid,
            "SUCCEEDED",
            response=result,
        )
        digest = finished.get("responseDigestSha256")
        if not isinstance(digest, str):
            raise McuUpdateCoordinatorError(
                "PRIVILEGED_ACTION_RECEIPT_INVALID",
                "privileged helper result has no durable digest",
            )
        return digest

    def _release_completed_update(self, update: dict[str, Any]) -> None:
        if update["state"] in {"DEFERRED", "REJECTED"}:
            status = self.safety_store.get_status()
            if (
                status.get("jobGateState") == "LOCKED"
                and status.get("maintenancePhase") == "DRAINING"
                and status.get("maintenanceOwnerUid") == update["updateUid"]
                and status.get("maintenanceFenceToken")
                == update["maintenanceFenceToken"]
                and status.get("activeJobPermitCount") == 0
                and status.get("unreconciledPhysicalActionCount") == 0
            ):
                self.safety_store.resume_update_drain(
                    update["updateUid"],
                    update["maintenanceFenceToken"],
                )
            self.safety_store.abort_update_drain(
                update["updateUid"],
                update["maintenanceFenceToken"],
                evidence_sha256=update["resultEvidenceSha256"],
            )
            self.journal.finish_release(update["updateUid"])
            return
        if update["state"] not in {"SUCCEEDED", "ROLLED_BACK"}:
            raise McuUpdateCoordinatorError(
                "MCU_UPDATE_NOT_VERIFIED",
                "unverified MCU update cannot release maintenance",
            )
        status = self.safety_store.get_status()
        if (
            status.get("jobGateState") == "OPEN"
            and status.get("maintenanceOwnerUid") is None
        ):
            if update["releaseState"] == "PREPARED":
                self.journal.finish_release(update["updateUid"])
            return
        fence_token = update["maintenanceFenceToken"]
        self.journal.prepare_release(update["updateUid"], fence_token)
        self.safety_store.release_update_maintenance(
            update["updateUid"],
            fence_token,
            outcome=update["state"],
            evidence_sha256=update["resultEvidenceSha256"],
        )
        self.journal.finish_release(update["updateUid"])

    def _finish_pre_hardware(
        self,
        update: Mapping[str, Any],
        *,
        outcome: str,
        error_code: str,
        error_message: str,
    ) -> None:
        evidence_digest = canonical_local_payload_sha256(
            {
                "outcome": outcome,
                "errorCode": error_code,
                "updateUid": update["updateUid"],
                "maintenanceFenceToken": update["maintenanceFenceToken"],
                "drainStartedAt": update["drainStartedAt"],
            }
        )
        completed = self.journal.finish_pre_hardware_update(
            update["updateUid"],
            outcome,
            evidence_digest,
            error_code=error_code,
            error_message=error_message,
        )
        self._release_completed_update(completed)

    def _drain_expired(self, update: Mapping[str, Any]) -> bool:
        value = update.get("drainStartedAt")
        if not isinstance(value, str):
            raise McuUpdateCoordinatorError(
                "MCU_DRAIN_DEADLINE_INVALID",
                "MCU update drain has no durable start time",
            )
        try:
            started_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise McuUpdateCoordinatorError(
                "MCU_DRAIN_DEADLINE_INVALID",
                "MCU update drain start time is invalid",
            ) from error
        now = self._utc_now()
        if started_at.tzinfo is None or now.tzinfo is None:
            raise McuUpdateCoordinatorError(
                "MCU_DRAIN_DEADLINE_INVALID",
                "MCU update drain time must include a timezone",
            )
        return (now - started_at).total_seconds() >= DRAIN_TIMEOUT_SECONDS

    def _record_step_error(
        self,
        update: dict[str, Any],
        error: McuUpdateCoordinatorError,
    ) -> None:
        current = self.journal.get_update(update["updateUid"])
        if current is None or current["state"] in MCU_UPDATE_TERMINAL_STATES:
            return
        retryable = {
            "BUSINESS_MAINTENANCE_UNAVAILABLE",
            "SERVICE_NOT_READY",
            "BUSINESS_RUNTIME_NOT_READY",
            "MCU_MAINTENANCE_BUSY",
            "MCU_MAINTENANCE_IN_PROGRESS",
            "MCU_UPDATE_BUSY",
            "PRIVILEGED_ACTION_RESULT_UNKNOWN",
            "HELPER_AUTHORIZATION_UNAVAILABLE",
            "MCU_VERIFICATION_RESULT_UNKNOWN",
            "RESULT_UNKNOWN",
            "SERVICE_STOPPING",
        }
        if error.uncertain and current["state"] in {
            "INITIAL_QUIESCE",
            "PREPARING_TARGET_RETRY",
            "PREPARING_ROLLBACK",
            "STOPPING_TARGET",
            "STOPPING_ROLLBACK",
            "FLASHING_TARGET",
            "FLASHING_ROLLBACK",
            "STARTING_TARGET_VERIFY",
            "STARTING_ROLLBACK_VERIFY",
        }:
            try:
                self.journal.transition(
                    current["updateUid"],
                    "RECOVERING",
                    expected_states={current["state"]},
                    fields={
                        "last_error_code": _stable_error_code(error.code),
                        "last_error_message": str(error),
                    },
                )
                return
            except McuUpdateStoreError:
                pass
        if error.code in retryable:
            self.journal.transition(
                current["updateUid"],
                current["state"],
                fields={
                    "last_error_code": _stable_error_code(error.code),
                    "last_error_message": str(error),
                },
            )
            return
        if current["state"] == "INITIAL_OBSERVE":
            # No UART handoff, service stop, flash, or GPIO mutation has
            # happened yet.  A deterministic evidence/protocol rejection is
            # therefore safe to finish without stranding the device in an
            # update lock, provided the durable gate still proves that it is
            # only draining for this exact update and fence.
            status = self.safety_store.get_status()
            if (
                status.get("jobGateState") == "DRAINING"
                and status.get("maintenanceOwnerUid") == current["updateUid"]
                and status.get("maintenanceFenceToken")
                == current["maintenanceFenceToken"]
            ):
                self._finish_pre_hardware(
                    current,
                    outcome="REJECTED",
                    error_code=_stable_error_code(error.code),
                    error_message=str(error),
                )
                return
        failed = self.journal.fail_locked(
            current["updateUid"],
            _stable_error_code(error.code),
            str(error),
        )
        fence = failed.get("maintenanceFenceToken")
        if isinstance(fence, int) and not isinstance(fence, bool):
            try:
                self.safety_store.lock_update_maintenance(
                    failed["updateUid"],
                    fence,
                    reason_code="MCU_UPDATE_FAILED",
                )
            except UpdaterStoreError:
                logger.exception("failed to retain MCU maintenance safety lock")

    def _new_uid(self) -> str:
        value = self._uuid_factory()
        if not isinstance(value, uuid.UUID) or value.version != 4:
            raise RuntimeError("MCU coordinator UUID factory must return UUIDv4")
        return str(value)


def _parse_application_evidence(response: Mapping[str, Any]) -> dict[str, Any]:
    digest = _require_evidence_digest(response, "OBSERVE")
    f3 = response.get("f3FirmwareIdentity")
    f1 = response.get("f1SelfTest")
    identity = f3.get("firmwareIdentity") if isinstance(f3, Mapping) else None
    identity_digest = (
        canonical_local_payload_sha256(dict(identity))
        if isinstance(identity, Mapping)
        else None
    )
    application_ready = bool(
        isinstance(f3, Mapping)
        and f3.get("queryStatus") == "OK"
        and f3.get("mode") == 1
        and f3.get("statusCode") == 0
        and f3.get("safeFlags") == 0x0F
        and identity_digest is not None
        and response.get("uartHandedOff") is False
    )
    healthy = bool(
        application_ready
        and isinstance(f1, Mapping)
        and f1.get("queryStatus") == "OK"
        and f1.get("communicationHealthy") is True
        and f1.get("validFlags") == 3
        and isinstance(f1.get("weightGrams"), int)
        and not isinstance(f1.get("weightGrams"), bool)
        and 0 <= f1["weightGrams"] <= 350_000
        and isinstance(f1.get("infraredBlocked"), bool)
        and f1.get("smokeState") in {"NORMAL", "ALARM"}
        and f1.get("smokeSensorHealth") == "OK"
    )
    return {
        "evidenceSha256": digest,
        "identitySha256": identity_digest if application_ready else None,
        "healthy": healthy,
    }


def _require_evidence_digest(response: Mapping[str, Any], stage: str) -> str:
    if not isinstance(response, Mapping) or response.get("evidenceStage") != stage:
        raise McuUpdateCoordinatorError(
            "MCU_MAINTENANCE_EVIDENCE_INVALID",
            "business MCU maintenance evidence stage is invalid",
        )
    digest = response.get("evidenceSha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise McuUpdateCoordinatorError(
            "MCU_MAINTENANCE_EVIDENCE_INVALID",
            "business MCU maintenance evidence digest is invalid",
        )
    return digest


def _stable_error_code(value: Any) -> str:
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in str(value).upper()
    ).strip("_")
    return (normalized or "MCU_UPDATE_FAILED")[:64]

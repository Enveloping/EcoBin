"""Permanent updater control process with opt-in local update candidates.

The default remains fail-closed and exposes only diagnosis.  One explicit
candidate switch enables the durable job-permit and physical-action RPCs; a
second, image-only switch enables the root-triggered MCU migration candidate.
Remote business and MCU update commands remain disabled in every posture.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Iterable
from typing import Any

from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlAction,
    LocalControlActionError,
    LocalControlServer,
)
from updater_store import UpdaterStore, UpdaterStoreError


logger = logging.getLogger("device-updater")

DEFAULT_STATE_PATH = "/var/lib/ecobin/updater/updater.db"
DEFAULT_MCU_UPDATE_STATE_PATH = "/var/lib/ecobin/updater/mcu-updates.db"
DEFAULT_BUSINESS_PACKAGE_ROOT = "/var/lib/ecobin/updater/business-packages"
DEFAULT_BUSINESS_STAGING_ROOT = "/var/lib/ecobin/updater/staging"
DEFAULT_BUSINESS_SIGNING_KEYS = "/usr/share/ecobin/business-release-keys"
DEFAULT_SOCKET_PATH = "/run/ecobin/updater/control.sock"
UPDATER_LOCAL_PROTOCOL_NAME = "ecobin.updater.control"

DISABLED_UPDATE_ACTIONS = frozenset(
    {
        "START_BUSINESS_UPDATE",
        "START_MCU_UPDATE",
        "CANCEL_UPDATE",
        "RENEW_DOWNLOAD_AUTHORIZATION",
    }
)

JOB_ACTION_FIELDS = {
    "REQUEST_JOB_PERMIT": frozenset(
        {
            "permitUid",
            "workUid",
            "commandUid",
            "workType",
            "requestDigestSha256",
        }
    ),
    "BEGIN_JOB": frozenset(
        {"permitUid", "beginUid", "permitDigestSha256"}
    ),
    "GET_JOB_PERMIT": frozenset({"permitUid"}),
    "ABANDON_JOB_PERMIT": frozenset(
        {"permitUid", "dispositionUid", "evidenceSha256"}
    ),
    "COMPLETE_JOB": frozenset(
        {"permitUid", "completionUid", "outcome", "completionDigestSha256"}
    ),
    "PREPARE_PHYSICAL_ACTION": frozenset(
        {
            "actionUid",
            "permitUid",
            "workUid",
            "commandUid",
            "actionKey",
            "actionKind",
            "actionDigestSha256",
            "dispatchAttemptToken",
        }
    ),
    "ARM_PHYSICAL_ACTION": frozenset(
        {"actionUid", "dispatchAttemptToken"}
    ),
    "CANCEL_PREPARED_PHYSICAL_ACTION": frozenset(
        {
            "actionUid",
            "receiptUid",
            "dispatchAttemptToken",
            "evidenceDigestSha256",
        }
    ),
    "ABORT_PHYSICAL_ACTION_DISPATCH": frozenset(
        {
            "actionUid",
            "receiptUid",
            "dispatchAttemptToken",
            "evidenceDigestSha256",
        }
    ),
    "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT": frozenset(
        {
            "actionUid",
            "receiptUid",
            "dispatchAttemptToken",
            "outcome",
            "evidenceDigestSha256",
        }
    ),
    "GET_PHYSICAL_ACTION": frozenset({"actionUid"}),
    "CONFIRM_PHYSICAL_ACTION": frozenset(
        {
            "actionUid",
            "receiptUid",
            "outcome",
            "confirmationBasis",
            "evidenceDigestSha256",
        }
    ),
}

ROOT_JOB_GATE_ACTION_FIELDS = {
    "GET_STAGE4_RECONCILIATION_STATUS": frozenset(),
    "ACTIVATE_STAGE4_JOB_GATE": frozenset(
        {
            "operationUid",
            "evidenceDigest",
            "expectedManagementStateSequence",
        }
    ),
    "LOCK_STAGE4_JOB_GATE": frozenset(
        {
            "operationUid",
            "evidenceDigest",
            "expectedManagementStateSequence",
        }
    ),
}

ROOT_MCU_CANDIDATE_ACTION_FIELDS = {
    "QUEUE_LOCAL_MCU_UPDATE": frozenset(
        {
            "updateUid",
            "commandUid",
            "targetPackageSha256",
            "rollbackPackageSha256",
        }
    ),
    "GET_MCU_UPDATE": frozenset({"updateUid"}),
    "AUTHORIZE_PRIVILEGED_HELPER_ACTION": frozenset(
        {
            "helperComponent",
            "helperAction",
            "updateUid",
            "actionUid",
            "payloadSha256",
        }
    ),
}

ROOT_BUSINESS_CANDIDATE_ACTION_FIELDS = {
    "QUEUE_LOCAL_BUSINESS_UPDATE": frozenset(
        {
            "updateUid",
            "deploymentUid",
            "commandUid",
            "releaseId",
            "versionName",
            "releaseSequence",
            "packageSha256",
            "packageSize",
            "signingKeyId",
        }
    ),
    "GET_BUSINESS_UPDATE": frozenset({"updateUid"}),
}


class UpdaterControlHandler:
    """Expose truthful status and thin, durable stage-four operations."""

    def __init__(
        self,
        store: UpdaterStore,
        mcu_coordinator: Any | None = None,
        business_coordinator: Any | None = None,
    ) -> None:
        self.store = store
        self.mcu_coordinator = mcu_coordinator
        self.business_coordinator = business_coordinator
        self._software_update_queue_lock = threading.Lock()

    def get_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        result = {
            **self.store.get_status(),
            "status": "READY",
            "localProtocolName": UPDATER_LOCAL_PROTOCOL_NAME,
            "localProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "localProtocolMinor": LOCAL_PROTOCOL_MINOR,
        }
        coordinator = self.mcu_coordinator
        result["mcuUpdateCandidateEnabled"] = coordinator is not None
        result["privilegedHelperMutationEnabled"] = (
            coordinator is not None or self.business_coordinator is not None
        )
        if coordinator is not None:
            result["mcuUpdateCandidate"] = coordinator.get_status()
        business_coordinator = self.business_coordinator
        result["businessUpdateCandidateEnabled"] = business_coordinator is not None
        if business_coordinator is not None:
            result["businessUpdateCandidate"] = business_coordinator.get_status()
        return result

    def get_stage4_reconciliation_status(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.store.get_job_gate_reconciliation_status()

    def activate_stage4_job_gate(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(
            self.store.activate_stage4_job_gate,
            payload,
        )

    def lock_stage4_job_gate(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(
            self.store.lock_stage4_job_gate,
            payload,
        )

    def request_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.request_job_permit, payload)

    def begin_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.begin_job, payload)

    def get_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.get_job_permit, payload)

    def abandon_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.abandon_job_permit, payload)

    def complete_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.complete_job, payload)

    def prepare_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(self.store.prepare_physical_action, payload)

    def arm_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(self.store.arm_physical_action, payload)

    def cancel_prepared_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(
            self.store.cancel_prepared_physical_action,
            payload,
        )

    def abort_physical_action_dispatch(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(
            self.store.abort_physical_action_dispatch,
            payload,
        )

    def confirm_live_physical_action_result(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(
            self.store.confirm_live_physical_action_result,
            payload,
        )

    def get_physical_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store_call(self.store.get_physical_action, payload)

    def confirm_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._store_call(self.store.confirm_physical_action, payload)

    def queue_local_mcu_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._software_update_queue_lock:
            self._require_other_update_idle(
                self.business_coordinator,
                code="BUSINESS_UPDATE_BUSY",
                message="a business runtime update is still active",
            )
            return self._coordinator_call("queue_local", payload)

    def get_mcu_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._coordinator_call("get_update", payload)

    def queue_local_business_update(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._software_update_queue_lock:
            self._require_other_update_idle(
                self.mcu_coordinator,
                code="MCU_UPDATE_BUSY",
                message="an MCU firmware update is still active",
            )
            return self._business_coordinator_call("queue_local", payload)

    def get_business_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._business_coordinator_call("get_update", payload)

    def authorize_privileged_helper_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        component = payload.get("helperComponent")
        if component == "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER":
            return self._business_coordinator_call(
                "authorize_privileged_action", payload
            )
        return self._coordinator_call("authorize_privileged_action", payload)

    def _business_coordinator_call(
        self,
        operation_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        coordinator = self.business_coordinator
        if coordinator is None:
            raise LocalControlActionError(
                "FEATURE_DISABLED",
                "business update candidate is not enabled",
            )
        operation = getattr(coordinator, operation_name)
        try:
            return operation(payload)
        except Exception as error:
            try:
                from business_update_coordinator import (
                    BusinessUpdateCoordinatorError,
                )
            except ImportError:
                BusinessUpdateCoordinatorError = ()  # type: ignore[assignment,misc]
            if isinstance(error, BusinessUpdateCoordinatorError):
                raise LocalControlActionError(error.code, str(error)) from error
            raise

    @staticmethod
    def _require_other_update_idle(
        coordinator: Any | None,
        *,
        code: str,
        message: str,
    ) -> None:
        if coordinator is None:
            return
        try:
            status = coordinator.get_status()
        except Exception as error:
            raise LocalControlActionError(
                "UPDATE_COORDINATION_UNAVAILABLE",
                "the other software update journal could not be confirmed",
            ) from error
        if not isinstance(status, dict) or "activeUpdate" not in status:
            raise LocalControlActionError(
                "UPDATE_COORDINATION_UNAVAILABLE",
                "the other software update journal returned invalid status",
            )
        if status["activeUpdate"] is not None:
            raise LocalControlActionError(code, message)

    def _coordinator_call(
        self,
        operation_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        coordinator = self.mcu_coordinator
        if coordinator is None:
            raise LocalControlActionError(
                "FEATURE_DISABLED",
                "MCU update candidate is not enabled",
            )
        operation = getattr(coordinator, operation_name)
        try:
            return operation(payload)
        except Exception as error:
            try:
                from mcu_update_coordinator import McuUpdateCoordinatorError
            except ImportError:
                McuUpdateCoordinatorError = ()  # type: ignore[assignment,misc]
            if isinstance(error, McuUpdateCoordinatorError):
                raise LocalControlActionError(error.code, str(error)) from error
            raise

    @staticmethod
    def _store_call(
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return operation(payload)
        except UpdaterStoreError as error:
            raise LocalControlActionError(error.code, str(error)) from error

    @staticmethod
    def reject_disabled_update(
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise LocalControlActionError(
            "FEATURE_DISABLED",
            "device software updates remain disabled in this candidate",
        )


class UpdaterAgent:
    def __init__(
        self,
        store: UpdaterStore,
        server: LocalControlServer,
        mcu_coordinator: Any | None = None,
        business_coordinator: Any | None = None,
    ) -> None:
        self.store = store
        self.server = server
        self.mcu_coordinator = mcu_coordinator
        self.business_coordinator = business_coordinator
        self._stop_event = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            self.server.start()
            if self.mcu_coordinator is not None:
                self.mcu_coordinator.start()
            if self.business_coordinator is not None:
                self.business_coordinator.start()
        except Exception:
            if self.business_coordinator is not None:
                self.business_coordinator.stop()
            if self.mcu_coordinator is not None:
                self.mcu_coordinator.stop()
            self.server.stop()
            self.store.close()
            raise
        self._started = True
        logger.info("device updater control plane ready")

    def request_stop(self) -> None:
        self._stop_event.set()

    def wait(self) -> None:
        while not self._stop_event.is_set():
            if (
                self.mcu_coordinator is not None
                and self.mcu_coordinator.failure is not None
            ):
                raise RuntimeError(
                    "MCU update coordinator failed"
                ) from self.mcu_coordinator.failure
            if (
                self.business_coordinator is not None
                and self.business_coordinator.failure is not None
            ):
                raise RuntimeError(
                    "business update coordinator failed"
                ) from self.business_coordinator.failure
            if not self.server.wait_stopped(timeout_seconds=0.25):
                continue
            if self._stop_event.is_set():
                return
            failure = self.server.failure
            if failure is not None:
                raise RuntimeError(
                    "device updater control server failed"
                ) from failure
            raise RuntimeError(
                "device updater control server stopped unexpectedly"
            )

    def stop(self) -> None:
        self.request_stop()
        if self.business_coordinator is not None:
            self.business_coordinator.stop()
        if self.mcu_coordinator is not None:
            self.mcu_coordinator.stop()
        if self._started:
            self.server.stop()
            self._started = False
        if self.mcu_coordinator is not None:
            self.mcu_coordinator.journal.close()
        if self.business_coordinator is not None:
            self.business_coordinator.journal.close()
        self.store.close()
        logger.info("device updater stopped")


def build_agent(args: argparse.Namespace) -> UpdaterAgent:
    allowed_uids = resolve_allowed_uids(
        args.allowed_uid,
        getattr(args, "allowed_user", None),
    )
    socket_gid = resolve_socket_gid(
        getattr(args, "socket_group", None)
    )
    candidate_enabled = bool(
        getattr(args, "enable_stage4_candidate", False)
    )
    mcu_candidate_enabled = bool(
        getattr(args, "enable_mcu_update_candidate", False)
    )
    business_candidate_enabled = bool(
        getattr(args, "enable_business_update_candidate", False)
    )
    if mcu_candidate_enabled and not candidate_enabled:
        raise ValueError(
            "MCU update candidate requires the stage-four job gate candidate"
        )
    if business_candidate_enabled and not candidate_enabled:
        raise ValueError(
            "business update candidate requires the stage-four job gate candidate"
        )
    business_uids: list[int] = []
    if candidate_enabled:
        configured_business_uids = getattr(args, "business_uid", None)
        configured_business_user = getattr(args, "business_user", None)
        if (
            configured_business_uids is None
            and configured_business_user is None
            and "ecobin-business" in (getattr(args, "allowed_user", None) or ())
        ):
            configured_business_user = "ecobin-business"
        business_uids = resolve_role_uids(
            configured_business_uids,
            configured_business_user,
            role="business",
        )
        missing = set(business_uids).difference(allowed_uids)
        if missing:
            raise ValueError(
                "business action UID must also be in the socket allowlist"
            )
    store = UpdaterStore(
        args.state,
        release_version=args.release_version,
        enable_stage4_candidate=candidate_enabled,
    )
    store.initialize()
    mcu_coordinator = None
    business_coordinator = None
    try:
        if mcu_candidate_enabled:
            from mcu_update_coordinator import McuUpdateCoordinator
            from mcu_update_package import McuFirmwarePackageStager
            from mcu_update_store import McuUpdateStore

            mcu_journal = McuUpdateStore(args.mcu_update_state)
            mcu_journal.initialize()
            mcu_coordinator = McuUpdateCoordinator(
                safety_store=store,
                journal=mcu_journal,
                package_stager=McuFirmwarePackageStager(
                    args.mcu_firmware_root,
                    args.mcu_signing_keys,
                    args.mcu_hardware_compatibility,
                ),
            )
        if business_candidate_enabled:
            from business_update_coordinator import BusinessUpdateCoordinator
            from business_update_package import BusinessReleasePackageStager
            from business_update_store import BusinessUpdateStore

            business_journal = BusinessUpdateStore(args.state)
            business_journal.initialize()
            business_coordinator = BusinessUpdateCoordinator(
                safety_store=store,
                journal=business_journal,
                package_stager=BusinessReleasePackageStager(
                    args.business_package_root,
                    args.business_staging_root,
                    args.business_signing_keys,
                ),
            )
        handler = UpdaterControlHandler(
            store,
            mcu_coordinator,
            business_coordinator,
        )
        actions = build_control_actions(
            handler,
            allowed_uids=allowed_uids,
            business_uids=business_uids,
            enable_stage4_candidate=candidate_enabled,
            enable_mcu_update_candidate=mcu_candidate_enabled,
            enable_business_update_candidate=business_candidate_enabled,
        )
        server = LocalControlServer(
            args.socket,
            protocol_name=UPDATER_LOCAL_PROTOCOL_NAME,
            actions=actions,
            allowed_uids=allowed_uids,
            socket_mode=0o660,
            socket_gid=socket_gid,
        )
    except Exception:
        if business_coordinator is not None:
            business_coordinator.journal.close()
        if mcu_coordinator is not None:
            mcu_coordinator.journal.close()
        store.close()
        raise
    return UpdaterAgent(
        store,
        server,
        mcu_coordinator,
        business_coordinator,
    )


def build_control_actions(
    handler: UpdaterControlHandler,
    *,
    allowed_uids: Iterable[int],
    business_uids: Iterable[int] = (),
    enable_stage4_candidate: bool = False,
    enable_mcu_update_candidate: bool = False,
    enable_business_update_candidate: bool = False,
) -> dict[str, LocalControlAction]:
    action_uids = frozenset(allowed_uids)
    if 0 not in action_uids:
        raise ValueError("updater socket allowlist must include root UID 0")
    root_uids = frozenset({0})
    actions = {
        "HEALTH": LocalControlAction(
            handler.get_status,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        "GET_STATUS": LocalControlAction(
            handler.get_status,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        **{
            action: LocalControlAction(
                handler.reject_disabled_update,
                payload_fields=frozenset(),
                allowed_uids=action_uids,
            )
            for action in DISABLED_UPDATE_ACTIONS
        },
    }
    root_handlers = {
        "GET_STAGE4_RECONCILIATION_STATUS": (
            handler.get_stage4_reconciliation_status
        ),
        "ACTIVATE_STAGE4_JOB_GATE": handler.activate_stage4_job_gate,
        "LOCK_STAGE4_JOB_GATE": handler.lock_stage4_job_gate,
    }
    actions.update(
        {
            action: LocalControlAction(
                root_handlers[action],
                payload_fields=fields,
                allowed_uids=root_uids,
            )
            for action, fields in ROOT_JOB_GATE_ACTION_FIELDS.items()
        }
    )
    if enable_mcu_update_candidate or enable_business_update_candidate:
        if not enable_stage4_candidate:
            raise ValueError(
                "local update candidates require the stage-four candidate"
            )
        actions["AUTHORIZE_PRIVILEGED_HELPER_ACTION"] = LocalControlAction(
            handler.authorize_privileged_helper_action,
            payload_fields=ROOT_MCU_CANDIDATE_ACTION_FIELDS[
                "AUTHORIZE_PRIVILEGED_HELPER_ACTION"
            ],
            allowed_uids=root_uids,
        )
    if enable_mcu_update_candidate:
        if not enable_stage4_candidate:
            raise ValueError(
                "MCU update candidate requires the stage-four candidate"
            )
        mcu_handlers = {
            "QUEUE_LOCAL_MCU_UPDATE": handler.queue_local_mcu_update,
            "GET_MCU_UPDATE": handler.get_mcu_update,
        }
        actions.update(
            {
                action: LocalControlAction(
                    mcu_handlers[action],
                    payload_fields=fields,
                    allowed_uids=root_uids,
                )
                for action, fields in ROOT_MCU_CANDIDATE_ACTION_FIELDS.items()
                if action != "AUTHORIZE_PRIVILEGED_HELPER_ACTION"
            }
        )
    if enable_business_update_candidate:
        business_handlers = {
            "QUEUE_LOCAL_BUSINESS_UPDATE": handler.queue_local_business_update,
            "GET_BUSINESS_UPDATE": handler.get_business_update,
        }
        actions.update(
            {
                action: LocalControlAction(
                    business_handlers[action],
                    payload_fields=fields,
                    allowed_uids=root_uids,
                )
                for action, fields in ROOT_BUSINESS_CANDIDATE_ACTION_FIELDS.items()
            }
        )
    if not enable_stage4_candidate:
        return actions
    job_uids = frozenset(business_uids)
    if not job_uids:
        raise ValueError(
            "stage-four candidate requires a non-empty business UID set"
        )
    if not job_uids.issubset(action_uids):
        raise ValueError(
            "business action UIDs must be included in the socket allowlist"
        )
    handlers = {
        "REQUEST_JOB_PERMIT": handler.request_job_permit,
        "BEGIN_JOB": handler.begin_job,
        "GET_JOB_PERMIT": handler.get_job_permit,
        "ABANDON_JOB_PERMIT": handler.abandon_job_permit,
        "COMPLETE_JOB": handler.complete_job,
        "PREPARE_PHYSICAL_ACTION": handler.prepare_physical_action,
        "ARM_PHYSICAL_ACTION": handler.arm_physical_action,
        "CANCEL_PREPARED_PHYSICAL_ACTION": (
            handler.cancel_prepared_physical_action
        ),
        "ABORT_PHYSICAL_ACTION_DISPATCH": (
            handler.abort_physical_action_dispatch
        ),
        "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT": (
            handler.confirm_live_physical_action_result
        ),
        "GET_PHYSICAL_ACTION": handler.get_physical_action,
        "CONFIRM_PHYSICAL_ACTION": handler.confirm_physical_action,
    }
    actions.update(
        {
            action: LocalControlAction(
                handlers[action],
                payload_fields=fields,
                allowed_uids=job_uids,
            )
            for action, fields in JOB_ACTION_FIELDS.items()
        }
    )
    return actions


def resolve_role_uids(
    role_uids: Iterable[int] | None,
    role_user: str | None,
    *,
    role: str,
    user_lookup: Callable[[str], int] | None = None,
) -> list[int]:
    """Resolve one action role without inheriting root diagnosis access."""

    if not isinstance(role, str) or not role:
        raise ValueError("action role must be non-empty")
    resolved: set[int] = set()
    for uid in role_uids or ():
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            raise ValueError(f"--{role}-uid must be positive and non-root")
        resolved.add(uid)
    if role_user is not None:
        if not isinstance(role_user, str) or not role_user:
            raise ValueError(f"--{role}-user must be non-empty")
        lookup = user_lookup or _system_user_uid
        try:
            uid = lookup(role_user)
        except KeyError as error:
            raise ValueError(
                f"--{role}-user does not exist: {role_user}"
            ) from error
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            raise ValueError(
                f"--{role}-user has an invalid non-root UID: {role_user}"
            )
        resolved.add(uid)
    if not resolved:
        raise ValueError(f"stage-four candidate requires a {role} UID")
    return sorted(resolved)


def resolve_allowed_uids(
    allowed_uids: Iterable[int] | None,
    allowed_users: Iterable[str] | None,
    *,
    user_lookup: Callable[[str], int] | None = None,
) -> list[int]:
    """Resolve CLI user names without weakening explicit UID policy."""

    resolved: set[int] = {0}
    for uid in allowed_uids or ():
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError("--allowed-uid must be non-negative")
        resolved.add(uid)
    lookup = user_lookup or _system_user_uid
    for username in allowed_users or ():
        if not isinstance(username, str) or not username:
            raise ValueError("--allowed-user must be non-empty")
        try:
            uid = lookup(username)
        except KeyError as error:
            raise ValueError(
                f"--allowed-user does not exist: {username}"
            ) from error
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError(
                f"--allowed-user has an invalid UID: {username}"
            )
        resolved.add(uid)
    # Root is always retained for the explicitly accepted stage-three local
    # diagnosis/recovery boundary; configured identities extend that set.
    return sorted(resolved)


def _system_user_uid(username: str) -> int:
    try:
        import pwd
    except ImportError as error:  # pragma: no cover - target OS is Linux
        raise RuntimeError("system user lookup is unavailable") from error
    return pwd.getpwnam(username).pw_uid


def resolve_socket_gid(
    group_name: str | None,
    *,
    group_lookup: Callable[[str], int] | None = None,
) -> int | None:
    if group_name is None:
        return None
    if not isinstance(group_name, str) or not group_name:
        raise ValueError("--socket-group must be non-empty")
    lookup = group_lookup or _system_group_gid
    try:
        gid = lookup(group_name)
    except KeyError as error:
        raise ValueError(
            f"--socket-group does not exist: {group_name}"
        ) from error
    if isinstance(gid, bool) or not isinstance(gid, int) or gid < 0:
        raise ValueError(
            f"--socket-group has an invalid GID: {group_name}"
        )
    return gid


def _system_group_gid(group_name: str) -> int:
    try:
        import grp
    except ImportError as error:  # pragma: no cover - target OS is Linux
        raise RuntimeError("system group lookup is unavailable") from error
    return grp.getgrnam(group_name).gr_gid


def notify_systemd(message: str) -> None:
    """Send one bounded lifecycle notification without libsystemd."""

    if message not in {"READY=1", "STOPPING=1"}:
        raise ValueError("unsupported systemd notification")
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # Linux abstract Unix-domain socket
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.sendto(message.encode("ascii"), address)


def notify_systemd_ready() -> None:
    """Report readiness only after the updater control socket is listening."""

    notify_systemd("READY=1")


def notify_systemd_stopping() -> None:
    notify_systemd("STOPPING=1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        default=os.getenv("ECOBIN_UPDATER_STATE_PATH", DEFAULT_STATE_PATH),
    )
    parser.add_argument(
        "--socket",
        default=os.getenv("ECOBIN_UPDATER_SOCKET", DEFAULT_SOCKET_PATH),
    )
    parser.add_argument(
        "--release-version",
        default=os.getenv("ECOBIN_UPDATER_RELEASE_VERSION"),
    )
    parser.add_argument(
        "--allowed-uid",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--allowed-user",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--socket-group",
        default=os.getenv("ECOBIN_UPDATER_SOCKET_GROUP"),
    )
    parser.add_argument(
        "--enable-stage4-candidate",
        action="store_true",
        help=(
            "enable only the candidate job gate and physical safety ledger; "
            "software updates remain disabled unless a separate candidate "
            "switch is also present"
        ),
    )
    parser.add_argument(
        "--enable-mcu-update-candidate",
        action="store_true",
        help=(
            "enable only the root-triggered MCU update migration candidate; "
            "remote MCU update commands remain disabled"
        ),
    )
    parser.add_argument(
        "--enable-business-update-candidate",
        action="store_true",
        help=(
            "enable only the root-triggered local signed business update "
            "candidate; remote update commands remain disabled"
        ),
    )
    parser.add_argument(
        "--mcu-update-state",
        default=os.getenv(
            "ECOBIN_MCU_UPDATE_STATE_PATH",
            DEFAULT_MCU_UPDATE_STATE_PATH,
        ),
    )
    parser.add_argument(
        "--mcu-firmware-root",
        default=os.getenv(
            "ECOBIN_PERMANENT_MCU_FIRMWARE_ROOT",
            "/var/lib/ecobin/updater/mcu-firmware",
        ),
    )
    parser.add_argument(
        "--mcu-signing-keys",
        default=os.getenv(
            "ECOBIN_PERMANENT_MCU_SIGNING_KEYS",
            "/usr/share/ecobin/mcu-release-keys",
        ),
    )
    parser.add_argument(
        "--mcu-hardware-compatibility",
        default=os.getenv(
            "ECOBIN_MCU_HARDWARE_COMPATIBILITY",
            "ECOBIN_MAINBOARD_V1.1",
        ),
    )
    parser.add_argument(
        "--business-package-root",
        default=os.getenv(
            "ECOBIN_BUSINESS_PACKAGE_ROOT",
            DEFAULT_BUSINESS_PACKAGE_ROOT,
        ),
    )
    parser.add_argument(
        "--business-staging-root",
        default=os.getenv(
            "ECOBIN_BUSINESS_STAGING_ROOT",
            DEFAULT_BUSINESS_STAGING_ROOT,
        ),
    )
    parser.add_argument(
        "--business-signing-keys",
        default=os.getenv(
            "ECOBIN_BUSINESS_SIGNING_KEYS",
            DEFAULT_BUSINESS_SIGNING_KEYS,
        ),
    )
    parser.add_argument(
        "--business-uid",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--business-user",
        default=os.getenv("ECOBIN_BUSINESS_USER"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-22s] %(levelname)-5s %(message)s",
        stream=sys.stdout,
    )
    args = build_parser().parse_args(argv)
    agent = build_agent(args)

    def request_stop(signum: int, _frame: Any) -> None:
        logger.info("signal %d, stopping device updater", signum)
        agent.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        agent.start()
        notify_systemd_ready()
        agent.wait()
    finally:
        try:
            notify_systemd_stopping()
        finally:
            agent.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

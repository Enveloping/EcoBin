"""Root-owned, AF_UNIX-only command boundary for P7 factory acceptance."""

from __future__ import annotations

import copy
from http import HTTPStatus
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import stat
import subprocess
import threading
from typing import Any, Callable, Mapping, Optional

try:
    import grp
except ModuleNotFoundError:  # Windows test collection; production is Debian.
    class _UnsupportedGroupDatabase:
        def getgrnam(self, _name: str) -> object:
            raise OSError("POSIX group database is unavailable")

    grp = _UnsupportedGroupDatabase()  # type: ignore[assignment]

from .acceptance_config import AcceptanceConfiguration
from .acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from .acceptance_hardware import (
    FixedFrameAcceptanceMcu,
    FixedRoleCameraProbe,
    OpenCvCapture,
    ReadOnlyStm32RomProbe,
)
from .acceptance_storage import AcceptanceLockBusy, AcceptanceStorageError
from first_boot.atomic_json import AtomicJsonFile as PublicAtomicJsonFile
from first_boot.atomic_json import root_group_owner


CONTROL_SOCKET = Path("/run/ecobin/factory-test/control.sock")
PUBLIC_PROJECTION_PATH = Path("/run/ecobin/factory-portal/acceptance.json")
IMAGE_RELEASE_PATH = Path("/etc/ecobin/image-release.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
MAXIMUM_REQUEST_BYTES = 4096
MAXIMUM_RESPONSE_BYTES = 64 * 1024
CLIENT_SOCKET_TIMEOUT_SECONDS = 2.0
MAXIMUM_CONCURRENT_CLIENTS = 4
_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_PHYSICAL_FAILED_SAFE_CODES = {
    "upgradeLine": "UPGRADE_LINE_FAILED_BUT_APPLICATION_RECOVERED",
    "delivery": "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED",
    "clean": "CLEAN_FAILED_BUT_APPLICATION_RECOVERED",
}


class AcceptanceCommandError(RuntimeError):
    def __init__(self, code: str, status: HTTPStatus) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _exact_parameters(
    value: object,
    fields: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise AcceptanceCommandError(
            "ACTION_PARAMETERS_INVALID", HTTPStatus.BAD_REQUEST
        )
    return value


def _require_true(parameters: Mapping[str, Any], name: str, code: str) -> None:
    if parameters.get(name) is not True:
        raise AcceptanceCommandError(code, HTTPStatus.UNPROCESSABLE_ENTITY)


def _is_terminal_physical_failure(name: str, value: object) -> bool:
    basic_match = bool(
        isinstance(value, dict)
        and value.get("status") == "FAILED_SAFE"
        and value.get("resultCode") == _PHYSICAL_FAILED_SAFE_CODES[name]
        and isinstance(value.get("sendAttempts"), int)
        and not isinstance(value.get("sendAttempts"), bool)
        and value["sendAttempts"] > 0
    )
    if not basic_match or not isinstance(value, dict):
        return False
    if name == "delivery":
        return value.get("operatorAreaSafeConfirmed") is True
    if name == "clean":
        return value.get("cleanDoorConfirmed") is True
    return True


def _is_safely_passed(name: str, value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") != "PASSED":
        return False
    if name == "delivery":
        return value.get("operatorAreaSafeConfirmed") is True
    if name == "clean":
        return value.get("cleanDoorConfirmed") is True
    return True


def _check_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "NOT_RUN", "resultCode": "NOT_RUN"}
    result: dict[str, Any] = {
        "status": value.get("status", "NOT_RUN"),
        "resultCode": value.get("resultCode", "NOT_RUN"),
    }
    for name in (
        "emptyWeightGrams",
        "loadedWeightGrams",
        "removedWeightGrams",
        "deltaGrams",
        "targetDeltaGrams",
        "toleranceGrams",
        "sendAttempts",
        "romWritePerformed",
        "romDeviceId",
        "cleanDoorConfirmed",
        "operatorAreaSafeConfirmed",
    ):
        if name in value:
            result[name] = value[name]
    action = value.get("result")
    if isinstance(action, dict):
        result["result"] = {
            key: action.get(key)
            for key in (
                "preWeightGrams",
                "postWeightGrams",
                "weightDeltaGrams",
                "infraredBlocked",
            )
        }
    return result


def _mcu_identity_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        name: value.get(name)
        for name in (
            "fixedFrameRevision",
            "firmwareVersion",
            "firmwareVersionCode",
            "firmwareIdentityHex",
        )
    }


class AcceptanceCommandController:
    """Validate public intentions and serialize every physical mutation."""

    def __init__(
        self,
        executor: FactoryAcceptanceExecutor,
        config: AcceptanceConfiguration,
        *,
        image_release_id: str,
        boot_id: str,
        wall_time_trusted: bool,
        projection_writer: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        if _RELEASE_ID.fullmatch(image_release_id) is None:
            raise ValueError("image release ID is invalid")
        if not boot_id or len(boot_id) > 80:
            raise ValueError("boot ID is invalid")
        self._executor = executor
        self._config = config
        self._image_release_id = image_release_id
        self._boot_id = boot_id
        self._wall_time_trusted = wall_time_trusted
        self._projection_writer = projection_writer
        self._action_lock = threading.Lock()
        self._projection_lock = threading.Lock()

    def projection(self, *, idempotent: bool = False) -> dict[str, Any]:
        state = self._executor.snapshot()
        checks = state.get("checks") if isinstance(state.get("checks"), dict) else {}
        recovery = state.get("recovery")
        camera = checks.get("cameras") if isinstance(checks, dict) else None
        camera_review: Optional[dict[str, Any]] = None
        if (
            isinstance(camera, dict)
            and camera.get("status") == "RUNNING"
            and isinstance(camera.get("reviewNonce"), str)
        ):
            nonce = camera["reviewNonce"]
            camera_review = {
                "nonce": nonce,
                "expiresMonotonicMs": camera.get("reviewExpiresMonotonicMs"),
                "outsideImage": f"/api/v1/acceptance/camera/outside/{nonce}",
                "insideImage": f"/api/v1/acceptance/camera/inside/{nonce}",
            }
        result = {
            "schemaVersion": 1,
            "executorAvailable": True,
            "status": state.get("status", "NOT_RUN"),
            "phase": state.get("phase", "NOT_RUN"),
            "revision": state.get("revision", 0),
            "idempotent": bool(idempotent),
            "imageReleaseId": state.get("imageReleaseId"),
            "hardwareConfigSummary": self._config.digest()[:12].upper(),
            "mcuIdentity": _mcu_identity_summary(state.get("mcuIdentity")),
            "checks": {
                name: _check_summary(checks.get(name))
                for name in (
                    "mcu",
                    "weight",
                    "upgradeLine",
                    "cameras",
                    "delivery",
                    "clean",
                )
            },
            "recovery": (
                {
                    "context": recovery.get("context"),
                    "resultCode": recovery.get("resultCode"),
                    "hardwareVerified": recovery.get("hardwareVerified") is True,
                    "awaitingDoorConfirmation": (
                        recovery.get("awaitingDoorConfirmation") is True
                    ),
                    "awaitingAreaSafetyConfirmation": (
                        recovery.get("awaitingAreaSafetyConfirmation") is True
                    ),
                }
                if isinstance(recovery, dict)
                else None
            ),
            "cameraReview": camera_review,
        }
        result["allowedActions"] = self._allowed_actions(result)
        if self._projection_writer is not None:
            public = copy.deepcopy(result)
            public["idempotent"] = False
            with self._projection_lock:
                self._projection_writer(public)
        return result

    @staticmethod
    def _allowed_actions(projection: Mapping[str, Any]) -> list[str]:
        status = projection.get("status")
        checks = projection.get("checks") or {}
        recovery = projection.get("recovery")
        if status == "NOT_RUN":
            return ["START"]
        if status == "FAILED":
            return ["RESTART_FAILED_RUN"]
        if status == "PASSED":
            return []
        if status == "RECOVERY_REQUIRED":
            if (
                isinstance(recovery, dict)
                and recovery.get("awaitingAreaSafetyConfirmation") is True
            ):
                return ["CONFIRM_DELIVERY_AREA_SAFE"]
            if (
                isinstance(recovery, dict)
                and recovery.get("awaitingDoorConfirmation") is True
            ):
                return ["CONFIRM_CLEAN_DOOR"]
            return ["RECOVER"]
        if status != "RUNNING":
            return []
        if projection.get("phase") == "FINALIZING_REPORT":
            return ["FINALIZE"]
        if projection.get("phase") == "LEGACY_ACTION_SAFETY_FAILURE_READY":
            return ["FINALIZE"]
        if any(
            _is_terminal_physical_failure(name, checks.get(name))
            for name in _PHYSICAL_FAILED_SAFE_CODES
        ):
            return ["FINALIZE"]
        if checks.get("mcu", {}).get("status") != "PASSED":
            return ["CHECK_MCU"]
        weight = checks.get("weight", {})
        if weight.get("resultCode") == "WAITING_FOR_500G_LOAD":
            return ["CAPTURE_LOADED_WEIGHT"]
        if weight.get("resultCode") == "WAITING_FOR_WEIGHT_REMOVAL":
            return ["CONFIRM_WEIGHT_REMOVED"]
        if weight.get("status") != "PASSED":
            return ["CAPTURE_EMPTY_WEIGHT"]
        cameras = checks.get("cameras", {})
        if cameras.get("resultCode") == "WAITING_FOR_CAMERA_ROLE_CONFIRMATION":
            return ["CONFIRM_CAMERAS"]
        if cameras.get("status") != "PASSED":
            return ["CAPTURE_CAMERAS"]
        upgrade = checks.get("upgradeLine", {})
        if upgrade.get("status") == "FAILED_SAFE":
            return []
        if upgrade.get("status") != "PASSED":
            return ["CHECK_UPGRADE_LINE"]
        delivery = checks.get("delivery", {})
        if delivery.get("status") == "FAILED_SAFE":
            return []
        if delivery.get("status") != "PASSED":
            return ["RUN_DELIVERY"]
        if not _is_safely_passed("delivery", delivery):
            return []
        clean = checks.get("clean", {})
        if clean.get("status") == "FAILED_SAFE":
            return []
        if clean.get("status") != "PASSED":
            return ["RUN_CLEAN"]
        if not _is_safely_passed("clean", clean):
            return []
        return ["FINALIZE"]

    def _is_achieved(self, operation: str, state: Mapping[str, Any]) -> bool:
        checks = state.get("checks") or {}
        status = state.get("status")
        if operation == "START":
            return status != "NOT_RUN"
        if operation == "CHECK_MCU":
            return checks.get("mcu", {}).get("status") == "PASSED"
        if operation == "CAPTURE_EMPTY_WEIGHT":
            return checks.get("weight", {}).get("resultCode") in {
                "WAITING_FOR_500G_LOAD",
                "WAITING_FOR_WEIGHT_REMOVAL",
                "WEIGHT_500G_WITHIN_490_510_AND_REMOVED",
            }
        if operation == "CAPTURE_LOADED_WEIGHT":
            return checks.get("weight", {}).get("resultCode") in {
                "WAITING_FOR_WEIGHT_REMOVAL",
                "WEIGHT_500G_WITHIN_490_510_AND_REMOVED",
            }
        if operation == "CONFIRM_WEIGHT_REMOVED":
            return checks.get("weight", {}).get("status") == "PASSED"
        if operation == "CAPTURE_CAMERAS":
            return checks.get("cameras", {}).get("status") in {"RUNNING", "PASSED"}
        if operation == "CONFIRM_CAMERAS":
            return checks.get("cameras", {}).get("status") == "PASSED"
        if operation == "CHECK_UPGRADE_LINE":
            return checks.get("upgradeLine", {}).get("status") == "PASSED"
        if operation == "RUN_DELIVERY":
            return checks.get("delivery", {}).get("status") in {
                "RECOVERY_REQUIRED",
                "PASSED",
            }
        if operation == "RUN_CLEAN":
            return checks.get("clean", {}).get("status") in {
                "RECOVERY_REQUIRED",
                "PASSED",
            }
        if operation == "CONFIRM_CLEAN_DOOR":
            return checks.get("clean", {}).get("status") == "PASSED"
        if operation == "CONFIRM_DELIVERY_AREA_SAFE":
            return (
                checks.get("delivery", {}).get(
                    "operatorAreaSafeConfirmed"
                )
                is True
            )
        if operation == "RECOVER":
            recovery = state.get("recovery")
            return status != "RECOVERY_REQUIRED" or (
                isinstance(recovery, dict)
                and recovery.get("awaitingAreaSafetyConfirmation") is True
                and recovery.get("deliveryConfirmationDisposition")
                == "FAILED_SAFE_AFTER_CONFIRMATION"
            )
        if operation == "FINALIZE":
            return (
                status in {"PASSED", "FAILED"}
                and state.get("phase") == "COMPLETE"
            )
        return False

    def execute(self, request: object) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "operation",
            "expectedRevision",
            "parameters",
        }:
            raise AcceptanceCommandError(
                "ACTION_REQUEST_INVALID", HTTPStatus.BAD_REQUEST
            )
        operation = request.get("operation")
        expected_revision = request.get("expectedRevision")
        if not isinstance(operation, str) or operation not in {
            "START",
            "RESTART_FAILED_RUN",
            "CHECK_MCU",
            "CAPTURE_EMPTY_WEIGHT",
            "CAPTURE_LOADED_WEIGHT",
            "CONFIRM_WEIGHT_REMOVED",
            "CAPTURE_CAMERAS",
            "CONFIRM_CAMERAS",
            "CHECK_UPGRADE_LINE",
            "RUN_DELIVERY",
            "RUN_CLEAN",
            "CONFIRM_DELIVERY_AREA_SAFE",
            "CONFIRM_CLEAN_DOOR",
            "RECOVER",
            "FINALIZE",
        }:
            raise AcceptanceCommandError(
                "ACTION_NOT_SUPPORTED", HTTPStatus.BAD_REQUEST
            )
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise AcceptanceCommandError(
                "EXPECTED_REVISION_INVALID", HTTPStatus.BAD_REQUEST
            )
        if not self._action_lock.acquire(blocking=False):
            raise AcceptanceCommandError(
                "ACCEPTANCE_EXECUTOR_BUSY", HTTPStatus.CONFLICT
            )
        try:
            state = self._executor.snapshot()
            if state.get("revision") != expected_revision:
                if self._is_achieved(operation, state):
                    return self.projection(idempotent=True)
                raise AcceptanceCommandError(
                    "ACCEPTANCE_REVISION_CONFLICT", HTTPStatus.CONFLICT
                )
            if self._is_achieved(operation, state):
                return self.projection(idempotent=True)
            if operation not in self._allowed_actions(state):
                raise AcceptanceCommandError(
                    "ACTION_NOT_ALLOWED_IN_CURRENT_STATE",
                    HTTPStatus.CONFLICT,
                )
            parameters = request["parameters"]
            self._perform(operation, parameters, state)
            return self.projection()
        except AcceptanceError as error:
            status = (
                HTTPStatus.LOCKED
                if error.recovery_required
                else HTTPStatus.UNPROCESSABLE_ENTITY
            )
            raise AcceptanceCommandError(error.code, status) from error
        finally:
            self._action_lock.release()

    def _perform(
        self,
        operation: str,
        parameters_value: object,
        state: Mapping[str, Any],
    ) -> None:
        if operation == "START":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirmOfflineAcceptance"})
            )
            _require_true(
                parameters,
                "confirmOfflineAcceptance",
                "OFFLINE_ACCEPTANCE_CONFIRMATION_REQUIRED",
            )
            self._executor.begin_run(
                image_release_id=self._image_release_id,
                boot_id=self._boot_id,
                wall_time_trusted=self._wall_time_trusted,
                hardware_config_digest=self._config.digest(),
            )
            return
        if operation == "RESTART_FAILED_RUN":
            parameters = _exact_parameters(
                parameters_value,
                frozenset({"confirmRestartFailedAcceptance"}),
            )
            _require_true(
                parameters,
                "confirmRestartFailedAcceptance",
                "RESTART_FAILED_ACCEPTANCE_CONFIRMATION_REQUIRED",
            )
            if state.get("status") != "FAILED":
                raise AcceptanceCommandError(
                    "FAILED_ACCEPTANCE_REQUIRED", HTTPStatus.CONFLICT
                )
            self._executor.begin_run(
                image_release_id=self._image_release_id,
                boot_id=self._boot_id,
                wall_time_trusted=self._wall_time_trusted,
                hardware_config_digest=self._config.digest(),
                restart_terminal=True,
            )
            return
        if operation == "CHECK_MCU":
            _exact_parameters(parameters_value, frozenset())
            self._executor.check_mcu()
            return
        if operation == "CAPTURE_EMPTY_WEIGHT":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirmScaleEmpty"})
            )
            _require_true(
                parameters, "confirmScaleEmpty", "EMPTY_SCALE_CONFIRMATION_REQUIRED"
            )
            self._executor.capture_empty_weight()
            return
        if operation == "CAPTURE_LOADED_WEIGHT":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirm500gPlaced"})
            )
            _require_true(
                parameters, "confirm500gPlaced", "TEST_WEIGHT_CONFIRMATION_REQUIRED"
            )
            self._executor.capture_loaded_weight()
            return
        if operation == "CONFIRM_WEIGHT_REMOVED":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirm500gRemoved"})
            )
            _require_true(
                parameters, "confirm500gRemoved", "WEIGHT_REMOVAL_CONFIRMATION_REQUIRED"
            )
            self._executor.confirm_weight_removed()
            return
        if operation == "CAPTURE_CAMERAS":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirmCaptureNow"})
            )
            _require_true(
                parameters, "confirmCaptureNow", "CAMERA_CAPTURE_CONFIRMATION_REQUIRED"
            )
            self._executor.capture_cameras()
            return
        if operation == "CONFIRM_CAMERAS":
            parameters = _exact_parameters(
                parameters_value,
                frozenset(
                    {
                        "reviewNonce",
                        "outsideRoleConfirmed",
                        "insideRoleConfirmed",
                    }
                ),
            )
            nonce = parameters.get("reviewNonce")
            if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
                raise AcceptanceCommandError(
                    "CAMERA_REVIEW_NONCE_INVALID", HTTPStatus.BAD_REQUEST
                )
            self._executor.confirm_cameras(
                review_nonce=nonce,
                outside_role_confirmed=(
                    parameters.get("outsideRoleConfirmed") is True
                ),
                inside_role_confirmed=(
                    parameters.get("insideRoleConfirmed") is True
                ),
            )
            return
        if operation == "CHECK_UPGRADE_LINE":
            parameters = _exact_parameters(
                parameters_value,
                frozenset({"confirmReadOnlyBootloaderProbe"}),
            )
            _require_true(
                parameters,
                "confirmReadOnlyBootloaderProbe",
                "BOOTLOADER_PROBE_CONFIRMATION_REQUIRED",
            )
            self._executor.check_upgrade_line()
            return
        if operation in {"RUN_DELIVERY", "RUN_CLEAN"}:
            parameters = _exact_parameters(
                parameters_value, frozenset({"operatorAreaSafeConfirmed"})
            )
            _require_true(
                parameters,
                "operatorAreaSafeConfirmed",
                "OPERATOR_SAFETY_CONFIRMATION_REQUIRED",
            )
            self._executor.run_action(
                "DELIVERY" if operation == "RUN_DELIVERY" else "CLEAN",
                operator_area_safe_confirmed=True,
            )
            return
        if operation == "CONFIRM_CLEAN_DOOR":
            parameters = _exact_parameters(
                parameters_value, frozenset({"cleanDoorClosedConfirmed"})
            )
            _require_true(
                parameters,
                "cleanDoorClosedConfirmed",
                "CLEAN_DOOR_CONFIRMATION_REQUIRED",
            )
            self._executor.confirm_clean_door_closed(operator_confirmed=True)
            return
        if operation == "CONFIRM_DELIVERY_AREA_SAFE":
            parameters = _exact_parameters(
                parameters_value, frozenset({"operatorAreaSafeConfirmed"})
            )
            _require_true(
                parameters,
                "operatorAreaSafeConfirmed",
                "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED",
            )
            self._executor.confirm_delivery_area_safe(
                operator_confirmed=True
            )
            return
        if operation == "RECOVER":
            parameters = _exact_parameters(
                parameters_value,
                frozenset({"confirmRecovery", "cleanDoorClosedConfirmed"}),
            )
            _require_true(
                parameters,
                "confirmRecovery",
                "RECOVERY_CONFIRMATION_REQUIRED",
            )
            self._executor.recover(
                clean_door_closed_confirmed=(
                    parameters.get("cleanDoorClosedConfirmed") is True
                )
            )
            return
        if operation == "FINALIZE":
            parameters = _exact_parameters(
                parameters_value, frozenset({"confirmFinalize"})
            )
            _require_true(
                parameters,
                "confirmFinalize",
                "FINALIZE_CONFIRMATION_REQUIRED",
            )
            checks = state.get("checks") or {}
            required = ("mcu", "weight", "upgradeLine", "cameras", "delivery", "clean")
            all_passed = all(
                _is_safely_passed(name, checks.get(name))
                for name in required
            )
            failed_safe_terminal = (
                state.get("status") == "RUNNING"
                and state.get("recovery") is None
                and state.get("activeAction") is None
                and any(
                    _is_terminal_physical_failure(name, checks.get(name))
                    for name in _PHYSICAL_FAILED_SAFE_CODES
                )
            )
            legacy_safety_failure = (
                state.get("status") == "RUNNING"
                and state.get("phase")
                == "LEGACY_ACTION_SAFETY_FAILURE_READY"
                and state.get("recovery") is None
                and state.get("activeAction") is None
            )
            report_finalization_pending = (
                state.get("status") == "RUNNING"
                and state.get("phase") == "FINALIZING_REPORT"
                and state.get("pendingFinalStatus") in {"PASSED", "FAILED"}
                and state.get("recovery") is None
                and state.get("activeAction") is None
            )
            if (
                not all_passed
                and not failed_safe_terminal
                and not legacy_safety_failure
                and not report_finalization_pending
            ):
                raise AcceptanceCommandError(
                    "ALL_ACCEPTANCE_CHECKS_REQUIRED",
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            self._executor.finalize()
            return
        raise AssertionError("unreachable operation")


class _AcceptanceRequestHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(CLIENT_SOCKET_TIMEOUT_SECONDS)
        super().setup()

    def handle(self) -> None:
        try:
            line = self.rfile.readline(MAXIMUM_REQUEST_BYTES + 1)
        except (TimeoutError, socket.timeout):
            self._write_error("IPC_REQUEST_TIMEOUT", HTTPStatus.REQUEST_TIMEOUT)
            return
        if len(line) > MAXIMUM_REQUEST_BYTES or not line.endswith(b"\n"):
            self._write_error("IPC_REQUEST_TOO_LARGE", HTTPStatus.BAD_REQUEST)
            return
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error("IPC_REQUEST_INVALID", HTTPStatus.BAD_REQUEST)
            return
        if request == {"operation": "STATUS"}:
            self._write({"ok": True, "data": self.server.controller.projection()})
            return
        try:
            data = self.server.controller.execute(request)
        except AcceptanceCommandError as error:
            self._write_error(error.code, error.status)
            return
        self._write({"ok": True, "data": data})

    def _write_error(self, code: str, status: HTTPStatus) -> None:
        self._write({"ok": False, "error": code, "httpStatus": status.value})

    def _write(self, value: dict[str, Any]) -> None:
        encoded = (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAXIMUM_RESPONSE_BYTES:
            encoded = b'{"error":"IPC_RESPONSE_TOO_LARGE","httpStatus":500,"ok":false}\n'
        self.wfile.write(encoded)


_ThreadingUnixStreamServer = getattr(
    socketserver,
    "ThreadingUnixStreamServer",
    socketserver.ThreadingTCPServer,
)


class AcceptanceUnixServer(_ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        path: Path,
        controller: AcceptanceCommandController,
        *,
        portal_group: str,
    ) -> None:
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("Unix-domain sockets are unavailable")
        if not path.is_absolute():
            raise ValueError("acceptance control socket must be absolute")
        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o750)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(metadata.st_mode):
                raise RuntimeError("acceptance control path is not a socket")
            path.unlink()
        self.controller = controller
        self._path = path
        self._client_slots = threading.BoundedSemaphore(
            MAXIMUM_CONCURRENT_CLIENTS
        )
        super().__init__(str(path), _AcceptanceRequestHandler)
        os.chmod(path, 0o660)
        os.chown(path, 0, grp.getgrnam(portal_group).gr_gid)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        if not self._client_slots.acquire(blocking=False):
            try:
                request.settimeout(CLIENT_SOCKET_TIMEOUT_SECONDS)
                request.sendall(
                    b'{"error":"IPC_CONCURRENCY_LIMIT","httpStatus":503,"ok":false}\n'
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._client_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: object
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._client_slots.release()

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass


def _read_small_regular(path: Path, maximum_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
            raise RuntimeError(f"{path} is invalid")
        value = os.read(descriptor, maximum_bytes + 1).decode("utf-8")
    finally:
        os.close(descriptor)
    return value


def _image_release_id() -> str:
    document = json.loads(_read_small_regular(IMAGE_RELEASE_PATH, 64 * 1024))
    value = (
        document.get("releaseId", document.get("imageReleaseId"))
        if isinstance(document, dict)
        else None
    )
    if not isinstance(value, str) or _RELEASE_ID.fullmatch(value) is None:
        raise RuntimeError("image release identity is invalid")
    return value


def _boot_id() -> str:
    value = _read_small_regular(BOOT_ID_PATH, 128).strip().lower()
    if re.fullmatch(r"[0-9a-f-]{32,36}", value) is None:
        raise RuntimeError("boot ID is invalid")
    return value


def _wall_time_trusted() -> bool:
    completed = subprocess.run(
        (
            "/usr/bin/timedatectl",
            "show",
            "--property=NTPSynchronized",
            "--value",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
        shell=False,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    return completed.returncode == 0 and completed.stdout.strip().lower() == "yes"


def build_executor(config: AcceptanceConfiguration) -> FactoryAcceptanceExecutor:
    mcu = FixedFrameAcceptanceMcu.for_port(config.serial_port)
    cameras = FixedRoleCameraProbe(
        outside_source=config.outside_camera,
        inside_source=config.inside_camera,
        temporary_directory=config.photo_directory,
        capture=OpenCvCapture(),
        shared_group_readable=True,
    )
    bootloader = ReadOnlyStm32RomProbe(
        gpio_path=config.gpio_path,
        boot0_wpi=config.boot0_wpi,
        reset_wpi=config.reset_wpi,
        serial_port=config.serial_port,
        stm32flash_path=config.stm32flash_path,
    )
    return FactoryAcceptanceExecutor(
        mcu=mcu,
        bootloader=bootloader,
        cameras=cameras,
        state_path=config.state_path,
        report_path=config.report_path,
        weight_stable_sample_count=config.weight_stable_sample_count,
        weight_stable_max_spread_grams=config.weight_stable_max_spread_grams,
        weight_sample_interval_ms=config.weight_sample_interval_ms,
        weight_sample_timeout_ms=config.weight_sample_timeout_ms,
        camera_review_ttl_ms=config.camera_review_ttl_ms,
    )


def main() -> int:
    config = AcceptanceConfiguration.from_mapping(os.environ)
    executor = build_executor(config)
    try:
        executor.open()
    except (AcceptanceLockBusy, AcceptanceStorageError, AcceptanceError) as error:
        raise SystemExit(f"factory acceptance executor cannot start: {error}") from error
    server: Optional[AcceptanceUnixServer] = None
    try:
        portal_group_id = grp.getgrnam("ecobin-factory-web").gr_gid
        public_projection = PublicAtomicJsonFile(
            PUBLIC_PROJECTION_PATH,
            mode=0o640,
            directory_mode=0o750,
            owner=root_group_owner(portal_group_id),
            maximum_bytes=64 * 1024,
        )
        controller = AcceptanceCommandController(
            executor,
            config,
            image_release_id=_image_release_id(),
            boot_id=_boot_id(),
            wall_time_trusted=_wall_time_trusted(),
            projection_writer=public_projection.write_object,
        )
        controller.projection()
        server = AcceptanceUnixServer(
            CONTROL_SOCKET,
            controller,
            portal_group="ecobin-factory-web",
        )

        def stop(_signal: int, _frame: object) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.25)
    finally:
        if server is not None:
            server.server_close()
        executor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

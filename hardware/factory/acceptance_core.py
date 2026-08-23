"""Isolated, fail-safe offline hardware acceptance state machine.

The executor is intentionally not an HTTP handler.  P7's later local portal
adapter may expose a narrow API, but this core can be exercised directly and
does not import or instantiate EdgeStore, MQTT, COS, enrollment, production
photo management, or any backend client.
"""

from __future__ import annotations

import copy
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from factory.acceptance_hardware import (
    AcceptanceHardwareError,
    FixedFrameAcceptanceMcu,
    FixedRoleCameraProbe,
    NetworkAccessForbidden,
    deny_network_access,
    identities_equal,
    sanitize_firmware_identity,
    sanitize_self_test,
)
from factory.acceptance_storage import (
    AcceptanceLease,
    AtomicJsonFile,
)


STATE_SCHEMA_VERSION = 2
LEGACY_STATE_SCHEMA_VERSION = 1
LEGACY_SAFETY_FAILURE_PHASE = "LEGACY_ACTION_SAFETY_FAILURE_READY"
REPORT_SCHEMA_VERSION = 1
REPORT_STATUSES = {
    "NOT_RUN",
    "RUNNING",
    "PASSED",
    "FAILED",
    "RECOVERY_REQUIRED",
}
ACTION_TYPES = {"DELIVERY", "CLEAN"}
REQUIRED_PASSED_CHECKS = (
    "mcu",
    "weight",
    "upgradeLine",
    "cameras",
    "delivery",
    "clean",
)
DEFAULT_STATE_PATH = Path("/var/lib/ecobin/factory-test/state.json")
DEFAULT_REPORT_PATH = Path("/var/lib/ecobin/factory-test/report.json")
DEFAULT_INSTANCE_LOCK = Path("/run/lock/ecobin/factory-acceptance.lock")
DEFAULT_UART_LOCK = Path("/run/lock/ecobin/uart5.lock")
DEFAULT_WEIGHT_STABLE_SAMPLE_COUNT = 3
DEFAULT_WEIGHT_STABLE_MAX_SPREAD_GRAMS = 2
DEFAULT_WEIGHT_SAMPLE_INTERVAL_MS = 100
DEFAULT_WEIGHT_SAMPLE_TIMEOUT_MS = 3_000
DEFAULT_CAMERA_REVIEW_TTL_MS = 300_000


class AcceptanceError(RuntimeError):
    """Stable executor result without leaking an underlying exception."""

    def __init__(self, code: str, *, recovery_required: bool = False) -> None:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code) is None:
            raise ValueError("acceptance error code is invalid")
        self.code = code
        self.recovery_required = recovery_required
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", value):
        return value
    return fallback


def _check_passed(state: dict, name: str) -> bool:
    check = (state.get("checks") or {}).get(name, {})
    if check.get("status") != "PASSED":
        return False
    if name == "delivery":
        return check.get("operatorAreaSafeConfirmed") is True
    if name == "clean":
        return check.get("cleanDoorConfirmed") is True
    return True


def _validate_release_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value
    ) is None:
        raise ValueError("image release ID is invalid")
    return value


def _validate_boot_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value
    ) is None:
        raise ValueError("boot ID is invalid")
    return value


def _validate_isolated_path(path: Path, *, kind: str) -> None:
    resolved = path.resolve(strict=False)
    production_db = Path("/var/lib/ecobin/edge.db").resolve(strict=False)
    production_photo_roots = (
        Path("/var/lib/ecobin/photos").resolve(strict=False),
        Path("/var/lib/ecobin/hardware/photos").resolve(strict=False),
    )
    if resolved == production_db or resolved.name == "edge.db":
        raise ValueError(f"acceptance {kind} must not be a production EdgeStore")
    if any(resolved == root or root in resolved.parents for root in production_photo_roots):
        raise ValueError(f"acceptance {kind} must not be inside production photos")


class FactoryAcceptanceExecutor:
    """Own one offline hardware acceptance run and its recovery lock."""

    def __init__(
        self,
        *,
        mcu: FixedFrameAcceptanceMcu,
        bootloader: object,
        cameras: FixedRoleCameraProbe,
        state_path: Path | str = DEFAULT_STATE_PATH,
        report_path: Path | str = DEFAULT_REPORT_PATH,
        instance_lock_path: Path | str = DEFAULT_INSTANCE_LOCK,
        uart_lock_path: Path | str = DEFAULT_UART_LOCK,
        fault_hook: Optional[Callable[[str], None]] = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        weight_stable_sample_count: int = DEFAULT_WEIGHT_STABLE_SAMPLE_COUNT,
        weight_stable_max_spread_grams: int = DEFAULT_WEIGHT_STABLE_MAX_SPREAD_GRAMS,
        weight_sample_interval_ms: int = DEFAULT_WEIGHT_SAMPLE_INTERVAL_MS,
        weight_sample_timeout_ms: int = DEFAULT_WEIGHT_SAMPLE_TIMEOUT_MS,
        camera_review_ttl_ms: int = DEFAULT_CAMERA_REVIEW_TTL_MS,
    ) -> None:
        if not isinstance(mcu, FixedFrameAcceptanceMcu):
            raise TypeError("mcu must be FixedFrameAcceptanceMcu")
        for method in (
            "enter_system_bootloader",
            "boot_application",
            "force_application_selection",
            "probe_read_only",
        ):
            if not callable(getattr(bootloader, method, None)):
                raise TypeError(f"bootloader does not implement {method}")
        if not isinstance(cameras, FixedRoleCameraProbe):
            raise TypeError("cameras must be FixedRoleCameraProbe")
        state = Path(state_path)
        report = Path(report_path)
        if state == report:
            raise ValueError("acceptance state and report paths must differ")
        _validate_isolated_path(state, kind="state")
        _validate_isolated_path(report, kind="report")
        self.mcu = mcu
        self.bootloader = bootloader
        self.cameras = cameras
        self._fault_hook = fault_hook
        self._monotonic = monotonic
        self._sleeper = sleeper
        for name, value, minimum, maximum in (
            ("weight_stable_sample_count", weight_stable_sample_count, 1, 10),
            (
                "weight_stable_max_spread_grams",
                weight_stable_max_spread_grams,
                0,
                10,
            ),
            ("weight_sample_interval_ms", weight_sample_interval_ms, 0, 1_000),
            ("weight_sample_timeout_ms", weight_sample_timeout_ms, 1, 15_000),
            ("camera_review_ttl_ms", camera_review_ttl_ms, 1, 900_000),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{name} is invalid")
        self._weight_stable_sample_count = weight_stable_sample_count
        self._weight_stable_max_spread_grams = weight_stable_max_spread_grams
        self._weight_sample_interval_ms = weight_sample_interval_ms
        self._weight_sample_timeout_ms = weight_sample_timeout_ms
        self._camera_review_ttl_ms = camera_review_ttl_ms
        self._state_file = AtomicJsonFile(
            state,
            fault_hook=fault_hook,
            fault_prefix="acceptance_state",
        )
        self._report_file = AtomicJsonFile(
            report,
            fault_hook=fault_hook,
            fault_prefix="acceptance_report",
        )
        self._lease = AcceptanceLease(instance_lock_path, uart_lock_path)
        self._opened = False

    def __enter__(self) -> "FactoryAcceptanceExecutor":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def open(self) -> None:
        if self._opened:
            return
        self._lease.acquire()
        try:
            self.mcu.open()
            self._opened = True
            self._reconcile_finalizing_report()
            self._reconcile_interrupted_armed_action()
            self._reconcile_interrupted_camera_review()
        except Exception:
            try:
                self.mcu.close()
            finally:
                self._lease.release()
            raise

    def close(self) -> None:
        if not self._opened:
            return
        self._opened = False
        try:
            self.mcu.close()
        finally:
            self._lease.release()

    def _require_open(self) -> None:
        if not self._opened or not self._lease.acquired:
            raise AcceptanceError("ACCEPTANCE_EXECUTOR_NOT_OPEN")

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _load_state(self) -> dict:
        value = self._state_file.read()
        if value is None:
            return {
                "schemaVersion": STATE_SCHEMA_VERSION,
                "revision": 0,
                "status": "NOT_RUN",
                "phase": "NOT_RUN",
                "checks": {},
                "recovery": None,
                "activeAction": None,
            }
        if value.get("schemaVersion") == LEGACY_STATE_SCHEMA_VERSION:
            if value.get("status") not in REPORT_STATUSES:
                raise AcceptanceError("ACCEPTANCE_STATE_STATUS_INVALID")
            if not isinstance(value.get("revision"), int) or isinstance(
                value.get("revision"), bool
            ):
                raise AcceptanceError("ACCEPTANCE_STATE_REVISION_INVALID")
            value = self._migrate_legacy_state(value)
        elif value.get("schemaVersion") != STATE_SCHEMA_VERSION:
            raise AcceptanceError("ACCEPTANCE_STATE_SCHEMA_UNSUPPORTED")
        if value.get("status") not in REPORT_STATUSES:
            raise AcceptanceError("ACCEPTANCE_STATE_STATUS_INVALID")
        if not isinstance(value.get("revision"), int):
            raise AcceptanceError("ACCEPTANCE_STATE_REVISION_INVALID")
        return value

    def _migrate_legacy_state(self, legacy: dict) -> dict:
        """Upgrade schema 1 without trusting its pre-action safety booleans.

        Schema 1 could mark AA/EE complete without binding a separate
        post-action physical observation.  An unfinished recovery remains
        locked so the new recovery path can reset and re-prove the MCU.  A
        completed or failed-safe legacy action without the new proof is made
        terminal FAILED and can only be repeated in a fresh acceptance run.
        """

        state = copy.deepcopy(legacy)
        checks = state.get("checks")
        if not isinstance(checks, dict):
            checks = {}
            state["checks"] = checks
        unsafe_actions: list[str] = []
        for name, confirmation in (
            ("delivery", "operatorAreaSafeConfirmed"),
            ("clean", "cleanDoorConfirmed"),
        ):
            check = checks.get(name)
            if (
                isinstance(check, dict)
                and check.get("status") in {"PASSED", "FAILED_SAFE"}
                and check.get(confirmation) is not True
            ):
                unsafe_actions.append(name)
        recovery_pending = (
            state.get("status") == "RECOVERY_REQUIRED"
            or isinstance(state.get("recovery"), dict)
            or isinstance(state.get("activeAction"), dict)
        )
        if unsafe_actions and not recovery_pending:
            for name in unsafe_actions:
                check = checks[name]
                check["status"] = "FAILED"
                check["resultCode"] = (
                    "LEGACY_ACTION_REQUIRES_NEW_ACCEPTANCE_RUN"
                )
            state["status"] = "FAILED"
            state["phase"] = "LEGACY_ACTION_SAFETY_CONFIRMATION_REQUIRED"
            state["recovery"] = None
            state["activeAction"] = None
        elif unsafe_actions:
            # Preserve the current hardware recovery lock, but remember that
            # this run can never pass.  Once the in-flight action is made
            # physically safe, the recovery/confirmation path converts the
            # run to a durable FAILED report instead of exposing a dead-end
            # RUNNING state.
            state["legacySafetyFailuresPending"] = unsafe_actions
        state["schemaVersion"] = STATE_SCHEMA_VERSION
        state["revision"] = int(state["revision"]) + 1
        self._state_file.write(state)
        return copy.deepcopy(state)

    @staticmethod
    def _prepare_legacy_safety_failure(state: dict) -> bool:
        pending = state.get("legacySafetyFailuresPending")
        if pending is None:
            return False
        if (
            not isinstance(pending, list)
            or not pending
            or any(name not in {"delivery", "clean"} for name in pending)
        ):
            raise AcceptanceError("ACCEPTANCE_LEGACY_SAFETY_STATE_INVALID")
        if (
            state.get("status") == "RECOVERY_REQUIRED"
            or state.get("recovery") is not None
            or state.get("activeAction") is not None
        ):
            return False
        checks = state.setdefault("checks", {})
        for name in pending:
            check = checks.get(name)
            if not isinstance(check, dict):
                check = {}
                checks[name] = check
            check["status"] = "FAILED"
            check["resultCode"] = (
                "LEGACY_ACTION_REQUIRES_NEW_ACCEPTANCE_RUN"
            )
        state.pop("legacySafetyFailuresPending", None)
        state["status"] = "RUNNING"
        state["phase"] = LEGACY_SAFETY_FAILURE_PHASE
        return True

    def _save_state(self, state: dict) -> dict:
        value = copy.deepcopy(state)
        value["schemaVersion"] = STATE_SCHEMA_VERSION
        value["revision"] = int(value.get("revision", 0)) + 1
        self._state_file.write(value)
        state.clear()
        state.update(copy.deepcopy(value))
        return copy.deepcopy(value)

    def snapshot(self) -> dict:
        self._require_open()
        return copy.deepcopy(self._load_state())

    def begin_run(
        self,
        *,
        image_release_id: str,
        boot_id: str,
        wall_time_trusted: bool,
        hardware_config_digest: str,
        trusted_wall_time_utc: Optional[str] = None,
        restart_terminal: bool = False,
    ) -> dict:
        self._require_open()
        release = _validate_release_id(image_release_id)
        boot = _validate_boot_id(boot_id)
        if (
            not isinstance(hardware_config_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", hardware_config_digest) is None
        ):
            raise ValueError("hardware config digest is invalid")
        if not isinstance(wall_time_trusted, bool):
            raise ValueError("wall_time_trusted must be boolean")
        if wall_time_trusted and not trusted_wall_time_utc:
            trusted_wall_time_utc = _utc_now()
        if trusted_wall_time_utc is not None and (
            not isinstance(trusted_wall_time_utc, str)
            or len(trusted_wall_time_utc) > 64
        ):
            raise ValueError("trusted wall time is invalid")
        state = self._load_state()
        if state["status"] in {"RUNNING", "RECOVERY_REQUIRED"}:
            if (
                state.get("imageReleaseId") == release
                and state.get("bootId") == boot
                and state.get("hardwareConfigDigest") == hardware_config_digest
            ):
                return copy.deepcopy(state)
            raise AcceptanceError(
                "EXISTING_ACCEPTANCE_RUN_NOT_FINISHED",
                recovery_required=state["status"] == "RECOVERY_REQUIRED",
            )
        if state["status"] in {"PASSED", "FAILED"} and not restart_terminal:
            if (
                state.get("imageReleaseId") != release
                or state.get("hardwareConfigDigest") != hardware_config_digest
            ):
                raise AcceptanceError(
                    "TERMINAL_ACCEPTANCE_BOUND_TO_DIFFERENT_CONFIG"
                )
            return copy.deepcopy(state)
        started_monotonic_ms = int(self._monotonic() * 1000)
        state = {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "revision": state.get("revision", 0),
            "status": "RUNNING",
            "phase": "STARTED",
            "imageReleaseId": release,
            "bootId": boot,
            "hardwareConfigDigest": hardware_config_digest,
            "timing": {
                "startedMonotonicMs": started_monotonic_ms,
                "wallTimeTrusted": wall_time_trusted,
                "trustedStartedAtUtc": trusted_wall_time_utc,
            },
            "mcuIdentity": None,
            "checks": {},
            "recovery": None,
            "activeAction": None,
        }
        return self._save_state(state)

    @staticmethod
    def _require_running(state: dict) -> None:
        if state.get("status") == "RECOVERY_REQUIRED" or state.get("recovery"):
            raise AcceptanceError(
                "ACCEPTANCE_RECOVERY_REQUIRED",
                recovery_required=True,
            )
        if state.get("status") != "RUNNING":
            raise AcceptanceError("ACCEPTANCE_RUN_NOT_RUNNING")
        if state.get("phase") == "FINALIZING_REPORT":
            raise AcceptanceError("ACCEPTANCE_REPORT_FINALIZATION_PENDING")

    def _query_identity(self) -> dict:
        with deny_network_access():
            return sanitize_firmware_identity(self.mcu.query_identity())

    def _query_self_test(self) -> dict:
        with deny_network_access():
            return sanitize_self_test(self.mcu.query_self_test())

    def _query_run_bound_mcu(self, state: dict) -> tuple[dict, dict]:
        """Re-prove the immutable F3 identity before a physical mutation."""

        expected = state.get("mcuIdentity")
        if not isinstance(expected, dict):
            raise AcceptanceError("ACCEPTANCE_MCU_IDENTITY_BINDING_INVALID")
        try:
            current = self._query_identity()
            if not identities_equal(expected, current):
                raise AcceptanceError(
                    "MCU_IDENTITY_CHANGED_SINCE_INITIAL_CHECK"
                )
            self_test = self._query_self_test()
        except AcceptanceHardwareError as error:
            raise AcceptanceError(error.code) from error
        return current, self_test

    def _stable_weight(self) -> tuple[int, list[int]]:
        """Return the median of one bounded stable window of F1 samples."""

        deadline = self._monotonic() + self._weight_sample_timeout_ms / 1000.0
        window: list[int] = []
        maximum_reads = max(
            self._weight_stable_sample_count,
            self._weight_sample_timeout_ms
            // max(1, self._weight_sample_interval_ms)
            + self._weight_stable_sample_count,
        )
        reads = 0
        while reads < maximum_reads:
            reads += 1
            weight = self._query_self_test()["weightGrams"]
            window.append(weight)
            if len(window) > self._weight_stable_sample_count:
                window.pop(0)
            if (
                len(window) == self._weight_stable_sample_count
                and max(window) - min(window)
                <= self._weight_stable_max_spread_grams
            ):
                ordered = sorted(window)
                return ordered[len(ordered) // 2], list(window)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            interval = min(
                remaining,
                self._weight_sample_interval_ms / 1000.0,
            )
            if interval > 0:
                self._sleeper(interval)
        raise AcceptanceHardwareError("WEIGHT_READING_NOT_STABLE")

    def _record_weight_failure(self, state: dict, code: str) -> None:
        weight = state.setdefault("checks", {}).setdefault("weight", {})
        weight["status"] = "FAILED"
        weight["resultCode"] = _stable_code(code, "WEIGHT_CHECK_FAILED")
        state["phase"] = "WEIGHT_CHECK_FAILED"
        self._save_state(state)

    def check_mcu(self) -> dict:
        self._require_open()
        state = self._load_state()
        self._require_running(state)
        try:
            identity = self._query_identity()
            self_test = self._query_self_test()
        except AcceptanceHardwareError as error:
            state["checks"]["mcu"] = {
                "status": "FAILED",
                "resultCode": error.code,
            }
            state["phase"] = "MCU_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError(error.code) from error
        state["mcuIdentity"] = identity
        state["checks"]["mcu"] = {
            "status": "PASSED",
            "resultCode": "MCU_REVISION_2_AND_F1_HEALTHY",
            "selfTest": self_test,
        }
        state["phase"] = "MCU_CHECK_PASSED"
        return self._save_state(state)

    def capture_empty_weight(self) -> dict:
        self._require_open()
        state = self._load_state()
        self._require_running(state)
        if not _check_passed(state, "mcu"):
            raise AcceptanceError("MCU_CHECK_REQUIRED")
        try:
            value, samples = self._stable_weight()
        except AcceptanceHardwareError as error:
            self._record_weight_failure(state, error.code)
            raise AcceptanceError(error.code) from error
        state["checks"]["weight"] = {
            "status": "RUNNING",
            "resultCode": "WAITING_FOR_500G_LOAD",
            "emptyWeightGrams": value,
            "emptyStableSamplesGrams": samples,
            "targetDeltaGrams": 500,
            "toleranceGrams": 10,
            "stableSampleCount": self._weight_stable_sample_count,
            "stableMaxSpreadGrams": self._weight_stable_max_spread_grams,
            "sampleIntervalMs": self._weight_sample_interval_ms,
            "sampleTimeoutMs": self._weight_sample_timeout_ms,
        }
        state["phase"] = "WAITING_FOR_500G_LOAD"
        return self._save_state(state)

    def capture_loaded_weight(self) -> dict:
        self._require_open()
        state = self._load_state()
        self._require_running(state)
        weight = state.get("checks", {}).get("weight", {})
        if weight.get("status") != "RUNNING" or not isinstance(
            weight.get("emptyWeightGrams"), int
        ):
            raise AcceptanceError("EMPTY_WEIGHT_REQUIRED")
        try:
            loaded, samples = self._stable_weight()
        except AcceptanceHardwareError as error:
            self._record_weight_failure(state, error.code)
            raise AcceptanceError(error.code) from error
        delta = loaded - weight["emptyWeightGrams"]
        weight["loadedWeightGrams"] = loaded
        weight["loadedStableSamplesGrams"] = samples
        weight["deltaGrams"] = delta
        if not 490 <= delta <= 510:
            weight["status"] = "FAILED"
            weight["resultCode"] = "WEIGHT_DELTA_OUT_OF_RANGE"
            state["phase"] = "WEIGHT_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError("WEIGHT_DELTA_OUT_OF_RANGE")
        weight["resultCode"] = "WAITING_FOR_WEIGHT_REMOVAL"
        state["phase"] = "WAITING_FOR_WEIGHT_REMOVAL"
        return self._save_state(state)

    def confirm_weight_removed(self) -> dict:
        self._require_open()
        state = self._load_state()
        self._require_running(state)
        weight = state.get("checks", {}).get("weight", {})
        if weight.get("resultCode") != "WAITING_FOR_WEIGHT_REMOVAL":
            raise AcceptanceError("LOADED_WEIGHT_REQUIRED")
        try:
            removed, samples = self._stable_weight()
        except AcceptanceHardwareError as error:
            self._record_weight_failure(state, error.code)
            raise AcceptanceError(error.code) from error
        weight["removedWeightGrams"] = removed
        weight["removedStableSamplesGrams"] = samples
        if abs(removed - weight["emptyWeightGrams"]) > weight["toleranceGrams"]:
            weight["status"] = "FAILED"
            weight["resultCode"] = "TEST_WEIGHT_NOT_REMOVED"
            state["phase"] = "WEIGHT_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError("TEST_WEIGHT_NOT_REMOVED")
        weight["status"] = "PASSED"
        weight["resultCode"] = "WEIGHT_500G_WITHIN_490_510_AND_REMOVED"
        state["phase"] = "WEIGHT_CHECK_PASSED"
        return self._save_state(state)

    def capture_cameras(self) -> dict:
        """Capture a fresh volatile pair before asking the operator to confirm."""

        self._require_open()
        state = self._load_state()
        self._require_running(state)
        try:
            with deny_network_access():
                summary = self.cameras.capture_pending()
        except AcceptanceHardwareError as error:
            state["checks"]["cameras"] = {
                "status": "FAILED",
                "resultCode": error.code,
            }
            state["phase"] = "CAMERA_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError(error.code) from error
        except NetworkAccessForbidden as error:
            state["checks"]["cameras"] = {
                "status": "FAILED",
                "resultCode": "NETWORK_ACCESS_FORBIDDEN",
            }
            state["phase"] = "CAMERA_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError("NETWORK_ACCESS_FORBIDDEN") from error
        state["checks"]["cameras"] = {
            "status": "RUNNING",
            "resultCode": summary["resultCode"],
            "outside": summary["outside"],
            "inside": summary["inside"],
            "reviewNonce": summary["reviewNonce"],
            "reviewCreatedMonotonicMs": int(self._monotonic() * 1000),
            "reviewExpiresMonotonicMs": (
                int(self._monotonic() * 1000) + self._camera_review_ttl_ms
            ),
        }
        state["phase"] = "WAITING_FOR_CAMERA_ROLE_CONFIRMATION"
        return self._save_state(state)

    def confirm_cameras(
        self,
        *,
        review_nonce: str,
        outside_role_confirmed: bool,
        inside_role_confirmed: bool,
    ) -> dict:
        self._require_open()
        state = self._load_state()
        self._require_running(state)
        cameras = state.get("checks", {}).get("cameras", {})
        if (
            cameras.get("status") != "RUNNING"
            or cameras.get("resultCode")
            != "WAITING_FOR_CAMERA_ROLE_CONFIRMATION"
        ):
            raise AcceptanceError("CAMERA_CAPTURE_REQUIRED")
        if review_nonce != cameras.get("reviewNonce"):
            raise AcceptanceError("CAMERA_REVIEW_NONCE_MISMATCH")
        if (
            not isinstance(cameras.get("reviewExpiresMonotonicMs"), int)
            or int(self._monotonic() * 1000)
            > cameras["reviewExpiresMonotonicMs"]
        ):
            self.cameras.discard_all_pending()
            cameras["status"] = "FAILED"
            cameras["resultCode"] = "CAMERA_REVIEW_EXPIRED"
            cameras.pop("reviewNonce", None)
            state["phase"] = "CAMERA_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError("CAMERA_REVIEW_EXPIRED")
        if outside_role_confirmed is not True or inside_role_confirmed is not True:
            raise AcceptanceError("CAMERA_ROLE_NOT_CONFIRMED")
        try:
            with deny_network_access():
                summary = self.cameras.confirm_pending(review_nonce, cameras)
        except AcceptanceHardwareError as error:
            cameras["status"] = "FAILED"
            cameras["resultCode"] = error.code
            cameras.pop("reviewNonce", None)
            state["phase"] = "CAMERA_CHECK_FAILED"
            self._save_state(state)
            raise AcceptanceError(error.code) from error
        state["checks"]["cameras"] = {
            "status": "PASSED",
            "resultCode": summary["resultCode"],
            "outside": summary["outside"],
            "inside": summary["inside"],
        }
        state["phase"] = "CAMERA_CHECK_PASSED"
        return self._save_state(state)

    @staticmethod
    def _prepare_response_ok(response: dict) -> bool:
        return bool(
            isinstance(response, dict)
            and response.get("queryStatus") == "OK"
            and response.get("mode") == 2
            and response.get("statusCode") == 0
            and response.get("protocolRevision") == 2
            and response.get("safeFlags") == 0x1F
            and response.get("executed") is True
        )

    def _arm_recovery(
        self,
        state: dict,
        *,
        context: str,
        phase: str,
        original_identity: dict,
        reason: str,
    ) -> None:
        state["status"] = "RECOVERY_REQUIRED"
        state["phase"] = phase
        state["recovery"] = {
            "required": True,
            "context": context,
            "originalMcuIdentity": copy.deepcopy(original_identity),
            "resultCode": _stable_code(reason, "RECOVERY_REQUIRED"),
            "hardwareVerified": False,
        }

    def _update_recovery_reason(self, code: str) -> None:
        state = self._load_state()
        recovery = state.get("recovery")
        if isinstance(recovery, dict):
            recovery["resultCode"] = _stable_code(code, "RECOVERY_REQUIRED")
            state["status"] = "RECOVERY_REQUIRED"
            self._save_state(state)

    def check_upgrade_line(self) -> dict:
        """F2 prepare + BOOT0/NRST + read-only ROM probe + app re-proof."""

        self._require_open()
        state = self._load_state()
        self._require_running(state)
        if not _check_passed(state, "mcu"):
            raise AcceptanceError("MCU_CHECK_REQUIRED")
        prior_upgrade = state.get("checks", {}).get("upgradeLine")
        if isinstance(prior_upgrade, dict):
            raise AcceptanceError(
                "UPGRADE_LINE_ALREADY_ATTEMPTED_IN_CURRENT_RUN"
            )
        identity, _self_test = self._query_run_bound_mcu(state)
        self._arm_recovery(
            state,
            context="UPGRADE_LINE",
            phase="F2_COMMAND_MAY_HAVE_BEEN_SENT",
            original_identity=identity,
            reason="F2_COMMAND_MAY_HAVE_BEEN_SENT",
        )
        state["checks"]["upgradeLine"] = {
            "status": "RECOVERY_REQUIRED",
            "resultCode": "F2_COMMAND_MAY_HAVE_BEEN_SENT",
            "prepareSendAttempts": 1,
        }
        self._save_state(state)
        self._fault("upgrade_line.after_prepare_journal")
        try:
            with deny_network_access():
                prepare = self.mcu.execute_update_prepare()
            if not self._prepare_response_ok(prepare):
                raise AcceptanceHardwareError("MCU_F2_PREPARE_FAILED")
            state = self._load_state()
            state["phase"] = "F2_PREPARED"
            state["checks"]["upgradeLine"]["resultCode"] = "MCU_F2_PREPARED"
            self._save_state(state)
            self._fault("upgrade_line.after_prepare_response")

            self.mcu.close()
            state = self._load_state()
            state["phase"] = "ENTERING_STM32_ROM"
            self._save_state(state)
            self._fault("upgrade_line.before_boot0_nrst")
            with deny_network_access():
                self.bootloader.enter_system_bootloader()
            state = self._load_state()
            state["phase"] = "STM32_ROM_MAY_BE_ACTIVE"
            state["checks"]["upgradeLine"]["resultCode"] = "STM32_ROM_MAY_BE_ACTIVE"
            self._save_state(state)
            self._fault("upgrade_line.after_boot0_nrst")
            with deny_network_access():
                probe = self.bootloader.probe_read_only()
            if probe.get("detected") is not True:
                raise AcceptanceHardwareError("STM32_ROM_NOT_DETECTED")
            if probe.get("deviceId") != "0x0410":
                raise AcceptanceHardwareError("STM32_ROM_IDENTITY_INVALID")
            state = self._load_state()
            state["phase"] = "RETURNING_TO_APPLICATION"
            state["checks"]["upgradeLine"]["resultCode"] = probe.get(
                "resultCode", "STM32_ROM_DETECTED_READ_ONLY"
            )
            self._save_state(state)
            self._fault("upgrade_line.after_rom_probe")

            with deny_network_access():
                self.bootloader.boot_application()
            self.mcu.open()
            self.mcu.clear_input_for_recovery()
            invalid_marker = self.mcu.business_input_marker()
            recovered_identity = self._query_identity()
            if (
                not identities_equal(identity, recovered_identity)
                or not identities_equal(
                    state.get("mcuIdentity", {}), recovered_identity
                )
            ):
                raise AcceptanceHardwareError("MCU_IDENTITY_CHANGED_AFTER_ROM_PROBE")
            self._query_self_test()
            self.mcu.require_business_quiet(
                quiet_ms=100,
                invalid_marker=invalid_marker,
            )
        except AcceptanceHardwareError as error:
            self._update_recovery_reason(error.code)
            raise AcceptanceError(error.code, recovery_required=True) from error
        except Exception as error:
            self._update_recovery_reason("UPGRADE_LINE_UNEXPECTED_FAILURE")
            raise AcceptanceError(
                "UPGRADE_LINE_UNEXPECTED_FAILURE",
                recovery_required=True,
            ) from error
        state = self._load_state()
        state["status"] = "RUNNING"
        state["phase"] = "UPGRADE_LINE_PASSED"
        state["recovery"] = None
        state["checks"]["upgradeLine"] = {
            "status": "PASSED",
            "resultCode": "F2_BOOT0_NRST_ROM_READ_ONLY_AND_APP_RECOVERY_PASSED",
            "prepareSendAttempts": 1,
            "romWritePerformed": False,
            "romDeviceId": "0x0410",
        }
        return self._save_state(state)

    @staticmethod
    def _action_check_name(action: str) -> str:
        if action == "DELIVERY":
            return "delivery"
        if action == "CLEAN":
            return "clean"
        raise ValueError("action must be DELIVERY or CLEAN")

    def _require_action_prerequisites(self, state: dict, action: str) -> None:
        for name in ("mcu", "weight", "upgradeLine", "cameras"):
            if not _check_passed(state, name):
                raise AcceptanceError(f"{name.upper()}_CHECK_REQUIRED")
        if state.get("activeAction") is not None:
            raise AcceptanceError("ANOTHER_FACTORY_ACTION_IS_ACTIVE")
        if action == "CLEAN" and not _check_passed(state, "delivery"):
            raise AcceptanceError("DELIVERY_SAFE_CHECK_REQUIRED")
        check_name = self._action_check_name(action)
        prior = state.get("checks", {}).get(check_name)
        if not isinstance(prior, dict):
            return
        proven_not_sent = (
            prior.get("status") == "FAILED_SAFE"
            and prior.get("resultCode")
            == f"{action}_INTERRUPTED_BEFORE_COMMAND"
            and prior.get("sendAttempts") == 0
        )
        if not proven_not_sent:
            raise AcceptanceError(f"{action}_ALREADY_ATTEMPTED_IN_CURRENT_RUN")

    def run_action(
        self,
        action: str,
        *,
        operator_area_safe_confirmed: bool,
        timeout_ms: int = 60_000,
        quiet_ms: int = 150,
    ) -> dict:
        """Send BB+AA or EE once, persist the final result, then verify safety."""

        self._require_open()
        if action not in ACTION_TYPES:
            raise ValueError("action must be DELIVERY or CLEAN")
        if operator_area_safe_confirmed is not True:
            raise AcceptanceError("OPERATOR_SAFETY_CONFIRMATION_REQUIRED")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        state = self._load_state()
        self._require_running(state)
        self._require_action_prerequisites(state, action)
        check_name = self._action_check_name(action)
        identity, _self_test = self._query_run_bound_mcu(state)
        active = {
            "type": action,
            "phase": "ARMED",
            "sendAttempts": 0,
            "originalMcuIdentity": identity,
            "result": None,
        }
        state["activeAction"] = active
        state["phase"] = f"{action}_ARMED"
        state["checks"][check_name] = {
            "status": "RUNNING",
            "resultCode": f"{action}_ARMED",
            "sendAttempts": 0,
        }
        self._save_state(state)
        self._fault(f"{action.lower()}.after_armed")

        state = self._load_state()
        active = state["activeAction"]
        active["phase"] = "COMMAND_MAY_HAVE_BEEN_SENT"
        active["sendAttempts"] = 1
        self._arm_recovery(
            state,
            context=action,
            phase=f"{action}_COMMAND_MAY_HAVE_BEEN_SENT",
            original_identity=identity,
            reason=f"{action}_COMMAND_MAY_HAVE_BEEN_SENT",
        )
        state["checks"][check_name] = {
            "status": "RECOVERY_REQUIRED",
            "resultCode": f"{action}_COMMAND_MAY_HAVE_BEEN_SENT",
            "sendAttempts": 1,
        }
        self._save_state(state)
        self._fault(f"{action.lower()}.after_command_journal")
        try:
            with deny_network_access():
                self.mcu.write_action_once(action)
            self._fault(f"{action.lower()}.after_serial_write")
            state = self._load_state()
            state["phase"] = f"{action}_WAITING_FINAL_RESULT"
            state["activeAction"]["phase"] = "WAITING_FINAL_RESULT"
            state["checks"][check_name]["resultCode"] = "WAITING_FINAL_RESULT"
            self._save_state(state)
            self._fault(f"{action.lower()}.after_waiting_journal")

            with deny_network_access():
                result = self.mcu.await_final_result(action, timeout_ms)
            state = self._load_state()
            state["phase"] = f"{action}_RESULT_RECORDED"
            state["activeAction"]["phase"] = "RESULT_RECORDED"
            state["activeAction"]["result"] = result
            state["checks"][check_name]["resultCode"] = "FINAL_RESULT_RECORDED"
            state["checks"][check_name]["result"] = result
            self._save_state(state)
            self._fault(f"{action.lower()}.after_result_recorded")

            invalid_marker = self.mcu.business_input_marker()
            recovered_identity = self._query_identity()
            if (
                not identities_equal(identity, recovered_identity)
                or not identities_equal(
                    state.get("mcuIdentity", {}), recovered_identity
                )
            ):
                raise AcceptanceHardwareError("MCU_IDENTITY_CHANGED_AFTER_ACTION")
            self_test = self._query_self_test()
            self.mcu.require_business_quiet(
                quiet_ms=quiet_ms,
                invalid_marker=invalid_marker,
            )
        except AcceptanceHardwareError as error:
            self._update_recovery_reason(error.code)
            raise AcceptanceError(error.code, recovery_required=True) from error
        except Exception as error:
            self._update_recovery_reason(f"{action}_UNEXPECTED_FAILURE")
            raise AcceptanceError(
                f"{action}_UNEXPECTED_FAILURE",
                recovery_required=True,
            ) from error
        state = self._load_state()
        result = state["activeAction"]["result"]
        if action == "DELIVERY":
            # The boolean accepted before BB+AA only proves that it was safe
            # to start the mechanism.  DD and the post-action MCU checks
            # cannot prove that the delivery opening is no longer obstructed
            # or otherwise unsafe.  Keep the exclusive recovery lock until a
            # new, separately persisted operator observation is submitted.
            state["phase"] = "DELIVERY_AWAITING_AREA_CONFIRMATION"
            state["activeAction"]["phase"] = "AWAITING_AREA_CONFIRMATION"
            state["recovery"]["hardwareVerified"] = True
            state["recovery"]["awaitingAreaSafetyConfirmation"] = True
            state["recovery"]["deliveryConfirmationDisposition"] = (
                "PASS_AFTER_CONFIRMATION"
            )
            state["recovery"]["resultCode"] = (
                "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
            )
            state["checks"][check_name] = {
                "status": "RECOVERY_REQUIRED",
                "resultCode": (
                    "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
                ),
                "sendAttempts": 1,
                "result": result,
                "postActionSelfTest": self_test,
                "operatorAreaSafeConfirmed": False,
            }
            saved = self._save_state(state)
            self._fault("delivery.after_awaiting_area_confirmation")
            return saved
        if action == "CLEAN":
            # A boolean submitted before EE cannot prove the door leaf is
            # closed after EF.  Persist the completed electrical/protocol
            # checks while retaining the exclusive recovery lock, then wait
            # for a separate operator observation bound to this state.
            state["phase"] = "CLEAN_AWAITING_DOOR_CONFIRMATION"
            state["activeAction"]["phase"] = "AWAITING_DOOR_CONFIRMATION"
            state["recovery"]["hardwareVerified"] = True
            state["recovery"]["awaitingDoorConfirmation"] = True
            state["recovery"]["resultCode"] = (
                "CLEAN_DOOR_CONFIRMATION_REQUIRED"
            )
            state["checks"][check_name] = {
                "status": "RECOVERY_REQUIRED",
                "resultCode": "CLEAN_DOOR_CONFIRMATION_REQUIRED",
                "sendAttempts": 1,
                "result": result,
                "postActionSelfTest": self_test,
                "cleanDoorConfirmed": False,
            }
            saved = self._save_state(state)
            self._fault("clean.after_awaiting_door_confirmation")
            return saved
        raise AssertionError("unsupported factory action")

    def confirm_delivery_area_safe(
        self,
        *,
        operator_confirmed: bool,
        quiet_ms: int = 150,
    ) -> dict:
        """Bind a fresh post-action area observation to one delivery state."""

        self._require_open()
        if operator_confirmed is not True:
            raise AcceptanceError(
                "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
            )
        state = self._load_state()
        delivery = state.get("checks", {}).get("delivery", {})
        if (
            delivery.get("operatorAreaSafeConfirmed") is True
            and delivery.get("status") in {"PASSED", "FAILED_SAFE"}
        ):
            return copy.deepcopy(state)
        recovery = state.get("recovery")
        active = state.get("activeAction")
        if (
            state.get("status") != "RECOVERY_REQUIRED"
            or state.get("phase") != "DELIVERY_AWAITING_AREA_CONFIRMATION"
            or not isinstance(recovery, dict)
            or recovery.get("context") != "DELIVERY"
            or recovery.get("awaitingAreaSafetyConfirmation") is not True
            or recovery.get("deliveryConfirmationDisposition")
            not in {
                "PASS_AFTER_CONFIRMATION",
                "FAILED_SAFE_AFTER_CONFIRMATION",
            }
            or not isinstance(active, dict)
            or active.get("type") != "DELIVERY"
            or active.get("phase") != "AWAITING_AREA_CONFIRMATION"
        ):
            raise AcceptanceError(
                "DELIVERY_AREA_SAFETY_CONFIRMATION_NOT_PENDING"
            )
        original = recovery.get("originalMcuIdentity")
        if not isinstance(original, dict):
            raise AcceptanceError(
                "ACCEPTANCE_RECOVERY_STATE_INVALID",
                recovery_required=True,
            )
        try:
            # Confirmation can happen after a browser, service, or device
            # restart.  Re-select and re-prove the application so an old
            # F3/F1 observation is never treated as current hardware state.
            self.mcu.close()
            with deny_network_access():
                self.bootloader.force_application_selection()
                self.bootloader.boot_application()
            self.mcu.open()
            self.mcu.clear_input_for_recovery()
            self.mcu.require_business_quiet(quiet_ms=quiet_ms)
            invalid_marker = self.mcu.business_input_marker()
            identity = self._query_identity()
            if (
                not identities_equal(original, identity)
                or not identities_equal(state.get("mcuIdentity", {}), identity)
            ):
                raise AcceptanceHardwareError(
                    "MCU_IDENTITY_CHANGED_DURING_RECOVERY"
                )
            self_test = self._query_self_test()
            self.mcu.require_business_quiet(
                quiet_ms=quiet_ms,
                invalid_marker=invalid_marker,
            )
        except AcceptanceHardwareError as error:
            self._update_recovery_reason(error.code)
            raise AcceptanceError(error.code, recovery_required=True) from error
        except Exception as error:
            self._update_recovery_reason("APPLICATION_RECOVERY_FAILED")
            raise AcceptanceError(
                "APPLICATION_RECOVERY_FAILED",
                recovery_required=True,
            ) from error
        state = self._load_state()
        prior = state["checks"]["delivery"]
        disposition = state["recovery"]["deliveryConfirmationDisposition"]
        passed = disposition == "PASS_AFTER_CONFIRMATION"
        state["checks"]["delivery"] = {
            "status": "PASSED" if passed else "FAILED_SAFE",
            "resultCode": (
                "DELIVERY_SAFE_VERIFIED"
                if passed
                else "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED"
            ),
            "sendAttempts": prior.get("sendAttempts", 1),
            "result": prior.get("result"),
            "postActionSelfTest": prior.get("postActionSelfTest"),
            "recoverySelfTest": prior.get("recoverySelfTest"),
            "areaConfirmationSelfTest": self_test,
            "operatorAreaSafeConfirmed": True,
        }
        state["status"] = "RUNNING"
        state["phase"] = (
            "DELIVERY_SAFE_VERIFIED" if passed else "DELIVERY_RECOVERED_SAFE"
        )
        state["recovery"] = None
        state["activeAction"] = None
        legacy_failure_ready = self._prepare_legacy_safety_failure(state)
        saved = self._save_state(state)
        if legacy_failure_ready or not passed:
            # Publish a durable FAILED report before offering a new run.  If
            # power is lost in this deliberate gap, allowedActions exposes
            # FINALIZE only and the send-attempt gate still forbids BB+AA.
            self._fault(
                "legacy_safety.after_recovered_before_finalize"
                if legacy_failure_ready
                else "delivery.after_recovered_safe_before_finalize"
            )
            self.finalize()
            return copy.deepcopy(self._load_state())
        return saved

    def confirm_clean_door_closed(
        self,
        *,
        operator_confirmed: bool,
        quiet_ms: int = 150,
    ) -> dict:
        """Confirm the post-EF physical door state and re-prove the MCU."""

        self._require_open()
        if operator_confirmed is not True:
            raise AcceptanceError("CLEAN_DOOR_CONFIRMATION_REQUIRED")
        state = self._load_state()
        recovery = state.get("recovery")
        active = state.get("activeAction")
        clean = state.get("checks", {}).get("clean", {})
        if (
            state.get("status") != "RECOVERY_REQUIRED"
            or state.get("phase") != "CLEAN_AWAITING_DOOR_CONFIRMATION"
            or not isinstance(recovery, dict)
            or recovery.get("context") != "CLEAN"
            or recovery.get("awaitingDoorConfirmation") is not True
            or not isinstance(active, dict)
            or active.get("type") != "CLEAN"
            or active.get("phase") != "AWAITING_DOOR_CONFIRMATION"
            or not isinstance(clean.get("result"), dict)
        ):
            if clean.get("status") == "PASSED":
                return copy.deepcopy(state)
            raise AcceptanceError("CLEAN_DOOR_CONFIRMATION_NOT_PENDING")
        original = recovery.get("originalMcuIdentity")
        if not isinstance(original, dict):
            raise AcceptanceError(
                "ACCEPTANCE_RECOVERY_STATE_INVALID",
                recovery_required=True,
            )
        try:
            # This explicit reset makes a confirmation after service/device
            # restart safe: a persisted pre-crash F3/F1 observation is never
            # treated as current hardware state.
            self.mcu.close()
            with deny_network_access():
                self.bootloader.force_application_selection()
                self.bootloader.boot_application()
            self.mcu.open()
            self.mcu.clear_input_for_recovery()
            self.mcu.require_business_quiet(quiet_ms=quiet_ms)
            invalid_marker = self.mcu.business_input_marker()
            identity = self._query_identity()
            if (
                not identities_equal(original, identity)
                or not identities_equal(state.get("mcuIdentity", {}), identity)
            ):
                raise AcceptanceHardwareError(
                    "MCU_IDENTITY_CHANGED_DURING_RECOVERY"
                )
            self_test = self._query_self_test()
            self.mcu.require_business_quiet(
                quiet_ms=quiet_ms,
                invalid_marker=invalid_marker,
            )
        except AcceptanceHardwareError as error:
            self._update_recovery_reason(error.code)
            raise AcceptanceError(error.code, recovery_required=True) from error
        except Exception as error:
            self._update_recovery_reason("APPLICATION_RECOVERY_FAILED")
            raise AcceptanceError(
                "APPLICATION_RECOVERY_FAILED",
                recovery_required=True,
            ) from error
        state = self._load_state()
        prior = state["checks"]["clean"]
        state["checks"]["clean"] = {
            "status": "PASSED",
            "resultCode": "CLEAN_SAFE_VERIFIED",
            "sendAttempts": prior.get("sendAttempts", 1),
            "result": prior.get("result"),
            "postActionSelfTest": prior.get("postActionSelfTest"),
            "doorConfirmationSelfTest": self_test,
            "cleanDoorConfirmed": True,
        }
        state["status"] = "RUNNING"
        state["phase"] = "CLEAN_SAFE_VERIFIED"
        state["recovery"] = None
        state["activeAction"] = None
        legacy_failure_ready = self._prepare_legacy_safety_failure(state)
        saved = self._save_state(state)
        if legacy_failure_ready:
            self._fault("legacy_safety.after_recovered_before_finalize")
            self.finalize()
            return copy.deepcopy(self._load_state())
        return saved

    def recover(
        self,
        *,
        clean_door_closed_confirmed: bool = False,
        quiet_ms: int = 150,
    ) -> dict:
        """Reset to application, re-prove original F3/F1, then clear the lock.

        A clean-action recovery additionally requires a human to confirm that
        the door leaf is physically closed.  Electrical output state is never
        treated as a door-position sensor.
        """

        self._require_open()
        state = self._load_state()
        recovery = state.get("recovery")
        if state.get("status") != "RECOVERY_REQUIRED" or not isinstance(
            recovery, dict
        ):
            raise AcceptanceError("NO_ACCEPTANCE_RECOVERY_REQUIRED")
        context = recovery.get("context")
        original = recovery.get("originalMcuIdentity")
        if context not in ACTION_TYPES | {"UPGRADE_LINE"} or not isinstance(
            original, dict
        ):
            raise AcceptanceError(
                "ACCEPTANCE_RECOVERY_STATE_INVALID",
                recovery_required=True,
            )
        if recovery.get("awaitingDoorConfirmation") is True:
            raise AcceptanceError(
                "USE_CLEAN_DOOR_CONFIRMATION",
                recovery_required=True,
            )
        if recovery.get("awaitingAreaSafetyConfirmation") is True:
            raise AcceptanceError(
                "USE_DELIVERY_AREA_SAFETY_CONFIRMATION",
                recovery_required=True,
            )
        try:
            self.mcu.close()
            with deny_network_access():
                self.bootloader.force_application_selection()
                self.bootloader.boot_application()
            self.mcu.open()
            self.mcu.clear_input_for_recovery()
            self.mcu.require_business_quiet(quiet_ms=quiet_ms)
            invalid_marker = self.mcu.business_input_marker()
            identity = self._query_identity()
            if (
                not identities_equal(original, identity)
                or not identities_equal(state.get("mcuIdentity", {}), identity)
            ):
                raise AcceptanceHardwareError("MCU_IDENTITY_CHANGED_DURING_RECOVERY")
            self_test = self._query_self_test()
            self.mcu.require_business_quiet(
                quiet_ms=quiet_ms,
                invalid_marker=invalid_marker,
            )
        except AcceptanceHardwareError as error:
            self._update_recovery_reason(error.code)
            raise AcceptanceError(error.code, recovery_required=True) from error
        except Exception as error:
            self._update_recovery_reason("APPLICATION_RECOVERY_FAILED")
            raise AcceptanceError(
                "APPLICATION_RECOVERY_FAILED",
                recovery_required=True,
            ) from error
        state = self._load_state()
        state["recovery"]["hardwareVerified"] = True
        state["recovery"]["resultCode"] = "APPLICATION_F3_F1_RECOVERED"
        self._save_state(state)
        if context == "DELIVERY":
            state = self._load_state()
            prior = state.get("checks", {}).get("delivery", {})
            state["phase"] = "DELIVERY_AWAITING_AREA_CONFIRMATION"
            state["activeAction"]["phase"] = "AWAITING_AREA_CONFIRMATION"
            state["recovery"]["awaitingAreaSafetyConfirmation"] = True
            state["recovery"]["deliveryConfirmationDisposition"] = (
                "FAILED_SAFE_AFTER_CONFIRMATION"
            )
            state["recovery"]["resultCode"] = (
                "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
            )
            state["checks"]["delivery"] = {
                "status": "RECOVERY_REQUIRED",
                "resultCode": (
                    "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
                ),
                "sendAttempts": prior.get("sendAttempts", 0),
                "result": prior.get("result"),
                "postActionSelfTest": prior.get("postActionSelfTest"),
                "recoverySelfTest": self_test,
                "operatorAreaSafeConfirmed": False,
            }
            return self._save_state(state)
        if context == "CLEAN" and clean_door_closed_confirmed is not True:
            self._update_recovery_reason("CLEAN_DOOR_CONFIRMATION_REQUIRED")
            raise AcceptanceError(
                "CLEAN_DOOR_CONFIRMATION_REQUIRED",
                recovery_required=True,
            )
        state = self._load_state()
        check_name = (
            "upgradeLine"
            if context == "UPGRADE_LINE"
            else self._action_check_name(context)
        )
        prior = state.get("checks", {}).get(check_name, {})
        state["checks"][check_name] = {
            "status": "FAILED_SAFE",
            "resultCode": f"{context}_FAILED_BUT_APPLICATION_RECOVERED",
            "sendAttempts": prior.get(
                "sendAttempts",
                prior.get("prepareSendAttempts", 0),
            ),
            "result": prior.get("result"),
            "recoverySelfTest": self_test,
            "cleanDoorConfirmed": (
                True if context == "CLEAN" else None
            ),
        }
        state["status"] = "RUNNING"
        state["phase"] = f"{context}_RECOVERED_SAFE"
        state["recovery"] = None
        state["activeAction"] = None
        legacy_failure_ready = self._prepare_legacy_safety_failure(state)
        saved = self._save_state(state)
        if legacy_failure_ready or context in {"CLEAN", "UPGRADE_LINE"}:
            self._fault(
                "legacy_safety.after_recovered_before_finalize"
                if legacy_failure_ready
                else f"{context.lower()}.after_recovered_safe_before_finalize"
            )
            self.finalize()
            return copy.deepcopy(self._load_state())
        return saved

    @staticmethod
    def _report_from_state(state: dict, status: str, finished_ms: int) -> dict:
        checks = state.get("checks", {})
        weight = checks.get("weight", {})
        cameras = checks.get("cameras", {})
        delivery = checks.get("delivery", {})
        clean = checks.get("clean", {})

        def action_summary(value: dict) -> dict:
            result = value.get("result") or {}
            return {
                "status": value.get("status", "NOT_RUN"),
                "resultCode": value.get("resultCode", "NOT_RUN"),
                "preWeightGrams": result.get("preWeightGrams"),
                "postWeightGrams": result.get("postWeightGrams"),
                "weightDeltaGrams": result.get("weightDeltaGrams"),
                "infraredBlocked": result.get("infraredBlocked"),
                "operatorAreaSafeConfirmed": value.get(
                    "operatorAreaSafeConfirmed"
                ),
                "cleanDoorConfirmed": value.get("cleanDoorConfirmed"),
            }

        return {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "recoveryRequired": status == "RECOVERY_REQUIRED",
            "imageReleaseId": state.get("imageReleaseId"),
            "hardwareConfigDigest": state.get("hardwareConfigDigest"),
            "bootId": state.get("bootId"),
            "timing": {
                "startedMonotonicMs": (state.get("timing") or {}).get(
                    "startedMonotonicMs"
                ),
                "finishedMonotonicMs": finished_ms,
                "wallTimeTrusted": (state.get("timing") or {}).get(
                    "wallTimeTrusted", False
                ),
                "trustedStartedAtUtc": (state.get("timing") or {}).get(
                    "trustedStartedAtUtc"
                ),
                "trustedFinishedAtUtc": (
                    _utc_now()
                    if (state.get("timing") or {}).get("wallTimeTrusted")
                    else None
                ),
            },
            "mcuIdentity": state.get("mcuIdentity"),
            "cameraSummary": {
                "status": cameras.get("status", "NOT_RUN"),
                "resultCode": cameras.get("resultCode", "NOT_RUN"),
                "outside": cameras.get("outside"),
                "inside": cameras.get("inside"),
            },
            "checks": {
                "mcu": {
                    "status": checks.get("mcu", {}).get("status", "NOT_RUN"),
                    "resultCode": checks.get("mcu", {}).get(
                        "resultCode", "NOT_RUN"
                    ),
                },
                "weight": {
                    "status": weight.get("status", "NOT_RUN"),
                    "resultCode": weight.get("resultCode", "NOT_RUN"),
                    "emptyWeightGrams": weight.get("emptyWeightGrams"),
                    "loadedWeightGrams": weight.get("loadedWeightGrams"),
                    "removedWeightGrams": weight.get("removedWeightGrams"),
                    "deltaGrams": weight.get("deltaGrams"),
                    "targetDeltaGrams": 500,
                    "toleranceGrams": 10,
                    "stableSampleCount": weight.get("stableSampleCount"),
                    "stableMaxSpreadGrams": weight.get(
                        "stableMaxSpreadGrams"
                    ),
                    "sampleIntervalMs": weight.get("sampleIntervalMs"),
                    "sampleTimeoutMs": weight.get("sampleTimeoutMs"),
                },
                "upgradeLine": {
                    "status": checks.get("upgradeLine", {}).get(
                        "status", "NOT_RUN"
                    ),
                    "resultCode": checks.get("upgradeLine", {}).get(
                        "resultCode", "NOT_RUN"
                    ),
                    "romWritePerformed": checks.get("upgradeLine", {}).get(
                        "romWritePerformed", False
                    ),
                    "romDeviceId": checks.get("upgradeLine", {}).get(
                        "romDeviceId"
                    ),
                },
                "delivery": action_summary(delivery),
                "clean": action_summary(clean),
            },
        }

    @staticmethod
    def _validate_report(report: dict) -> None:
        if report.get("schemaVersion") != REPORT_SCHEMA_VERSION:
            raise AcceptanceError("ACCEPTANCE_REPORT_SCHEMA_INVALID")
        if report.get("status") not in REPORT_STATUSES:
            raise AcceptanceError("ACCEPTANCE_REPORT_STATUS_INVALID")
        if report.get("recoveryRequired") is not (
            report.get("status") == "RECOVERY_REQUIRED"
        ):
            raise AcceptanceError("ACCEPTANCE_REPORT_RECOVERY_INVALID")
        if (
            not isinstance(report.get("hardwareConfigDigest"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", report["hardwareConfigDigest"]
            )
            is None
        ):
            raise AcceptanceError("ACCEPTANCE_REPORT_CONFIG_BINDING_INVALID")
        identity = report.get("mcuIdentity")
        identity_invalid = (
            not isinstance(identity, dict)
            or identity.get("fixedFrameRevision") != 2
            or not isinstance(identity.get("firmwareVersion"), str)
            or not 5 <= len(identity["firmwareVersion"]) <= 32
            or not isinstance(identity.get("firmwareVersionCode"), int)
            or isinstance(identity.get("firmwareVersionCode"), bool)
            or identity["firmwareVersionCode"] <= 0
            or not isinstance(identity.get("firmwareIdentityHex"), str)
            or re.fullmatch(
                r"[0-9a-f]{16}", identity["firmwareIdentityHex"]
            )
            is None
            or int(identity["firmwareIdentityHex"], 16) == 0
        ) if isinstance(identity, dict) else True
        if report.get("status") == "PASSED" and identity_invalid:
            raise AcceptanceError("ACCEPTANCE_REPORT_MCU_BINDING_INVALID")
        if identity is not None and identity_invalid:
            raise AcceptanceError("ACCEPTANCE_REPORT_MCU_BINDING_INVALID")
        if (
            report.get("status") == "PASSED"
            and report.get("checks", {})
            .get("delivery", {})
            .get("operatorAreaSafeConfirmed")
            is not True
        ):
            raise AcceptanceError(
                "ACCEPTANCE_REPORT_DELIVERY_SAFETY_CONFIRMATION_INVALID"
            )
        if (
            report.get("status") == "PASSED"
            and report.get("checks", {})
            .get("clean", {})
            .get("cleanDoorConfirmed")
            is not True
        ):
            raise AcceptanceError(
                "ACCEPTANCE_REPORT_CLEAN_SAFETY_CONFIRMATION_INVALID"
            )
        forbidden_key_fragments = (
            "password",
            "secret",
            "token",
            "onenet",
            "mqtt",
            "cos",
            "order",
            "bag",
            "tenant",
            "organization",
            "user",
            "commanduid",
            "rawframe",
            "photo",
            "url",
        )

        def inspect(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    lowered = str(key).replace("_", "").lower()
                    if any(fragment in lowered for fragment in forbidden_key_fragments):
                        raise AcceptanceError("ACCEPTANCE_REPORT_FORBIDDEN_FIELD")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
            elif isinstance(value, str) and len(value) > 160:
                raise AcceptanceError("ACCEPTANCE_REPORT_VALUE_TOO_LONG")

        inspect(report)

    def finalize(self) -> dict:
        """Atomically publish the final private report, then close state."""

        self._require_open()
        state = self._load_state()
        if (
            state.get("status") == "RECOVERY_REQUIRED"
            or state.get("recovery") is not None
            or state.get("activeAction") is not None
        ):
            raise AcceptanceError(
                "ACCEPTANCE_RECOVERY_REQUIRED",
                recovery_required=True,
            )
        if all(_check_passed(state, name) for name in REQUIRED_PASSED_CHECKS):
            final_status = "PASSED"
        else:
            final_status = "FAILED"
        finished_ms = int(self._monotonic() * 1000)
        report = self._report_from_state(state, final_status, finished_ms)
        self._validate_report(report)
        state["phase"] = "FINALIZING_REPORT"
        state["pendingFinalStatus"] = final_status
        state["pendingFinishedMonotonicMs"] = finished_ms
        self._save_state(state)
        self._fault("finalize.after_state_journal")
        self._report_file.write(report)
        self._fault("finalize.after_report_commit")
        state = self._load_state()
        state["status"] = final_status
        state["phase"] = "COMPLETE"
        state.pop("pendingFinalStatus", None)
        state.pop("pendingFinishedMonotonicMs", None)
        self._save_state(state)
        return copy.deepcopy(report)

    def read_report(self) -> Optional[dict]:
        self._require_open()
        report = self._report_file.read()
        if report is not None:
            self._validate_report(report)
        return report

    def _reconcile_finalizing_report(self) -> None:
        state = self._load_state()
        if state.get("phase") != "FINALIZING_REPORT":
            return
        report = self._report_file.read()
        if report is None:
            return
        self._validate_report(report)
        if (
            report.get("status") != state.get("pendingFinalStatus")
            or report.get("imageReleaseId") != state.get("imageReleaseId")
            or report.get("hardwareConfigDigest")
            != state.get("hardwareConfigDigest")
            or report.get("bootId") != state.get("bootId")
            or (report.get("timing") or {}).get("finishedMonotonicMs")
            != state.get("pendingFinishedMonotonicMs")
        ):
            # A restarted terminal run intentionally retains its preceding
            # valid report until the new report is atomically committed.  If
            # power is lost after journaling FINALIZING_REPORT but before that
            # replace, the old report must neither be adopted nor prevent the
            # new finalization from being retried.  Invalid or damaged reports
            # are still rejected by _validate_report above.
            return
        state["status"] = report["status"]
        state["phase"] = "COMPLETE"
        state.pop("pendingFinalStatus", None)
        state.pop("pendingFinishedMonotonicMs", None)
        self._save_state(state)

    def _reconcile_interrupted_armed_action(self) -> None:
        """Clear only a command-proven-not-sent ARMED crash boundary.

        ``COMMAND_MAY_HAVE_BEEN_SENT`` is persisted in a later atomic commit
        before the first UART write.  Therefore an ARMED record with zero send
        attempts is the sole action state that can be closed without resetting
        the MCU.  No physical command is resumed automatically.
        """

        state = self._load_state()
        active = state.get("activeAction")
        if not isinstance(active, dict):
            return
        action = active.get("type")
        if (
            state.get("status") != "RUNNING"
            or action not in ACTION_TYPES
            or active.get("phase") != "ARMED"
            or active.get("sendAttempts") != 0
        ):
            return
        check_name = self._action_check_name(action)
        state["checks"][check_name] = {
            "status": "FAILED_SAFE",
            "resultCode": f"{action}_INTERRUPTED_BEFORE_COMMAND",
            "sendAttempts": 0,
            "result": None,
        }
        state["activeAction"] = None
        state["phase"] = f"{action}_INTERRUPTED_BEFORE_COMMAND"
        legacy_failure_ready = self._prepare_legacy_safety_failure(state)
        self._save_state(state)
        if legacy_failure_ready:
            self._fault("legacy_safety.after_recovered_before_finalize")
            self.finalize()

    def _reconcile_interrupted_camera_review(self) -> None:
        """Never accept an operator confirmation from a prior executor run."""

        state = self._load_state()
        cameras = state.get("checks", {}).get("cameras")
        if (
            state.get("status") != "RUNNING"
            or state.get("phase") != "WAITING_FOR_CAMERA_ROLE_CONFIRMATION"
            or not isinstance(cameras, dict)
        ):
            return
        self.cameras.discard_all_pending()
        state["checks"]["cameras"] = {
            "status": "FAILED",
            "resultCode": "CAMERA_REVIEW_INVALIDATED_BY_EXECUTOR_RESTART",
        }
        state["phase"] = "CAMERA_CHECK_FAILED"
        self._save_state(state)

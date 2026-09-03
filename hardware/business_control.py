"""Local control surface for the replaceable business runtime.

Stage three ships this adapter with the business release so its protocol can
be exercised under the future ``ecobin-business`` account.  The legacy
runtime does not enable it yet: OneNet still belongs to the existing business
process and the permanent job gate is intentionally deferred to stage four.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlAction,
    LocalControlActionError,
    LocalControlServer,
)


BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
BUSINESS_COMPONENT = "BUSINESS_RUNTIME"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_FIRMWARE_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}\Z")
_QUERY_STATUSES = frozenset(
    {"OK", "TIMEOUT", "PROTOCOL_ERROR", "NOT_PERFORMED", "UNKNOWN"}
)
MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION = 1
MCU_MAINTENANCE_EVIDENCE_DOMAIN = "ecobin.business.mcu-maintenance-evidence"
_EVIDENCE_STAGES = frozenset({"OBSERVE", "QUIESCE", "VERIFY"})

_PORT_ERROR_MESSAGES = {
    "MCU_MAINTENANCE_BUSY": "MCU maintenance handoff is busy",
    "MCU_OBSERVATION_UNAVAILABLE": "MCU maintenance state could not be observed",
    "MCU_QUIESCE_FAILED": "MCU could not be quiesced for update",
    "MCU_VERIFICATION_FAILED": "MCU post-update verification failed",
    "MCU_UART_UNAVAILABLE": "MCU application UART is unavailable",
}


@dataclass(frozen=True)
class McuFirmwareIdentity:
    """Strict, non-secret identity fields obtained from an F3 response."""

    protocol_revision: int
    firmware_version_code: int
    firmware_version: str
    firmware_identity_hex: str

    def __post_init__(self) -> None:
        if self.protocol_revision != 2 or isinstance(self.protocol_revision, bool):
            raise ValueError("F3 protocol revision must be revision 2")
        if (
            isinstance(self.firmware_version_code, bool)
            or not isinstance(self.firmware_version_code, int)
            or not 1 <= self.firmware_version_code <= 0xFFFFFFFF
        ):
            raise ValueError("F3 firmware version code is invalid")
        if (
            not isinstance(self.firmware_version, str)
            or _FIRMWARE_VERSION_PATTERN.fullmatch(self.firmware_version) is None
        ):
            raise ValueError("F3 firmware version is invalid")
        if (
            not isinstance(self.firmware_identity_hex, str)
            or re.fullmatch(r"[0-9a-f]{16}", self.firmware_identity_hex) is None
            or self.firmware_identity_hex == "0" * 16
        ):
            raise ValueError("F3 firmware identity is invalid")

    def to_wire(self) -> dict[str, Any]:
        return {
            "protocolRevision": self.protocol_revision,
            "firmwareVersionCode": self.firmware_version_code,
            "firmwareVersion": self.firmware_version,
            "firmwareIdentityHex": self.firmware_identity_hex,
        }


def mcu_firmware_identity_sha256(identity: McuFirmwareIdentity) -> str:
    """Return the canonical digest used to bind post-flash F3 verification."""

    if not isinstance(identity, McuFirmwareIdentity):
        raise TypeError("MCU firmware identity is invalid")
    encoded = json.dumps(
        identity.to_wire(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class McuF3Evidence:
    """One bounded F3 response, including diagnostic nonzero status."""

    query_status: str
    mode: int | None
    status_code: int | None
    safe_flags: int | None
    firmware_identity: McuFirmwareIdentity | None

    def __post_init__(self) -> None:
        if self.query_status not in _QUERY_STATUSES:
            raise ValueError("F3 query status is invalid")
        if self.mode is not None and (
            isinstance(self.mode, bool)
            or not isinstance(self.mode, int)
            or self.mode not in {1, 2}
        ):
            raise ValueError("F3 mode is invalid")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or self.status_code not in {0, 1, 2, 3}
        ):
            raise ValueError("F3 status code is invalid")
        if self.safe_flags is not None and (
            isinstance(self.safe_flags, bool)
            or not isinstance(self.safe_flags, int)
            or not 0 <= self.safe_flags <= 0x1F
        ):
            raise ValueError("F3 safe flags are invalid")
        if self.query_status == "OK":
            if (
                self.mode is None
                or self.status_code is None
                or self.safe_flags is None
                or not isinstance(self.firmware_identity, McuFirmwareIdentity)
            ):
                raise ValueError("complete F3 facts are required for an OK response")
        elif any(
            value is not None
            for value in (
                self.mode,
                self.status_code,
                self.safe_flags,
                self.firmware_identity,
            )
        ):
            raise ValueError("failed F3 query cannot carry response facts")

    def to_wire(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "queryStatus": self.query_status,
            "mode": self.mode,
            "statusCode": self.status_code,
            "safeFlags": self.safe_flags,
            "firmwareIdentity": None,
        }
        if self.firmware_identity is not None:
            result["firmwareIdentity"] = self.firmware_identity.to_wire()
        return result


@dataclass(frozen=True)
class McuF1Evidence:
    """One bounded F1 self-test response."""

    query_status: str
    communication_healthy: bool | None
    valid_flags: int | None
    weight_grams: int | None
    infrared_blocked: bool | None
    smoke_state: str
    smoke_sensor_health: str

    def __post_init__(self) -> None:
        if self.query_status not in _QUERY_STATUSES:
            raise ValueError("F1 query status is invalid")
        if self.communication_healthy is not None and not isinstance(
            self.communication_healthy, bool
        ):
            raise ValueError("F1 communication fact is invalid")
        if self.valid_flags is not None and (
            isinstance(self.valid_flags, bool)
            or not isinstance(self.valid_flags, int)
            or not 0 <= self.valid_flags <= 3
        ):
            raise ValueError("F1 valid flags are invalid")
        if self.weight_grams is not None and (
            isinstance(self.weight_grams, bool)
            or not isinstance(self.weight_grams, int)
            or not 0 <= self.weight_grams <= 0xFFFFFF
        ):
            raise ValueError("F1 weight is invalid")
        if self.infrared_blocked is not None and not isinstance(
            self.infrared_blocked, bool
        ):
            raise ValueError("F1 infrared fact is invalid")
        if self.smoke_state not in {"NORMAL", "ALARM", "UNKNOWN"}:
            raise ValueError("F1 smoke state is invalid")
        if self.smoke_sensor_health not in {
            "OK",
            "TIMEOUT",
            "PROTOCOL_ERROR",
            "SENSOR_FAULT",
            "UNKNOWN",
            "NOT_PERFORMED",
        }:
            raise ValueError("F1 smoke sensor health is invalid")
        if self.query_status == "OK":
            if (
                self.communication_healthy is not True
                or self.valid_flags is None
                or (self.weight_grams is None) != (self.valid_flags & 0x01 == 0)
                or (self.infrared_blocked is None)
                != (self.valid_flags & 0x02 == 0)
                or (
                    self.smoke_state in {"NORMAL", "ALARM"}
                    and self.smoke_sensor_health != "OK"
                )
                or (
                    self.smoke_state == "UNKNOWN"
                    and self.smoke_sensor_health != "SENSOR_FAULT"
                )
            ):
                raise ValueError("OK F1 response facts are inconsistent")
        elif self.query_status in {"TIMEOUT", "PROTOCOL_ERROR"}:
            if (
                self.communication_healthy is not False
                or self.valid_flags != 0
                or self.weight_grams is not None
                or self.infrared_blocked is not None
                or self.smoke_state != "UNKNOWN"
                or self.smoke_sensor_health != self.query_status
            ):
                raise ValueError("failed F1 response facts are inconsistent")
        else:
            if (
                self.communication_healthy is not None
                or self.valid_flags is not None
                or self.weight_grams is not None
                or self.infrared_blocked is not None
                or self.smoke_state != "UNKNOWN"
                or self.smoke_sensor_health != self.query_status
            ):
                raise ValueError("absent F1 response facts are inconsistent")

    def to_wire(self) -> dict[str, Any]:
        return {
            "queryStatus": self.query_status,
            "communicationHealthy": self.communication_healthy,
            "validFlags": self.valid_flags,
            "weightGrams": self.weight_grams,
            "infraredBlocked": self.infrared_blocked,
            "smokeState": self.smoke_state,
            "smokeSensorHealth": self.smoke_sensor_health,
        }


@dataclass(frozen=True)
class McuMaintenanceEvidence:
    """Allowlisted F3, F1 and UART facts returned by the injected port."""

    f3: McuF3Evidence
    f1: McuF1Evidence
    uart_handed_off: bool | None

    def __post_init__(self) -> None:
        if not isinstance(self.f3, McuF3Evidence):
            raise ValueError("F3 evidence is invalid")
        if not isinstance(self.f1, McuF1Evidence):
            raise ValueError("F1 evidence is invalid")
        if self.uart_handed_off is not None and not isinstance(
            self.uart_handed_off, bool
        ):
            raise ValueError("UART handoff fact is invalid")

    def to_wire(self) -> dict[str, Any]:
        return {
            "f3FirmwareIdentity": self.f3.to_wire(),
            "f1SelfTest": self.f1.to_wire(),
            "uartHandedOff": self.uart_handed_off,
        }


def mcu_maintenance_evidence_sha256(
    stage: str,
    evidence: McuMaintenanceEvidence,
    *,
    update_uid: str | None = None,
    handoff_uid: str | None = None,
    expected_observation_sha256: str | None = None,
    expected_firmware_identity_sha256: str | None = None,
    observed_flash_evidence_sha256: str | None = None,
) -> str:
    """Compute the versioned, stage-separated canonical evidence digest."""

    if stage not in _EVIDENCE_STAGES or not isinstance(
        evidence, McuMaintenanceEvidence
    ):
        raise ValueError("MCU maintenance evidence stage is invalid")
    if stage == "OBSERVE":
        if any(
            value is not None
            for value in (
                update_uid,
                handoff_uid,
                expected_observation_sha256,
                expected_firmware_identity_sha256,
                observed_flash_evidence_sha256,
            )
        ):
            raise ValueError("observation evidence cannot carry update bindings")
        bindings: dict[str, Any] = {}
    elif stage == "QUIESCE":
        update_uid = _require_uuid4_plain(update_uid, "updateUid")
        handoff_uid = _require_uuid4_plain(handoff_uid, "handoffUid")
        expected_observation_sha256 = _require_sha256_value(
            expected_observation_sha256, "expectedObservationSha256"
        )
        if any(
            value is not None
            for value in (
                expected_firmware_identity_sha256,
                observed_flash_evidence_sha256,
            )
        ):
            raise ValueError("quiesce evidence bindings are invalid")
        bindings = {
            "updateUid": update_uid,
            "handoffUid": handoff_uid,
            "expectedObservationSha256": expected_observation_sha256,
        }
    else:
        update_uid = _require_uuid4_plain(update_uid, "updateUid")
        handoff_uid = _require_uuid4_plain(handoff_uid, "handoffUid")
        expected_firmware_identity_sha256 = _require_sha256_value(
            expected_firmware_identity_sha256,
            "expectedFirmwareIdentitySha256",
        )
        observed_flash_evidence_sha256 = _require_sha256_value(
            observed_flash_evidence_sha256,
            "observedFlashEvidenceSha256",
        )
        if expected_observation_sha256 is not None:
            raise ValueError("verification evidence bindings are invalid")
        bindings = {
            "updateUid": update_uid,
            "handoffUid": handoff_uid,
            "expectedFirmwareIdentitySha256": expected_firmware_identity_sha256,
            "observedFlashEvidenceSha256": observed_flash_evidence_sha256,
        }
    document = {
        "domain": MCU_MAINTENANCE_EVIDENCE_DOMAIN,
        "schemaVersion": MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION,
        "stage": stage,
        "bindings": bindings,
        **evidence.to_wire(),
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class McuMaintenanceHandoffPort(Protocol):
    """Future UART adapter; this control module never touches hardware itself."""

    def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence: ...

    def quiesce_mcu_for_update(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_observation_sha256: str,
    ) -> McuMaintenanceEvidence: ...

    def verify_mcu_after_update(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_firmware_identity_sha256: str,
        observed_flash_evidence_sha256: str,
    ) -> McuMaintenanceEvidence: ...


@dataclass
class _MaintenanceCall:
    request_sha256: str
    state: str = "IN_PROGRESS"
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class McuMaintenancePortError(RuntimeError):
    """Stable, allowlisted failure raised by a future hardware adapter."""

    def __init__(self, code: str) -> None:
        message = _PORT_ERROR_MESSAGES.get(code)
        if message is None:
            raise ValueError("MCU maintenance port error code is invalid")
        super().__init__(message)
        self.code = code
        self.safe_message = message


class BusinessControlController:
    """Status plus an optional, injected stage-four MCU handoff candidate."""

    def __init__(
        self,
        release_version: str,
        *,
        utc_now: Callable[[], datetime] | None = None,
        instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        mcu_maintenance_port: McuMaintenanceHandoffPort | None = None,
        maintenance_handoff_enabled: bool = False,
    ) -> None:
        self.release_version = _require_release_version(release_version)
        if not isinstance(maintenance_handoff_enabled, bool):
            raise ValueError("maintenance handoff candidate flag must be boolean")
        if maintenance_handoff_enabled:
            _require_mcu_maintenance_port(mcu_maintenance_port)
        self._mcu_maintenance_port = mcu_maintenance_port
        self._maintenance_handoff_enabled = maintenance_handoff_enabled
        now = (utc_now or (lambda: datetime.now(timezone.utc)))()
        self.started_at = _format_utc(now)
        instance_uid = instance_uid_factory()
        if not isinstance(instance_uid, uuid.UUID) or instance_uid.version != 4:
            raise ValueError("business runtime instance identity must be UUIDv4")
        self.runtime_instance_uid = str(instance_uid)
        self._status = "STARTING"
        self._lock = threading.Lock()
        self._maintenance_operation_lock = threading.Lock()
        self._maintenance_state_lock = threading.Lock()
        self._latest_observation: tuple[str, McuMaintenanceEvidence] | None = None
        self._maintenance_calls: dict[
            tuple[str, str, str], _MaintenanceCall
        ] = {}

    def mark_ready(self) -> None:
        with self._lock:
            if self._status == "STOPPING":
                raise RuntimeError("stopping business runtime cannot become ready")
            self._status = "READY"

    def mark_stopping(self) -> None:
        with self._lock:
            self._status = "STOPPING"

    def wait_for_maintenance_idle(self) -> None:
        """Drain the bounded MCU port call that may already be in flight.

        A maintenance port implementation must put a finite timeout around
        every UART operation.  Once the runtime is marked STOPPING, the
        second readiness check in each action prevents a waiter from starting
        a new port call after this barrier has been crossed.
        """

        self._maintenance_operation_lock.acquire()
        self._maintenance_operation_lock.release()

    def health(self, _payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            status = self._status
        return {
            "component": BUSINESS_COMPONENT,
            "status": status,
            "runtimeInstanceUid": self.runtime_instance_uid,
            "releaseVersion": self.release_version,
            "startedAt": self.started_at,
            "localProtocolName": BUSINESS_PROTOCOL_NAME,
            "localProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "localProtocolMinor": LOCAL_PROTOCOL_MINOR,
            # These values describe the real migration state.  Shipping the
            # socket adapter must not be confused with completing the OneNet
            # ownership cutover or enabling the stage-four safety gate.
            "managementArchitectureGeneration": "LEGACY_DIRECT",
            "cloudConnectionOwner": "BUSINESS_RUNTIME",
            "jobPermitEnforced": False,
            "maintenanceHandoffEnabled": self._maintenance_handoff_enabled,
        }

    def observe_mcu_maintenance_state(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_action_payload(payload, frozenset())
        self._require_runtime_ready()
        port = self._require_enabled_mcu_maintenance_port()
        if not self._maintenance_operation_lock.acquire(blocking=False):
            raise LocalControlActionError(
                "MCU_MAINTENANCE_BUSY",
                "MCU maintenance handoff is busy",
            )
        try:
            self._require_runtime_ready()
            evidence = self._require_evidence(
                self._invoke_maintenance_port(
                    port.observe_mcu_maintenance_state,
                )
            )
            digest = mcu_maintenance_evidence_sha256("OBSERVE", evidence)
            with self._maintenance_state_lock:
                self._latest_observation = (digest, evidence)
            return self._render_evidence("OBSERVE", digest, evidence)
        finally:
            self._maintenance_operation_lock.release()

    def quiesce_mcu_for_update(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_action_payload(
            payload,
            frozenset(
                {
                    "updateUid",
                    "handoffUid",
                    "expectedObservationSha256",
                }
            ),
        )
        update_uid = _require_uuid4_value(payload["updateUid"], "updateUid")
        handoff_uid = _require_uuid4_value(payload["handoffUid"], "handoffUid")
        expected_observation_sha256 = _require_action_sha256_value(
            payload["expectedObservationSha256"],
            "expectedObservationSha256",
        )
        port = self._require_enabled_mcu_maintenance_port()
        self._require_runtime_ready()
        request_sha256 = _canonical_request_sha256("QUIESCE", payload)

        def execute() -> dict[str, Any]:
            with self._maintenance_state_lock:
                observation = self._latest_observation
            if (
                observation is None
                or observation[0] != expected_observation_sha256
                or not _is_application_observation(observation[1])
            ):
                raise LocalControlActionError(
                    "MCU_OBSERVATION_MISMATCH",
                    "expected MCU observation is unavailable or no longer current",
                )
            evidence = self._require_evidence(
                self._invoke_maintenance_port(
                    lambda: port.quiesce_mcu_for_update(
                        update_uid=update_uid,
                        handoff_uid=handoff_uid,
                        expected_observation_sha256=expected_observation_sha256,
                    )
                )
            )
            if not _is_confirmed_quiesce(evidence):
                raise LocalControlActionError(
                    "MCU_QUIESCE_UNCONFIRMED",
                    "F3 safe maintenance mode and UART handoff were not confirmed",
                )
            digest = mcu_maintenance_evidence_sha256(
                "QUIESCE",
                evidence,
                update_uid=update_uid,
                handoff_uid=handoff_uid,
                expected_observation_sha256=expected_observation_sha256,
            )
            with self._maintenance_state_lock:
                self._latest_observation = None
            return {
                "updateUid": update_uid,
                "handoffUid": handoff_uid,
                "expectedObservationSha256": expected_observation_sha256,
                **self._render_evidence("QUIESCE", digest, evidence),
            }

        return self._run_idempotent_maintenance_action(
            "QUIESCE",
            update_uid,
            handoff_uid,
            request_sha256,
            execute,
        )

    def verify_mcu_after_update(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_action_payload(
            payload,
            frozenset(
                {
                    "updateUid",
                    "handoffUid",
                    "expectedFirmwareIdentitySha256",
                    "observedFlashEvidenceSha256",
                }
            ),
        )
        update_uid = _require_uuid4_value(payload["updateUid"], "updateUid")
        handoff_uid = _require_uuid4_value(payload["handoffUid"], "handoffUid")
        expected_identity_sha256 = _require_action_sha256_value(
            payload["expectedFirmwareIdentitySha256"],
            "expectedFirmwareIdentitySha256",
        )
        observed_flash_sha256 = _require_action_sha256_value(
            payload["observedFlashEvidenceSha256"],
            "observedFlashEvidenceSha256",
        )
        port = self._require_enabled_mcu_maintenance_port()
        self._require_runtime_ready()
        request_sha256 = _canonical_request_sha256("VERIFY", payload)

        def execute() -> dict[str, Any]:
            self._require_completed_quiesce(update_uid, handoff_uid)
            evidence = self._require_evidence(
                self._invoke_maintenance_port(
                    lambda: port.verify_mcu_after_update(
                        update_uid=update_uid,
                        handoff_uid=handoff_uid,
                        expected_firmware_identity_sha256=expected_identity_sha256,
                        observed_flash_evidence_sha256=observed_flash_sha256,
                    )
                )
            )
            if not _is_confirmed_verification(evidence):
                raise LocalControlActionError(
                    "MCU_VERIFICATION_UNCONFIRMED",
                    "F3 identity, F1 self-test and UART ownership were not confirmed",
                )
            identity = evidence.f3.firmware_identity
            if (
                identity is None
                or mcu_firmware_identity_sha256(identity)
                != expected_identity_sha256
            ):
                raise LocalControlActionError(
                    "MCU_IDENTITY_MISMATCH",
                    "observed MCU firmware identity differs from the expected identity",
                )
            digest = mcu_maintenance_evidence_sha256(
                "VERIFY",
                evidence,
                update_uid=update_uid,
                handoff_uid=handoff_uid,
                expected_firmware_identity_sha256=expected_identity_sha256,
                observed_flash_evidence_sha256=observed_flash_sha256,
            )
            return {
                "updateUid": update_uid,
                "handoffUid": handoff_uid,
                "expectedFirmwareIdentitySha256": expected_identity_sha256,
                "observedFlashEvidenceSha256": observed_flash_sha256,
                **self._render_evidence("VERIFY", digest, evidence),
            }

        return self._run_idempotent_maintenance_action(
            "VERIFY",
            update_uid,
            handoff_uid,
            request_sha256,
            execute,
        )

    def _require_runtime_ready(self) -> None:
        with self._lock:
            status = self._status
        if status == "READY":
            return
        if status == "STOPPING":
            raise LocalControlActionError(
                "SERVICE_STOPPING",
                "business runtime is stopping",
            )
        raise LocalControlActionError(
            "BUSINESS_RUNTIME_NOT_READY",
            "business runtime is not ready for MCU maintenance",
        )

    @staticmethod
    def _require_evidence(value: Any) -> McuMaintenanceEvidence:
        if not isinstance(value, McuMaintenanceEvidence):
            raise LocalControlActionError(
                "MCU_MAINTENANCE_EVIDENCE_INVALID",
                "MCU maintenance evidence is invalid",
            )
        return value

    @staticmethod
    def _render_evidence(
        stage: str,
        digest: str,
        evidence: McuMaintenanceEvidence,
    ) -> dict[str, Any]:
        return {
            "evidenceDomain": MCU_MAINTENANCE_EVIDENCE_DOMAIN,
            "evidenceSchemaVersion": MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION,
            "evidenceStage": stage,
            "evidenceSha256": digest,
            **evidence.to_wire(),
        }

    def _require_completed_quiesce(
        self, update_uid: str, handoff_uid: str
    ) -> None:
        with self._maintenance_state_lock:
            record = self._maintenance_calls.get(
                ("QUIESCE", update_uid, handoff_uid)
            )
        if record is None or record.state != "SUCCEEDED":
            raise LocalControlActionError(
                "MCU_HANDOFF_NOT_CONFIRMED",
                "the matching MCU quiesce handoff is not confirmed",
            )

    def _run_idempotent_maintenance_action(
        self,
        stage: str,
        update_uid: str,
        handoff_uid: str,
        request_sha256: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        key = (stage, update_uid, handoff_uid)
        with self._maintenance_state_lock:
            existing = self._maintenance_calls.get(key)
            if existing is not None:
                self._require_maintenance_call_identity(
                    existing,
                    request_sha256,
                )
                if existing.state != "FAILED":
                    return self._replay_maintenance_call(
                        existing,
                        request_sha256,
                    )
        if not self._maintenance_operation_lock.acquire(blocking=False):
            raise LocalControlActionError(
                "MCU_MAINTENANCE_BUSY",
                "MCU maintenance handoff is busy",
            )
        try:
            self._require_runtime_ready()
            with self._maintenance_state_lock:
                existing = self._maintenance_calls.get(key)
                if existing is not None:
                    self._require_maintenance_call_identity(
                        existing,
                        request_sha256,
                    )
                    if existing.state != "FAILED":
                        return self._replay_maintenance_call(
                            existing,
                            request_sha256,
                        )
                    record = existing
                    record.state = "IN_PROGRESS"
                    record.result = None
                    record.error_code = None
                    record.error_message = None
                else:
                    record = _MaintenanceCall(request_sha256=request_sha256)
                    self._maintenance_calls[key] = record
            try:
                result = operation()
            except LocalControlActionError as error:
                with self._maintenance_state_lock:
                    record.state = "FAILED"
                    record.error_code = error.code
                    record.error_message = error.message
                raise
            except Exception as cause:
                error = LocalControlActionError(
                    "MCU_MAINTENANCE_PORT_UNAVAILABLE",
                    "MCU maintenance operation could not be confirmed",
                )
                with self._maintenance_state_lock:
                    record.state = "FAILED"
                    record.error_code = error.code
                    record.error_message = error.message
                raise error from cause
            with self._maintenance_state_lock:
                record.state = "SUCCEEDED"
                record.result = deepcopy(result)
            return result
        finally:
            self._maintenance_operation_lock.release()

    @staticmethod
    def _require_maintenance_call_identity(
        record: _MaintenanceCall,
        request_sha256: str,
    ) -> None:
        if record.request_sha256 != request_sha256:
            raise LocalControlActionError(
                "MCU_MAINTENANCE_IDEMPOTENCY_CONFLICT",
                "MCU maintenance identity was reused with different evidence",
            )

    @staticmethod
    def _replay_maintenance_call(
        record: _MaintenanceCall, request_sha256: str
    ) -> dict[str, Any]:
        BusinessControlController._require_maintenance_call_identity(
            record,
            request_sha256,
        )
        if record.state == "IN_PROGRESS":
            raise LocalControlActionError(
                "MCU_MAINTENANCE_IN_PROGRESS",
                "the same MCU maintenance action is still in progress",
            )
        if record.state == "FAILED":
            raise LocalControlActionError(
                record.error_code or "MCU_MAINTENANCE_PORT_UNAVAILABLE",
                record.error_message or "MCU maintenance operation failed",
            )
        if record.state != "SUCCEEDED" or record.result is None:
            raise LocalControlActionError(
                "MCU_MAINTENANCE_PORT_UNAVAILABLE",
                "MCU maintenance operation could not be confirmed",
            )
        return deepcopy(record.result)

    def _require_enabled_mcu_maintenance_port(
        self,
    ) -> McuMaintenanceHandoffPort:
        port = self._mcu_maintenance_port
        if not self._maintenance_handoff_enabled or port is None:
            raise LocalControlActionError(
                "FEATURE_DISABLED",
                "MCU maintenance handoff is not enabled",
            )
        return port

    @staticmethod
    def _invoke_maintenance_port(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except McuMaintenancePortError as error:
            raise LocalControlActionError(error.code, error.safe_message) from error
        except Exception as error:
            raise LocalControlActionError(
                "MCU_MAINTENANCE_PORT_UNAVAILABLE",
                "MCU maintenance operation could not be confirmed",
            ) from error


class BusinessControlService:
    """Lifecycle wrapper used by the future non-root business process."""

    def __init__(
        self,
        controller: BusinessControlController,
        server: LocalControlServer,
    ) -> None:
        self.controller = controller
        self.server = server
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.server.start()
        self._started = True

    def mark_ready(self) -> None:
        if not self._started or not self.server.is_running:
            raise RuntimeError("business local control service is not running")
        self.controller.mark_ready()

    @property
    def is_running(self) -> bool:
        return self._started and self.server.is_running

    @property
    def failure(self) -> BaseException | None:
        return self.server.failure

    def stop(self) -> None:
        self.controller.mark_stopping()
        self.controller.wait_for_maintenance_idle()
        if self._started:
            self.server.stop()
            self._started = False


def build_business_control_service(
    socket_path: str | Path,
    *,
    release_version: str,
    allowed_uids: Iterable[int],
    socket_gid: int,
    utc_now: Callable[[], datetime] | None = None,
    instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    enable_mcu_maintenance_candidate: bool = False,
    mcu_maintenance_port: McuMaintenanceHandoffPort | None = None,
    updater_uids: Iterable[int] = (),
) -> BusinessControlService:
    """Build the adapter without resolving accounts or weakening UID checks."""

    action_uids = _require_uid_set(allowed_uids, "allowed_uids")
    if not isinstance(enable_mcu_maintenance_candidate, bool):
        raise ValueError("MCU maintenance candidate flag must be boolean")
    updater_action_uids: frozenset[int] = frozenset()
    if enable_mcu_maintenance_candidate:
        updater_action_uids = _require_uid_set(
            updater_uids,
            "updater_uids",
            allow_root=False,
        )
        _require_mcu_maintenance_port(mcu_maintenance_port)
    controller = BusinessControlController(
        release_version,
        utc_now=utc_now,
        instance_uid_factory=instance_uid_factory,
        mcu_maintenance_port=mcu_maintenance_port,
        maintenance_handoff_enabled=enable_mcu_maintenance_candidate,
    )
    actions = {
        "HEALTH": LocalControlAction(
            controller.health,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
        "GET_STATUS": LocalControlAction(
            controller.health,
            payload_fields=frozenset(),
            allowed_uids=action_uids,
        ),
    }
    if enable_mcu_maintenance_candidate:
        actions.update(
            {
                "OBSERVE_MCU_MAINTENANCE_STATE": LocalControlAction(
                    controller.observe_mcu_maintenance_state,
                    payload_fields=frozenset(),
                    allowed_uids=updater_action_uids,
                ),
                "QUIESCE_MCU_FOR_UPDATE": LocalControlAction(
                    controller.quiesce_mcu_for_update,
                    payload_fields=frozenset(
                        {
                            "updateUid",
                            "handoffUid",
                            "expectedObservationSha256",
                        }
                    ),
                    allowed_uids=updater_action_uids,
                ),
                "VERIFY_MCU_AFTER_UPDATE": LocalControlAction(
                    controller.verify_mcu_after_update,
                    payload_fields=frozenset(
                        {
                            "updateUid",
                            "handoffUid",
                            "expectedFirmwareIdentitySha256",
                            "observedFlashEvidenceSha256",
                        }
                    ),
                    allowed_uids=updater_action_uids,
                ),
            }
        )
    server = LocalControlServer(
        socket_path,
        protocol_name=BUSINESS_PROTOCOL_NAME,
        actions=actions,
        allowed_uids=action_uids | updater_action_uids,
        socket_mode=0o660,
        socket_gid=socket_gid,
    )
    return BusinessControlService(controller, server)


def _require_action_payload(
    payload: dict[str, Any],
    expected_fields: frozenset[str],
) -> None:
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            "MCU maintenance payload fields are invalid",
        )


def _require_uuid4_value(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a lowercase UUIDv4",
        )
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a lowercase UUIDv4",
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a lowercase UUIDv4",
        )
    return value


def _require_uuid4_plain(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _require_sha256_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _require_action_sha256_value(value: Any, field: str) -> str:
    try:
        return _require_sha256_value(value, field)
    except ValueError as error:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a lowercase SHA-256",
        ) from error


def _require_uid_set(
    values: Iterable[int],
    field: str,
    *,
    allow_root: bool = True,
) -> frozenset[int]:
    if values is None:
        raise ValueError(f"{field} must be a non-empty UID set")
    result = frozenset(values)
    if not result or any(
        isinstance(uid, bool)
        or not isinstance(uid, int)
        or uid < 0
        or (not allow_root and uid == 0)
        for uid in result
    ):
        qualifier = "non-negative" if allow_root else "positive non-root"
        raise ValueError(f"{field} must be a non-empty set of {qualifier} UIDs")
    return result


def _require_mcu_maintenance_port(
    port: McuMaintenanceHandoffPort | None,
) -> McuMaintenanceHandoffPort:
    required_methods = (
        "observe_mcu_maintenance_state",
        "quiesce_mcu_for_update",
        "verify_mcu_after_update",
    )
    if port is None or any(
        not callable(getattr(port, method, None)) for method in required_methods
    ):
        raise ValueError("MCU maintenance handoff port is invalid")
    return port


def _canonical_request_sha256(stage: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "domain": MCU_MAINTENANCE_EVIDENCE_DOMAIN,
            "schemaVersion": MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION,
            "stage": stage,
            "payload": payload,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _is_application_observation(evidence: McuMaintenanceEvidence) -> bool:
    f3 = evidence.f3
    return (
        f3.query_status == "OK"
        and f3.mode == 1
        and f3.status_code == 0
        and f3.safe_flags == 0x0F
        and f3.firmware_identity is not None
        and evidence.uart_handed_off is False
    )


def _is_confirmed_quiesce(evidence: McuMaintenanceEvidence) -> bool:
    f3 = evidence.f3
    return (
        f3.query_status == "OK"
        and f3.mode == 2
        and f3.status_code == 0
        and f3.safe_flags == 0x1F
        and f3.firmware_identity is not None
        and evidence.f1.query_status != "UNKNOWN"
        and evidence.uart_handed_off is True
    )


def _is_confirmed_verification(evidence: McuMaintenanceEvidence) -> bool:
    f3 = evidence.f3
    f1 = evidence.f1
    return (
        f3.query_status == "OK"
        and f3.mode == 1
        and f3.status_code == 0
        and f3.safe_flags == 0x0F
        and f3.firmware_identity is not None
        and f1.query_status == "OK"
        and f1.communication_healthy is True
        and f1.valid_flags == 3
        and f1.weight_grams is not None
        and 0 <= f1.weight_grams <= 350_000
        and isinstance(f1.infrared_blocked, bool)
        and f1.smoke_state in {"NORMAL", "ALARM"}
        and f1.smoke_sensor_health == "OK"
        and evidence.uart_handed_off is False
    )


def _require_release_version(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError("business release version is invalid")
    return value


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("business runtime clock must be timezone-aware")
    rendered = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")

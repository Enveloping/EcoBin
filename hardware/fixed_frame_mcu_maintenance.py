"""Business-owned fixed-frame UART handoff for permanent MCU updates.

The permanent updater owns update policy and durable maintenance state.  The
replaceable business runtime remains the only process allowed to use the MCU
application protocol, so it exposes three deliberately small operations:
observe the current application, stop it safely and relinquish the UART, then
re-open the application UART and prove the installed firmware and sensors.

This adapter never drives BOOT0/NRST and never flashes bytes.  Those privileged
operations stay in the fixed, socket-activated MCU helper.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from business_control import (
    McuF1Evidence,
    McuF3Evidence,
    McuFirmwareIdentity,
    McuMaintenanceEvidence,
    McuMaintenancePortError,
)


_FAILED_QUERY_STATUSES = frozenset({"TIMEOUT", "PROTOCOL_ERROR"})


class FixedFrameMcuMaintenancePort:
    """Translate the real revision-2 F3/F1 protocol into strict evidence."""

    def __init__(
        self,
        uart_link: Any,
        *,
        active_work_reader: Callable[[], Mapping[str, Any] | None],
        shutdown_requested: Callable[[], bool],
        maintenance_authorizer: Callable[..., None],
    ) -> None:
        for method in (
            "open",
            "close",
            "query_firmware_identity",
            "execute_firmware_update_prepare",
            "query_self_test",
        ):
            if not callable(getattr(uart_link, method, None)):
                raise ValueError("fixed-frame MCU maintenance UART is invalid")
        if not callable(active_work_reader):
            raise ValueError("active-work reader must be callable")
        if not callable(shutdown_requested):
            raise ValueError("shutdown-state reader must be callable")
        if not callable(maintenance_authorizer):
            raise ValueError("maintenance authorizer must be callable")
        self._uart = uart_link
        self._active_work_reader = active_work_reader
        self._shutdown_requested = shutdown_requested
        self._maintenance_authorizer = maintenance_authorizer

    def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence:
        self._require_runtime_available()
        if not bool(getattr(self._uart, "is_open", False)):
            try:
                opened = self._uart.open()
            except Exception as error:
                raise McuMaintenancePortError("MCU_UART_UNAVAILABLE") from error
            if opened is not True:
                raise McuMaintenancePortError("MCU_UART_UNAVAILABLE")
        return McuMaintenanceEvidence(
            f3=_f3_evidence(self._uart.query_firmware_identity()),
            f1=_f1_evidence(self._uart.query_self_test()),
            uart_handed_off=False,
        )

    def quiesce_mcu_for_update(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_observation_sha256: str,
    ) -> McuMaintenanceEvidence:
        return self._quiesce_mcu(
            update_uid=update_uid,
            handoff_uid=handoff_uid,
            expected_observation_sha256=expected_observation_sha256,
            require_healthy_self_test=True,
        )

    def quiesce_mcu_for_recovery(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_observation_sha256: str,
    ) -> McuMaintenanceEvidence:
        return self._quiesce_mcu(
            update_uid=update_uid,
            handoff_uid=handoff_uid,
            expected_observation_sha256=expected_observation_sha256,
            require_healthy_self_test=False,
        )

    def _quiesce_mcu(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_observation_sha256: str,
        require_healthy_self_test: bool,
    ) -> McuMaintenanceEvidence:
        self._require_runtime_available()
        self._require_permanent_maintenance(
            update_uid,
            "QUIESCE",
            handoff_uid=handoff_uid,
            observation_evidence_sha256=expected_observation_sha256,
        )
        self._require_no_active_work()
        self._require_uart_open()

        # Re-prove the application and sensors immediately before F2.  An old
        # observation digest alone is not sufficient if the MCU or business
        # work state changed while the updater was draining the device.
        current_f3 = _f3_evidence(self._uart.query_firmware_identity())
        current_f1 = _f1_evidence(self._uart.query_self_test())
        if not _application_identity_ready(current_f3) or (
            require_healthy_self_test and not _self_test_ready(current_f1)
        ):
            raise McuMaintenancePortError("MCU_QUIESCE_FAILED")
        # Re-read the durable fence after the bounded UART observations.  New
        # work cannot race into the following F2 boundary while the updater
        # still proves MAINTENANCE with no outstanding permits or actions.
        self._require_permanent_maintenance(
            update_uid,
            "QUIESCE",
            handoff_uid=handoff_uid,
            observation_evidence_sha256=expected_observation_sha256,
        )
        self._require_no_active_work()

        raw_prepare: Mapping[str, Any] | None = None
        prepare_error: Exception | None = None
        close_error: Exception | None = None
        try:
            raw_prepare = self._uart.execute_firmware_update_prepare()
        except Exception as error:  # keep UART handoff fail-closed
            prepare_error = error
        finally:
            # Once an F2 write may have happened, normal business traffic must
            # stop even when its F3 response was lost.  The permanent updater
            # retains the maintenance lock and performs explicit recovery.
            try:
                self._uart.close()
            except Exception as error:  # pragma: no cover - defensive
                close_error = error

        if close_error is not None or bool(getattr(self._uart, "is_open", True)):
            raise McuMaintenancePortError("MCU_UART_UNAVAILABLE") from close_error
        if prepare_error is not None or raw_prepare is None:
            raise McuMaintenancePortError("MCU_QUIESCE_FAILED") from prepare_error
        return McuMaintenanceEvidence(
            f3=_f3_evidence(raw_prepare),
            f1=current_f1,
            uart_handed_off=True,
        )

    def verify_mcu_after_update(
        self,
        *,
        update_uid: str,
        handoff_uid: str,
        expected_firmware_identity_sha256: str,
        observed_flash_evidence_sha256: str,
        quiesce_evidence_sha256: str,
    ) -> McuMaintenanceEvidence:
        # The controller binds these values into the returned evidence digest
        # and checks the expected firmware identity.  This hardware port only
        # obtains fresh facts from the fixed application protocol.
        self._require_runtime_available()
        self._require_permanent_maintenance(
            update_uid,
            "VERIFY",
            handoff_uid=handoff_uid,
            quiesce_evidence_sha256=quiesce_evidence_sha256,
            expected_firmware_identity_sha256=(
                expected_firmware_identity_sha256
            ),
            observed_flash_evidence_sha256=observed_flash_evidence_sha256,
        )
        self._require_no_active_work()
        if not bool(getattr(self._uart, "is_open", False)):
            try:
                opened = self._uart.open()
            except Exception as error:
                raise McuMaintenancePortError(
                    "MCU_UART_UNAVAILABLE"
                ) from error
            if opened is not True:
                raise McuMaintenancePortError("MCU_UART_UNAVAILABLE")
        return McuMaintenanceEvidence(
            f3=_f3_evidence(self._uart.query_firmware_identity()),
            f1=_f1_evidence(self._uart.query_self_test()),
            uart_handed_off=False,
        )

    def _require_runtime_available(self) -> None:
        try:
            stopping = self._shutdown_requested()
        except Exception as error:
            raise McuMaintenancePortError(
                "MCU_OBSERVATION_UNAVAILABLE"
            ) from error
        if stopping is not False:
            raise McuMaintenancePortError("MCU_MAINTENANCE_BUSY")

    def _require_no_active_work(self) -> None:
        try:
            active = self._active_work_reader()
        except Exception as error:
            raise McuMaintenancePortError(
                "MCU_OBSERVATION_UNAVAILABLE"
            ) from error
        if active is not None:
            raise McuMaintenancePortError("MCU_MAINTENANCE_BUSY")

    def _require_permanent_maintenance(
        self,
        update_uid: str,
        stage: str,
        **bindings: str,
    ) -> None:
        try:
            self._maintenance_authorizer(update_uid, stage, **bindings)
        except Exception as error:
            raise McuMaintenancePortError(
                "MCU_MAINTENANCE_NOT_AUTHORIZED"
            ) from error

    def _require_uart_open(self) -> None:
        if not bool(getattr(self._uart, "is_open", False)):
            raise McuMaintenancePortError("MCU_UART_UNAVAILABLE")


def _f3_evidence(raw: Any) -> McuF3Evidence:
    if not isinstance(raw, Mapping):
        return _failed_f3("PROTOCOL_ERROR")
    query_status = raw.get("queryStatus")
    if query_status != "OK":
        return _failed_f3(
            query_status if query_status in _FAILED_QUERY_STATUSES else "PROTOCOL_ERROR"
        )
    try:
        identity = McuFirmwareIdentity(
            protocol_revision=raw.get("protocolRevision"),
            firmware_version_code=raw.get("firmwareVersionCode"),
            firmware_version=raw.get("firmwareVersion"),
            firmware_identity_hex=raw.get("firmwareIdentityHex"),
        )
        return McuF3Evidence(
            query_status="OK",
            mode=raw.get("mode"),
            status_code=raw.get("statusCode"),
            safe_flags=raw.get("safeFlags"),
            firmware_identity=identity,
        )
    except (TypeError, ValueError):
        return _failed_f3("PROTOCOL_ERROR")


def _failed_f3(query_status: str) -> McuF3Evidence:
    return McuF3Evidence(
        query_status=query_status,
        mode=None,
        status_code=None,
        safe_flags=None,
        firmware_identity=None,
    )


def _f1_evidence(raw: Any) -> McuF1Evidence:
    if not isinstance(raw, Mapping):
        return _failed_f1("PROTOCOL_ERROR")
    query_status = raw.get("queryStatus")
    if query_status != "OK":
        return _failed_f1(
            query_status if query_status in _FAILED_QUERY_STATUSES else "PROTOCOL_ERROR"
        )
    try:
        return McuF1Evidence(
            query_status="OK",
            communication_healthy=raw.get("communicationHealthy"),
            valid_flags=raw.get("validFlags"),
            weight_grams=raw.get("weightGrams"),
            infrared_blocked=raw.get("infraredBlocked"),
            smoke_state=raw.get("smokeState"),
            smoke_sensor_health=raw.get("smokeSensorHealth"),
        )
    except (TypeError, ValueError):
        return _failed_f1("PROTOCOL_ERROR")


def _failed_f1(query_status: str) -> McuF1Evidence:
    return McuF1Evidence(
        query_status=query_status,
        communication_healthy=False,
        valid_flags=0,
        weight_grams=None,
        infrared_blocked=None,
        smoke_state="UNKNOWN",
        smoke_sensor_health=query_status,
    )


def _application_identity_ready(evidence: McuF3Evidence) -> bool:
    return bool(
        evidence.query_status == "OK"
        and evidence.mode == 1
        and evidence.status_code == 0
        and evidence.safe_flags == 0x0F
        and evidence.firmware_identity is not None
    )


def _self_test_ready(evidence: McuF1Evidence) -> bool:
    return bool(
        evidence.query_status == "OK"
        and evidence.communication_healthy is True
        and evidence.valid_flags == 3
        and evidence.weight_grams is not None
        and isinstance(evidence.infrared_blocked, bool)
        and evidence.smoke_state in {"NORMAL", "ALARM"}
        and evidence.smoke_sensor_health == "OK"
    )

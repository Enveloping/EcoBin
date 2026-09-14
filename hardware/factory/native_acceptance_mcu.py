"""UART-v2 MCU facade for isolated, journalled factory acceptance.

The factory executor owns one nonblocking, exclusive serial endpoint.  Every
probe/query, configuration, action, and result identity is atomically
reserved before its one bounded write.  A lost reply is recovered only by a
read-only query; a mechanical START is never replayed.
"""

from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable, Optional
import uuid

from mcu_configuration import (
    NATIVE_DEVICE_CONSTANTS,
    NATIVE_PORT_CONSTANTS,
    NativeMcuConfiguration,
)
from uart2_transport import NativeUartTransport
import uart2_protocol as uart
from uart_link import compute_native_mcu_payload_sha256

from .acceptance_hardware import AcceptanceHardwareError
from .acceptance_storage import AcceptanceStorageError, AtomicJsonFile


DEFAULT_STATE_PATH = Path(
    "/var/lib/ecobin/factory-test/native-uart-state.json"
)
STATE_SCHEMA_VERSION = 1
MAXIMUM_SAFE_IDENTIFIER = 9_007_199_254_740_991
FACTORY_BOOT_ID_NAMESPACE = 8_000_000_000_000_000
MAXIMUM_COMMAND_SEQUENCE = 0xFFFFFFFF
DEFAULT_BOOT_ATTEMPT_TIMEOUT_MS = 250
POLL_INTERVAL_SECONDS = 0.005
MAXIMUM_WEIGHT_GRAMS = 350_000
MINIMUM_WEIGHT_GRAMS = -MAXIMUM_WEIGHT_GRAMS
MAXIMUM_WEIGHT_FACT_AGE_MS = 750
MAXIMUM_ENVIRONMENT_FACT_AGE_MS = 1_000
COMMAND_RESPONSE_TIMEOUT_MS = 250
RESULT_CONFIRM_TIMEOUT_MS = 3_000
MAXIMUM_PENDING_REPLY_FRAMES = 256
MAXIMUM_COMPLETED_ACTIONS = 16
FACTORY_CONFIG_VERSION = 3
FACTORY_CONFIG_PROFILE = "ECOBIN_FACTORY_UART_V2_ONE_PORT_V2"
FACTORY_DEVICE_CONFIG = {
    "continueDeliveryWaitMs": 30_000,
    "negativeWeightThresholdGrams": 500,
    "deliveryAutoCloseMs": 120_000,
    "weightMeasurementTimeoutMs": 5_000,
    "deliveryDoorTravelWaitMs": 30_000,
    "cleanSolenoidPulseMs": 1_000,
    "smokeMonitoringEnabled": True,
}
FACTORY_PORT_CONFIG = {
    "portNo": 1,
    "displayName": "投口1",
    "enabled": True,
    "unitPriceTenThousandths": 4_500,
    "fullnessMode": "SENSOR_OR_WEIGHT",
    "configuredFullWeightGrams": 50_000,
    "fullnessSettleWaitMs": 5_000,
    "fullnessSensorKind": "ULTRASONIC",
    "fullnessDistanceThresholdMm": 600,
    "fullnessSampleCount": 5,
    "fullnessMinimumValidSampleCount": 3,
    "fullnessEchoTimeoutUs": 30_000,
    "weightStableWindowMs": 1_500,
    "weightMaximumFluctuationGrams": 100,
    "weightRequiredSampleCount": 5,
    "weightMeasurementTimeoutMs": 5_000,
    # The scale reports an absolute signed value.  A mechanically useful
    # installation may have a large stable zero offset; factory acceptance
    # validates the known load by delta in its dedicated weight step.
    "weightMinimumGrams": MINIMUM_WEIGHT_GRAMS,
    "weightMaximumGrams": 350_000,
    "calibrationVersion": 4,
}
COMMAND_IDENTITY_FIELDS = (
    "mcuCommandUid",
    "commandDigestSha256",
    "targetMcuBootId",
    "commandSequence",
)
USABLE_RESULT_MEASUREMENT_KINDS = {"STABLE_MEAN", "TIMEOUT_MEDIAN"}


def _factory_configuration() -> NativeMcuConfiguration:
    content = {
        "profile": FACTORY_CONFIG_PROFILE,
        "deviceConfig": FACTORY_DEVICE_CONFIG,
        "ports": [FACTORY_PORT_CONFIG],
    }
    content_sha256 = hashlib.sha256(
        json.dumps(
            content,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "mcuConfigurationProfile": "UART_V2_SIMPLIFIED",
        "config": {
            "version": FACTORY_CONFIG_VERSION,
            "contentSha256": content_sha256,
        },
        "deviceConfig": dict(FACTORY_DEVICE_CONFIG),
        "ports": [dict(FACTORY_PORT_CONFIG)],
    }
    mcu_sha256 = compute_native_mcu_payload_sha256(payload)
    device = dict(FACTORY_DEVICE_CONFIG) | dict(NATIVE_DEVICE_CONSTANTS)
    port = {
        key: value
        for key, value in FACTORY_PORT_CONFIG.items()
        if key != "displayName"
    } | dict(NATIVE_PORT_CONSTANTS)
    return NativeMcuConfiguration(
        config_version=FACTORY_CONFIG_VERSION,
        content_sha256=content_sha256,
        expected_sha256=mcu_sha256,
        device=device,
        ports=[port],
    )


class NativeAcceptanceMcu:
    """Factory-only owner of the current read-only UART-v2 observations."""

    def __init__(
        self,
        *,
        port: str,
        state_path: Path | str,
        serial_factory: Optional[Callable[..., object]],
        boot_attempt_timeout_ms: int,
    ) -> None:
        if port != "/dev/ttyS5":
            raise ValueError("factory native UART must be /dev/ttyS5")
        if (
            type(boot_attempt_timeout_ms) is not int
            or boot_attempt_timeout_ms < 1
        ):
            raise ValueError("boot attempt timeout must be positive")
        self._port_name = port
        self._serial_factory = serial_factory
        self._state_file = AtomicJsonFile(
            state_path,
            fault_prefix="native_acceptance_uart_state",
        )
        self._boot_attempt_timeout_ms = boot_attempt_timeout_ms
        self._port = None
        self._transport: NativeUartTransport | None = None
        self._mcu_boot_id = 0
        self._pending_frames: deque[tuple[str, dict]] = deque()

    @classmethod
    def for_port(
        cls,
        port: str = "/dev/ttyS5",
        *,
        state_path: Path | str = DEFAULT_STATE_PATH,
        serial_factory: Optional[Callable[..., object]] = None,
        boot_attempt_timeout_ms: int = DEFAULT_BOOT_ATTEMPT_TIMEOUT_MS,
    ) -> "NativeAcceptanceMcu":
        return cls(
            port=port,
            state_path=state_path,
            serial_factory=serial_factory,
            boot_attempt_timeout_ms=boot_attempt_timeout_ms,
        )

    @property
    def is_open(self) -> bool:
        return bool(self._port is not None and self._port.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        factory = self._serial_factory
        if factory is None:
            if os.name != "posix":
                raise AcceptanceHardwareError("UART5_EXCLUSIVE_UNAVAILABLE")
            import serial

            factory = serial.Serial
        port = None
        try:
            port = factory(
                port=self._port_name,
                baudrate=115200,
                timeout=0,
                write_timeout=1,
                exclusive=True,
            )
            transport = NativeUartTransport(port)
        except Exception as error:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            raise AcceptanceHardwareError("UART5_OPEN_FAILED") from error
        self._port = port
        self._transport = transport

    def close(self) -> None:
        port = self._port
        self._transport = None
        self._port = None
        self._mcu_boot_id = 0
        if port is not None:
            try:
                port.close()
            except Exception as error:
                raise AcceptanceHardwareError("UART5_CLOSE_FAILED") from error

    def query_identity(self, timeout_ms: int = 3_000) -> dict:
        deadline = self._deadline(timeout_ms)
        self.open()
        while time.monotonic() < deadline:
            boot_id = self._ensure_boot(deadline)
            query_id = self._reserve_identifier("lastQueryId")
            self._write(
                "QUERY_DEVICE_IDENTITY",
                query_id,
                {"queryId": query_id, "targetMcuBootId": boot_id},
            )
            reply = self._await_reply(
                "DEVICE_IDENTITY_REPLY",
                deadline,
                lambda values: values["queryId"] == query_id
                and values["targetMcuBootId"] == boot_id,
            )
            if reply is None:
                break
            if (
                reply["status"] == "BOOT_MISMATCH"
                or reply["currentMcuBootId"] != boot_id
            ):
                self._mcu_boot_id = 0
                continue
            if reply["status"] != "AVAILABLE":
                raise AcceptanceHardwareError("MCU_IDENTITY_UNAVAILABLE")
            self._observe_highest_command_sequence(
                reply["highestCommandSequence"]
            )
            identity = (
                reply["firmwareIdentityHigh"] << 32
            ) | reply["firmwareIdentityLow"]
            return {
                "queryStatus": "OK",
                "mode": 1,
                "statusCode": 0,
                "protocolRevision": reply["protocolMajor"],
                "mcuBootId": reply["currentMcuBootId"],
                "mcuHighestCommandSequence": reply[
                    "highestCommandSequence"
                ],
                "mcuCapabilityBitmap": reply["capabilityBitmap"],
                "firmwareVersion": reply["firmwareVersion"],
                "firmwareVersionCode": reply["firmwareVersionCode"],
                "firmwareIdentityHex": f"{identity:016x}",
            }
        raise AcceptanceHardwareError("MCU_IDENTITY_TIMEOUT")

    def query_self_test(self, timeout_ms: int = 3_000) -> dict:
        deadline = self._deadline(timeout_ms)
        self.open()
        self._ensure_configuration(deadline)
        return self._map_self_test(self._query_device_facts(deadline))

    @staticmethod
    def _map_self_test(facts: dict) -> dict:
        captured = facts["capturedUptimeMs"]
        if type(captured) is not int or captured < 0:
            raise AcceptanceHardwareError("MCU_DEVICE_FACTS_INVALID")

        weight = facts["scaleWeightGrams"]
        if (
            facts["scaleReadStatus"] != "VALID"
            or type(weight) is not int
            or not MINIMUM_WEIGHT_GRAMS <= weight <= MAXIMUM_WEIGHT_GRAMS
        ):
            raise AcceptanceHardwareError("MCU_WEIGHT_FACT_UNAVAILABLE")
        NativeAcceptanceMcu._require_fresh_observation(
            captured,
            facts["scaleCapturedUptimeMs"],
            MAXIMUM_WEIGHT_FACT_AGE_MS,
            "MCU_WEIGHT_FACT_STALE",
        )

        smoke_state = facts["smokeObservationState"]
        smoke_projection = {
            "NORMAL": (0, "OK"),
            "ALARM": (1, "ALARM"),
            "UNAVAILABLE": (2, "UNAVAILABLE"),
            "NOT_OBSERVED": (3, "NOT_OBSERVED"),
        }.get(smoke_state)
        if smoke_projection is None:
            raise AcceptanceHardwareError("MCU_SMOKE_FACT_UNHEALTHY")
        # UNAVAILABLE carries no physical observation whose age could be
        # evaluated.  The MCU may legitimately leave its timestamp at the
        # boot default while preserving the auxiliary warning.
        if smoke_state in {"NORMAL", "ALARM"}:
            NativeAcceptanceMcu._require_fresh_observation(
                captured,
                facts["smokeObservedUptimeMs"],
                MAXIMUM_ENVIRONMENT_FACT_AGE_MS,
                "MCU_SMOKE_FACT_STALE",
            )

        kind = facts["fullnessObservationKind"]
        fullness_status = facts["fullnessReadStatus"]
        if kind not in {
            "DIGITAL_INFRARED",
            "ULTRASONIC",
        }:
            raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
        if fullness_status not in {"VALID", "NOT_OBSERVED", "UNAVAILABLE"}:
            raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
        distance = None
        fullness_blocked = None
        if fullness_status == "VALID":
            if kind == "DIGITAL_INFRARED":
                fullness_blocked = facts["fullnessInfraredBlocked"]
                if not isinstance(fullness_blocked, bool):
                    raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
            else:
                distance = facts["fullnessDistanceMm"]
                if type(distance) is not int or not 0 <= distance <= 4_000:
                    raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
                fullness_blocked = distance < FACTORY_PORT_CONFIG[
                    "fullnessDistanceThresholdMm"
                ]
        # Only a VALID sample claims a current physical observation.
        # UNAVAILABLE/NOT_OBSERVED remain non-blocking auxiliary facts.
        if fullness_status == "VALID":
            NativeAcceptanceMcu._require_fresh_observation(
                captured,
                facts["fullnessCapturedUptimeMs"],
                MAXIMUM_ENVIRONMENT_FACT_AGE_MS,
                "MCU_FULLNESS_FACT_STALE",
            )
        return {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": weight,
            # These three fields identify one physical MCU scale observation.
            # Factory sampling may poll faster than the 250 ms acquisition
            # cadence, so the same cached observation must not be counted more
            # than once.
            "mcuBootId": facts["currentMcuBootId"],
            "scaleAttemptSequence": facts["scaleAttemptSequence"],
            "scaleCapturedUptimeMs": facts["scaleCapturedUptimeMs"],
            "infraredValid": (
                kind == "DIGITAL_INFRARED" and fullness_status == "VALID"
            ),
            # The factory-report v2 compatibility field historically used the
            # infrared name.  For an ultrasonic installation it is the exact
            # threshold projection below, while the raw kind/distance remain
            # available so no caller can mistake it for an infrared reading.
            "infraredBlocked": fullness_blocked,
            "fullnessSensorKind": kind,
            "fullnessReadStatus": fullness_status,
            "fullnessDistanceMm": distance,
            "fullnessDistanceThresholdMm": (
                FACTORY_PORT_CONFIG["fullnessDistanceThresholdMm"]
                if kind == "ULTRASONIC"
                else None
            ),
            "fullnessBlocked": fullness_blocked,
            "smokeCode": smoke_projection[0],
            "smokeState": smoke_state,
            "smokeSensorHealth": smoke_projection[1],
        }

    def execute_update_prepare(self, timeout_ms: int = 3_000) -> dict:
        del timeout_ms
        raise AcceptanceHardwareError("MCU_UPDATE_PREPARE_UNSUPPORTED")

    def prepare_action(self, action: str, action_token: str) -> dict:
        """Durably allocate one factory START identity without writing it."""

        if action not in {"DELIVERY", "CLEAN"}:
            raise ValueError("action must be DELIVERY or CLEAN")
        try:
            parsed_token = uuid.UUID(action_token)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("action_token must be a nonzero canonical UUID") from error
        if str(parsed_token) != action_token or parsed_token.int == 0:
            raise ValueError("action_token must be a nonzero canonical UUID")
        deadline = self._deadline(3_000)
        self.open()
        boot_id = self._ensure_configuration(deadline)
        state = self._load_state()
        active = state.get("activeAction")
        if isinstance(active, dict):
            active = self._validate_active_action(active)
        if isinstance(active, dict) and not active.get("released"):
            if (
                active.get("action") != action
                or active.get("acceptanceActionToken") != action_token
            ):
                raise AcceptanceHardwareError("MCU_FACTORY_ACTION_UNRELEASED")
            if active.get("targetMcuBootId") != boot_id:
                raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
            return self._action_identity(active)
        if isinstance(active, dict):
            self._append_completed_action(state, active)

        configuration = _factory_configuration()
        command_sequence = self._reserve_command_sequence(state)
        command_uid = str(uuid.uuid4())
        work_uid = str(uuid.uuid4())
        name = (
            "START_DELIVERY_SESSION"
            if action == "DELIVERY"
            else "START_CLEAN_OPERATION"
        )
        common = {
            "mcuCommandUid": command_uid,
            "commandDigestSha256": "00" * 32,
            "targetMcuBootId": boot_id,
            "commandSequence": command_sequence,
            "portNo": 1,
            "configVersion": FACTORY_CONFIG_VERSION,
            "configContentSha256": self._configuration_content_sha256(
                configuration
            ),
            "startExecutionWindowMs": 5_000,
        }
        if action == "DELIVERY":
            values = common | {
                "sessionUid": work_uid,
                "unitPriceTenThousandths": FACTORY_PORT_CONFIG[
                    "unitPriceTenThousandths"
                ],
                "continueDeliveryWaitMs": FACTORY_DEVICE_CONFIG[
                    "continueDeliveryWaitMs"
                ],
                "negativeWeightThresholdGrams": FACTORY_DEVICE_CONFIG[
                    "negativeWeightThresholdGrams"
                ],
                "deliveryAutoCloseMs": FACTORY_DEVICE_CONFIG[
                    "deliveryAutoCloseMs"
                ],
            }
        else:
            values = common | {
                "operationUid": work_uid,
                "operationWindowMs": 300_000,
            }
        values["commandDigestSha256"] = uart.compute_command_digest(name, values)
        payload = uart.encode_payload(name, values)
        state["activeAction"] = {
            "action": action,
            "acceptanceActionToken": action_token,
            "commandName": name,
            "workUid": work_uid,
            **{field: values[field] for field in COMMAND_IDENTITY_FIELDS},
            "payloadHex": payload.hex(),
            "writeAttempted": False,
            "accepted": False,
            "resultPayloadHex": None,
            "resultFacts": None,
            "mappedResult": None,
            "resultSavedPayloadHex": None,
            "resultSavedWriteAttempted": False,
            "released": False,
            "recoveryDisposition": None,
        }
        self._save_state(state)
        return self._action_identity(state["activeAction"])

    def write_action_once(
        self,
        action: str,
        action_token: str | None = None,
    ) -> None:
        if action not in {"DELIVERY", "CLEAN"}:
            raise ValueError("action must be DELIVERY or CLEAN")
        state = self._load_state()
        active = state.get("activeAction")
        if isinstance(active, dict):
            active = self._validate_active_action(active)
        if action_token is not None:
            if (
                not isinstance(active, dict)
                or active.get("released")
                or active.get("action") != action
                or active.get("acceptanceActionToken") != action_token
            ):
                raise AcceptanceHardwareError(
                    "MCU_FACTORY_ACTION_IDENTITY_MISMATCH"
                )
        elif not isinstance(active, dict) or active.get("released"):
            # Kept only for direct adapter use and its isolated tests.  The
            # factory executor always supplies its durable action token.
            self.prepare_action(action, str(uuid.uuid4()))
            active = self._require_active_action(action)
        if active.get("action") != action:
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_UNRELEASED")
        deadline = self._deadline(3_000)
        self.open()
        if active.get("targetMcuBootId") != self._ensure_configuration(deadline):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
        self._ensure_command_accepted(active["mcuCommandUid"], deadline)

    def inspect_action(self, action: str, action_token: str) -> dict | None:
        """Inspect only the exact prepared factory action in the local journal."""

        active = self._load_state().get("activeAction")
        if active is None:
            return None
        active = self._validate_active_action(active)
        if (
            active["action"] != action
            or active["acceptanceActionToken"] != action_token
        ):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_IDENTITY_MISMATCH")
        return self._action_identity(active) | {
            "writeAttempted": active["writeAttempted"],
            "accepted": active["accepted"],
            "resultRecorded": active["resultPayloadHex"] is not None,
            "released": active["released"],
            "recoveryDisposition": active["recoveryDisposition"],
        }

    def recover_action_disposition(
        self,
        action: str,
        action_token: str,
        timeout_ms: int = 3_000,
    ) -> str:
        """Prove NOT_SENT, BOOT_CHANGED, RUNNING, or RESULT_AVAILABLE.

        This method never creates or replays START.  QUERY_COMMAND is used only
        when the durable write-intent bit makes the actual write ambiguous.
        """

        inspected = self.inspect_action(action, action_token)
        if inspected is None:
            return "NOT_PREPARED"
        if inspected["released"]:
            return "RESULT_RELEASED"
        if not inspected["writeAttempted"]:
            self._set_action_recovery_disposition(action_token, "NOT_SENT")
            return "NOT_SENT"
        deadline = self._deadline(timeout_ms)
        self.open()
        active = self._require_active_action(action)
        if active.get("mappedResult") is not None:
            return "RESULT_AVAILABLE"
        if self._ensure_boot(deadline) != active["targetMcuBootId"]:
            self._set_action_recovery_disposition(action_token, "BOOT_CHANGED")
            return "BOOT_CHANGED"
        if active.get("resultPayloadHex"):
            return "RESULT_AVAILABLE"
        if not active["accepted"]:
            try:
                self._ensure_command_accepted(active["mcuCommandUid"], deadline)
            except AcceptanceHardwareError as error:
                if error.code == "MCU_COMMAND_NOT_SEEN":
                    self._set_action_recovery_disposition(
                        action_token, "NOT_SENT"
                    )
                    return "NOT_SENT"
                if error.code == "MCU_COMMAND_BOOT_MISMATCH":
                    self._set_action_recovery_disposition(
                        action_token, "BOOT_CHANGED"
                    )
                    return "BOOT_CHANGED"
                raise
            active = self._require_active_action(action)
        reply = self._query_work(active, deadline)
        if reply["status"] == "RUNNING":
            return "RUNNING"
        if reply["status"] == "RESULT_HELD":
            self._query_result_from_work(reply, deadline)
            self._poll_until_result(action, deadline)
            return "RESULT_AVAILABLE"
        if reply["status"] == "RESULT_RELEASED":
            return "RESULT_RELEASED"
        if reply["status"] == "BOOT_MISMATCH":
            self._set_action_recovery_disposition(action_token, "BOOT_CHANGED")
            return "BOOT_CHANGED"
        raise AcceptanceHardwareError("MCU_FACTORY_WORK_NOT_FOUND")

    def retire_recovery_action(
        self,
        action: str,
        action_token: str,
        disposition: str,
    ) -> None:
        """Retire only an exact action with a durable no-execution proof."""

        if disposition not in {"NOT_SENT", "BOOT_CHANGED"}:
            raise ValueError("unsupported recovery action disposition")
        state = self._load_state()
        active = state.get("activeAction")
        if active is None:
            return
        active = self._validate_active_action(active)
        if (
            active["action"] != action
            or active["acceptanceActionToken"] != action_token
            or active["recoveryDisposition"] != disposition
        ):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_IDENTITY_MISMATCH")
        retired = dict(active) | {"retiredRecoveryDisposition": disposition}
        self._append_completed_action(state, retired)
        state["activeAction"] = None
        self._save_state(state)

    def _set_action_recovery_disposition(
        self, action_token: str, disposition: str
    ) -> None:
        state = self._load_state()
        active = state.get("activeAction")
        if not isinstance(active, dict):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_MISSING")
        active = self._validate_active_action(active)
        if active["acceptanceActionToken"] != action_token:
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_IDENTITY_MISMATCH")
        active["recoveryDisposition"] = disposition
        self._save_state(state)

    @staticmethod
    def _action_identity(active: dict) -> dict:
        return {
            "action": active["action"],
            "acceptanceActionToken": active["acceptanceActionToken"],
            "workUid": active["workUid"],
            **{field: active[field] for field in COMMAND_IDENTITY_FIELDS},
        }

    @staticmethod
    def _append_completed_action(state: dict, action: dict) -> None:
        completed = state["completedActions"]
        completed.append(action)
        if len(completed) > MAXIMUM_COMPLETED_ACTIONS:
            del completed[:-MAXIMUM_COMPLETED_ACTIONS]

    def await_final_result(self, action: str, timeout_ms: int) -> dict:
        if action not in {"DELIVERY", "CLEAN"}:
            raise ValueError("action must be DELIVERY or CLEAN")
        deadline = self._deadline(timeout_ms)
        self.open()
        active = self._require_active_action(action)
        if active["targetMcuBootId"] != self._ensure_boot(deadline):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
        while not active.get("resultPayloadHex") and time.monotonic() < deadline:
            self._poll_frames()
            active = self._require_active_action(action)
            if active.get("resultPayloadHex"):
                break
            reply = self._query_work(active, deadline)
            if reply["status"] == "RESULT_HELD":
                self._query_result_from_work(reply, deadline)
            elif reply["status"] in {"NOT_FOUND", "IDENTITY_CONFLICT"}:
                raise AcceptanceHardwareError("MCU_FACTORY_WORK_NOT_FOUND")
            elif reply["status"] == "BOOT_MISMATCH":
                raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
            active = self._require_active_action(action)
        if not active.get("resultPayloadHex"):
            raise AcceptanceHardwareError("FINAL_RESULT_TIMEOUT")
        return self._map_and_store_action_result(action, deadline)

    def recover_final_result(self, action: str, timeout_ms: int) -> dict:
        """Recover only an original result; never create or resend START."""

        if action not in {"DELIVERY", "CLEAN"}:
            raise ValueError("action must be DELIVERY or CLEAN")
        deadline = self._deadline(timeout_ms)
        self.open()
        active = self._require_active_action(action)
        # A mapped result is already durable factory evidence.  It remains
        # usable if the MCU subsequently restarted; only an unfinished action
        # is abandoned on a new boot.
        if active.get("mappedResult") is not None:
            return active["mappedResult"]
        boot_id = self._ensure_boot(deadline)
        if active["targetMcuBootId"] != boot_id:
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
        if active.get("resultPayloadHex"):
            return self._map_and_store_action_result(action, deadline)
        reply = self._query_work(active, deadline)
        status = reply["status"]
        if status == "RUNNING":
            raise AcceptanceHardwareError("MCU_FACTORY_WORK_STILL_RUNNING")
        if status == "RESULT_HELD":
            self._query_result_from_work(reply, deadline)
            self._poll_until_result(action, deadline)
            return self._map_and_store_action_result(action, deadline)
        if status == "BOOT_MISMATCH":
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
        raise AcceptanceHardwareError("MCU_FACTORY_WORK_NOT_FOUND")

    def confirm_final_result(self, result: dict) -> None:
        if not isinstance(result, dict):
            raise ValueError("factory result must be an object")
        deadline = self._deadline(RESULT_CONFIRM_TIMEOUT_MS)
        self.open()
        state = self._load_state()
        active = state.get("activeAction")
        if isinstance(active, dict):
            active = self._validate_active_action(active)
        if not isinstance(active, dict) or active.get("mappedResult") != result:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_IDENTITY_MISMATCH")
        if active.get("released"):
            return
        raw = bytes.fromhex(active["resultPayloadHex"])
        values = uart.decode_payload("WORK_RESULT", raw)
        saved_values = {
            key: values[key]
            for key in (
                "mcuBootId",
                "resultSequence",
                "workUid",
                "resultDigestSha256",
            )
        }
        saved_payload = uart.encode_payload("RESULT_SAVED", saved_values)
        if active.get("resultSavedPayloadHex") not in {
            None,
            saved_payload.hex(),
        }:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_IDENTITY_MISMATCH")
        if active.get("resultSavedPayloadHex") is None:
            active["resultSavedPayloadHex"] = saved_payload.hex()
            state["activeAction"] = active
            self._save_state(state)
        if not active.get("resultSavedWriteAttempted"):
            active["resultSavedWriteAttempted"] = True
            state["activeAction"] = active
            self._save_state(state)
        # RESULT_SAVED is a non-mechanical, identity-bound idempotent custody
        # acknowledgement.  Retrying the exact bytes is required after a
        # process crash between the durable intent above and the serial write.
        self._write_payload("RESULT_SAVED", saved_payload, values["resultSequence"])
        reply = self._await_reply(
            "RESULT_SAVED_REPLY",
            min(deadline, time.monotonic() + COMMAND_RESPONSE_TIMEOUT_MS / 1000),
            lambda item: all(item[key] == saved_values[key] for key in saved_values),
        )
        if reply is not None and reply["status"] in {
            "RELEASED",
            "ALREADY_RELEASED",
        }:
            self._mark_result_released()
            return
        if reply is not None and reply["status"] == "BOOT_MISMATCH":
            # The exact result is already durably mapped above.  A new MCU
            # boot cannot still retain the old result, so no custody release
            # remains to be acknowledged.
            self._mark_result_released()
            return
        while time.monotonic() < deadline:
            status = self._query_result_status(saved_values, deadline)
            if status == "RELEASED":
                self._mark_result_released()
                return
            if status == "BOOT_MISMATCH":
                self._mark_result_released()
                return
            if status in {"NOT_FOUND", "IDENTITY_CONFLICT"}:
                raise AcceptanceHardwareError("MCU_FACTORY_RESULT_NOT_FOUND")
        raise AcceptanceHardwareError("MCU_FACTORY_RESULT_RELEASE_UNCONFIRMED")

    def business_input_marker(self) -> dict[str, int]:
        return {"workResultCount": self._load_state()["workResultCount"]}

    def require_business_quiet(
        self,
        quiet_ms: int = 150,
        *,
        invalid_marker: Optional[dict[str, int]] = None,
    ) -> None:
        if type(quiet_ms) is not int or quiet_ms < 0:
            raise ValueError("quiet_ms must be a non-negative integer")
        marker = invalid_marker or self.business_input_marker()
        if set(marker) != {"workResultCount"}:
            raise ValueError("business input marker is invalid")
        deadline = time.monotonic() + quiet_ms / 1000
        while time.monotonic() < deadline:
            self._poll_frames()
            if self._load_state()["workResultCount"] > marker["workResultCount"]:
                raise AcceptanceHardwareError("DUPLICATE_OR_LATE_FINAL_RESULT")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    def clear_input_for_recovery(self) -> None:
        self.open()
        self._poll_frames()
        self._pending_frames.clear()

    def _query_device_facts(self, deadline: float) -> dict:
        while time.monotonic() < deadline:
            boot_id = self._ensure_boot(deadline)
            query_id = self._reserve_identifier("lastQueryId")
            values = {
                "queryId": query_id,
                "targetMcuBootId": boot_id,
                "portNo": 1,
            }
            self._write("QUERY_DEVICE_FACTS", query_id, values)
            facts = self._await_reply(
                "DEVICE_FACTS_REPLY",
                deadline,
                lambda item: all(item[key] == values[key] for key in values),
            )
            if facts is None:
                break
            if facts["status"] == "BOOT_MISMATCH" or facts[
                "currentMcuBootId"
            ] != boot_id:
                self._mcu_boot_id = 0
                continue
            if facts["status"] != "AVAILABLE":
                raise AcceptanceHardwareError("MCU_DEVICE_FACTS_UNAVAILABLE")
            return facts
        raise AcceptanceHardwareError("MCU_DEVICE_FACTS_TIMEOUT")

    def _ensure_configuration(
        self,
        deadline: float,
        *,
        _renewed_after_not_seen: bool = False,
    ) -> int:
        boot_id = self._ensure_boot(deadline)
        self._synchronize_command_sequence(deadline, boot_id)
        candidate = _factory_configuration()
        facts = self._query_device_facts(deadline)
        if facts["currentMcuBootId"] != boot_id:
            boot_id = facts["currentMcuBootId"]
            self._synchronize_command_sequence(deadline, boot_id)
        if self._facts_match_configuration(facts, candidate):
            return boot_id
        state = self._load_state()
        journal = state.get("configuration")
        matches_journal = self._configuration_journal_matches(
            journal, boot_id, candidate
        )
        if (
            isinstance(journal, dict)
            and journal.get("targetMcuBootId") == boot_id
            and not matches_journal
        ):
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID")
        if not matches_journal:
            first_sequence = self._reserve_command_sequences(
                state, candidate.part_count
            )
            application_uid = str(uuid.uuid4())
            commands = []
            for index in range(1, candidate.part_count + 1):
                command_uid = str(uuid.uuid4())
                name, raw = candidate.encode_part(
                    index,
                    application_uid=application_uid,
                    mcu_command_uid=command_uid,
                    target_mcu_boot_id=boot_id,
                    command_sequence=first_sequence + index - 1,
                )
                decoded = uart.decode_payload(name, raw)
                commands.append(
                    {
                        "messageName": name,
                        **{
                            field: decoded[field]
                            for field in COMMAND_IDENTITY_FIELDS
                        },
                        "payloadHex": raw.hex(),
                        "writeAttempted": False,
                        "accepted": False,
                    }
                )
            state["configuration"] = {
                "profile": FACTORY_CONFIG_PROFILE,
                "applicationUid": application_uid,
                "targetMcuBootId": boot_id,
                "configVersion": FACTORY_CONFIG_VERSION,
                "contentSha256": self._configuration_content_sha256(candidate),
                "mcuPayloadSha256": candidate.mcu_payload_sha256,
                "commands": commands,
                "applied": False,
            }
            self._save_state(state)
            journal = state["configuration"]
        try:
            for command in journal["commands"]:
                self._ensure_command_accepted(command["mcuCommandUid"], deadline)
        except AcceptanceHardwareError as error:
            if error.code != "MCU_COMMAND_NOT_SEEN" or _renewed_after_not_seen:
                raise
            # QUERY_COMMAND proved that the ambiguous configuration part never
            # reached this MCU boot.  Replace the whole non-mechanical staging
            # transaction with fresh identities/sequences; do not replay old
            # bytes and never create a mechanical START here.
            state = self._load_state()
            current = state.get("configuration")
            if not self._configuration_journal_matches(
                current, boot_id, candidate
            ):
                raise AcceptanceHardwareError("MCU_UART_STATE_INVALID") from error
            state["configuration"] = None
            self._save_state(state)
            return self._ensure_configuration(
                deadline,
                _renewed_after_not_seen=True,
            )
        while time.monotonic() < deadline:
            facts = self._query_device_facts(deadline)
            if self._facts_match_configuration(facts, candidate):
                state = self._load_state()
                state["configuration"]["applied"] = True
                self._save_state(state)
                return boot_id
            if not facts["configStaging"]:
                break
        raise AcceptanceHardwareError("MCU_FACTORY_CONFIG_NOT_APPLIED")

    def _synchronize_command_sequence(self, deadline: float, boot_id: int) -> None:
        query_id = self._reserve_identifier("lastQueryId")
        values = {"queryId": query_id, "targetMcuBootId": boot_id}
        self._write("QUERY_DEVICE_IDENTITY", query_id, values)
        reply = self._await_reply(
            "DEVICE_IDENTITY_REPLY",
            deadline,
            lambda item: all(item[key] == values[key] for key in values),
        )
        if reply is None:
            raise AcceptanceHardwareError("MCU_IDENTITY_TIMEOUT")
        if reply["status"] == "BOOT_MISMATCH" or reply[
            "currentMcuBootId"
        ] != boot_id:
            self._mcu_boot_id = 0
            raise AcceptanceHardwareError("MCU_COMMAND_BOOT_MISMATCH")
        if reply["status"] != "AVAILABLE":
            raise AcceptanceHardwareError("MCU_IDENTITY_UNAVAILABLE")
        self._observe_highest_command_sequence(reply["highestCommandSequence"])

    def _observe_highest_command_sequence(self, highest: int) -> None:
        if type(highest) is not int or not 0 <= highest <= MAXIMUM_COMMAND_SEQUENCE:
            raise AcceptanceHardwareError("MCU_IDENTITY_UNAVAILABLE")
        state = self._load_state()
        if highest > state["lastCommandSequence"]:
            state["lastCommandSequence"] = highest
            self._save_state(state)

    @staticmethod
    def _facts_match_configuration(
        facts: dict, candidate: NativeMcuConfiguration
    ) -> bool:
        return (
            facts["status"] == "AVAILABLE"
            and not facts["configStaging"]
            and facts["appliedConfigVersion"] == FACTORY_CONFIG_VERSION
            and facts["appliedContentSha256"]
            == NativeAcceptanceMcu._configuration_content_sha256(candidate)
            and facts["appliedMcuPayloadSha256"] == candidate.mcu_payload_sha256
        )

    @staticmethod
    def _configuration_content_sha256(candidate: NativeMcuConfiguration) -> str:
        name, raw = candidate._parts[0]  # noqa: SLF001
        return uart.decode_payload(name, raw)["contentSha256"]

    @classmethod
    def _configuration_journal_matches(
        cls,
        journal: object,
        boot_id: int,
        candidate: NativeMcuConfiguration,
    ) -> bool:
        if not (
            isinstance(journal, dict)
            and set(journal)
            == {
                "profile",
                "applicationUid",
                "targetMcuBootId",
                "configVersion",
                "contentSha256",
                "mcuPayloadSha256",
                "commands",
                "applied",
            }
            and journal.get("profile") == FACTORY_CONFIG_PROFILE
            and journal.get("targetMcuBootId") == boot_id
            and journal.get("configVersion") == FACTORY_CONFIG_VERSION
            and journal.get("contentSha256")
            == cls._configuration_content_sha256(candidate)
            and journal.get("mcuPayloadSha256") == candidate.mcu_payload_sha256
            and isinstance(journal.get("applied"), bool)
            and isinstance(journal.get("commands"), list)
            and len(journal["commands"]) == candidate.part_count
        ):
            return False
        try:
            application_uid = str(uuid.UUID(journal["applicationUid"]))
            if application_uid != journal["applicationUid"] or not uuid.UUID(
                application_uid
            ).int:
                return False
            for index, command in enumerate(journal["commands"], 1):
                if not (
                    isinstance(command, dict)
                    and set(command)
                    == {
                        "messageName",
                        *COMMAND_IDENTITY_FIELDS,
                        "payloadHex",
                        "writeAttempted",
                        "accepted",
                    }
                    and isinstance(command["writeAttempted"], bool)
                    and isinstance(command["accepted"], bool)
                ):
                    return False
                uid = str(uuid.UUID(command["mcuCommandUid"]))
                if uid != command["mcuCommandUid"] or not uuid.UUID(uid).int:
                    return False
                name, raw = candidate.encode_part(
                    index,
                    application_uid=application_uid,
                    mcu_command_uid=uid,
                    target_mcu_boot_id=boot_id,
                    command_sequence=command["commandSequence"],
                )
                if (
                    command["messageName"] != name
                    or command["payloadHex"] != raw.hex()
                    or any(
                        command[field]
                        != uart.decode_payload(name, raw)[field]
                        for field in COMMAND_IDENTITY_FIELDS
                    )
                ):
                    return False
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def _ensure_command_accepted(self, command_uid: str, deadline: float) -> None:
        command = self._find_command(command_uid)
        if command.get("accepted"):
            return
        if not command.get("writeAttempted"):
            self._update_command(command_uid, writeAttempted=True)
            self._write_payload(
                command["messageName"],
                bytes.fromhex(command["payloadHex"]),
                command["commandSequence"],
            )
            response = self._await_reply(
                "COMMAND_DECISION",
                min(
                    deadline,
                    time.monotonic()
                    + COMMAND_RESPONSE_TIMEOUT_MS / 1000,
                ),
                lambda item: self._command_identity_matches(command, item),
            )
            if response is not None:
                self._accept_command_outcome(command_uid, response)
                return
        query_id = self._reserve_identifier("lastQueryId")
        query = {"queryId": query_id} | {
            field: command[field] for field in COMMAND_IDENTITY_FIELDS
        }
        self._write("QUERY_COMMAND", query_id, query)
        response = self._await_reply(
            "COMMAND_QUERY_RESULT",
            deadline,
            lambda item: item["queryId"] == query_id
            and self._command_identity_matches(command, item),
        )
        if response is None:
            raise AcceptanceHardwareError("MCU_COMMAND_OUTCOME_TIMEOUT")
        self._accept_command_outcome(command_uid, response)

    def _accept_command_outcome(self, command_uid: str, response: dict) -> None:
        outcome = response["outcome"]
        if response["currentMcuBootId"] != self._find_command(command_uid)[
            "targetMcuBootId"
        ] or outcome == "BOOT_MISMATCH":
            raise AcceptanceHardwareError("MCU_COMMAND_BOOT_MISMATCH")
        if outcome != "ACCEPTED":
            code = {
                "NOT_SEEN": "MCU_COMMAND_NOT_SEEN",
                "IDENTITY_CONFLICT": "MCU_COMMAND_IDENTITY_CONFLICT",
                "OLD_DETAILS_UNAVAILABLE": "MCU_COMMAND_DETAILS_UNAVAILABLE",
                "REJECTED": "MCU_COMMAND_REJECTED",
            }.get(outcome, "MCU_COMMAND_REJECTED")
            raise AcceptanceHardwareError(code)
        self._update_command(command_uid, accepted=True)

    @staticmethod
    def _command_identity_matches(command: dict, response: dict) -> bool:
        return all(command[field] == response[field] for field in COMMAND_IDENTITY_FIELDS)

    def _find_command(self, command_uid: str) -> dict:
        state = self._load_state()
        journal = state.get("configuration")
        if isinstance(journal, dict):
            for command in journal.get("commands", []):
                if command.get("mcuCommandUid") == command_uid:
                    return command
        active = state.get("activeAction")
        if isinstance(active, dict):
            active = self._validate_active_action(active)
        if isinstance(active, dict) and active.get("mcuCommandUid") == command_uid:
            return {
                "messageName": active["commandName"],
                **active,
            }
        raise AcceptanceHardwareError("MCU_COMMAND_JOURNAL_MISSING")

    def _update_command(self, command_uid: str, **changes: object) -> None:
        state = self._load_state()
        journal = state.get("configuration")
        if isinstance(journal, dict):
            for command in journal.get("commands", []):
                if command.get("mcuCommandUid") == command_uid:
                    command.update(changes)
                    self._save_state(state)
                    return
        active = state.get("activeAction")
        if isinstance(active, dict) and active.get("mcuCommandUid") == command_uid:
            active.update(changes)
            self._save_state(state)
            return
        raise AcceptanceHardwareError("MCU_COMMAND_JOURNAL_MISSING")

    def _require_active_action(self, action: str) -> dict:
        active = self._load_state().get("activeAction")
        if not isinstance(active, dict) or active.get("action") != action:
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_MISSING")
        return self._validate_active_action(active)

    @staticmethod
    def _validate_active_action(active: dict) -> dict:
        required = {
            "action",
            "acceptanceActionToken",
            "commandName",
            "workUid",
            *COMMAND_IDENTITY_FIELDS,
            "payloadHex",
            "writeAttempted",
            "accepted",
            "resultPayloadHex",
            "resultFacts",
            "mappedResult",
            "resultSavedPayloadHex",
            "resultSavedWriteAttempted",
            "released",
            "recoveryDisposition",
        }
        try:
            if set(active) != required or active["action"] not in {
                "DELIVERY",
                "CLEAN",
            }:
                raise ValueError
            expected_name = (
                "START_DELIVERY_SESSION"
                if active["action"] == "DELIVERY"
                else "START_CLEAN_OPERATION"
            )
            if active["commandName"] != expected_name:
                raise ValueError
            for key in ("workUid", "mcuCommandUid", "acceptanceActionToken"):
                parsed = uuid.UUID(active[key])
                if str(parsed) != active[key] or not parsed.int:
                    raise ValueError
            raw = bytes.fromhex(active["payloadHex"])
            values = uart.decode_payload(expected_name, raw)
            work_key = (
                "sessionUid" if active["action"] == "DELIVERY" else "operationUid"
            )
            candidate = _factory_configuration()
            if (
                values[work_key] != active["workUid"]
                or values["portNo"] != 1
                or values["configVersion"] != FACTORY_CONFIG_VERSION
                or values["configContentSha256"]
                != NativeAcceptanceMcu._configuration_content_sha256(candidate)
                or values["startExecutionWindowMs"] != 5_000
                or any(
                    values[field] != active[field]
                    for field in COMMAND_IDENTITY_FIELDS
                )
                or not all(
                    isinstance(active[key], bool)
                    for key in (
                        "writeAttempted",
                        "accepted",
                        "resultSavedWriteAttempted",
                        "released",
                    )
                )
                or active["recoveryDisposition"]
                not in {None, "NOT_SENT", "BOOT_CHANGED"}
                or (active["accepted"] and not active["writeAttempted"])
            ):
                raise ValueError
            if active["action"] == "DELIVERY":
                if (
                    values["unitPriceTenThousandths"]
                    != FACTORY_PORT_CONFIG["unitPriceTenThousandths"]
                    or values["continueDeliveryWaitMs"]
                    != FACTORY_DEVICE_CONFIG["continueDeliveryWaitMs"]
                    or values["negativeWeightThresholdGrams"]
                    != FACTORY_DEVICE_CONFIG["negativeWeightThresholdGrams"]
                    or values["deliveryAutoCloseMs"]
                    != FACTORY_DEVICE_CONFIG["deliveryAutoCloseMs"]
                ):
                    raise ValueError
            elif values["operationWindowMs"] != 300_000:
                raise ValueError
            result_hex = active["resultPayloadHex"]
            if result_hex is not None:
                result = uart.decode_payload(
                    "WORK_RESULT", bytes.fromhex(result_hex)
                )
                if (
                    result["mcuBootId"] != active["targetMcuBootId"]
                    or result["workUid"] != active["workUid"]
                    or result["workType"]
                    != NativeAcceptanceMcu._work_type(active["action"])
                    or result["originCommandUid"] != active["mcuCommandUid"]
                    or result["originCommandSequence"]
                    != active["commandSequence"]
                ):
                    raise ValueError
                if not active["writeAttempted"] or not active["accepted"]:
                    raise ValueError
            if active["mappedResult"] is not None and not isinstance(
                active["mappedResult"], dict
            ):
                raise ValueError
            if active["resultFacts"] is not None and not isinstance(
                active["resultFacts"], dict
            ):
                raise ValueError
            if (active["mappedResult"] is None) != (
                active["resultFacts"] is None
            ):
                raise ValueError
            if active["mappedResult"] is not None:
                uart.validate_payload_semantics(
                    "DEVICE_FACTS_REPLY", active["resultFacts"]
                )
                if (
                    result_hex is None
                    or active["resultFacts"]["status"] != "AVAILABLE"
                    or active["resultFacts"]["targetMcuBootId"]
                    != active["targetMcuBootId"]
                    or active["resultFacts"]["currentMcuBootId"]
                    != active["targetMcuBootId"]
                    or NativeAcceptanceMcu._map_action_result(
                        active["action"], result, active["resultFacts"]
                    )
                    != active["mappedResult"]
                ):
                    raise ValueError
            saved_hex = active["resultSavedPayloadHex"]
            if saved_hex is not None:
                saved = uart.decode_payload(
                    "RESULT_SAVED", bytes.fromhex(saved_hex)
                )
                if result_hex is None or any(
                    saved[key] != result[key]
                    for key in (
                        "mcuBootId",
                        "resultSequence",
                        "workUid",
                        "resultDigestSha256",
                    )
                ):
                    raise ValueError
            if (
                active["resultSavedWriteAttempted"] and saved_hex is None
            ) or (
                active["released"]
                and (
                    saved_hex is None
                    or not active["resultSavedWriteAttempted"]
                    or active["mappedResult"] is None
                )
            ):
                raise ValueError
            if active["recoveryDisposition"] == "NOT_SENT" and (
                active["accepted"]
                or result_hex is not None
                or saved_hex is not None
                or active["released"]
            ):
                raise ValueError
        except (
            KeyError,
            TypeError,
            ValueError,
            uart.ProtocolError,
            AcceptanceHardwareError,
        ) as error:
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID") from error
        return active

    def _query_work(self, active: dict, deadline: float) -> dict:
        query_id = self._reserve_identifier("lastQueryId")
        values = {
            "queryId": query_id,
            **{field: active[field] for field in COMMAND_IDENTITY_FIELDS},
            "workUid": active["workUid"],
            "workType": self._work_type(active["action"]),
            "portNo": 1,
        }
        self._write("QUERY_WORK", query_id, values)
        reply = self._await_reply(
            "WORK_QUERY_REPLY",
            deadline,
            lambda item: all(item[key] == values[key] for key in values),
        )
        if reply is None:
            raise AcceptanceHardwareError("MCU_FACTORY_WORK_QUERY_TIMEOUT")
        return reply

    def _query_result_from_work(self, work: dict, deadline: float) -> None:
        identity = {
            "mcuBootId": work["currentMcuBootId"],
            "resultSequence": work["resultSequence"],
            "workUid": work["workUid"],
            "resultDigestSha256": work["resultDigestSha256"],
        }
        status = self._query_result_status(identity, deadline)
        if status == "BOOT_MISMATCH":
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_BOOT_CHANGED")
        if status not in {"HELD", "RELEASED"}:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_NOT_FOUND")

    def _query_result_status(self, identity: dict, deadline: float) -> str:
        query_id = self._reserve_identifier("lastQueryId")
        values = {"queryId": query_id} | identity
        self._write("QUERY_RESULT", query_id, values)
        reply = self._await_reply(
            "RESULT_QUERY_REPLY",
            deadline,
            lambda item: item["queryId"] == query_id
            and all(item[key] == identity[key] for key in identity),
        )
        if reply is None:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_QUERY_TIMEOUT")
        return reply["status"]

    def _poll_until_result(self, action: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self._poll_frames():
                continue
            if self._require_active_action(action).get("resultPayloadHex"):
                return
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        raise AcceptanceHardwareError("FINAL_RESULT_TIMEOUT")

    def _map_and_store_action_result(self, action: str, deadline: float) -> dict:
        active = self._require_active_action(action)
        if active.get("mappedResult") is not None:
            return active["mappedResult"]
        raw = bytes.fromhex(active["resultPayloadHex"])
        values = uart.decode_payload("WORK_RESULT", raw)
        facts = self._query_device_facts(deadline)
        result = self._map_action_result(action, values, facts)
        state = self._load_state()
        current = state.get("activeAction")
        if not isinstance(current, dict) or current.get("resultPayloadHex") != raw.hex():
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_IDENTITY_MISMATCH")
        current["resultFacts"] = facts
        current["mappedResult"] = result
        self._save_state(state)
        return result

    @staticmethod
    def _map_action_result(action: str, values: dict, facts: dict) -> dict:
        success_reason = (
            values["finishReason"] in {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED"}
            if action == "DELIVERY"
            else values["finishReason"] == "CLEAN_CONFIRMED"
        )
        if (
            not success_reason
            or (
                action == "CLEAN"
                and values["physicalCloseConfirmed"] is not True
            )
            or values["initialKind"] not in USABLE_RESULT_MEASUREMENT_KINDS
            or values["finalKind"] not in USABLE_RESULT_MEASUREMENT_KINDS
            or type(values["initialWeightGrams"]) is not int
            or type(values["finalWeightGrams"]) is not int
            or not MINIMUM_WEIGHT_GRAMS
            <= values["initialWeightGrams"]
            <= MAXIMUM_WEIGHT_GRAMS
            or not MINIMUM_WEIGHT_GRAMS
            <= values["finalWeightGrams"]
            <= MAXIMUM_WEIGHT_GRAMS
        ):
            raise AcceptanceHardwareError("MCU_FACTORY_WORK_FAILED")
        fullness = NativeAcceptanceMcu._map_fullness(facts)
        initial = values["initialWeightGrams"]
        final = values["finalWeightGrams"]
        result = {
            "preWeightGrams": initial,
            "postWeightGrams": final,
            "weightDeltaGrams": final - initial if action == "DELIVERY" else initial - final,
            "finishReason": values["finishReason"],
            **fullness,
        }
        if action == "DELIVERY":
            result.update(
                {
                    # The native MCU result proves its final commanded output,
                    # not a separate physical-position feedback signal.
                    "deliveryDoorCommand": "CLOSE",
                    "deliveryDoorOutputStatus": "COMMAND_DISPATCHED",
                    "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
                }
            )
        else:
            result.update(
                {
                    "cleanLockPowerState": "DEENERGIZED",
                    "cleanSolenoidHealth": "UNKNOWN",
                    "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
                    "cleanerPhysicalCloseConfirmed": True,
                }
            )
        return result

    @staticmethod
    def _map_fullness(facts: dict) -> dict:
        captured = facts["capturedUptimeMs"]
        kind = facts["fullnessObservationKind"]
        status = facts["fullnessReadStatus"]
        if kind not in {"DIGITAL_INFRARED", "ULTRASONIC"} or status not in {
            "VALID",
            "NOT_OBSERVED",
            "UNAVAILABLE",
        }:
            raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
        if status == "VALID":
            NativeAcceptanceMcu._require_fresh_observation(
                captured,
                facts["fullnessCapturedUptimeMs"],
                MAXIMUM_ENVIRONMENT_FACT_AGE_MS,
                "MCU_FULLNESS_FACT_STALE",
            )
        if status != "VALID":
            return {
                "infraredBlocked": None,
                "fullnessSensorKind": kind,
                "fullnessReadStatus": status,
                "fullnessDistanceMm": None,
                "fullnessDistanceThresholdMm": (
                    FACTORY_PORT_CONFIG["fullnessDistanceThresholdMm"]
                    if kind == "ULTRASONIC"
                    else None
                ),
                "fullnessBlocked": None,
            }
        if kind == "ULTRASONIC":
            distance = facts["fullnessDistanceMm"]
            if type(distance) is not int or not 0 <= distance <= 4_000:
                raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
            threshold = FACTORY_PORT_CONFIG["fullnessDistanceThresholdMm"]
            blocked = distance < threshold
            return {
                # Compatibility projection for the v2 factory report.  Raw
                # ultrasonic evidence is kept alongside it.
                "infraredBlocked": blocked,
                "fullnessSensorKind": kind,
                "fullnessReadStatus": status,
                "fullnessDistanceMm": distance,
                "fullnessDistanceThresholdMm": threshold,
                "fullnessBlocked": blocked,
            }
        infrared = facts["fullnessInfraredBlocked"]
        if not isinstance(infrared, bool):
            raise AcceptanceHardwareError("MCU_FULLNESS_FACT_UNAVAILABLE")
        return {
            "infraredBlocked": infrared,
            "fullnessSensorKind": kind,
            "fullnessReadStatus": status,
            "fullnessDistanceMm": None,
            "fullnessDistanceThresholdMm": None,
            "fullnessBlocked": infrared,
        }

    def _mark_result_released(self) -> None:
        state = self._load_state()
        active = state.get("activeAction")
        if not isinstance(active, dict):
            raise AcceptanceHardwareError("MCU_FACTORY_ACTION_MISSING")
        active["released"] = True
        self._save_state(state)

    @staticmethod
    def _require_fresh_observation(
        captured: int,
        observed: int,
        maximum_age_ms: int,
        error_code: str,
    ) -> None:
        if (
            type(observed) is not int
            or not 0 <= observed <= captured
            or captured - observed > maximum_age_ms
        ):
            raise AcceptanceHardwareError(error_code)

    @staticmethod
    def _deadline(timeout_ms: int) -> float:
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("timeout_ms must be a positive integer")
        return time.monotonic() + timeout_ms / 1000.0

    def _ensure_boot(self, deadline: float) -> int:
        if self._mcu_boot_id:
            return self._mcu_boot_id
        while time.monotonic() < deadline:
            attempt_deadline = min(
                deadline,
                time.monotonic()
                + self._boot_attempt_timeout_ms / 1000.0,
            )
            probe_id = self._reserve_identifier("lastQueryId")
            self._write(
                "BOOT_PROBE", probe_id, {"probeId": probe_id}
            )
            probe = self._await_reply(
                "BOOT_PROBE_REPLY",
                attempt_deadline,
                lambda values: values["probeId"] == probe_id,
            )
            if probe is None:
                continue
            if probe["mcuBootId"]:
                self._mcu_boot_id = probe["mcuBootId"]
                return self._mcu_boot_id

            proposed = self._reserve_identifier(
                "lastProposedMcuBootId"
            )
            self._write(
                "BIND_BOOT",
                probe_id,
                {
                    "probeId": probe_id,
                    "proposedMcuBootId": proposed,
                },
            )
            bind = self._await_reply(
                "BIND_BOOT_REPLY",
                attempt_deadline,
                lambda values: values["probeId"] == probe_id
                and values["proposedMcuBootId"] == proposed,
            )
            if bind is None or bind["status"] == "PROBE_MISMATCH":
                continue
            if (
                bind["status"] == "BOUND"
                and bind["mcuBootId"] == proposed
            ) or (
                bind["status"] == "ALREADY_BOUND"
                and bind["mcuBootId"] > 0
            ):
                self._mcu_boot_id = bind["mcuBootId"]
                return self._mcu_boot_id
            raise AcceptanceHardwareError("MCU_BOOT_BIND_INVALID")
        raise AcceptanceHardwareError("MCU_BOOT_TIMEOUT")

    def _load_state(self) -> dict:
        try:
            state = self._state_file.read()
        except AcceptanceStorageError as error:
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID") from error
        if state is None:
            return {
                "schemaVersion": STATE_SCHEMA_VERSION,
                "lastQueryId": 0,
                "lastProposedMcuBootId": 0,
                "lastCommandSequence": 0,
                "configuration": None,
                "activeAction": None,
                "completedActions": [],
                "workResultCount": 0,
            }
        if not isinstance(state, dict) or state.get(
            "schemaVersion"
        ) != STATE_SCHEMA_VERSION:
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID")
        allowed = {
            "schemaVersion",
            "lastQueryId",
            "lastProposedMcuBootId",
            "lastCommandSequence",
            "configuration",
            "activeAction",
            "completedActions",
            "workResultCount",
        }
        if not set(state).issubset(allowed):
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID")
        state.setdefault("lastCommandSequence", 0)
        state.setdefault("configuration", None)
        state.setdefault("activeAction", None)
        state.setdefault("completedActions", [])
        state.setdefault("workResultCount", 0)
        for name in ("lastQueryId", "lastProposedMcuBootId"):
            value = state.get(name)
            if (
                type(value) is not int
                or not 0 <= value <= MAXIMUM_SAFE_IDENTIFIER
            ):
                raise AcceptanceHardwareError("MCU_UART_STATE_INVALID")
        if (
            type(state["lastCommandSequence"]) is not int
            or not 0 <= state["lastCommandSequence"] <= MAXIMUM_COMMAND_SEQUENCE
            or type(state["workResultCount"]) is not int
            or state["workResultCount"] < 0
            or not isinstance(state["completedActions"], list)
            or len(state["completedActions"]) > MAXIMUM_COMPLETED_ACTIONS
            or (
                state["configuration"] is not None
                and not isinstance(state["configuration"], dict)
            )
            or (
                state["activeAction"] is not None
                and not isinstance(state["activeAction"], dict)
            )
        ):
            raise AcceptanceHardwareError("MCU_UART_STATE_INVALID")
        return state

    def _save_state(self, state: dict) -> None:
        try:
            self._state_file.write(state)
        except AcceptanceStorageError as error:
            raise AcceptanceHardwareError("MCU_UART_STATE_WRITE_FAILED") from error

    def _reserve_identifier(self, name: str) -> int:
        state = self._load_state()
        previous = state[name]
        if name == "lastProposedMcuBootId":
            value = (
                previous + 1
                if previous >= FACTORY_BOOT_ID_NAMESPACE
                else FACTORY_BOOT_ID_NAMESPACE + 1
            )
        else:
            value = previous + 1
        if value > MAXIMUM_SAFE_IDENTIFIER:
            raise AcceptanceHardwareError("MCU_UART_COUNTER_EXHAUSTED")
        state[name] = value
        self._save_state(state)
        return value

    @staticmethod
    def _reserve_command_sequence(state: dict) -> int:
        previous = state["lastCommandSequence"]
        if previous >= MAXIMUM_COMMAND_SEQUENCE:
            raise AcceptanceHardwareError("MCU_UART_COUNTER_EXHAUSTED")
        state["lastCommandSequence"] = previous + 1
        return previous + 1

    @staticmethod
    def _reserve_command_sequences(state: dict, count: int) -> int:
        previous = state["lastCommandSequence"]
        if type(count) is not int or count < 1 or previous + count > MAXIMUM_COMMAND_SEQUENCE:
            raise AcceptanceHardwareError("MCU_UART_COUNTER_EXHAUSTED")
        state["lastCommandSequence"] = previous + count
        return previous + 1

    def _write(self, name: str, identity: int, values: dict) -> None:
        self._write_payload(name, uart.encode_payload(name, values), identity)

    def _write_payload(self, name: str, payload: bytes, identity: int) -> None:
        transport = self._transport
        if transport is None:
            raise AcceptanceHardwareError("UART5_NOT_OPEN")
        frame = uart.encode_frame(
            name,
            (identity - 1) % 0xFFFFFFFF + 1,
            payload,
        )
        try:
            written = transport.write(frame)
        except (OSError, ValueError) as error:
            raise AcceptanceHardwareError("UART5_WRITE_FAILED") from error
        if type(written) is not int or written != len(frame):
            raise AcceptanceHardwareError("UART5_SHORT_WRITE")

    def _await_reply(
        self,
        expected_name: str,
        deadline: float,
        matches: Callable[[dict], bool],
    ) -> dict | None:
        while time.monotonic() < deadline:
            retained: deque[tuple[str, dict]] = deque()
            found = None
            while self._pending_frames:
                name, values = self._pending_frames.popleft()
                if found is None and name == expected_name and matches(values):
                    found = values
                else:
                    retained.append((name, values))
            self._pending_frames.extend(retained)
            if found is not None:
                return found
            if self._poll_frames():
                continue
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        return None

    def _poll_frames(self) -> int:
        transport = self._transport
        if transport is None:
            raise AcceptanceHardwareError("UART5_NOT_OPEN")
        try:
            frames = transport.poll(int(time.monotonic() * 1000))
        except (OSError, ValueError) as error:
            raise AcceptanceHardwareError("UART5_READ_FAILED") from error
        for frame in frames:
            try:
                decoded = uart.decode_frame(frame, sender_role="MCU")
                name = decoded["messageName"]
                if name == "WORK_RESULT":
                    self._journal_work_result(decoded["payload"])
                    continue
                values = uart.decode_payload(name, decoded["payload"])
            except (KeyError, TypeError, ValueError) as error:
                raise AcceptanceHardwareError("UART5_INVALID_FRAME") from error
            if len(self._pending_frames) >= MAXIMUM_PENDING_REPLY_FRAMES:
                self._pending_frames.popleft()
            self._pending_frames.append((name, values))
        return len(frames)

    def _journal_work_result(self, payload: bytes) -> None:
        try:
            values = uart.decode_payload("WORK_RESULT", payload)
        except (KeyError, TypeError, ValueError) as error:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_INVALID") from error
        state = self._load_state()
        active = state.get("activeAction")
        if not isinstance(active, dict):
            raise AcceptanceHardwareError("MCU_UNEXPECTED_WORK_RESULT")
        expected_type = self._work_type(active.get("action"))
        if (
            values["mcuBootId"] != active.get("targetMcuBootId")
            or values["workUid"] != active.get("workUid")
            or values["workType"] != expected_type
            or values["portNo"] != 1
            or values["configVersion"] != FACTORY_CONFIG_VERSION
            or values["originCommandUid"] != active.get("mcuCommandUid")
            or values["originCommandSequence"]
            != active.get("commandSequence")
        ):
            raise AcceptanceHardwareError("MCU_UNEXPECTED_WORK_RESULT")
        raw_hex = payload.hex()
        existing = active.get("resultPayloadHex")
        if existing not in {None, raw_hex}:
            raise AcceptanceHardwareError("MCU_FACTORY_RESULT_CONFLICT")
        active["resultPayloadHex"] = raw_hex
        # An identity- and digest-valid final result proves that the exact
        # START reached the MCU even when its COMMAND_DECISION was lost.
        active["writeAttempted"] = True
        active["accepted"] = True
        state["workResultCount"] += 1
        self._save_state(state)

    @staticmethod
    def _work_type(action: object) -> str:
        if action == "DELIVERY":
            return "DELIVERY_SESSION"
        if action == "CLEAN":
            return "CLEAN_OPERATION"
        raise AcceptanceHardwareError("MCU_FACTORY_ACTION_MISSING")

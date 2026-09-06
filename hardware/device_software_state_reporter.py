"""Reliable actual software-state reporting owned by the permanent updater."""

from __future__ import annotations

import logging
import re
import threading
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from business_update_store import BusinessUpdateStore
from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
)
from trusted_clock import event_clock_fields


logger = logging.getLogger("device-software-state-reporter")

EVENT_TYPE = "DEVICE_SOFTWARE_STATE_REPORTED"
TARGET_TYPE = "DEVICE_ASSET"
COMMUNICATION_PROTOCOL_NAME = "ecobin.communication.control"
BUSINESS_PROTOCOL_NAME = "ecobin.business.control"
DEFAULT_COMMUNICATION_SOCKET = "/run/ecobin/communication/control.sock"
DEFAULT_BUSINESS_SOCKET = "/run/ecobin/business/control.sock"
BUSINESS_PACKAGE_FORMAT_VERSION = 1
MCU_PACKAGE_FORMAT_VERSION = 1
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}\Z")
_IMAGE_GENERATION = r"([0-9]{8}-[0-9]{2,6})"
_IMAGE_COMPONENT_VERSIONS = {
    "business": re.compile(rf"hardware-runtime-{_IMAGE_GENERATION}\Z"),
    "communication": re.compile(rf"communication-{_IMAGE_GENERATION}\Z"),
    "updater": re.compile(rf"updater-{_IMAGE_GENERATION}\Z"),
}
_UART_STATES = frozenset(
    {"DISCONNECTED", "NEGOTIATING", "READY", "INCOMPATIBLE", "FAULT"}
)


class DeviceSoftwareStateReporter:
    """Freeze changed cross-process facts before durable cloud hand-off."""

    def __init__(
        self,
        *,
        journal: BusinessUpdateStore,
        safety_store: Any,
        communication_client: LocalControlClient,
        business_client: LocalControlClient | None = None,
        retry_seconds: float = 2.0,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        if retry_seconds <= 0:
            raise ValueError("software state retry interval must be positive")
        self.journal = journal
        self.safety_store = safety_store
        self.communication_client = communication_client
        self.business_client = business_client or LocalControlClient(
            DEFAULT_BUSINESS_SOCKET,
            protocol_name=BUSINESS_PROTOCOL_NAME,
        )
        self._retry_seconds = float(retry_seconds)
        self._uuid_factory = uuid_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None
        self._legacy_business_instance_uid: str | None = None
        self._legacy_mcu_facts: dict[str, Any] | None = None

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
            name="device-software-state",
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
                    "device software state reporter did not stop in time"
                )
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def process_once(self) -> bool:
        pending = self.journal.list_pending_software_state_deliveries(limit=1)
        if pending:
            return self._submit(pending[0])
        try:
            communication = self.communication_client.request(
                "GET_STATUS", {}
            )
        except (LocalControlUnavailable, LocalControlRemoteError):
            return False
        if not _valid_component_status(
            communication,
            "COMMUNICATION_AGENT",
            "ecobin.communication.control",
        ):
            raise RuntimeError("communication software identity is invalid")
        target_uid = communication.get("authenticatedDeviceName")
        if (
            not isinstance(target_uid, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target_uid) is None
        ):
            raise RuntimeError("authenticated device identity is unavailable")

        business = self._read_business_facts()
        semantic_payload = _semantic_payload(
            safety=self.safety_store.get_status(),
            communication=communication,
            business=business,
            installed_release=self.journal.confirmed_installed_release(),
        )
        event_uid = self._uuid_factory()
        if not isinstance(event_uid, uuid.UUID) or event_uid.version != 4:
            raise RuntimeError("software state UUID factory must return UUIDv4")
        clock = event_clock_fields()
        delivery = self.journal.reserve_software_state_delivery(
            target_uid,
            str(event_uid),
            clock["occurredAt"],
            clock["clockQuality"],
            semantic_payload,
        )
        if delivery is None:
            return False
        return self._submit(delivery)

    def _read_business_facts(self) -> Mapping[str, Any] | None:
        try:
            result = self.business_client.request(
                "GET_SOFTWARE_RUNTIME_FACTS", {}
            )
        except LocalControlUnavailable:
            return None
        except LocalControlRemoteError as error:
            if error.code not in {"REQUEST_INVALID", "FEATURE_DISABLED"}:
                return None
            return self._read_legacy_business_facts()
        if not _valid_component_status(
            result,
            "BUSINESS_RUNTIME",
            "ecobin.business.control",
        ):
            raise RuntimeError("business software identity is invalid")
        return result

    def _read_legacy_business_facts(self) -> Mapping[str, Any] | None:
        """Bridge the already-installed v7 read-only maintenance surface."""

        try:
            status = self.business_client.request("GET_STATUS", {})
        except (LocalControlUnavailable, LocalControlRemoteError):
            return None
        if not _valid_component_status(
            status,
            "BUSINESS_RUNTIME",
            "ecobin.business.control",
        ):
            raise RuntimeError("legacy business software identity is invalid")
        instance_uid = status.get("runtimeInstanceUid")
        if (
            self._legacy_mcu_facts is None
            or instance_uid != self._legacy_business_instance_uid
        ):
            facts = _unavailable_legacy_mcu_facts()
            try:
                observation = self.business_client.request(
                    "OBSERVE_MCU_MAINTENANCE_STATE", {}
                )
            except (LocalControlUnavailable, LocalControlRemoteError):
                observation = None
            if isinstance(observation, Mapping):
                facts = _legacy_mcu_facts(observation)
            self._legacy_business_instance_uid = (
                instance_uid if isinstance(instance_uid, str) else None
            )
            self._legacy_mcu_facts = facts
        return {**dict(status), **dict(self._legacy_mcu_facts)}

    def _submit(self, delivery: Mapping[str, Any]) -> bool:
        try:
            result = self.communication_client.request(
                "SUBMIT_UPDATER_EVENT",
                {
                    "eventUid": delivery["eventUid"],
                    "eventType": EVENT_TYPE,
                    "targetType": TARGET_TYPE,
                    "targetUid": delivery["targetUid"],
                    "commandUid": None,
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
                "communication rejected immutable software state"
            ) from error
        if (
            not isinstance(result, dict)
            or result.get("eventUid") != delivery["eventUid"]
            or result.get("disposition") not in {"ACCEPTED", "DUPLICATE"}
            or result.get("durableAccepted") is not True
        ):
            raise RuntimeError(
                "communication returned a malformed software state receipt"
            )
        self.journal.mark_software_state_delivery_accepted(
            delivery["managementStateSequence"],
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
            logger.exception("device software state reporter failed")


def _semantic_payload(
    *,
    safety: Mapping[str, Any],
    communication: Mapping[str, Any],
    business: Mapping[str, Any] | None,
    installed_release: Mapping[str, Any] | None,
) -> dict[str, Any]:
    gate = safety.get("jobGateState")
    if gate not in {"OPEN", "DRAINING", "MAINTENANCE", "LOCKED"}:
        gate = "LOCKED"
    updater_version = _version(safety.get("releaseVersion"), "updater")
    communication_version = _version(
        communication.get("releaseVersion"),
        "communication agent",
    )

    business_status = business.get("status") if business is not None else None
    process_state = {
        "STARTING": "STARTING",
        "READY": "RUNNING",
        "STOPPING": "STOPPED",
    }.get(business_status, "STOPPED" if business is None else "FAILED")
    updater_business = business is not None
    agent_business = bool(
        business is not None
        and business.get("cloudProxyIngressEnabled") is True
        and business.get("cloudConnectionOwner") == "COMMUNICATION_AGENT"
        and communication.get("onenetOwnership") == "ENABLED"
        and communication.get("businessEventIngress") == "ENABLED"
    )
    agent_updater = True

    active_release = None
    if (
        business is not None
        and installed_release is not None
        and business.get("releaseVersion")
        == installed_release.get("versionName")
    ):
        active_release = dict(installed_release)
    image_bridge_ready = bool(
        business is not None
        and installed_release is None
        and _same_image_generation(
            business.get("releaseVersion"),
            communication_version,
            updater_version,
        )
    )
    business_ready = bool(
        business_status == "READY"
        and (active_release is not None or image_bridge_ready)
        and agent_business
        and updater_business
    )

    mcu_firmware = None
    uart_state = "DISCONNECTED"
    uart_protocol = None
    capability_bitmap = "0000000000000000"
    if business is not None:
        mcu_firmware = _mcu_firmware(business.get("mcuFirmware"))
        uart_state = business.get("uartState")
        if uart_state not in _UART_STATES:
            uart_state = "FAULT"
        uart_protocol = _uart_protocol(business.get("uartProtocol"))
        capability = business.get("capabilityBitmapHex")
        if (
            isinstance(capability, str)
            and re.fullmatch(r"[0-9a-f]{16}", capability) is not None
        ):
            capability_bitmap = capability

    return {
        "managementArchitectureGeneration": "PERMANENT_V1",
        "businessAdmissionState": gate,
        "communicationAgent": {
            "versionName": communication_version,
            "managementTransportProtocolMajor": 1,
            "managementTransportProtocolMinor": 0,
            "businessLocalProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "businessLocalProtocolMinor": LOCAL_PROTOCOL_MINOR,
            "updaterLocalProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "updaterLocalProtocolMinor": LOCAL_PROTOCOL_MINOR,
        },
        "deviceUpdater": {
            "versionName": updater_version,
            "deviceMaintenanceProtocolMajor": 1,
            "deviceMaintenanceProtocolMinor": 0,
            "businessLocalProtocolMajor": LOCAL_PROTOCOL_MAJOR,
            "businessLocalProtocolMinor": LOCAL_PROTOCOL_MINOR,
            "businessPackageFormatVersion": BUSINESS_PACKAGE_FORMAT_VERSION,
            "mcuPackageFormatVersion": MCU_PACKAGE_FORMAT_VERSION,
        },
        "activeBusinessRelease": active_release,
        "businessProcessState": process_state,
        "businessReady": business_ready,
        "negotiatedProtocols": {
            **_negotiated("agentBusiness", agent_business),
            **_negotiated("agentUpdater", agent_updater),
            **_negotiated("updaterBusiness", updater_business),
        },
        "mcuFirmware": mcu_firmware,
        "uartState": uart_state,
        "uartProtocol": uart_protocol,
        "capabilityBitmapHex": capability_bitmap,
    }


def _valid_component_status(
    value: Any,
    component: str,
    protocol: str,
) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("component") == component
        and value.get("localProtocolName") == protocol
        and value.get("localProtocolMajor") == LOCAL_PROTOCOL_MAJOR
        and value.get("localProtocolMinor") == LOCAL_PROTOCOL_MINOR
        and _VERSION.fullmatch(str(value.get("releaseVersion", "")))
        is not None
    )


def _version(value: Any, component: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise RuntimeError(f"{component} version is invalid")
    return value


def _same_image_generation(
    business_version: Any,
    communication_version: str,
    updater_version: str,
) -> bool:
    versions = {
        "business": business_version,
        "communication": communication_version,
        "updater": updater_version,
    }
    generations: list[str] = []
    for component, value in versions.items():
        if not isinstance(value, str):
            return False
        match = _IMAGE_COMPONENT_VERSIONS[component].fullmatch(value)
        if match is None:
            return False
        generations.append(match.group(1))
    return len(set(generations)) == 1


def _negotiated(prefix: str, negotiated: bool) -> dict[str, Any]:
    return {
        f"{prefix}Negotiated": negotiated,
        f"{prefix}Major": LOCAL_PROTOCOL_MAJOR if negotiated else 0,
        f"{prefix}Minor": LOCAL_PROTOCOL_MINOR if negotiated else 0,
    }


def _mcu_firmware(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    version = value.get("versionName")
    version_code = value.get("versionCode")
    identity = value.get("identityHex")
    revision = value.get("fixedFrameRevision")
    if not (
        isinstance(version, str)
        and _VERSION.fullmatch(version) is not None
        and isinstance(version_code, int)
        and not isinstance(version_code, bool)
        and 1 <= version_code <= 0xFFFFFFFF
        and isinstance(identity, str)
        and re.fullmatch(r"[0-9a-f]{16}", identity) is not None
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and 1 <= revision <= 255
    ):
        return None
    return {
        "versionName": version,
        "versionCode": version_code,
        "identityHex": identity,
        "fixedFrameRevision": revision,
    }


def _uart_protocol(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"major", "minor"}:
        return None
    major = value.get("major")
    minor = value.get("minor")
    if (
        isinstance(major, bool)
        or not isinstance(major, int)
        or not 1 <= major <= 255
        or isinstance(minor, bool)
        or not isinstance(minor, int)
        or not 0 <= minor <= 255
    ):
        return None
    return {"major": major, "minor": minor}


def _unavailable_legacy_mcu_facts() -> dict[str, Any]:
    return {
        "mcuFirmware": None,
        "uartState": "FAULT",
        "uartProtocol": None,
        "capabilityBitmapHex": "0000000000000000",
    }


def _legacy_mcu_facts(observation: Mapping[str, Any]) -> dict[str, Any]:
    facts = _unavailable_legacy_mcu_facts()
    f3 = observation.get("f3FirmwareIdentity")
    f1 = observation.get("f1SelfTest")
    if not isinstance(f3, Mapping) or not isinstance(f1, Mapping):
        return facts
    identity = f3.get("firmwareIdentity")
    if (
        f3.get("queryStatus") == "OK"
        and f3.get("statusCode") == 0
        and isinstance(identity, Mapping)
    ):
        facts["mcuFirmware"] = _mcu_firmware(
            {
                "versionName": identity.get("firmwareVersion"),
                "versionCode": identity.get("firmwareVersionCode"),
                "identityHex": identity.get("firmwareIdentityHex"),
                "fixedFrameRevision": identity.get("protocolRevision"),
            }
        )
    if (
        f1.get("queryStatus") == "OK"
        and f1.get("communicationHealthy") is True
        and observation.get("uartHandedOff") is False
    ):
        facts["uartState"] = "READY"
    return facts


__all__ = [
    "DeviceSoftwareStateReporter",
    "EVENT_TYPE",
    "TARGET_TYPE",
]

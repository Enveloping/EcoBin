"""Native business owner used by the explicitly selected uart-v2 gateway.

MCU owns the process. This owner sends one START, queries the original work,
commits its complete result and consumes the original backend confirmation.
It never runs the old per-action recovery driver or converts native frames to v1.
Serial creation, polling and commands must all run on the same foreground thread.
"""
from dataclasses import asdict, fields
import hashlib
import json
import os
from time import monotonic_ns
import uuid

from job_safety import JobPermit, JobSafetyError, PermanentJobSafety, command_request_digest
from mcu_configuration import NativeMcuConfiguration, NATIVE_DEVICE_CONSTANTS, NATIVE_PORT_CONSTANTS
from camera_capture import CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS
from photo_manager import DELIVERY_OPEN_SLOTS, DELIVERY_CLOSE_SLOTS, CLEAN_OPEN_SLOTS, CLEAN_CLOSE_SLOTS
from mcu_process_handoff import McuProcessEventHandoff
from mcu_result_handoff import McuResultHandoff
from mcu_session import McuBootSession, McuCommandDispatcher
from mcu_work_query import McuDeviceFactsQuery, McuDeviceIdentityQuery, McuWorkQuery
from native_result_report import NativeResultReporter, check_job_permit
from native_delivery_issue_report import NativeDeliveryIssueReporter
from native_device_entry_url import (
    JOURNAL_KEY as DEVICE_ENTRY_URL_JOURNAL_KEY,
    encode_command as encode_device_entry_url_command,
    new_journal as new_device_entry_url_journal,
    new_reload_journal as new_device_entry_url_reload_journal,
    rebase_journal as rebase_device_entry_url_journal,
    terminal_journal as terminal_device_entry_url_journal,
    validate_journal as validate_device_entry_url_journal,
)
from native_job_rpc import NativeJobRpc
from onenet_wire import validate_command_envelope
from uart2_transport import NativeUartTransport
import uart2_protocol as uart


IDENTITY_FIELDS = {"mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence"}
PROFILE = "UART_V2_SIMPLIFIED"
PHOTO_WAIT_MS = int(CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS * 1000) + 1000
FACTS_MAXIMUM_AGE_MS = NATIVE_PORT_CONSTANTS["weightMaximumSampleAgeMs"]
ISSUE_REPORT_BATCH = 10
_RPC_PENDING = object()
KNOWN_MCU_CAPABILITY = int(
    uart.REGISTRY["capabilityPolicy"]["knownMaskHex"], 16
)
REQUIRED_MCU_CAPABILITY = int(
    uart.REGISTRY["capabilityPolicy"]["requiredMcuMaskHex"], 16
)


class NativeBusinessRuntime:
    native_protocol = 2
    compatibility_mode = False
    is_simulated = False
    port_count = 1
    def __init__(self, store, safety, *, device_name, photo_manager=None,
                 connected=lambda: False, clock=lambda: monotonic_ns() // 1000000,
                 communication_timeout_ms=10000):
        if not isinstance(safety, PermanentJobSafety) or not safety.enabled:
            raise ValueError("native business requires the permanent job authority")
        if type(communication_timeout_ms) is not int or communication_timeout_ms < 1000:
            raise ValueError("native communication timeout must be at least one second")
        self.store, self.safety, self.device_name = store, safety, device_name
        self.photo, self.connected, self.clock = photo_manager, connected, clock
        self.timeout_ms = communication_timeout_ms
        self.transport = self.boot = self.dispatcher = self.facts_query = None
        self.identity_query = None
        self._port = None
        self._mcu_boot_id = 0
        self._identity_boot_id = 0
        self.verified_firmware_identity = None
        self._mcu_firmware_version = ""
        self._mcu_capability = 0
        self._last_alive = None
        self._opened_at = None
        self._work_query = self._handoff = None
        self._query_start_uid = None
        self._live_starts = set()
        self._facts = None
        self._facts_requested_at = None
        self._runtime_observation = {
            "mcuBootId": 0,
            "mcuCapability": 0,
            "mcuFirmwareVersion": "",
            "mcuFirmwareIdentity": None,
            "deviceFacts": None,
        }
        self._environment_fact_keys = {}
        self._environment_waits = {}
        self._photo_deadlines = {}
        self._rpc = None
        self._start_grants = set()
        self._prepared_completions = {}
        self._prepared_issue_completions = {}
        self._reported_issues = set()
        self._issue_reports_pending = set()
        self._issue_report_cursor = ""
        self._scale_wait = None
        self._control_wait_uid = None
        self._control_wait_since = None
        self._baseline_recovery_deadlines = {}
        self._baseline_release_recovery_deadlines = {}
        self._runtime_instance_uid = str(uuid.uuid4())
        self._dispatch_authority = None
        self._device_entry_url_link_refresh_pending = True
        self.reporter = NativeResultReporter(store, safety, device_name=device_name, photo_manager=photo_manager)
        self.issue_reporter = NativeDeliveryIssueReporter(store, device_name=device_name)

    @property
    def is_open(self):
        return self._port is not None and self._port.is_open

    @property
    def current_mcu_boot_id(self):
        """Return only the MCU boot identity proven by the current probe window."""
        if not self.is_open or self.boot is None:
            return 0
        return self.boot.current_boot(self.clock()) or 0

    @property
    def mcu_session_ready(self):
        return self._identity_ready(self.current_mcu_boot_id)

    def current_device_facts(self):
        """Expose one fresh read-only DEVICE_FACTS observation for acceptance."""
        if self.boot is None or not self._identity_ready(self._mcu_boot_id):
            return None
        return self._fresh_facts(self.clock())

    def current_runtime_observation(self):
        """Return the foreground owner's coherent, already-fresh snapshot.

        The runtime snapshot publisher runs on another thread and must not
        advance the UART session clock or inspect mutable query objects.  The
        foreground poller replaces this small immutable view only after it has
        checked the current boot, identity and DEVICE_FACTS freshness.
        """
        observed = self._runtime_observation
        return {
            **observed,
            "mcuFirmwareIdentity": (
                dict(observed["mcuFirmwareIdentity"])
                if isinstance(observed["mcuFirmwareIdentity"], dict)
                else None
            ),
            "deviceFacts": (
                dict(observed["deviceFacts"])
                if isinstance(observed["deviceFacts"], dict)
                else None
            ),
        }

    def _refresh_runtime_observation(self, now):
        current_boot = self.boot.current_boot(now) if self.boot is not None else None
        identity_ready = self._identity_ready(current_boot)
        facts = self._fresh_facts(now) if identity_ready else None
        self._runtime_observation = {
            "mcuBootId": self._mcu_boot_id,
            "mcuCapability": self._mcu_capability,
            "mcuFirmwareVersion": self._mcu_firmware_version if identity_ready else "",
            "mcuFirmwareIdentity": (
                dict(self.verified_firmware_identity)
                if identity_ready
                else None
            ),
            "deviceFacts": facts,
        }

    def _identity_ready(self, boot_id):
        return bool(
            boot_id
            and self._identity_boot_id == boot_id
            and isinstance(self.verified_firmware_identity, dict)
            and self._mcu_firmware_version
        )

    def _require_identity_ready(self, boot_id):
        if not self._identity_ready(boot_id):
            raise JobSafetyError(
                "MCU_IDENTITY_UNAVAILABLE",
                "MCU identity and command sequence are not synchronized",
            )

    def _clear_identity(self):
        self._identity_boot_id = 0
        self.verified_firmware_identity = None
        self._mcu_firmware_version = ""
        self._mcu_capability = 0

    def _accept_identity_observation(self, now):
        if self.identity_query is None or self._identity_ready(self._mcu_boot_id):
            return
        observed = self.identity_query.observation(now)
        if observed is None:
            return
        boot_id = self._mcu_boot_id
        version = observed.get("firmwareVersion")
        version_code = observed.get("firmwareVersionCode")
        high = observed.get("firmwareIdentityHigh")
        low = observed.get("firmwareIdentityLow")
        port_count = observed.get("portCount")
        capability = observed.get("capabilityBitmap")
        if (
            observed.get("status") != "AVAILABLE"
            or observed.get("targetMcuBootId") != boot_id
            or observed.get("currentMcuBootId") != boot_id
            or observed.get("protocolMajor") != 2
            or observed.get("protocolMinor") != 0
            or type(port_count) is not int
            or port_count != 1
            or type(capability) is not int
            or capability & ~KNOWN_MCU_CAPABILITY
            or capability & REQUIRED_MCU_CAPABILITY
            != REQUIRED_MCU_CAPABILITY
            or type(version_code) is not int
            or not 1 <= version_code <= 0xFFFFFFFF
            or not isinstance(version, str)
            or not 5 <= len(version) <= 32
            or not version.isascii()
            or any(ord(character) < 0x20 or ord(character) > 0x7E for character in version)
            or type(high) is not int
            or type(low) is not int
        ):
            return
        identity = (high << 32) | low
        if identity == 0:
            return
        # Factory and production use separate ledgers but can hand over one
        # live MCU boot. Raise the production sequence floor before any
        # mutable command can be prepared; local higher reservations win.
        self.store.synchronize_native_command_sequence(
            boot_id,
            observed["highestCommandSequence"],
        )
        self._identity_boot_id = boot_id
        self._mcu_firmware_version = version
        self._mcu_capability = capability
        self.port_count = port_count
        self.verified_firmware_identity = {
            "queryStatus": "OK",
            "statusCode": 0,
            "fixedFrameRevision": 2,
            "firmwareVersionCode": version_code,
            "firmwareVersion": version,
            "firmwareIdentityHex": f"{identity:016x}",
        }

    @property
    def uart_state(self):
        if not self.is_open:
            return "DISCONNECTED"
        if self.store.get_state("native_blocking_fault"):
            return "FAULT"
        return "READY" if self.mcu_session_ready else "STARTING"

    def communication_fault_status(self):
        """Return read-only facts for one explicit operator recovery action."""
        reason = self.store.get_state("native_blocking_fault") or None
        fault = self.store.get_active_edge_fault("UART", "UART_PROTOCOL")
        detail = {}
        if fault is not None:
            try:
                candidate = json.loads(fault["detail_json"] or "{}")
                detail = candidate if isinstance(candidate, dict) else {}
            except (TypeError, ValueError):
                detail = {}
        now = self.clock()
        facts = self._fresh_facts(now) if self.boot is not None else None
        current_boot = self.boot.current_boot(now) if self.boot is not None else None
        native_fault = bool(
            reason == "MCU_COMMUNICATION_UNAVAILABLE"
            and fault is not None
            and fault["severity"] == "BLOCK_DEVICE"
            and fault["port_no"] is None
            and detail.get("profile")
            == "native-control-communication-v1"
            and detail.get("reasonCode")
            == "MCU_COMMUNICATION_UNAVAILABLE"
            and detail.get("automaticRecovery") is False
        )
        fresh_communication = bool(
            current_boot
            and facts is not None
            and facts.get("status") == "AVAILABLE"
            and facts.get("currentMcuBootId") == current_boot
            and self._last_alive is not None
            and 0 <= now - self._last_alive < self.timeout_ms
        )
        return {
            "reasonCode": reason,
            "faultUid": fault["fault_uid"] if native_fault else None,
            "mcuBootId": current_boot,
            "freshCommunicationConfirmed": fresh_communication,
            "activeWorkUid": (
                (self.store.get_work_slot() or {}).get("work_uid")
            ),
            "manualRecoveryEligible": bool(
                native_fault
                and fresh_communication
                and self.store.get_work_slot() is None
            ),
        }

    def confirm_communication_fault_recovered(self, payload):
        """Clear only the exact communication stop selected by an operator.

        The local control thread never accesses UART. It can only consume the
        foreground owner's already-saved, still-fresh device-facts reply.
        Admission remains subject to configuration, weight, door-control and
        every other normal start check after this one latch is cleared.
        """
        expected = {"expectedFaultUid", "reason", "causeFixedConfirmed"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise JobSafetyError(
                "REQUEST_INVALID",
                "manual communication recovery fields are invalid",
            )
        fault_uid = payload["expectedFaultUid"]
        try:
            parsed_uid = uuid.UUID(fault_uid)
        except (TypeError, ValueError, AttributeError) as error:
            raise JobSafetyError(
                "REQUEST_INVALID",
                "expected fault identity must be a lowercase UUID",
            ) from error
        if str(parsed_uid) != fault_uid or not parsed_uid.int:
            raise JobSafetyError(
                "REQUEST_INVALID",
                "expected fault identity must be a lowercase UUID",
            )
        reason = payload["reason"]
        if (
            not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 512
            or any(ord(character) < 32 or ord(character) == 127
                   for character in reason)
        ):
            raise JobSafetyError(
                "REQUEST_INVALID",
                "manual communication recovery reason is invalid",
            )
        reason = reason.strip()
        if payload["causeFixedConfirmed"] is not True:
            raise JobSafetyError(
                "MANUAL_CONFIRMATION_REQUIRED",
                "operator must confirm that the communication cause is fixed",
            )
        status = self.communication_fault_status()
        if status["faultUid"] != fault_uid:
            raise JobSafetyError(
                "FAULT_IDENTITY_CHANGED",
                "active communication fault differs from the selected fault",
            )
        if status["activeWorkUid"] is not None:
            raise JobSafetyError(
                "DEVICE_BUSY",
                "an original business still owns the device",
            )
        if not status["freshCommunicationConfirmed"]:
            raise JobSafetyError(
                "MCU_COMMUNICATION_UNAVAILABLE",
                "fresh MCU communication has not been confirmed",
            )
        evidence = json.dumps(
            {
                "profile": "native-control-communication-manual-recovery-v1",
                "causeFixedConfirmed": True,
                "operatorReason": reason,
                "operatorReasonSha256": hashlib.sha256(
                    reason.encode("utf-8")
                ).hexdigest(),
                "mcuBootId": status["mcuBootId"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        disposition = self.store.recover_native_control_communication_fault(
            device_name=self.device_name,
            fault_uid=fault_uid,
            mcu_boot_id=status["mcuBootId"],
            recovery_evidence=evidence,
        )
        if disposition != "ACCEPTED":
            raise JobSafetyError(
                "FAULT_RECOVERY_CONFLICT",
                "communication fault changed before recovery was committed",
            )
        return {
            "disposition": "RECOVERED",
            "faultUid": fault_uid,
            "mcuBootId": status["mcuBootId"],
            "newBusinessAdmissionRecheckRequired": True,
        }

    def open(self, *, port, baudrate=115200, port_factory=None):
        if self.transport is not None:
            raise RuntimeError("native UART already has its foreground owner")
        # This startup check happens before any serial ownership or query.
        self._require_maintenance_free(permanent=True)
        if port_factory is None:
            if os.name != "posix":
                raise RuntimeError("live native UART requires POSIX exclusive ownership")
            import serial
            port_factory = serial.Serial
        self._port = port_factory(port=port, baudrate=baudrate, timeout=0, write_timeout=1, exclusive=True)
        self.transport = NativeUartTransport(self._port)
        self.boot = McuBootSession(self.store, self.transport.write)
        self.dispatcher = McuCommandDispatcher(self.store, self.boot, self.transport.write,
            arm=self._arm, clock=self.clock)
        self._opened_at = self.clock()
        self.store.recover_native_device_entry_url_commands()
        self._restore_configuration()
        self._rpc = NativeJobRpc()

    def close(self):
        if self._port is not None:
            self._port.close()
        if self._rpc is not None:
            self._rpc.close()

    def _require_maintenance_free(self, *, permanent=False):
        if (self.store.get_maintenance_lock() is not None
                or (permanent and self.safety.get_mcu_maintenance_status() is not None)):
            raise JobSafetyError("MCU_MAINTENANCE_ACTIVE", "MCU belongs to maintenance")

    def _rpc_call(self, identity, operation):
        """Only a matching completed reply is usable; never wait on this thread.

        An obsolete reply (e.g. an old boot) grants nothing to a newer request.
        Its external effects are still in the original permanent ledger; dropping
        this local reply neither undoes nor invents those facts.
        """
        completed = self._rpc.take_completed()
        if completed is not None and completed.identity == identity:
            if completed.error is not None:
                raise completed.error
            return completed.value
        self._rpc.submit(identity, operation)
        return _RPC_PENDING

    @staticmethod
    def _dispatch_identity(record):
        return (record["command_uid"], record["mcu_boot_id"],
            uart.decode_payload(record["message_name"], record["payload"])["commandDigestSha256"])

    def _send_with_authority(self, record, proof):
        # Valid only during this one foreground dispatch and its final check;
        # not a reusable maintenance/permit cache across commands or boots.
        self._dispatch_authority = (self._dispatch_identity(record), proof)
        try:
            return self.dispatcher.send_once(record["command_uid"])
        finally:
            self._dispatch_authority = None

    @staticmethod
    def _permit(slot):
        context = slot["context"]
        if context.get("native_protocol") != 2:
            raise JobSafetyError("LEGACY_WORK_REQUIRES_MANUAL", "retained work is not native business")
        saved = context["job_safety"]
        permit = JobPermit(**{field.name: saved[field.name] for field in fields(JobPermit)})
        if permit.work_uid != slot["work_uid"] or permit.work_type != slot["work_type"]:
            raise ValueError("native slot differs from its original permit")
        return permit

    def _check_configuration(self, identity):
        row = self.store.get_latest_applied_configuration()
        if row is None or row["payload"].get("mcuConfigurationProfile") != PROFILE:
            raise JobSafetyError("CONFIGURATION_NOT_APPLIED", "native configuration has not been applied")
        if row["payload"]["config"] != identity:
            raise JobSafetyError("CONFIGURATION_MISMATCH", "START differs from the frozen configuration")
        facts = self._fresh_facts(self.clock())
        if facts is None or facts["status"] != "AVAILABLE" or facts["configStaging"]:
            raise JobSafetyError("MCU_FACTS_UNAVAILABLE", "fresh MCU facts are required")
        if (facts["appliedConfigVersion"] != identity["version"]
                or facts["appliedContentSha256"] != identity["contentSha256"]
                or facts["appliedMcuPayloadSha256"] != identity["mcuPayloadSha256"]):
            raise JobSafetyError("CONFIGURATION_MISMATCH", "MCU has not confirmed the original configuration")
        return row, facts

    def _fresh_facts(self, now):
        # Starting a new query does not invalidate a previously verified reply.
        # Its age is still measured from THAT reply's original request, not the
        # latest poll or a retransmitted sample. A new MCU boot invalidates it.
        facts = self._facts
        if (facts is None or self._facts_requested_at is None
                or facts["currentMcuBootId"] != self.boot.current_boot(now)
                or not 0 <= now - self._facts_requested_at <= FACTS_MAXIMUM_AGE_MS):
            return None
        return dict(facts)

    def _fresh_weight(self, facts, now):
        if facts["status"] != "AVAILABLE" or facts["scaleReadStatus"] != "VALID":
            return None
        age = facts["capturedUptimeMs"] - facts["scaleCapturedUptimeMs"] + now - self._facts_requested_at
        return facts["scaleWeightGrams"] if 0 <= age <= FACTS_MAXIMUM_AGE_MS else None

    def _require_released_result_custody(self, facts):
        if facts["retainedWorkState"] != "RESULT_RELEASED":
            return
        saved = self.store.get_native_mcu_result(
            facts["currentMcuBootId"], facts["retainedResultSequence"]
        )
        if saved is not None and saved["work_uid"] == facts["retainedWorkUid"]:
            return
        if self.store.recognize_factory_released_result_baseline(
            facts["currentMcuBootId"],
            facts["retainedResultSequence"],
            facts["retainedWorkUid"],
        ):
            return
        raise ValueError("MCU released result has no corresponding local custody")

    def _clean_bag_interlock_clearance(self, command):
        payload = command["payload"]
        port_no = payload["portNo"]
        if not self.store.clean_restart_interlock_active(port_no):
            return None
        metadata = self.store.get_clean_restart_interlock_metadata(port_no)
        stored = self.store.get_command(command["commandUid"])
        expected_type = command["commandType"]
        if (
            metadata is None
            or metadata.get("profile")
            != "native-clean-bag-interlock-v1"
            or metadata.get("portNo") != port_no
            or not all(
                isinstance(metadata.get(name), str)
                and metadata[name]
                for name in (
                    "sourceWorkUid",
                    "sourceCommandUid",
                    "newBagUid",
                )
            )
            or "oldBagUid" not in metadata
            or (
                metadata.get("oldBagUid") is not None
                and (
                    not isinstance(metadata["oldBagUid"], str)
                    or not metadata["oldBagUid"]
                )
            )
            or stored is None
            or stored["state"] != "PROCESSING"
            or stored["command_type"] != expected_type
            or stored["payload"] != command
        ):
            raise JobSafetyError(
                "CLEAN_BAG_CONFIRMATION_REQUIRED",
                "an interrupted clean requires a matching backend-authorized START",
            )
        actual_bag_uid = (
            payload["bagUid"]
            if expected_type == "START_DELIVERY_SESSION"
            else payload["oldBagUid"]
        )
        if actual_bag_uid not in {
            bag_uid
            for bag_uid in (
                metadata.get("oldBagUid"),
                metadata["newBagUid"],
            )
            if isinstance(bag_uid, str) and bag_uid
        }:
            raise JobSafetyError(
                "CLEAN_BAG_CONFIRMATION_REQUIRED",
                "the backend-authorized START does not match the confirmed current bag",
            )
        return metadata

    def _check_start(self, command, *, permit=None, permit_snapshot=None):
        validate_command_envelope({key: value for key, value in command.items() if key != "cosGrant"})
        if command["targetDeviceName"] != self.device_name:
            raise ValueError("command belongs to another device")
        if not self.connected():
            raise JobSafetyError("NETWORK_UNAVAILABLE", "offline devices do not accept new business")
        fault = self.store.get_state("native_blocking_fault")
        if fault:
            raise JobSafetyError(fault, "native business is awaiting fault handling")
        self._require_maintenance_free()
        if self.boot is None or self.boot.current_boot(self.clock()) is None:
            raise JobSafetyError("MCU_COMMUNICATION_UNAVAILABLE", "fresh MCU communication required")
        self._require_identity_ready(self.boot.current_boot(self.clock()))
        payload = command["payload"]
        interlock_clearance = self._clean_bag_interlock_clearance(command)
        configuration, facts = self._check_configuration(payload["config"])
        port = next((row for row in configuration["payload"]["ports"] if row["portNo"] == payload["portNo"]), None)
        if port is None or not port["enabled"] or payload["portNo"] != 1:
            raise JobSafetyError("PORT_UNAVAILABLE", "this MCU exposes one enabled physical port")
        if self._fresh_weight(facts, self.clock()) is None:
            raise JobSafetyError("WEIGHT_UNAVAILABLE", "a fresh actual scale read is required")
        if facts["updateLatched"] or facts["lastDeliveryDoorCommand"] != "CLOSE" or facts["cleanLockPowered"]:
            raise JobSafetyError("MCU_CONTROL_NOT_READY", "MCU close/lock control is not ready")
        # PB5 and optional smoke/ranging/camera readings are not admission faults.
        if command["commandType"] == "START_DELIVERY_SESSION":
            if self.store.get_port_fullness_state(payload["portNo"], payload["bagUid"]) == "FULL":
                raise JobSafetyError("PORT_FULL", "the original bag is already known full")
        slot = self.store.get_work_slot()
        if permit is None:
            if any(row["payload"].get("mcuConfigurationProfile") == PROFILE
                    for row in self.store.list_pending_configurations()):
                raise JobSafetyError("MCU_CONFIGURATION_BUSY", "an original configuration is waiting to apply")
            if self._configuration_reload_pending():
                raise JobSafetyError("MCU_CONFIGURATION_BUSY", "the applied configuration is reloading after MCU reboot")
            if slot is not None or facts["retainedWorkState"] not in {"NONE", "RESULT_RELEASED"}:
                raise JobSafetyError("DEVICE_BUSY", "an original business still owns the device")
            self._require_released_result_custody(facts)
        else:
            if slot is None or self._permit(slot) != permit:
                raise ValueError("native START lost its original slot")
            check_job_permit(permit, permit_snapshot)
        return interlock_clearance

    def _check_baseline(self, command, *, permit=None, permit_snapshot=None):
        """Validate one non-mechanical empty-bag measurement authority."""
        validate_command_envelope(
            {key: value for key, value in command.items() if key != "cosGrant"}
        )
        if (
            command["commandType"] != "MEASURE_EMPTY_BAG_BASELINE"
            or command["targetDeviceName"] != self.device_name
        ):
            raise ValueError("baseline command belongs to another target")
        if not self.connected():
            raise JobSafetyError(
                "NETWORK_UNAVAILABLE",
                "offline devices do not accept a new baseline measurement",
            )
        fault = self.store.get_state("native_blocking_fault")
        if fault:
            raise JobSafetyError(
                fault,
                "native baseline is awaiting communication fault handling",
            )
        self._require_maintenance_free()
        now = self.clock()
        boot_id = self.boot.current_boot(now) if self.boot is not None else None
        if boot_id is None:
            raise JobSafetyError(
                "MCU_COMMUNICATION_UNAVAILABLE",
                "fresh MCU communication is required for baseline measurement",
            )
        self._require_identity_ready(boot_id)
        payload = command["payload"]
        if payload["emptyBagConfirmed"] is not True:
            raise JobSafetyError(
                "EMPTY_BAG_NOT_CONFIRMED",
                "the physical empty bag must be explicitly confirmed",
            )
        if payload["measurementTimeoutMs"] != 5000:
            raise JobSafetyError(
                "BASELINE_TIMEOUT_INVALID",
                "native baseline measurement timeout must be exactly five seconds",
            )
        configuration, facts = self._check_configuration(payload["config"])
        port = next(
            (
                row
                for row in configuration["payload"]["ports"]
                if row["portNo"] == payload["portNo"]
            ),
            None,
        )
        if port is None or not port["enabled"] or payload["portNo"] != 1:
            raise JobSafetyError(
                "PORT_UNAVAILABLE",
                "this MCU exposes one enabled physical port",
            )
        if facts["updateLatched"]:
            raise JobSafetyError(
                "MCU_CONTROL_NOT_READY",
                "MCU update control is not ready for baseline measurement",
            )
        slot = self.store.get_work_slot()
        if permit is None:
            if any(
                row["payload"].get("mcuConfigurationProfile") == PROFILE
                for row in self.store.list_pending_configurations()
            ) or self._configuration_reload_pending():
                raise JobSafetyError(
                    "MCU_CONFIGURATION_BUSY",
                    "the original configuration is changing",
                )
            if slot is not None or facts["retainedWorkState"] not in {
                "NONE",
                "RESULT_RELEASED",
            }:
                raise JobSafetyError(
                    "DEVICE_BUSY",
                    "an original business still owns the device",
                )
            self._require_released_result_custody(facts)
        else:
            if slot is None or self._permit(slot) != permit:
                raise ValueError("native baseline lost its original slot")
            record = self.store.get_native_command(
                slot["context"]["start_mcu_command_uid"]
            )
            if record is None or record["mcu_boot_id"] != boot_id:
                raise JobSafetyError(
                    "MCU_RESTART_FINAL_RESULT_UNAVAILABLE",
                    "baseline command belongs to an earlier MCU boot",
                )
            check_job_permit(permit, permit_snapshot)

    def start_delivery_command(self, command):
        return self._start(command, clean=False)

    def start_clean_command(self, command):
        return self._start(command, clean=True)

    def start_baseline_command(self, command):
        self._check_baseline(command)
        payload = command["payload"]
        native_uid = str(uuid.uuid4())
        permit = JobPermit(
            command["commandUid"],
            payload["measurementUid"],
            command["commandUid"],
            "BASELINE",
            command_request_digest(command),
        )
        record = self.store.prepare_native_baseline_work(
            command,
            permit,
            native_uid,
            self.boot.current_boot(self.clock()),
            runtime_instance_uid=self._runtime_instance_uid,
        )
        if record is None:
            raise JobSafetyError(
                "DEVICE_BUSY",
                "device slot was acquired by another owner",
            )
        self._live_starts.add(record["command_uid"])
        return {"native_pending": True, "mcu_command_uid": native_uid}

    def _start(self, command, *, clean):
        interlock_clearance = self._check_start(command)
        payload = command["payload"]
        key, work_type = ("operationUid", "CLEAN") if clean else ("sessionUid", "DELIVERY")
        start_bag_uid = payload["oldBagUid"] if clean else payload["bagUid"]
        uid = str(uuid.uuid4())
        values = {key: payload[key], "portNo": payload["portNo"],
            "configVersion": payload["config"]["version"], "configContentSha256": payload["config"]["contentSha256"],
            "startExecutionWindowMs": 5000}
        for name in (("operationWindowMs",) if clean else ("unitPriceTenThousandths", "continueDeliveryWaitMs",
                "negativeWeightThresholdGrams", "deliveryAutoCloseMs")):
            values[name] = payload[name]
        record = self.store.prepare_native_command(command["commandType"], uid, self.boot.current_boot(self.clock()), values)
        # The deterministic original permit is persisted BEFORE its first RPC.
        # Merely storing it grants nothing; _arm requires the real ACTIVE reply.
        permit = JobPermit(command["commandUid"], payload[key], command["commandUid"], work_type,
            command_request_digest(command))
        context = dict(native_protocol=2, phase="NATIVE_RUNNING", start_command_uid=command["commandUid"],
            start_mcu_command_uid=uid, start_runtime_instance_uid=self._runtime_instance_uid,
            start_bag_uid=start_bag_uid,
            job_safety=asdict(permit) | {"begin_uid": permit.work_uid})
        if not self.store.acquire_work_slot(
            work_type,
            permit.work_uid,
            payload["portNo"],
            context,
            clean_restart_interlock_clearance=interlock_clearance,
        ):
            if self.store.clean_restart_interlock_active(payload["portNo"]):
                raise JobSafetyError(
                    "CLEAN_BAG_CONFIRMATION_REQUIRED",
                    "the interrupted clean bag interlock changed before START acquisition",
                )
            raise JobSafetyError("DEVICE_BUSY", "device slot was acquired by another owner")
        if self.photo is not None:
            if command.get("cosGrant"):
                self.photo.offer_initial_grant("CLEAN_OPERATION" if clean else "DELIVERY_SESSION", permit.work_uid, command["cosGrant"])
            self._queue_photos(permit, before=True)
        self._live_starts.add(record["command_uid"])
        return dict(native_pending=True, mcu_command_uid=uid)

    def _start_authorization(self, permit, record):
        uid, boot_id, digest = self._dispatch_identity(record)
        command = self.store.get_command(permit.command_uid)["payload"]
        if uid not in self._start_grants:
            def request():
                if self.safety.get_mcu_maintenance_status() is not None:
                    raise JobSafetyError("MCU_MAINTENANCE_ACTIVE", "MCU belongs to maintenance")
                return self.safety.request_job(command, work_type=permit.work_type, work_uid=permit.work_uid)
            granted = self._rpc_call(("START_GRANT", uid, boot_id, digest), request)
            if granted is _RPC_PENDING:
                return _RPC_PENDING
            if granted != permit:
                raise ValueError("permanent authority changed the original permit")
            self._start_grants.add(uid)
        def begin():
            # A lost BEGIN/GET reply repeats only the original idempotent BEGIN,
            # not REQUEST_JOB (which is for a still-GRANTED original permit).
            self.safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
            return self.safety.get_job_permit(permit.permit_uid)
        snapshot = self._rpc_call(("START_BEGIN", uid, boot_id, digest), begin)
        if snapshot is not _RPC_PENDING:
            check_job_permit(permit, snapshot)
        return snapshot

    def _queue_photos(self, permit, *, before):
        phase = "open" if before else "close"
        method = "capture_" + ("clean_" if permit.work_type == "CLEAN" else "") + phase + "_photos_async"
        if not getattr(self.photo, method)(permit.work_uid):
            # The queue persists the slots first. Failure here is storage, not
            # an optional camera failure, so the foreground must not ignore it.
            raise RuntimeError("PHOTO_RESERVATION_NOT_PERSISTED")
        self._photo_deadlines[permit.work_uid, before] = self.clock() + PHOTO_WAIT_MS

    def _photos_ready(self, permit, *, before, now):
        if self.photo is None:
            return True
        names = ((CLEAN_OPEN_SLOTS if before else CLEAN_CLOSE_SLOTS) if permit.work_type == "CLEAN"
            else (DELIVERY_OPEN_SLOTS if before else DELIVERY_CLOSE_SLOTS))
        photos = {row["slot_name"]: row for row in self.store.get_photos_by_work(permit.work_uid)}
        if any(name not in photos for name in names):
            raise RuntimeError("PHOTO_RESERVATION_NOT_PERSISTED")
        key = permit.work_uid, before
        if any(photos[name]["state"] == "CAPTURE_PENDING" for name in names):
            deadline = self._photo_deadlines.setdefault(key, now + PHOTO_WAIT_MS)
            if now < deadline:
                return False
            # No camera call or capture lock on the UART owner. This atomically
            # retires only STILL pending slots; a concurrent completed capture
            # wins. Late frames cannot become photos of the preceding phase.
            self.photo.expire_pending_captures(permit.work_uid, names)
        self._photo_deadlines.pop(key, None)
        return True

    def apply_configuration_command(self, command):
        if self.store.get_work_slot() is not None:
            raise JobSafetyError("DEVICE_BUSY", "configuration cannot replace an active business")
        if command["targetDeviceName"] != self.device_name:
            raise ValueError("configuration belongs to another device")
        candidate = NativeMcuConfiguration.from_cloud_payload(command["payload"])
        app = command["payload"]["applicationUid"]
        pending = self._restore_configuration()
        if pending is not None and pending["application_uid"] != app:
            raise JobSafetyError("MCU_CONFIGURATION_BUSY", "another original configuration is unfinished")
        prior = self.store.get_configuration(app)
        if prior is not None:
            if (prior["command_uid"] != command["commandUid"] or prior["device_name"] != self.device_name
                    or prior["payload"] != command["payload"]):
                raise ValueError("configuration replay differs from its original cloud authority")
            if prior["state"] == "APPLIED":
                return  # A duplicate cannot turn an applied command back into waiting.
            if prior["state"] == "FAILED":
                raise JobSafetyError("MCU_CONFIGURATION_FAILED", "original configuration remains failed")
        if self._configuration_reload_pending():
            raise JobSafetyError("MCU_CONFIGURATION_BUSY", "the applied configuration is reloading after MCU reboot")
        part_uids = prior["part_command_uids"] if prior else [str(uuid.uuid4()) for _ in range(candidate.part_count)]
        disposition = self.store.save_configuration_edge(command, part_uids)
        if disposition not in {"ACCEPTED", "DUPLICATE"}:
            raise ValueError("native configuration custody: " + disposition)
        self._restore_configuration()
        self.store.mark_command_waiting_mcu(command["commandUid"], part_uids[-1], {"native_pending": True})

    def send_device_entry_url(self, url, *, command=None, stored_record=None):
        """Persist an asynchronous UART-v2 URL application continuation.

        This method owns no HMI success assumption.  Only the MCU's later
        DEVICE_ENTRY_URL_APPLY_RESULT can complete SYNC or resume acceptance.
        """
        if command is None or stored_record is None:
            raise ValueError("native device entry URL requires its cloud authority")
        if url != stored_record.get("deviceEntryUrl"):
            raise ValueError("native device entry URL differs from durable source")
        if command.get("targetDeviceName") != self.device_name:
            raise ValueError("device entry URL belongs to another device")
        existing = self.store.get_command(command["commandUid"])
        result = existing.get("result") if existing else None
        journal = None
        waiting_for_reload = False
        if isinstance(result, dict) and DEVICE_ENTRY_URL_JOURNAL_KEY in result:
            journal = validate_device_entry_url_journal(
                result[DEVICE_ENTRY_URL_JOURNAL_KEY]
            )
            if (
                journal["sourceCommandUid"] != command["commandUid"]
                or journal["deviceEntryUrl"] != url
                or journal["deviceEntryUrlSha256"]
                != stored_record["deviceEntryUrlSha256"]
            ):
                raise ValueError("native device entry URL replay conflicts")
            current_boot = self.boot.current_boot(self.clock()) if self.boot else None
            if journal["state"] == "FAILED":
                raise JobSafetyError(
                    "MCU_DEVICE_ENTRY_URL_" + journal["appliedEvidence"]["faultCode"],
                    "the original MCU URL application failed",
                )
            if journal["state"] == "APPLIED":
                evidence = self.store.get_native_device_entry_url_applied_evidence()
                if (
                    current_boot
                    and not self._device_entry_url_link_refresh_pending
                    and evidence is not None
                    and evidence["deviceEntryUrlSha256"]
                    == journal["deviceEntryUrlSha256"]
                    and evidence["mcuBootId"] == current_boot
                ):
                    return {
                        "native_pending": False,
                        "applied": True,
                        "mcu_command_uid": evidence["mcuCommandUid"],
                        "evidence": evidence,
                    }
                # The command-specific APPLIED fact belongs to an earlier
                # UART connection/boot observation.  Keep the acceptance
                # continuation parked until the foreground owner completes
                # the current LOCAL_RELOAD (or another current application).
                # Never run acceptance with a missing global current-link
                # proof and thereby manufacture a terminal NOT_APPLIED report.
                if current_boot is None:
                    waiting_for_reload = True
                else:
                    reload = self.store.get_native_device_entry_url_reload()
                    reload_matches = bool(
                        reload is not None
                        and reload["deviceEntryUrlSha256"]
                        == journal["deviceEntryUrlSha256"]
                        and reload["attempt"]["targetMcuBootId"]
                        == current_boot
                    )
                    pending_application = any(
                        item["journal"]["deviceEntryUrlSha256"]
                        == journal["deviceEntryUrlSha256"]
                        for item in self.store.list_native_device_entry_url_applications()
                    )
                    if (
                        self._device_entry_url_link_refresh_pending
                        or pending_application
                        or (reload_matches and reload["state"] == "WAITING")
                    ):
                        waiting_for_reload = True
                    elif reload_matches and reload["state"] == "FAILED":
                        raise JobSafetyError(
                            "MCU_DEVICE_ENTRY_URL_"
                            + reload["appliedEvidence"]["faultCode"],
                            "the current-link MCU URL reload failed",
                        )
                    else:
                        raise JobSafetyError(
                            "MCU_DEVICE_ENTRY_URL_PROOF_UNAVAILABLE",
                            "the current UART link lacks applied URL proof",
                        )
        if journal is None:
            current_boot = self.boot.current_boot(self.clock()) if self.boot else None
            journal = new_device_entry_url_journal(
                command,
                stored_record,
                target_mcu_boot_id=current_boot,
            )
            journal = self.store.begin_native_device_entry_url_application(
                command,
                journal,
            )
        attempt = journal["attempt"]
        return {
            "native_pending": True,
            "waiting_for_reload": waiting_for_reload,
            "mcu_command_uid": (
                attempt["commandUids"][-1]
                if attempt["commandUids"]
                else None
            ),
        }

    def _restore_configuration(self):
        """Recover the sole native configuration from durable authority, not its pointer.

        This never requeues START, regenerates configuration part IDs, changes
        an existing boot/sequence, or retries a claimed write. Expired commands
        may still be queried; _arm independently checks time before a new write.
        """
        from onenet_wire import _validate_command_envelope

        pending = []
        for row in self.store.list_pending_configurations():
            profile = row["payload"].get("mcuConfigurationProfile")
            if "mcuConfigurationProfile" not in row["payload"]:
                continue  # A retained legacy command is not an implicit native upgrade.
            if profile != PROFILE:
                raise ValueError("pending configuration has an unsupported explicit profile")
            pending.append(row)
        if len(pending) > 1:
            raise JobSafetyError("MCU_CONFIGURATION_CONFLICT", "multiple original native configurations are unfinished")
        marker = self.store.get_state("native_configuration_application")
        if marker and (not pending or marker != pending[0]["application_uid"]):
            prior = self.store.get_configuration(marker)
            if (prior is None or prior["payload"].get("mcuConfigurationProfile") != PROFILE
                    or prior["state"] not in {"APPLIED", "FAILED"}):
                raise ValueError("configuration pointer differs from original custody")
        if not pending:
            if marker:
                self.store.set_state("native_configuration_application", "")
            return None
        row = pending[0]
        original = self.store.get_command(row["command_uid"])
        if original is None or original["state"] not in {
                "PENDING", "PROCESSING", "WAITING_MCU_RESULT", "RECOVERY_REQUIRED"}:
            raise ValueError("pending configuration lost its nonterminal original command")
        command = original["payload"]
        # Revalidate already-committed authority without treating its historic
        # deadline as permission for a new write. No grant/default is invented.
        _validate_command_envelope(command, trusted_environment=None,
            trusted_business_release_download_base_url=None, expiry_reference_time=None)
        if (command["commandType"] != "APPLY_CONFIGURATION" or command["commandUid"] != row["command_uid"]
                or command["targetDeviceName"] != self.device_name or row["device_name"] != self.device_name
                or command["payload"] != row["payload"]
                or row["payload"]["applicationUid"] != row["application_uid"]
                or row["payload"]["config"] != dict(version=row["config_version"], contentSha256=row["content_sha256"],
                                                    mcuPayloadSha256=row["mcu_payload_sha256"])):
            raise ValueError("pending configuration differs from its original cloud command")
        candidate = NativeMcuConfiguration.from_cloud_payload(row["payload"])
        parts = row["part_command_uids"]
        if not isinstance(parts, list) or len(parts) != candidate.part_count or len(set(parts)) != len(parts):
            raise ValueError("pending configuration original part IDs are incomplete")
        missing, boot_ids = False, set()
        for index, uid in enumerate(parts, 1):
            if not isinstance(uid, str) or str(uuid.UUID(uid)) != uid or not uuid.UUID(uid).int:
                raise ValueError("pending configuration original part ID is invalid")
            record = self.store.get_native_command(uid)
            if record is None:
                missing = True
                continue
            if missing or record["conflict"]:
                raise ValueError("pending configuration original parts conflict or have a gap")
            name, raw = candidate.encode_part(index, application_uid=row["application_uid"], mcu_command_uid=uid,
                target_mcu_boot_id=record["mcu_boot_id"], command_sequence=record["command_sequence"])
            if record["message_name"] != name or record["payload"] != raw:
                raise ValueError("pending configuration original part bytes differ")
            boot_ids.add(record["mcu_boot_id"])
        if len(boot_ids) > 1:
            raise ValueError("pending configuration mixes original MCU boots")
        if marker != row["application_uid"]:
            self.store.set_state("native_configuration_application", row["application_uid"])
        return row

    def _device_entry_url_application_for_record(self, record):
        for pending in self.store.list_native_device_entry_url_applications():
            journal = pending["journal"]
            if record["command_uid"] in journal["attempt"]["commandUids"]:
                return pending
        reload = self.store.get_native_device_entry_url_reload()
        if (
            reload is not None
            and reload["state"] == "WAITING"
            and record["command_uid"] in reload["attempt"]["commandUids"]
        ):
            return {"command": None, "journal": reload}
        return None

    def _device_entry_url_poll(self, now):
        if not self._identity_ready(self.boot.current_boot(now)):
            return
        applications = self.store.list_native_device_entry_url_applications()
        # URL display is nonmechanical, but changing the visible page during a
        # delivery/clean operation is intentionally deferred until idle.
        if (
            self.store.get_work_slot() is not None
            or self.store.get_maintenance_lock() is not None
            or self.store.get_state("native_configuration_application")
        ):
            return
        current_boot = self.boot.current_boot(now)
        if not current_boot:
            return
        if applications:
            pending = applications[0]
        else:
            active = self.store.get_device_entry_url()
            if active is None:
                return
            evidence = self.store.get_native_device_entry_url_applied_evidence()
            source_command_uid = (
                self.store.get_native_device_entry_url_source_command_uid(
                    active["deviceEntryUrl"],
                    active["deviceEntryUrlSha256"],
                )
            )
            # A locally stored URL which never reached APPLIED is not a reload
            # authority. Its original cloud command already carries the
            # terminal failure and must not be silently retried here.
            if source_command_uid is None:
                return
            reload = self.store.get_native_device_entry_url_reload()
            evidence_matches_url = bool(
                evidence is not None
                and evidence["deviceEntryUrlSha256"]
                == active["deviceEntryUrlSha256"]
            )
            if (
                evidence_matches_url
                and evidence["mcuBootId"] == current_boot
                and not self._device_entry_url_link_refresh_pending
            ):
                return
            boot_changed = bool(
                (evidence_matches_url and evidence["mcuBootId"] != current_boot)
                or (
                    reload is not None
                    and reload["deviceEntryUrlSha256"]
                    == active["deviceEntryUrlSha256"]
                    and reload["attempt"]["targetMcuBootId"] != current_boot
                )
            )
            if (
                not self._device_entry_url_link_refresh_pending
                and not boot_changed
                and not (
                    reload is not None
                    and reload["deviceEntryUrlSha256"]
                    == active["deviceEntryUrlSha256"]
                    and reload["state"] == "WAITING"
                    and reload["attempt"]["targetMcuBootId"] == current_boot
                )
            ):
                # A terminal failure is retained for this connection.  A new
                # explicit reconnect or MCU boot may create a fresh attempt;
                # an ordinary poll must not silently loop on a failed write.
                return
            if (
                reload is None
                or reload["deviceEntryUrlSha256"]
                != active["deviceEntryUrlSha256"]
            ):
                reload = new_device_entry_url_reload_journal(
                    active,
                    source_command_uid=source_command_uid,
                    target_mcu_boot_id=current_boot,
                )
                self.store.save_native_device_entry_url_reload(reload)
                # This connection generation now owns one durable attempt.
                # Keep polling that identity instead of rebasing it on every
                # foreground loop while the result is still outstanding.
                self._device_entry_url_link_refresh_pending = False
            elif (
                reload["attempt"]["targetMcuBootId"] != current_boot
                or self._device_entry_url_link_refresh_pending
            ):
                rebased = rebase_device_entry_url_journal(
                    reload,
                    current_boot,
                )
                if not self.store.save_native_device_entry_url_reload(
                    rebased,
                    expected_application_uid=reload["attempt"]["applicationUid"],
                ):
                    return
                reload = rebased
                self._device_entry_url_link_refresh_pending = False
            elif reload["state"] != "WAITING":
                # FAILED stays explicit until a new boot or a newer URL. An
                # APPLIED record should normally also have current evidence;
                # corrupt/missing evidence fails closed instead of rewriting.
                return
            pending = {"command": None, "journal": reload}
        command = pending["command"]
        journal = pending["journal"]
        attempt = journal["attempt"]
        if attempt["targetMcuBootId"] != current_boot:
            rebased = rebase_device_entry_url_journal(journal, current_boot)
            if not self.store.replace_native_device_entry_url_journal(
                command["command_uid"],
                attempt["applicationUid"],
                rebased,
            ):
                return
            journal = rebased
            attempt = journal["attempt"]
        for index, uid in enumerate(attempt["commandUids"]):
            record = self.store.get_native_command(uid)
            if record is None:
                name, prototype = encode_device_entry_url_command(
                    journal,
                    index,
                    command_sequence=1,
                )
                values = uart.decode_payload(name, prototype)
                try:
                    record = self.store.prepare_native_command(
                        name,
                        uid,
                        current_boot,
                        {
                            key: value
                            for key, value in values.items()
                            if key not in IDENTITY_FIELDS
                        },
                    )
                except RuntimeError as error:
                    if "native command unresolved" in str(error):
                        return
                    raise
            else:
                name, expected = encode_device_entry_url_command(
                    journal,
                    index,
                    command_sequence=record["command_sequence"],
                )
                if record["message_name"] != name or record["payload"] != expected:
                    raise ValueError("stored device entry URL command bytes differ")
            if record["mcu_boot_id"] != current_boot or record["conflict"]:
                raise ValueError("device entry URL command identity conflicts")
            if record["decision_outcome"] == "REJECTED":
                raise JobSafetyError(
                    "MCU_DEVICE_ENTRY_URL_COMMAND_REJECTED",
                    "MCU rejected a validated URL application command",
                )
            if record["decision_outcome"] != "ACCEPTED":
                if not record["write_claimed"]:
                    self._send_with_authority(record, None)
                self.dispatcher.poll(uid, now)
                return
        # COMMIT acceptance does not mean the HMI command was queued. Querying
        # the exact COMMIT is the only recovery action; the MCU re-emits its
        # held APPLY_RESULT without writing the HMI a second time.
        self.dispatcher.poll(attempt["commandUids"][-1], now)

    def _accept_device_entry_url_result(self, payload):
        values = uart.decode_payload("DEVICE_ENTRY_URL_APPLY_RESULT", payload)
        found = self.store.find_native_device_entry_url_application(
            values["applicationUid"],
            values["mcuCommandUid"],
        )
        if found is None:
            outcome = self.store.save_unmatched_native_device_entry_url_apply_result(
                payload
            )
            if outcome == "CONFLICT":
                raise ValueError("device entry URL result identity conflict")
            return True
        journal = found["journal"]
        fresh_terminal = journal["state"] == "WAITING"
        terminal = (
            journal
            if not fresh_terminal
            else terminal_device_entry_url_journal(journal, payload, values)
        )
        outcome = self.store.save_native_device_entry_url_apply_result(
            payload,
            terminal,
        )
        if fresh_terminal and outcome in {"APPLIED", "FAILED"}:
            self._device_entry_url_link_refresh_pending = False
            self.store.requeue_native_acceptance_after_device_entry_url_reload(
                values["urlSha256"],
            )
        return outcome in {"APPLIED", "FAILED", "DUPLICATE"}

    def _arm(self, record):
        authority = self._dispatch_authority
        if authority is None or authority[0] != self._dispatch_identity(record):
            raise JobSafetyError("NATIVE_AUTHORITY_UNAVAILABLE", "this exact write has no completed authority request")
        if record["message_name"].startswith("DEVICE_ENTRY_URL_"):
            def check():
                self._require_maintenance_free()
                if authority[1] is not None:
                    raise JobSafetyError(
                        "MCU_MAINTENANCE_ACTIVE",
                        "MCU belongs to maintenance",
                    )
                if self.store.get_work_slot() is not None:
                    raise JobSafetyError(
                        "DEVICE_BUSY",
                        "device entry URL waits until the business is idle",
                    )
                pending = self._device_entry_url_application_for_record(record)
                if pending is None:
                    raise ValueError("device entry URL write lost its journal")
                if pending["command"] is None:
                    active = self.store.get_device_entry_url()
                    if (
                        active is None
                        or active["deviceEntryUrl"]
                        != pending["journal"]["deviceEntryUrl"]
                        or active["deviceEntryUrlSha256"]
                        != pending["journal"]["deviceEntryUrlSha256"]
                    ):
                        raise ValueError("device entry URL reload source changed")
                else:
                    command = pending["command"]["payload"]
                    if command["commandType"] == "SYNC_DEVICE_ENTRY_URL":
                        from onenet_wire import _validate_command_envelope
                        _validate_command_envelope(
                            command,
                            trusted_environment=None,
                            trusted_business_release_download_base_url=None,
                            expiry_reference_time=None,
                        )
                    else:
                        # Acceptance STS credentials are intentionally absent
                        # from SQLite. The command was fully validated before
                        # this journal was created; re-check its immutable
                        # non-secret digest instead of inventing a COS grant.
                        from onenet_wire import canonical_payload_sha256
                        stable = dict(command)
                        stable.pop("cosGrant", None)
                        if canonical_payload_sha256(stable) != pending["command"]["canonical_sha256"]:
                            raise ValueError("device acceptance authority changed")
                    if command["targetDeviceName"] != self.device_name:
                        raise ValueError("device entry URL belongs to another device")
            check()
            return check
        if record["message_name"].startswith("CONFIG_"):
            def check():
                self._require_maintenance_free()
                if authority[1] is not None:
                    raise JobSafetyError("MCU_MAINTENANCE_ACTIVE", "MCU belongs to maintenance")
                if self.store.get_work_slot() is not None:
                    raise JobSafetyError("DEVICE_BUSY", "configuration lost exclusive ownership")
                app = uart.decode_payload(record["message_name"], record["payload"])["applicationUid"]
                row = self.store.get_configuration(app)
                if row is None:
                    raise ValueError("configuration command has no original cloud authority")
                if record["command_uid"] in row["part_command_uids"]:
                    validate_command_envelope(self.store.get_command(row["command_uid"])["payload"])
                else:
                    self._check_configuration_reload_part(row, record)
            check()
            return check
        slot = self.store.get_work_slot()
        permit = self._permit(slot) if slot else None
        if permit is None or slot["context"]["start_mcu_command_uid"] != record["command_uid"]:
            raise ValueError("native dispatch is not the original START")
        if record["message_name"] == "MEASURE_BASELINE":
            command = self.store.get_command(permit.command_uid)["payload"]
            check = lambda: self._check_baseline(
                command,
                permit=permit,
                permit_snapshot=authority[1],
            )
            check()
            return check
        if record["message_name"] not in {"START_DELIVERY_SESSION", "START_CLEAN_OPERATION"}:
            raise ValueError("native business does not dispatch per-action commands")
        command = self.store.get_command(permit.command_uid)["payload"]
        check = lambda: self._check_start(command, permit=permit, permit_snapshot=authority[1])
        check()
        return check

    def _configuration_poll(self, now):
        if not self._identity_ready(self.boot.current_boot(now)):
            return
        app = self.store.get_state("native_configuration_application")
        if self.store.get_work_slot() is not None:
            return
        if not app:
            self._configuration_reload_poll(now)
            return
        row = self.store.get_configuration(app)
        if row is None:
            raise ValueError("original configuration disappeared")
        if row["state"] == "APPLIED":
            self.store.set_state("native_configuration_application", "")
            return
        candidate = NativeMcuConfiguration.from_cloud_payload(row["payload"])
        boot_id = self.boot.current_boot(now)
        if not boot_id:
            return
        for index, uid in enumerate(row["part_command_uids"], 1):
            record = self.store.get_native_command(uid)
            if record is None:
                # Sequence is allocated by EdgeStore, never from a loop counter.
                name, raw = candidate.encode_part(index, application_uid=app, mcu_command_uid=uid,
                    target_mcu_boot_id=boot_id, command_sequence=1)
                values = uart.decode_payload(name, raw)
                record = self.store.prepare_native_command(name, uid, boot_id,
                    {key: value for key, value in values.items() if key not in IDENTITY_FIELDS})
            if record["mcu_boot_id"] != boot_id or record["conflict"]:
                raise JobSafetyError("MCU_CONFIGURATION_INTERRUPTED", "configuration belongs to an earlier MCU boot")
            if record["decision_outcome"] == "REJECTED":
                raise JobSafetyError("MCU_CONFIGURATION_REJECTED", "MCU rejected the original configuration")
            if record["decision_outcome"] != "ACCEPTED":
                if not record["write_claimed"]:
                    proof = self._rpc_call(("CONFIG_MAINTENANCE",) + self._dispatch_identity(record),
                        self.safety.get_mcu_maintenance_status)
                    if proof is _RPC_PENDING:
                        return
                    self._send_with_authority(record, proof)
                self.dispatcher.poll(uid, now)
                return
        # Only real acceptance of all original parts, including COMMIT, reaches
        # this path. No compatibility-mode invented CONFIG_APPLY_RESULT frame.
        identity = row["payload"]["config"]
        outcome = self.store.apply_configuration_result(dict(applicationUid=app,
            mcuCommandUid=row["part_command_uids"][-1], status="APPLIED", configVersion=identity["version"],
            contentSha256=identity["contentSha256"], mcuPayloadSha256=identity["mcuPayloadSha256"], faultCode="NONE"))
        if outcome not in {"ACCEPTED", "DUPLICATE"}:
            raise ValueError("native configuration commit differs from original custody")
        self.store.set_state("native_configuration_application", "")

    def _configuration_reload_pending(self):
        row = self.store.get_latest_applied_configuration()
        if row is None or row["payload"].get("mcuConfigurationProfile") != PROFILE:
            return False
        original = self.store.get_command(row["command_uid"])
        if original is None:
            raise ValueError("applied configuration lost its original cloud command")
        result = original["result"]
        if result is None:
            return False
        if not isinstance(result, dict):
            raise ValueError("applied configuration result is malformed")
        if "nativeConfigurationReload" not in result:
            return False
        marker = result["nativeConfigurationReload"]
        if (not isinstance(marker, dict) or set(marker) != {"state", "evidence", "evidenceSha256"}
                or marker.get("state") not in {"PREPARED", "APPLIED"}):
            raise ValueError("applied configuration reload marker is malformed")
        return marker["state"] == "PREPARED"

    def _check_configuration_reload_part(self, row, record):
        from native_configuration_reload import read
        now = self.clock()
        facts = self._fresh_facts(now)
        if (self.boot.current_boot(now) != record["mcu_boot_id"] or facts is None
                or facts["status"] != "AVAILABLE" or facts["updateLatched"]
                or facts["retainedWorkState"] != "NONE"
                or facts["lastDeliveryDoorCommand"] != "CLOSE" or facts["cleanLockPowered"]):
            raise JobSafetyError("MCU_CONTROL_NOT_READY", "fresh rebooted MCU control is required for configuration reload")
        if self.store.list_pending_configurations():
            raise JobSafetyError("MCU_CONFIGURATION_BUSY", "a new cloud configuration owns this device")
        reload = read(self.store, row["application_uid"],
            target_mcu_boot_id=record["mcu_boot_id"], device_name=self.device_name)
        if (reload is None or reload["state"] != "PREPARED"
                or record["command_uid"] not in reload["part_command_uids"]):
            raise ValueError("configuration write is not an original reboot reload part")

    def _configuration_reload_poll(self, now):
        row = self.store.get_latest_applied_configuration()
        if row is None or row["payload"].get("mcuConfigurationProfile") != PROFILE:
            return
        facts = self._fresh_facts(now)
        boot_id = self.boot.current_boot(now)
        if not boot_id or facts is None or facts["status"] != "AVAILABLE":
            return
        original_part = self.store.get_native_command(row["part_command_uids"][0])
        if original_part is None:
            raise ValueError("applied native configuration lost its original parts")
        if boot_id <= original_part["mcu_boot_id"]:
            return  # A stale pre-COMMIT facts reply in the same boot is not a reboot.
        identity = row["payload"]["config"]
        matches = (facts["appliedConfigVersion"] == identity["version"]
            and facts["appliedContentSha256"] == identity["contentSha256"]
            and facts["appliedMcuPayloadSha256"] == identity["mcuPayloadSha256"])
        if matches and not self._configuration_reload_pending():
            return
        if (facts["updateLatched"] or facts["retainedWorkState"] != "NONE"
                or facts["lastDeliveryDoorCommand"] != "CLOSE" or facts["cleanLockPowered"]):
            return
        from native_configuration_reload import prepare, complete
        reload = prepare(self.store, row["application_uid"],
            target_mcu_boot_id=boot_id, device_name=self.device_name)
        if reload["state"] == "APPLIED":
            return
        candidate = NativeMcuConfiguration.from_cloud_payload(reload["payload"])
        for index, uid in enumerate(reload["part_command_uids"], 1):
            record = self.store.get_native_command(uid)
            if record is None:
                name, raw = candidate.encode_part(index, application_uid=row["application_uid"],
                    mcu_command_uid=uid, target_mcu_boot_id=boot_id, command_sequence=1)
                values = uart.decode_payload(name, raw)
                record = self.store.prepare_native_command(name, uid, boot_id,
                    {key: value for key, value in values.items() if key not in IDENTITY_FIELDS})
            if record["decision_outcome"] == "REJECTED":
                raise JobSafetyError("MCU_CONFIGURATION_REJECTED", "MCU rejected the applied configuration reload")
            if record["decision_outcome"] != "ACCEPTED":
                if not record["write_claimed"]:
                    proof = self._rpc_call(("CONFIG_RELOAD_MAINTENANCE",) + self._dispatch_identity(record),
                        self.safety.get_mcu_maintenance_status)
                    if proof is _RPC_PENDING:
                        return
                    self._send_with_authority(record, proof)
                self.dispatcher.poll(uid, now)
                return
        complete(self.store, row["application_uid"], target_mcu_boot_id=boot_id, device_name=self.device_name)

    def _work_poll(self, now):
        slot = self.store.get_work_slot()
        if slot is None:
            self._work_query = self._handoff = self._query_start_uid = None
            self._control_wait_uid = self._control_wait_since = None
            return
        permit = self._permit(slot)
        uid = slot["context"]["start_mcu_command_uid"]
        record = self.store.get_native_command(uid)
        if record is None:
            raise ValueError("native business lost its original START")
        from native_control_failure import MARKER as CONTROL_FAILURE_MARKER
        command = self.store.get_command(permit.command_uid)
        result = command["result"] if command is not None else None
        if isinstance(result, dict) and CONTROL_FAILURE_MARKER in result:
            self._complete_control_failure(permit, uid, result[CONTROL_FAILURE_MARKER])
            return
        if permit.work_type == "BASELINE":
            self._baseline_poll(slot, permit, record, now)
            return
        start = uart.decode_payload(record["message_name"], record["payload"])
        if self._query_start_uid != uid:
            identity = {key: start[key] for key in IDENTITY_FIELDS}
            identity.update(workUid=permit.work_uid, workType="CLEAN_OPERATION" if permit.work_type == "CLEAN" else "DELIVERY_SESSION",
                portNo=start["portNo"])
            self._work_query = McuWorkQuery(self.store, self.transport.write, identity)
            self._query_start_uid, self._handoff = uid, None
        # A new process never adds retained starts to this live-only set.
        if (uid in self._live_starts and not record["write_claimed"]
                and self.boot.current_boot(now) == record["mcu_boot_id"]
                and self._identity_ready(record["mcu_boot_id"])
                and self._photos_ready(permit, before=True, now=now)):
            proof = self._start_authorization(permit, record)
            if proof is not _RPC_PENDING:
                self._send_with_authority(record, proof)
                self._live_starts.discard(uid)
                self._start_grants.discard(uid)
        if record["write_claimed"]:
            self.dispatcher.poll(uid, now)
        observation = self._work_query.observation(now)
        if observation and observation["status"] == "RESULT_HELD" and self._handoff is None:
            self._handoff = McuResultHandoff(self.store, self.transport.write,
                dict(mcuBootId=start["targetMcuBootId"], resultSequence=observation["resultSequence"],
                    resultDigestSha256=observation["resultDigestSha256"], workUid=permit.work_uid))
        if self._handoff:
            self._handoff.poll(now)
        self._work_query.poll(now)
        decision = self.store.evaluate_native_work_recovery(permit, uid,
            current_boot=lambda: self.boot.current_boot(self.clock()))
        if decision["status"] == "RECOVERY_INTENT_RECORDED":
            if permit.work_type == "DELIVERY":
                # A fresh, saved newer boot is required. The archive transaction
                # checks again for a complete final packet; process weights alone
                # are never used to manufacture a successful delivery.
                decision = self.store.archive_native_delivery_issue(permit, uid, device_name=self.device_name,
                    current_boot=lambda: self.boot.current_boot(self.clock()))
            else:
                # The rebooted MCU has abandoned its volatile clean execution.
                # Reuse the exact failed-permit convergence, but retain a local
                # bag/tare interlock for the separate human reconciliation.
                from native_control_failure import MCU_RESTART_REASON
                pending = self.store.prepare_native_control_failure(
                    permit,
                    uid,
                    device_name=self.device_name,
                    stage="FAILED",
                    reason=MCU_RESTART_REASON,
                )
                if pending.get("state") == "PREPARED":
                    self._live_starts.discard(uid)
                    self._start_grants.discard(uid)
                return
        if decision["status"] == "DELIVERY_ISSUE_ARCHIVED":
            self._live_starts.discard(uid)
            self._start_grants.discard(uid)
            if self._report_issue(permit.work_uid):
                self._complete_delivery_issue(permit, uid)
            return
        if decision["status"] == "COMPLETE_RESULT_AVAILABLE":
            from native_result_evidence import terminal_failure_disposition
            value = uart.decode_payload("WORK_RESULT", decision["result"]["payload"])
            if terminal_failure_disposition(value) is not None:
                pending = self.store.prepare_native_result_failure(
                    permit,
                    uid,
                    device_name=self.device_name,
                )
                if pending.get("state") == "PREPARED":
                    failed = self.store.get_command(permit.command_uid)
                    self._complete_control_failure(
                        permit,
                        uid,
                        failed["result"][CONTROL_FAILURE_MARKER],
                    )
                return
            if self.photo is not None and not slot["context"].get("native_close_photos_requested"):
                self._queue_photos(permit, before=False)
                context = slot["context"] | {"native_close_photos_requested": True}
                self.store.update_work_context(permit.work_uid, context)
            if not self._photos_ready(permit, before=False, now=self.clock()):
                return
            report = self.store.get_native_result_report(permit, uid, device_name=self.device_name)
            if report is None:
                snapshot = self._rpc_call(("REPORT_PERMIT", permit.permit_uid, uid),
                    lambda: self.safety.get_job_permit(permit.permit_uid))
                if snapshot is _RPC_PENDING:
                    return
                report = self.reporter.prepare(permit, uid, permit_snapshot=snapshot)
            if report["state"] == "REPORT_CREATED":
                confirmation = self.store.get_native_result_confirmation(permit, uid, device_name=self.device_name)
                if confirmation is None or confirmation["outcome"] != "BUSINESS_APPLIED":
                    return  # Waiting for cloud must not poll the permanent job RPC on every UART tick.
                self._complete_reported_result(permit, uid, decision)

    @staticmethod
    def _baseline_scope(record):
        values = uart.decode_payload("MEASURE_BASELINE", record["payload"])
        return {
            "mcuCommandUid": values["mcuCommandUid"],
            "commandDigestSha256": values["commandDigestSha256"],
            "targetMcuBootId": values["targetMcuBootId"],
            "commandSequence": values["commandSequence"],
            "workUid": values["measurementUid"],
            "workType": "BASELINE_MEASUREMENT",
            "portNo": values["portNo"],
            "eventMessageType": "BASELINE_MEASUREMENT_RESULT",
            "stepSequence": 0,
            "configVersion": values["configVersion"],
        }

    def _baseline_receipt(self, record):
        scope = self._baseline_scope(record)
        raw_scope = uart.encode_payload(
            "QUERY_PROCESS_EVENT",
            scope | {"queryId": 1},
        )[8:]
        return self.store.get_native_process_receipt(raw_scope)

    def _baseline_poll(self, slot, permit, record, now):
        """Dispatch once, then use only exact query/receipt custody to finish."""
        uid = record["command_uid"]
        scope = self._baseline_scope(record)
        if self._query_start_uid != uid or not isinstance(
            self._handoff, McuProcessEventHandoff
        ):
            self._work_query = None
            self._handoff = McuProcessEventHandoff(
                self.store,
                self.transport.write,
                scope,
            )
            self._query_start_uid = uid
        if (
            uid in self._live_starts
            and not record["write_claimed"]
            and self.boot.current_boot(now) == record["mcu_boot_id"]
            and self._identity_ready(record["mcu_boot_id"])
        ):
            proof = self._start_authorization(permit, record)
            if proof is not _RPC_PENDING:
                self._send_with_authority(record, proof)
                self._live_starts.discard(uid)
                self._start_grants.discard(uid)
        if record["write_claimed"]:
            self.dispatcher.poll(uid, now)
            had_receipt = self._baseline_receipt(record) is not None
            query_id = self._handoff.poll(now)
            if (
                query_id is not None
                and slot["context"].get("start_runtime_instance_uid")
                != self._runtime_instance_uid
            ):
                if had_receipt and slot["context"].get(
                    "nativeBaselineMcuRelease"
                ) is None:
                    # The first inherited query can legitimately observe HELD
                    # and send PROCESS_EVENT_SAVED.  The MCU only exposes
                    # RELEASED to the following query, while control-failure
                    # polling runs before that query on the next tick.  Keep a
                    # fixed, non-renewing window for both query/reply rounds.
                    self._baseline_release_recovery_deadlines.setdefault(
                        uid,
                        now + (2 * self.timeout_ms),
                    )
                elif record["decision_outcome"] == "ACCEPTED":
                    # A process that inherited an already-accepted command
                    # gets one query-only recovery window even when the durable
                    # total deadline elapsed while Pi was down. Starting this
                    # bound at the actual committed query prevents an idle/new
                    # process from extending the business and never authorizes
                    # MEASURE replay.
                    self._baseline_recovery_deadlines.setdefault(
                        uid,
                        now + self.timeout_ms,
                    )
        receipt = self._baseline_receipt(record)
        if receipt is None:
            return
        command = self.store.get_command(permit.command_uid)
        result = command.get("result") if command is not None else None
        marker = (
            result.get("nativeBaselineCompletion")
            if isinstance(result, dict)
            else None
        )
        event_uid = (
            marker.get("evidence", {}).get("eventUid")
            if isinstance(marker, dict)
            else None
        ) or str(uuid.uuid4())
        pending = self.store.prepare_native_baseline_completion(
            permit,
            uid,
            device_name=self.device_name,
            event_uid=event_uid,
        )
        if pending["state"] != "PREPARED":
            return
        current_slot = self.store.get_work_slot()
        if current_slot is None:
            raise ValueError("native baseline released before permanent completion")
        release_proof = current_slot["context"].get(
            "nativeBaselineMcuRelease"
        )
        if release_proof is None:
            observed = self._handoff.observation(now)
            current_boot = self.boot.current_boot(now)
            if observed is not None and observed.get("status") == "RELEASED":
                release_proof = self.store.confirm_native_baseline_mcu_release(
                    permit,
                    uid,
                    release_observation=observed,
                )
            elif current_boot is not None and current_boot != record["mcu_boot_id"]:
                # A newly recognized MCU boot proves that the prior boot's
                # volatile held process slot no longer exists. The exact local
                # result remains the successful authority.
                release_proof = self.store.confirm_native_baseline_mcu_release(
                    permit,
                    uid,
                    replacement_mcu_boot_id=current_boot,
                )
            else:
                return
        digest = pending["evidenceSha256"]

        def complete():
            self.safety.complete_job(
                permit,
                completion_uid=permit.command_uid,
                outcome="SUCCEEDED",
                completion_digest_sha256=digest,
            )
            return self.safety.get_job_permit(permit.permit_uid)

        snapshot = self._rpc_call(
            ("BASELINE_COMPLETE", permit.permit_uid, uid, digest),
            complete,
        )
        if snapshot is _RPC_PENDING:
            return
        self.store.apply_native_baseline_completion(
            permit,
            uid,
            device_name=self.device_name,
            permit_snapshot=snapshot,
        )
        self._live_starts.discard(uid)
        self._start_grants.discard(uid)
        self._baseline_recovery_deadlines.pop(uid, None)
        self._baseline_release_recovery_deadlines.pop(uid, None)
        self._query_start_uid = None
        self._handoff = None

    @staticmethod
    def _permit_snapshot_matches(permit, snapshot):
        expected = dict(permitUid=permit.permit_uid, commandUid=permit.command_uid,
            workUid=permit.work_uid, workType=permit.work_type,
            requestDigestSha256=permit.request_digest_sha256)
        return isinstance(snapshot, dict) and all(snapshot.get(key) == value for key, value in expected.items())

    def _read_permit_or_missing(self, permit):
        try:
            return self.safety.get_job_permit(permit.permit_uid)
        except JobSafetyError as error:
            if error.code == "JOB_PERMIT_NOT_FOUND":
                return {"state": "NOT_FOUND", "permitUid": permit.permit_uid}
            raise

    def _complete_control_failure(self, permit, uid, marker):
        """Converge the original permanent permit, then release one exact local slot."""
        if (not isinstance(marker, dict) or marker.get("state") not in {"PREPARED", "APPLIED"}
                or not isinstance(marker.get("evidence"), dict)):
            raise ValueError("native control failure marker is malformed")
        if marker["state"] == "APPLIED":
            raise ValueError("an applied native control failure still owns a work slot")
        digest = marker.get("evidenceSha256")
        key = (permit.permit_uid, uid, digest)
        snapshot = self._rpc_call(("CONTROL_FAILURE_READ",) + key,
            lambda: self._read_permit_or_missing(permit))
        if snapshot is _RPC_PENDING:
            return
        if snapshot.get("state") != "NOT_FOUND" and not self._permit_snapshot_matches(permit, snapshot):
            raise ValueError("native control failure permanent permit identity conflicts")
        state = snapshot.get("state")
        if state == "GRANTED":
            if marker["evidence"].get("writeClaimed") is not False:
                raise ValueError("a write-claimed START cannot retain a granted permit")
            def abandon():
                self.safety.abandon_job(permit, disposition_uid=permit.command_uid,
                    evidence_sha256=digest)
                return self.safety.get_job_permit(permit.permit_uid)
            snapshot = self._rpc_call(("CONTROL_FAILURE_ABANDON",) + key, abandon)
            if snapshot is _RPC_PENDING:
                return
        elif state == "ACTIVE":
            def complete():
                self.safety.complete_job(permit, completion_uid=permit.command_uid,
                    outcome="FAILED", completion_digest_sha256=digest)
                return self.safety.get_job_permit(permit.permit_uid)
            snapshot = self._rpc_call(("CONTROL_FAILURE_COMPLETE",) + key, complete)
            if snapshot is _RPC_PENDING:
                return
        elif state not in {"NOT_FOUND", "ABANDONED", "COMPLETED"}:
            raise ValueError("native control failure permanent permit has an unsupported state")
        self.store.apply_native_control_failure(permit, uid,
            device_name=self.device_name, permit_snapshot=snapshot)
        self._live_starts.discard(uid)
        self._start_grants.discard(uid)
        self._baseline_recovery_deadlines.pop(uid, None)
        self._baseline_release_recovery_deadlines.pop(uid, None)
        self._control_wait_uid = self._control_wait_since = None

    def _latch_control_communication_fault(self, *, reason, mcu_boot_id):
        current = self.store.get_state("native_blocking_fault")
        if not current:
            self.store.set_state("native_blocking_fault", reason)
        # A different already-blocking fault must not be erased.  The UART
        # journal is independent and still makes this new failure visible.
        if self.store.get_active_edge_fault("UART", "UART_PROTOCOL") is None:
            disposition = self.store.observe_fault_and_create_event(device_name=self.device_name,
                component="UART", fault_code="UART_PROTOCOL", severity="BLOCK_DEVICE",
                mcu_boot_id=mcu_boot_id or None,
                detail=dict(profile="native-control-communication-v1", reasonCode=reason,
                    automaticRecovery=False))
            if disposition not in {"ACCEPTED", "DUPLICATE"}:
                raise RuntimeError("native communication fault could not be persisted")

    def _control_failure_poll(self, now, *, link_unavailable):
        """Start one durable exit only after the configured communication deadline."""
        from native_control_failure import (
            MARKER,
            BASELINE_RESULT_REASON,
            COMMUNICATION_REASON,
            MCU_RESTART_REASON,
            RESTART_REASON,
        )
        slot = self.store.get_work_slot()
        if slot is None:
            self._control_wait_uid = self._control_wait_since = None
            if link_unavailable:
                self._latch_control_communication_fault(reason=COMMUNICATION_REASON,
                    mcu_boot_id=self._mcu_boot_id)
            return
        permit = self._permit(slot)
        uid = slot["context"]["start_mcu_command_uid"]
        record = self.store.get_native_command(uid)
        command = self.store.get_command(permit.command_uid)
        result = command["result"] if command is not None else None
        if isinstance(result, dict) and MARKER in result:
            return
        if permit.work_type == "BASELINE" and (
            slot["work_state"] == "COMPLETING"
            or self._baseline_receipt(record) is not None
        ):
            # A committed exact result always wins over a later communication
            # deadline or boot observation. If exact MCU RELEASED custody
            # cannot be obtained in bounded time, retain the result/slot and
            # expose the communication fault; never downgrade the command to
            # failure merely to release occupancy.
            release = slot["context"].get("nativeBaselineMcuRelease")
            if release is None:
                release_deadline = (
                    self.store.native_baseline_release_deadline_status(
                        uid,
                        communication_timeout_ms=self.timeout_ms,
                    )
                )
                observed = (
                    self._handoff.observation(now)
                    if self._query_start_uid == uid
                    and isinstance(self._handoff, McuProcessEventHandoff)
                    else None
                )
                inherited = (
                    slot["context"].get("start_runtime_instance_uid")
                    != self._runtime_instance_uid
                )
                recovery_deadline = (
                    self._baseline_release_recovery_deadlines.get(uid)
                )
                current_boot = self.boot.current_boot(now)
                recognized_replacement_boot = (
                    current_boot is not None
                    and current_boot != record["mcu_boot_id"]
                )
                recovery_pending = (
                    inherited
                    and not link_unavailable
                    and (
                        recovery_deadline is None
                        or now < recovery_deadline
                    )
                )
                if (
                    (link_unavailable or release_deadline["expired"])
                    and not recovery_pending
                    and not (
                        isinstance(observed, dict)
                        and observed.get("status") == "RELEASED"
                    )
                    and not recognized_replacement_boot
                ):
                    self._latch_control_communication_fault(
                        reason=COMMUNICATION_REASON,
                        mcu_boot_id=record["mcu_boot_id"],
                    )
            return
        if permit.work_type == "BASELINE" and record["decision_outcome"] == "REJECTED":
            pending = self.store.prepare_native_control_failure(
                permit,
                uid,
                device_name=self.device_name,
                stage="REJECTED",
                reason=record["decision_error"],
            )
            if pending.get("state") == "PREPARED":
                self._live_starts.discard(uid)
                self._start_grants.discard(uid)
            return
        # The live-only dispatch token is deliberately not persisted.  When a
        # new Pi process finds an unclaimed START, SQLite proves that no serial
        # bytes were ever eligible to leave; close it explicitly instead of
        # replaying it or retaining the device forever.
        if (not record["write_claimed"]
                and slot["context"].get("start_runtime_instance_uid") != self._runtime_instance_uid):
            pending = self.store.prepare_native_control_failure(permit, uid,
                device_name=self.device_name, stage="PRE_START_FAILED", reason=RESTART_REASON)
            if pending.get("state") == "PREPARED":
                self._start_grants.discard(uid)
            return
        exact_command_timeout = False
        if record["write_claimed"] and record["decision_outcome"] is None:
            if self._control_wait_uid != uid:
                self._control_wait_uid, self._control_wait_since = uid, now
            exact_command_timeout = now - self._control_wait_since >= self.timeout_ms
        else:
            self._control_wait_uid = self._control_wait_since = None
        baseline_mcu_restarted = (
            permit.work_type == "BASELINE"
            and record["write_claimed"]
            and self.boot.current_boot(now) is not None
            and self.boot.current_boot(now) != record["mcu_boot_id"]
        )
        baseline_result_timeout = False
        if (
            permit.work_type == "BASELINE"
            and record["decision_outcome"] == "ACCEPTED"
        ):
            deadline = self.store.native_baseline_result_deadline_status(
                uid,
                communication_timeout_ms=self.timeout_ms,
            )
            baseline_result_timeout = deadline["expired"]
            if (
                baseline_result_timeout
                and not baseline_mcu_restarted
                and slot["context"].get("start_runtime_instance_uid")
                != self._runtime_instance_uid
            ):
                recovery_deadline = self._baseline_recovery_deadlines.get(uid)
                if recovery_deadline is None or now < recovery_deadline:
                    # _work_poll runs later in this foreground turn and starts
                    # the exact process query. Only its actual send creates the
                    # bounded grace deadline above.
                    return
        if (
            not link_unavailable
            and not exact_command_timeout
            and not baseline_mcu_restarted
            and not baseline_result_timeout
        ):
            return
        stage = "FAILED" if record["write_claimed"] else "PRE_START_FAILED"
        reason = (
            MCU_RESTART_REASON
            if baseline_mcu_restarted
            else COMMUNICATION_REASON
            if link_unavailable or exact_command_timeout
            else BASELINE_RESULT_REASON
        )
        pending = self.store.prepare_native_control_failure(permit, uid,
            device_name=self.device_name, stage=stage, reason=reason)
        if pending.get("state") != "PREPARED":
            return  # A complete final packet or an earlier terminal policy wins the race.
        self._live_starts.discard(uid)
        self._start_grants.discard(uid)
        if not baseline_mcu_restarted:
            self._latch_control_communication_fault(reason=COMMUNICATION_REASON,
                mcu_boot_id=record["mcu_boot_id"])

    def _complete_reported_result(self, permit, uid, decision):
        from native_business_completion import NORMAL_FINISH, AVAILABLE
        from native_result_evidence import terminal_weight_failure_candidate
        result = uart.decode_payload("WORK_RESULT", decision["result"]["payload"])
        normal = (result["finishReason"] in NORMAL_FINISH
            and all(result[key + "Kind"] in AVAILABLE for key in ("initial", "final")))
        if not normal and not terminal_weight_failure_candidate(result):
            return  # Other failure policies cannot borrow this result's completion.
        key = (permit.permit_uid, uid)
        pending = self._prepared_completions.get(key)
        if pending is None:
            snapshot = self._rpc_call(("COMPLETE_PREPARE",) + key,
                lambda: self.safety.get_job_permit(permit.permit_uid))
            if snapshot is _RPC_PENDING:
                return
            pending = self.store.prepare_native_business_completion(permit, uid,
                device_name=self.device_name, permit_snapshot=snapshot)
            if pending["state"] != "PREPARED":
                return
            self._prepared_completions[key] = pending
        digest = pending["evidenceSha256"]
        def complete():
            self.safety.complete_job(permit, completion_uid=permit.command_uid,
                outcome=pending["completionOutcome"], completion_digest_sha256=digest)
            return self.safety.get_job_permit(permit.permit_uid)
        snapshot = self._rpc_call(("COMPLETE_APPLY",) + key + (digest,), complete)
        if snapshot is _RPC_PENDING:
            return
        self.store.apply_native_business_completion(permit, uid,
            device_name=self.device_name, permit_snapshot=snapshot)
        self._prepared_completions.pop(key, None)

    def _report_issue(self, work_uid):
        if work_uid in self._reported_issues:
            return True
        created = self.issue_reporter.prepare(work_uid, limit=ISSUE_REPORT_BATCH)
        if len(created) == ISSUE_REPORT_BATCH:
            return False  # Limit new events per call; remaining evidence continues next time.
        self._reported_issues.add(work_uid)
        self._issue_reports_pending.discard(work_uid)
        return True

    def _issue_report_poll(self):
        # A crash can happen after late bytes commit but before their OneNet
        # event exists. Scan existing archives once per process, one at a time;
        # new late packets also enqueue their exact original work in memory.
        # No active slot is needed, and no historical result is projected onto
        # the new work/bag. The original event uniqueness provides deduplication.
        if self._issue_reports_pending:
            self._report_issue(next(iter(self._issue_reports_pending)))
        elif self._issue_report_cursor is not None:
            page = self.store.list_native_delivery_issue_work_uids(
                after_work_uid=self._issue_report_cursor, limit=1)
            if not page:
                self._issue_report_cursor = None
            elif self._report_issue(page[0]):
                self._issue_report_cursor = page[0]

    def _complete_delivery_issue(self, permit, uid):
        confirmation = self.store.get_native_delivery_issue_confirmation(permit.work_uid,
            device_name=self.device_name)
        if confirmation is None or confirmation["outcome"] != "BUSINESS_APPLIED":
            return  # Platform transport ACK / unrelated evidence ACK is not enough.
        key = (permit.permit_uid, uid)
        pending = self._prepared_issue_completions.get(key)
        if pending is None:
            snapshot = self._rpc_call(("ISSUE_COMPLETE_PREPARE",) + key,
                lambda: self.safety.get_job_permit(permit.permit_uid))
            if snapshot is _RPC_PENDING:
                return
            pending = self.store.prepare_native_issue_completion(permit, uid,
                device_name=self.device_name, permit_snapshot=snapshot)
            if pending["state"] != "PREPARED":
                return
            self._prepared_issue_completions[key] = pending
        digest = pending["evidenceSha256"]
        def complete():
            self.safety.complete_job(permit, completion_uid=permit.command_uid,
                outcome="CANCELLED", completion_digest_sha256=digest)
            return self.safety.get_job_permit(permit.permit_uid)
        snapshot = self._rpc_call(("ISSUE_COMPLETE_APPLY",) + key + (digest,), complete)
        if snapshot is _RPC_PENDING:
            return
        self.store.apply_native_issue_completion(permit, uid,
            device_name=self.device_name, permit_snapshot=snapshot)
        self._prepared_issue_completions.pop(key, None)

    def _scale_health_poll(self, now):
        """Report current unreadable scale data; a fresh later read clears only this fault.

        This does not cancel MCU sampling early, change any completed result,
        release occupancy, or clear manual communication/storage faults. The
        current business still reaches its own five-second measurement result.
        """
        facts = self._fresh_facts(now)
        if facts is None or facts["status"] != "AVAILABLE" or not facts["appliedConfigVersion"]:
            return
        fault = self.store.get_active_edge_fault("WEIGHT_SENSOR", "WEIGHT_SENSOR", 1)
        if self._fresh_weight(facts, now) is not None:
            self._scale_wait = None
            if fault is None:
                return
            detail = json.loads(fault["detail_json"] or "{}")
            if detail.get("profile") != "native-scale-read-v1":
                return  # Do not clear an unrelated legacy/manual diagnostic.
            boot = facts["currentMcuBootId"]
            newer = (boot > detail["mcuBootId"] or (boot == detail["mcuBootId"]
                and (facts["scaleCapturedUptimeMs"], facts["scaleAttemptSequence"])
                > (detail["capturedUptimeMs"], detail["attemptSequence"])))
            if not newer:
                return
            disposition = self.store.recover_fault_and_create_event(device_name=self.device_name,
                fault_uid=fault["fault_uid"], component="WEIGHT_SENSOR", fault_code="WEIGHT_SENSOR", port_no=1,
                recovery_evidence=f"NATIVE_VALID_SCALE:{boot}:{facts['scaleAttemptSequence']}:{facts['scaleCapturedUptimeMs']}",
                mcu_boot_id=boot)
        else:
            if fault is not None:
                return
            if facts["scaleReadStatus"] == "NOT_OBSERVED":
                identity = (facts["currentMcuBootId"], facts["appliedConfigVersion"])
                if self._scale_wait is None or self._scale_wait[:2] != identity:
                    self._scale_wait = (*identity, now)
                if now - self._scale_wait[2] < 5000:
                    return  # Still no new admission; allow initial acquisition to report its status.
            disposition = self.store.observe_fault_and_create_event(device_name=self.device_name,
                component="WEIGHT_SENSOR", fault_code="WEIGHT_SENSOR", severity="BLOCK_PORT", port_no=1,
                mcu_boot_id=facts["currentMcuBootId"], detail=dict(profile="native-scale-read-v1",
                    mcuBootId=facts["currentMcuBootId"], capturedUptimeMs=facts["scaleCapturedUptimeMs"],
                    attemptSequence=facts["scaleAttemptSequence"], readStatus=facts["scaleReadStatus"]))
        if disposition not in {"ACCEPTED", "DUPLICATE"}:
            raise RuntimeError("native scale health could not persist its exact fault transition")

    def _environment_configuration(self, port_no=1):
        applied = self.store.get_latest_applied_configuration()
        payload = applied.get("payload") if isinstance(applied, dict) else None
        if not isinstance(payload, dict):
            return None, None
        port = next((candidate for candidate in payload.get("ports", [])
            if isinstance(candidate, dict) and candidate.get("portNo") == port_no), None)
        device = payload.get("deviceConfig")
        return port, device if isinstance(device, dict) else None

    def _environment_warning_transition(self, *, component, fault_code,
                                        evidence_key, abnormal_reason,
                                        facts):
        if self._environment_fact_keys.get(component) == evidence_key:
            return
        self._environment_fact_keys[component] = evidence_key
        fault = self.store.get_active_edge_fault(component, fault_code, 1)
        if abnormal_reason is not None:
            if fault is not None:
                return
            disposition = self.store.observe_fault_and_create_event(
                device_name=self.device_name,
                component=component,
                fault_code=fault_code,
                severity="WARNING",
                port_no=1,
                mcu_boot_id=facts["currentMcuBootId"],
                detail={
                    "profile": "native-environment-observation-v1",
                    "reasonCode": abnormal_reason,
                    "mcuBootId": facts["currentMcuBootId"],
                    "capturedUptimeMs": evidence_key[1],
                },
            )
        else:
            if fault is None:
                return
            try:
                detail = json.loads(fault["detail_json"] or "{}")
            except (TypeError, ValueError):
                return
            if detail.get("profile") != "native-environment-observation-v1":
                return
            disposition = self.store.recover_fault_and_create_event(
                device_name=self.device_name,
                fault_uid=fault["fault_uid"],
                component=component,
                fault_code=fault_code,
                port_no=1,
                recovery_evidence=(
                    f"NATIVE_ENVIRONMENT_NORMAL:{facts['currentMcuBootId']}:{evidence_key[1]}"
                ),
                mcu_boot_id=facts["currentMcuBootId"],
            )
        if disposition not in {"ACCEPTED", "DUPLICATE"}:
            raise RuntimeError("native environment warning transition was not persisted")

    def _environment_health_poll(self, now):
        """Expose optional sensor warnings without changing admission policy."""
        facts = self._fresh_facts(now)
        if facts is None or facts.get("status") != "AVAILABLE":
            return
        port, device = self._environment_configuration()
        if port is None or device is None or not facts.get("appliedConfigVersion"):
            return
        boot_id = facts["currentMcuBootId"]

        smoke_state = facts.get("smokeObservationState")
        smoke_enabled = device.get("smokeMonitoringEnabled") is True
        smoke_reason = None
        if smoke_enabled:
            if smoke_state == "ALARM":
                smoke_reason = "SMOKE_ALARM"
            elif smoke_state == "UNAVAILABLE":
                smoke_reason = "SMOKE_SENSOR_UNAVAILABLE"
            elif smoke_state == "NOT_OBSERVED":
                identity = (boot_id, facts["appliedConfigVersion"])
                waiting = self._environment_waits.get("SMOKE_SENSOR")
                if waiting is None or waiting[:2] != identity:
                    self._environment_waits["SMOKE_SENSOR"] = (*identity, now)
                    waiting = self._environment_waits["SMOKE_SENSOR"]
                if now - waiting[2] < 5000:
                    smoke_state = None
                else:
                    smoke_reason = "SMOKE_NOT_OBSERVED"
            else:
                self._environment_waits.pop("SMOKE_SENSOR", None)
            if smoke_state is not None:
                self._environment_warning_transition(
                    component="SMOKE_SENSOR",
                    fault_code="SMOKE_SENSOR",
                    evidence_key=(
                        boot_id,
                        facts.get("smokeObservedUptimeMs", 0),
                        smoke_state,
                    ),
                    abnormal_reason=smoke_reason,
                    facts=facts,
                )

        configured_kind = port.get("fullnessSensorKind")
        observed_kind = facts.get("fullnessObservationKind")
        fullness_status = facts.get("fullnessReadStatus")
        fullness_reason = None
        if fullness_status == "VALID" and observed_kind == configured_kind:
            if observed_kind == "DIGITAL_INFRARED":
                if facts.get("fullnessInfraredBlocked") is True:
                    fullness_reason = "FULLNESS_BLOCKED"
            elif observed_kind == "ULTRASONIC":
                distance = facts.get("fullnessDistanceMm")
                threshold = port.get("fullnessDistanceThresholdMm")
                if type(distance) is not int or type(threshold) is not int:
                    fullness_reason = "FULLNESS_SENSOR_UNAVAILABLE"
                elif distance <= threshold:
                    fullness_reason = "FULLNESS_BLOCKED"
            else:
                fullness_reason = "FULLNESS_SENSOR_UNAVAILABLE"
            self._environment_waits.pop("FULLNESS_SENSOR", None)
        elif fullness_status == "NOT_OBSERVED":
            identity = (boot_id, facts["appliedConfigVersion"])
            waiting = self._environment_waits.get("FULLNESS_SENSOR")
            if waiting is None or waiting[:2] != identity:
                self._environment_waits["FULLNESS_SENSOR"] = (*identity, now)
                waiting = self._environment_waits["FULLNESS_SENSOR"]
            if now - waiting[2] < 5000:
                return
            fullness_reason = "FULLNESS_NOT_OBSERVED"
        else:
            self._environment_waits.pop("FULLNESS_SENSOR", None)
            fullness_reason = "FULLNESS_SENSOR_UNAVAILABLE"
        self._environment_warning_transition(
            component="FULLNESS_SENSOR",
            fault_code="FULLNESS_SENSOR_DIAGNOSTIC",
            evidence_key=(
                boot_id,
                facts.get("fullnessCapturedUptimeMs", 0),
                observed_kind,
                fullness_status,
                facts.get("fullnessInfraredBlocked"),
                facts.get("fullnessDistanceMm"),
            ),
            abnormal_reason=fullness_reason,
            facts=facts,
        )

    def poll(self):
        if self.transport is None:
            raise RuntimeError("native business UART has not been opened by its owner")
        now = self.clock()
        for frame in self.transport.poll(now):
            decoded = uart.decode_frame(frame, sender_role="MCU")
            accepted = self.boot.accept_frame(frame, now) or self.dispatcher.accept_frame(frame, now)
            if self.identity_query and self.identity_query.accept_frame(frame, now):
                accepted = True
            if self.identity_query and self.identity_query.conflict_payload is not None:
                self._clear_identity()
            if decoded["messageName"] == "DEVICE_ENTRY_URL_APPLY_RESULT":
                accepted = (
                    self._accept_device_entry_url_result(decoded["payload"])
                    or accepted
                )
            if self.facts_query and self.facts_query.accept_frame(frame, now):
                self._facts = self.facts_query.observation(now)
                self._facts_requested_at = self.facts_query.request_started_ms
                accepted = True
            if self.facts_query and self.facts_query.conflict_payload is not None:
                self._facts = self._facts_requested_at = None
            if self._work_query:
                accepted = self._work_query.accept_frame(frame, now) or accepted
            handed_off = self._handoff.accept_frame(frame, now) if self._handoff else False
            accepted = handed_off or accepted
            if not handed_off and decoded["messageName"] == "WORK_RESULT":
                # Preserve late evidence even without a live transfer; do not
                # acknowledge or project it onto another active business.
                self.store.save_native_mcu_result(decoded["payload"])
            if decoded["messageName"] == "WORK_RESULT":
                work_uid = uart.decode_payload("WORK_RESULT", decoded["payload"])["workUid"]
                if self.store.get_native_delivery_issue(work_uid) is not None:
                    self._reported_issues.discard(work_uid)
                    self._issue_reports_pending.add(work_uid)
            if accepted:
                self._last_alive = now
        boot_id = self.boot.current_boot(now)
        if boot_id and boot_id != self._mcu_boot_id:
            self._clear_identity()
            self._mcu_boot_id = boot_id
            self.identity_query = McuDeviceIdentityQuery(
                self.store,
                self.transport.write,
                target_mcu_boot_id=boot_id,
            )
            self.facts_query = McuDeviceFactsQuery(self.store, self.transport.write,
                target_mcu_boot_id=boot_id, port_no=1, interval_ms=NATIVE_DEVICE_CONSTANTS["weightPollIntervalMs"])
            self._facts = self._facts_requested_at = None
            self._refresh_runtime_observation(now)
        now = self.clock()
        self._accept_identity_observation(now)
        link_unavailable = now - (self._last_alive if self._last_alive is not None else self._opened_at) >= self.timeout_ms
        self._control_failure_poll(now, link_unavailable=link_unavailable)
        self._scale_health_poll(self.clock())
        self._environment_health_poll(self.clock())
        self._configuration_poll(self.clock())
        self._device_entry_url_poll(self.clock())
        self._work_poll(self.clock())
        self._issue_report_poll()
        if self.identity_query and not self._identity_ready(self._mcu_boot_id):
            self.identity_query.poll(self.clock())
        if self.facts_query:
            self.facts_query.poll(self.clock())
        self.boot.poll(self.clock())
        self._refresh_runtime_observation(self.clock())
        observable = self.current_runtime_observation()
        return dict(uartState=self.uart_state, mcuBootId=self._mcu_boot_id,
            activeWorkUid=(self.store.get_work_slot() or {}).get("work_uid"),
            firmwareIdentityHex=(observable["mcuFirmwareIdentity"] or {}).get("firmwareIdentityHex"),
            deviceFactsFingerprint=(hashlib.sha256(json.dumps(observable["deviceFacts"],
                sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                if observable["deviceFacts"] is not None else None))

    def accept_photo_upload_grant(self, command):
        if self.photo is None:
            raise RuntimeError("photo manager unavailable")
        # Can run on the cloud thread: no native UART access here.
        return self.photo.offer_upload_grant(command)

    def _unsupported(self, command):
        raise JobSafetyError("NATIVE_COMMAND_NOT_SUPPORTED", "this command has not been wired for native MCU")

    quarantine_delivery_recovery = _unsupported
    start_fullness_command = _unsupported
    end_clean_before_unlock_command = _unsupported
    resume_clean_command = _unsupported

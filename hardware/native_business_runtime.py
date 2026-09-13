"""Native normal-business owner used by the explicitly selected uart-v2 gateway.

MCU owns the process. This owner sends one START, queries the original work,
commits its complete result and consumes the original backend confirmation.
It never runs the old per-action recovery driver or converts native frames to v1.
Serial creation, polling and commands must all run on the same foreground thread.
"""
from dataclasses import asdict, fields
import os
from time import monotonic_ns
import uuid

from job_safety import JobPermit, JobSafetyError, PermanentJobSafety, command_request_digest
from mcu_configuration import NativeMcuConfiguration, NATIVE_DEVICE_CONSTANTS, NATIVE_PORT_CONSTANTS
from camera_capture import CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS
from photo_manager import DELIVERY_OPEN_SLOTS, DELIVERY_CLOSE_SLOTS, CLEAN_OPEN_SLOTS, CLEAN_CLOSE_SLOTS
from mcu_result_handoff import McuResultHandoff
from mcu_session import McuBootSession, McuCommandDispatcher
from mcu_work_query import McuDeviceFactsQuery, McuWorkQuery
from native_result_report import NativeResultReporter, check_job_permit
from native_job_rpc import NativeJobRpc
from onenet_wire import validate_command_envelope
from uart2_transport import NativeUartTransport
import uart2_protocol as uart


IDENTITY_FIELDS = {"mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence"}
PROFILE = "UART_V2_SIMPLIFIED"
PHOTO_WAIT_MS = int(CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS * 1000) + 1000
FACTS_MAXIMUM_AGE_MS = NATIVE_PORT_CONSTANTS["weightMaximumSampleAgeMs"]
_RPC_PENDING = object()


class NativeBusinessRuntime:
    native_protocol = 2
    compatibility_mode = False
    is_simulated = False
    verified_firmware_identity = None  # Not inferred from a successful UART probe.
    _mcu_firmware_version = ""
    _mcu_capability = 0

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
        self._port = None
        self._mcu_boot_id = 0
        self._last_alive = None
        self._opened_at = None
        self._work_query = self._handoff = None
        self._query_start_uid = None
        self._live_starts = set()
        self._facts = None
        self._facts_requested_at = None
        self._photo_deadlines = {}
        self._rpc = None
        self._start_grants = set()
        self._prepared_completions = {}
        self._dispatch_authority = None
        self.reporter = NativeResultReporter(store, safety, device_name=device_name, photo_manager=photo_manager)

    @property
    def is_open(self):
        return self._port is not None and self._port.is_open

    @property
    def uart_state(self):
        if not self.is_open:
            return "DISCONNECTED"
        if self.store.get_state("native_blocking_fault"):
            return "FAULT"
        return "READY" if self._mcu_boot_id else "STARTING"

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
        payload = command["payload"]
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
            if slot is not None or facts["retainedWorkState"] not in {"NONE", "RESULT_RELEASED"}:
                raise JobSafetyError("DEVICE_BUSY", "an original business still owns the device")
            if facts["retainedWorkState"] == "RESULT_RELEASED":
                saved = self.store.get_native_mcu_result(facts["currentMcuBootId"], facts["retainedResultSequence"])
                if saved is None or saved["work_uid"] != facts["retainedWorkUid"]:
                    raise ValueError("MCU released result has no corresponding local custody")
        else:
            if slot is None or self._permit(slot) != permit:
                raise ValueError("native START lost its original slot")
            check_job_permit(permit, permit_snapshot)

    def start_delivery_command(self, command):
        return self._start(command, clean=False)

    def start_clean_command(self, command):
        return self._start(command, clean=True)

    def _start(self, command, *, clean):
        self._check_start(command)
        payload = command["payload"]
        key, work_type = ("operationUid", "CLEAN") if clean else ("sessionUid", "DELIVERY")
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
            start_mcu_command_uid=uid, job_safety=asdict(permit) | {"begin_uid": permit.work_uid})
        if not self.store.acquire_work_slot(work_type, permit.work_uid, payload["portNo"], context):
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
        part_uids = prior["part_command_uids"] if prior else [str(uuid.uuid4()) for _ in range(candidate.part_count)]
        disposition = self.store.save_configuration_edge(command, part_uids)
        if disposition not in {"ACCEPTED", "DUPLICATE"}:
            raise ValueError("native configuration custody: " + disposition)
        self._restore_configuration()
        self.store.mark_command_waiting_mcu(command["commandUid"], part_uids[-1], {"native_pending": True})

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

    def _arm(self, record):
        authority = self._dispatch_authority
        if authority is None or authority[0] != self._dispatch_identity(record):
            raise JobSafetyError("NATIVE_AUTHORITY_UNAVAILABLE", "this exact write has no completed authority request")
        if record["message_name"].startswith("CONFIG_"):
            def check():
                self._require_maintenance_free()
                if authority[1] is not None:
                    raise JobSafetyError("MCU_MAINTENANCE_ACTIVE", "MCU belongs to maintenance")
                if self.store.get_work_slot() is not None:
                    raise JobSafetyError("DEVICE_BUSY", "configuration lost exclusive ownership")
                app = uart.decode_payload(record["message_name"], record["payload"])["applicationUid"]
                row = self.store.get_configuration(app)
                if row is None or record["command_uid"] not in row["part_command_uids"]:
                    raise ValueError("configuration command has no original cloud authority")
                validate_command_envelope(self.store.get_command(row["command_uid"])["payload"])
            check()
            return check
        slot = self.store.get_work_slot()
        permit = self._permit(slot) if slot else None
        if permit is None or slot["context"]["start_mcu_command_uid"] != record["command_uid"]:
            raise ValueError("native dispatch is not the original START")
        if record["message_name"] not in {"START_DELIVERY_SESSION", "START_CLEAN_OPERATION"}:
            raise ValueError("native business does not dispatch per-action commands")
        command = self.store.get_command(permit.command_uid)["payload"]
        check = lambda: self._check_start(command, permit=permit, permit_snapshot=authority[1])
        check()
        return check

    def _configuration_poll(self, now):
        app = self.store.get_state("native_configuration_application")
        if not app or self.store.get_work_slot() is not None:
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

    def _work_poll(self, now):
        slot = self.store.get_work_slot()
        if slot is None:
            self._work_query = self._handoff = self._query_start_uid = None
            return
        permit = self._permit(slot)
        uid = slot["context"]["start_mcu_command_uid"]
        record = self.store.get_native_command(uid)
        if record is None:
            raise ValueError("native business lost its original START")
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
        if decision["status"] == "COMPLETE_RESULT_AVAILABLE":
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
                self._complete_normal_result(permit, uid, decision)

    def _complete_normal_result(self, permit, uid, decision):
        from native_business_completion import NORMAL_FINISH, AVAILABLE
        result = uart.decode_payload("WORK_RESULT", decision["result"]["payload"])
        if (result["finishReason"] not in NORMAL_FINISH
                or any(result[key + "Kind"] not in AVAILABLE for key in ("initial", "final"))):
            return  # S2 failure policy must not call the normal-success completion.
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
                outcome="SUCCEEDED", completion_digest_sha256=digest)
            return self.safety.get_job_permit(permit.permit_uid)
        snapshot = self._rpc_call(("COMPLETE_APPLY",) + key + (digest,), complete)
        if snapshot is _RPC_PENDING:
            return
        self.store.apply_native_business_completion(permit, uid,
            device_name=self.device_name, permit_snapshot=snapshot)
        self._prepared_completions.pop(key, None)

    def poll(self):
        if self.transport is None:
            raise RuntimeError("native business UART has not been opened by its owner")
        now = self.clock()
        for frame in self.transport.poll(now):
            decoded = uart.decode_frame(frame, sender_role="MCU")
            accepted = self.boot.accept_frame(frame, now) or self.dispatcher.accept_frame(frame, now)
            if self.facts_query and self.facts_query.accept_frame(frame, now):
                self._facts = self.facts_query.observation(now)
                self._facts_requested_at = self.facts_query.request_started_ms
                accepted = True
            if self.facts_query and self.facts_query.conflict_payload is not None:
                self._facts = self._facts_requested_at = None
            if self._work_query:
                accepted = self._work_query.accept_frame(frame, now) or accepted
            if self._handoff:
                accepted = self._handoff.accept_frame(frame, now) or accepted
            elif decoded["messageName"] == "WORK_RESULT":
                # Preserve late evidence even without a live transfer; do not
                # acknowledge or project it onto another active business.
                self.store.save_native_mcu_result(decoded["payload"])
            if accepted:
                self._last_alive = now
        boot_id = self.boot.current_boot(now)
        if boot_id and boot_id != self._mcu_boot_id:
            self._mcu_boot_id = boot_id
            self.facts_query = McuDeviceFactsQuery(self.store, self.transport.write,
                target_mcu_boot_id=boot_id, port_no=1, interval_ms=NATIVE_DEVICE_CONSTANTS["weightPollIntervalMs"])
            self._facts = self._facts_requested_at = None
        self._configuration_poll(self.clock())
        self._work_poll(self.clock())
        if self.facts_query:
            self.facts_query.poll(self.clock())
        self.boot.poll(self.clock())
        now = self.clock()
        if now - (self._last_alive if self._last_alive is not None else self._opened_at) >= self.timeout_ms:
            self.store.set_state("native_blocking_fault", "MCU_COMMUNICATION_UNAVAILABLE")
        return dict(uartState=self.uart_state, mcuBootId=self._mcu_boot_id,
            activeWorkUid=(self.store.get_work_slot() or {}).get("work_uid"))

    def accept_photo_upload_grant(self, command):
        if self.photo is None:
            raise RuntimeError("photo manager unavailable")
        # Can run on the cloud thread: no native UART access here.
        return self.photo.offer_upload_grant(command)

    def _unsupported(self, command):
        raise JobSafetyError("NATIVE_COMMAND_NOT_SUPPORTED", "this command has not been wired for native MCU")

    quarantine_delivery_recovery = _unsupported
    start_fullness_command = _unsupported
    start_baseline_command = _unsupported
    end_clean_before_unlock_command = _unsupported
    resume_clean_command = _unsupported

"""Explicit recovery candidate loop: existing custody only, never admission.

This driver owns one native foreground transport. It does not construct the
legacy WorkManager, abort Pi-interrupted work, send mechanical commands or
create a replacement attempt. Successful startup is not business readiness.
"""
from time import monotonic_ns
import threading

from job_safety import PermanentJobSafety, JobSafetyError
from mcu_session import McuBootSession, McuCommandDispatcher
from mcu_actuator_handoff import McuActuatorEventHandoff
from native_delivery_recovery_close import (NativeRecoveryCloseRetirement,
    NativeRecoveryCloseReconciler, NativeRecoveryCloseWithdrawal)
from native_recovery_close_isolation import NativeRecoveryCloseIsolation
from uart2_transport import NativeUartTransport
import uart2_protocol as uart


class NativeRecoveryRuntime:
    def __init__(self, store, safety, transport, *, device_name, clock=lambda: monotonic_ns() // 1000000):
        if not isinstance(safety, PermanentJobSafety) or not isinstance(transport, NativeUartTransport):
            raise ValueError("native recovery needs permanent authority and exclusive native transport")
        if not isinstance(device_name, str) or not device_name or not callable(clock):
            raise ValueError("native recovery device identity and clock are required")
        self.store, self.safety, self.transport = store, safety, transport
        self.clock, self.device_name = clock, device_name
        self._owner, self._polling = threading.get_ident(), False
        self._snapshot = store.snapshot_native_recovery_close_startup()
        if self._snapshot is not None and self._snapshot["issue"]["deviceName"] != device_name:
            raise ValueError("native recovery issue belongs to another device")
        self._ids = tuple(binding["action"].action_uid for binding in self._snapshot["bindings"]) if self._snapshot else ()
        self._retired, self._confirmed, self._cursor = set(), set(), 0
        self._isolated = {}
        self._next_recovery = 0
        self.boot = McuBootSession(store, self._write)
        self._retirement = NativeRecoveryCloseRetirement(store, safety)
        self._withdrawal = NativeRecoveryCloseWithdrawal(store, safety)
        self._confirmation = NativeRecoveryCloseReconciler(store, safety)
        self._isolation = NativeRecoveryCloseIsolation(store, safety, self.boot, clock=clock)
        self._dispatcher = McuCommandDispatcher(store, self.boot, self._write, arm=self._deny_action, clock=clock)
        self._query_uid = None
        self._observed_boot, self._handoff = None, None
        self._state = "WAITING_BOOT"

    @staticmethod
    def _deny_action(record):
        raise ValueError("recovery candidate cannot authorize an action")

    def _refresh_handoff(self, now):
        boot_id = self.boot.current_boot(now)
        if boot_id is not None and boot_id != self._observed_boot:
            self._observed_boot = boot_id
            self._handoff = McuActuatorEventHandoff(self.store, self._write, boot_id)

    def _write(self, raw):
        name = uart.decode_frame(raw, sender_role="EDGE")["messageName"]
        if name not in {"BOOT_PROBE", "BIND_BOOT", "QUERY_COMMAND", "QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED"}:
            raise ValueError("recovery candidate cannot send mechanical or business commands")
        return self.transport.write(raw)

    def _status(self, boot_id):
        return dict(state=self._state, admissionAllowed=False, mcuBootId=boot_id,
            workUid=self._snapshot["issue"]["workUid"] if self._snapshot else None)

    def _active(self):
        issue = self._snapshot["issue"]
        slot = self.store.get_work_slot()
        return (slot == self._snapshot["slot"]
            and self.store.get_native_delivery_issue(issue["workUid"]) == issue)

    def _advance_recovery(self, boot_id):
        remaining = [uid for uid in self._ids if uid not in self._retired | self._confirmed | self._isolated.keys()]
        remaining = [uid for uid in remaining if self.store.get_native_command(uid)["write_claimed"]
            or self.store.get_native_command(uid)["mcu_boot_id"] == boot_id]
        if not remaining or self.clock() < self._next_recovery:
            return
        uid = remaining[self._cursor % len(remaining)]
        # Historical complete output remains valid when its MCU has reset or
        # is offline. This does not authorize any output on the new MCU boot.
        self._cursor += 1
        self._next_recovery = self.clock() + 1000
        try:
            self._state = self._resume_one(uid)
        except JobSafetyError:
            self._state = "WAITING_PERMANENT_LEDGER"

    def _resume_one(self, uid):
        record = self.store.get_native_command(uid)
        if record["write_claimed"]:
            self._query_uid = uid
            if self._confirmation.reconcile(uid) is not None:
                self._confirmed.add(uid)
                self._query_uid = None
                return "CLOSE_OUTPUT_CONFIRMED"
            proof = self.store.get_native_recovery_close_isolation(uid)
            boot_id = self.boot.current_boot(self.clock())
            if proof is not None or (boot_id is not None and boot_id > record["mcu_boot_id"]):
                isolated = self._isolation.reconcile(uid)
                if isolated is not None:
                    self._isolated[uid] = isolated
                    self._query_uid = None
                    return "OLD_CLOSE_ISOLATED_BY_REBOOT"
            return "AWAITING_ACTION_EVIDENCE"
        try:
            ledger = self.safety.get_physical_action(uid)
        except JobSafetyError as error:
            if error.code != "PHYSICAL_ACTION_NOT_FOUND":
                raise
            ledger = None
        if ledger is not None and ledger["dispatchMode"] != "PREPARED_ONLY":
            if ledger["state"] != "ARMED" or ledger["dispatchMode"] != "TWO_PHASE_V3":
                return "AWAITING_AUTHORIZED_DISPOSITION"
            self._withdrawal.reconcile(uid)
            self._retired.add(uid)
            return "AUTHORIZED_DISPATCH_WITHDRAWN"
        self._retirement.reconcile(uid)
        self._retired.add(uid)
        return "RETIREMENT_CONFIRMED"

    def poll(self):
        if self._polling or threading.get_ident() != self._owner:
            raise RuntimeError("native recovery requires one non-reentrant foreground owner")
        self._polling = True
        try:
            now = self.clock()
            for raw in self.transport.poll(now):
                self.boot.accept_frame(raw, now)
                self._refresh_handoff(now)
                decoded = uart.decode_frame(raw, sender_role="MCU")
                if decoded["messageName"] == "WORK_RESULT" and self._snapshot is not None:
                    value = uart.decode_payload("WORK_RESULT", decoded["payload"])
                    if value["workUid"] == self._snapshot["issue"]["workUid"]:
                        # Archived-result custody validates the complete original
                        # START/boot identity. No normal report or current-bag write.
                        # No uncorrelated RESULT_SAVED is sent by this candidate.
                        self.store.save_native_mcu_result(decoded["payload"])
                if decoded["messageName"] in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}:
                    value = uart.decode_payload(decoded["messageName"], decoded["payload"])
                    if value["mcuCommandUid"] in self._ids:
                        self._dispatcher.accept_frame(raw, now)
                if self._handoff is not None and self._snapshot is not None and self._active():
                    self._handoff.accept_frame(raw, now)
            self.boot.poll(self.clock())
            boot_id = self.boot.current_boot(self.clock())
            active = self._snapshot is not None and self._active()
            newer = False
            if active:
                current = self.store.snapshot_native_recovery_close_startup()
                current_ids = {binding["action"].action_uid for binding in current["bindings"]}
                if not set(self._ids) <= current_ids:
                    raise ValueError("native recovery startup preparation disappeared")
                newer = bool(current_ids - set(self._ids))
                if not newer:
                    self._advance_recovery(boot_id)
            boot_id = self.boot.current_boot(self.clock())
            if boot_id is None:
                self._state = "WAITING_BOOT"
            elif self._snapshot is None:
                self._state = "NO_ARCHIVED_DELIVERY"
            elif not active:
                self._state = "ORIGINAL_OCCUPANCY_CHANGED"
            elif newer:
                self._state = "PREPARATION_CREATED_AFTER_STARTUP"
            elif boot_id != self._snapshot["issue"]["targetMcuBootId"]:
                self._state = "TARGET_BOOT_CHANGED"
            elif len(self._retired | self._confirmed) == len(self._ids):
                self._state = "CLOSE_OUTPUT_CONFIRMED" if self._ids and self._ids[-1] in self._confirmed else "NEW_CLOSE_REQUIRED"
            if boot_id is not None and active and not newer:
                self._refresh_handoff(self.clock())
                self._handoff.poll(self.clock())
                if self._query_uid is not None:
                    self._dispatcher.poll(self._query_uid, self.clock())
            return self._status(self.boot.current_boot(self.clock()))
        finally:
            self._polling = False

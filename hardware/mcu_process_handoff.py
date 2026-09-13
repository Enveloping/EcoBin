"""Candidate process custody only; never business or mechanical authorization."""
from __future__ import annotations

import uart2_protocol as uart


def decode_process_scope(scope: bytes) -> dict:
    if not isinstance(scope, bytes):
        raise ValueError("process scope requires immutable original bytes")
    values = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + scope)
    del values["queryId"]  # Local shape validation only, never a transmitted ID.
    return values


def process_event_receipt(scope: bytes, message_name: str, payload: bytes) -> bytes:
    """Match full bytes to caller's ORIGINAL accepted work scope, not vice versa.

    This pure function computes a reference, not permission to transmit it.
    EdgeStore returns it only after the evidence/context transaction COMMIT.
    CLEAN_FINAL has no command UID in its body: its original command is supplied
    by the retained business owner/query scope, not invented from event fields.
    """
    original = decode_process_scope(scope)
    if message_name != original["eventMessageType"] or not isinstance(payload, bytes):
        raise ValueError("process event type differs from original scope")
    values = uart.decode_payload(message_name, payload)
    subject = {"WORK_PREOPEN_WEIGHT_READY": "sessionUid", "WORK_POSTCLOSE_WEIGHT_READY": "sessionUid", "DELIVERY_SELECTION": "sessionUid",
               "WORK_PREUNLOCK_WEIGHT_READY": "operationUid", "CLEAN_FINAL_WEIGHT_READY": "operationUid",
               "CLEAN_UNLOCK_REQUESTED": "operationUid", "CLEAN_FINISH_REQUESTED": "operationUid",
               "CLEAN_COMPLETION_CONFIRMED": "operationUid",
               "FULLNESS_SAMPLE_RESULT": "detectionUid", "BASELINE_MEASUREMENT_RESULT": "measurementUid"}[message_name]
    if (values["mcuBootId"] != original["targetMcuBootId"] or values[subject] != original["workUid"]
            or values["portNo"] != original["portNo"] or values["configVersion"] != original["configVersion"]
            or values.get("roundIndex", values.get("cleanActionSequence", 0)) != original["stepSequence"]
            or ("mcuCommandUid" in values and values["mcuCommandUid"] != original["mcuCommandUid"])):
        raise ValueError("process measurement differs from original work/step/configuration")
    return uart.encode_payload("PROCESS_EVENT_SAVED", dict(mcuBootId=values["mcuBootId"],
        mcuEventSequence=values["mcuEventSequence"], eventMessageType=message_name,
        eventDigestSha256=uart.compute_process_event_digest(message_name, payload)))


class McuProcessEventHandoff:
    """Restore ORIGINAL scope from the business ledger, then query-only custody.

    Single foreground owner, exclusive bounded writer; no UART open/retry/queue.
    A fresh exact query associates the held event with the original scope.
    Saved references follow full evidence/context COMMIT, once per query. They
    carry no authority or new freshness for motion. Only another fresh query
    observes RELEASED; that does not complete work or clear occupancy.
    """
    def __init__(self, store, write, original_scope, *, interval_ms=1000):
        from mcu_work_query import McuProcessEventQuery

        self._query = McuProcessEventQuery(store, write, original_scope, interval_ms=interval_ms)
        self._scope = uart.encode_payload("QUERY_PROCESS_EVENT", dict(original_scope) | {"queryId": 1})[8:]
        self._identity = decode_process_scope(self._scope)
        self._store, self._write = store, write
        self._confirmed_query_id = None
        self.last_write_error = None

    def poll(self, now_ms):
        query_id = self._query.poll(now_ms)
        if query_id is not None:
            self.last_write_error = self._query.last_write_error
        return query_id

    def observation(self, now_ms):
        return self._query.observation(now_ms)

    def _confirm(self, name, payload, observation):
        if self._confirmed_query_id == observation["queryId"]:
            return
        receipt = self._store.save_native_process_receipt(self._scope, name, payload)
        tx_id = self._store.reserve_native_query_id()  # committed before the single write
        frame = uart.encode_frame("PROCESS_EVENT_SAVED", ((tx_id - 1) % 0xFFFFFFFF) + 1, receipt)
        self._confirmed_query_id = observation["queryId"]  # consume even on error/short write
        self.last_write_error = None
        try:
            count = self._write(frame)
            if type(count) is not int or count != len(frame):
                self.last_write_error = "SHORT_WRITE"
        except OSError:
            self.last_write_error = "WRITE_FAILED"

    def accept_frame(self, frame, now_ms):
        self._query.observation(now_ms)  # validate monotonic time even for ignored frames
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
        except (ValueError, TypeError):
            return False
        name, payload = decoded["messageName"], decoded["payload"]
        if name == "PROCESS_EVENT_QUERY_REPLY":
            previous = self._query.observation(now_ms)
            if not self._query.accept_frame(frame, now_ms):
                if previous is not None and self._query.conflict_payload is not None:
                    self._store.retain_native_process_query_disagreement(
                        uart.encode_payload("PROCESS_EVENT_QUERY_REPLY", previous), payload)
                return False
            observed = self._query.observation(now_ms)
            if observed["status"] == "HELD":
                record = self._store.get_native_process_event(self._identity["eventMessageType"], observed["targetMcuBootId"], observed["mcuEventSequence"])
                if record is not None:
                    if (record["message_name"] != self._identity["eventMessageType"]
                            or uart.compute_process_event_digest(record["message_name"], record["payload"]) != observed["eventDigestSha256"]):
                        self._store.retain_native_process_query_conflict(record["message_name"], record["payload"], payload)
                        raise ValueError("held query conflicts with locally retained process evidence")
                    self._confirm(record["message_name"], record["payload"], observed)
            return True
        if name != self._identity["eventMessageType"]:
            return False
        observed = self._query.observation(now_ms)
        if observed is None or observed["status"] != "HELD":
            return False
        values = uart.decode_payload(name, payload)
        if (values["mcuBootId"], values["mcuEventSequence"]) != (observed["targetMcuBootId"], observed["mcuEventSequence"]):
            return False
        if uart.compute_process_event_digest(name, payload) != observed["eventDigestSha256"]:
            # Preserve conflicting full bytes as diagnostic evidence, never ACK.
            self._store.retain_native_process_query_conflict(name, payload,
                uart.encode_payload("PROCESS_EVENT_QUERY_REPLY", observed))
            return False
        self._confirm(name, payload, observed)
        return True

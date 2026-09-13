"""Native actuator byte custody, not enabled in main and not business authority."""
from __future__ import annotations

import uart2_protocol as uart
from mcu_work_query import McuActuatorEventQuery


class McuActuatorEventHandoff:
    """Single foreground owner with exclusive bounded writer; opens no port.

    Query oldest held record using a fresh durable ID. Confirm its precise body
    only after SQLite COMMIT, once per fresh query. Retry only by another query,
    never by replaying mechanical work. A Pi restart starts again from cursor 0.
    Local continuation has a distinct message type and saved selection cause;
    its command UID names the original grant, NOT another Pi-dispatched command.
    MCU-reported command/work IDs remain diagnostic claims pending reconciliation
    with the real business/action ledger; custody cannot complete or authorize it.
    """
    def __init__(self, store, write, boot_id, *, interval_ms=1000):
        self._query = McuActuatorEventQuery(store, write, boot_id, interval_ms=interval_ms)
        self._store, self._write = store, write
        self._confirmed_query_id = None
        self.last_write_error = None

    def poll(self, now_ms):
        result = self._query.poll(now_ms)
        if result is not None:
            self.last_write_error = self._query.last_write_error
        return result

    def observation(self, now_ms):
        return self._query.observation(now_ms)

    def _confirm(self, name, payload, observed):
        if self._confirmed_query_id == observed["queryId"]:
            return
        saved = self._store.save_native_actuator_event(name, payload)
        tx_id = self._store.reserve_native_query_id()
        frame = uart.encode_frame("ACTUATOR_EVENT_SAVED", ((tx_id - 1) % 0xFFFFFFFF) + 1, saved)
        self._confirmed_query_id = observed["queryId"]  # consumed even if write fails
        self.last_write_error = None
        try:
            count = self._write(frame)
            if type(count) is not int or count != len(frame):
                self.last_write_error = "SHORT_WRITE"
        except OSError:
            self.last_write_error = "WRITE_FAILED"

    def accept_frame(self, frame, now_ms):
        self._query.observation(now_ms)  # validate the clock even for ignored frames
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
        except (ValueError, TypeError):
            return False
        name, payload = decoded["messageName"], decoded["payload"]
        if name == "ACTUATOR_EVENT_QUERY_REPLY":
            if not self._query.accept_frame(frame, now_ms):
                return False
            observed = self._query.observation(now_ms)
            if observed["status"] == "HELD":
                row = self._store.get_native_actuator_event(observed["targetMcuBootId"], observed["mcuEventSequence"])
                if row is not None:
                    if (row["message_name"] != observed["eventMessageType"]
                            or uart.compute_actuator_event_digest(row["message_name"], row["payload"]) != observed["eventDigestSha256"]):
                        self._store.retain_native_actuator_query_conflict(row["message_name"], row["payload"], payload)
                        raise ValueError("actuator query conflicts with durable evidence")
                    self._confirm(row["message_name"], row["payload"], observed)
            return True
        if name not in uart.REGISTRY["sessionPolicy"]["actuatorEventMessages"]:
            return False  # saved replies and transport ACKs provide no fresh state
        observed = self._query.observation(now_ms)
        if observed is None or observed["status"] != "HELD":
            return False
        values = uart.decode_payload(name, payload)
        if (values["mcuBootId"], values["mcuEventSequence"]) != (observed["targetMcuBootId"], observed["mcuEventSequence"]):
            return False
        if name != observed["eventMessageType"] or uart.compute_actuator_event_digest(name, payload) != observed["eventDigestSha256"]:
            self._store.retain_native_actuator_query_conflict(name, payload,
                uart.encode_payload("ACTUATOR_EVENT_QUERY_REPLY", observed))
            return False
        self._confirm(name, payload, observed)
        return True

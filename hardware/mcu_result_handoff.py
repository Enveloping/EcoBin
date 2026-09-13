"""Candidate exact-result transfer, not wired into main or business recovery.

Single foreground owner and bounded, exclusive, non-reentrant writer required.
Caller supplies the FULL identity learned from the original work query, never
guesses a digest or derives it from the abbreviated device-facts snapshot.
Only read-only queries and precise RESULT_SAVED are sent; neither grants
mechanical work, clears business occupancy nor classifies financial value.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from edge_store import EdgeStore
from mcu_work_query import McuResultQuery
import uart2_protocol as uart


class McuResultHandoff:
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int],
                 result_identity: Mapping, *, interval_ms: int = 1000):
        self._query = McuResultQuery(store, write, result_identity, interval_ms=interval_ms)
        self._identity = uart.encode_payload("RESULT_SAVED", dict(result_identity))
        self._key = uart.decode_payload("RESULT_SAVED", self._identity)
        self._store, self._write = store, write
        self._interval = interval_ms
        self._last_now = -1
        self._confirm_after = 0
        self._saved_receipt: dict | None = None
        self.last_write_error: str | None = None

    def _check_time(self, now_ms: int) -> None:
        if type(now_ms) is not int or now_ms < 0 or now_ms < self._last_now:
            raise ValueError("handoff clock must be non-negative monotonic milliseconds")
        self._last_now = now_ms

    @property
    def saved_receipt(self) -> dict | None:
        """Local committed custody only, not MCU release or cloud completion."""
        return dict(self._saved_receipt) if self._saved_receipt is not None else None

    def poll(self, now_ms: int) -> int | None:
        self._check_time(now_ms)
        result = self._query.poll(now_ms)
        if result is not None:
            self.last_write_error = self._query.last_write_error
        return result

    def query_observation(self, now_ms: int) -> dict | None:
        self._check_time(now_ms)
        return self._query.observation(now_ms)

    def _commit_and_confirm(self, payload: bytes, now_ms: int) -> None:
        # Revalidate bytes and the durable task even on restart/retransmission.
        receipt = self._store.save_native_mcu_result(payload)
        self._saved_receipt = receipt
        if now_ms < self._confirm_after:
            return
        sequence = self._store.reserve_native_query_id()
        frame = uart.encode_frame("RESULT_SAVED", ((sequence - 1) % 0xFFFFFFFF) + 1,
                                  receipt["savedPayload"])
        self._confirm_after = now_ms + self._interval
        self.last_write_error = None
        try:
            count = self._write(frame)  # one attempt; never flush or fill a short write
            if type(count) is not int or count != len(frame):
                self.last_write_error = "SHORT_WRITE"
        except OSError:
            self.last_write_error = "WRITE_FAILED"

    def accept_frame(self, frame: bytes, now_ms: int) -> bool:
        """Invalid/unrelated frames ignored; storage/conflict errors propagate.

        Complete late results may still be saved as evidence. RESULT_SAVED_REPLY
        lacks a fresh query identity and is deliberately not treated as a current
        observation; a subsequent QUERY_RESULT can observe the released reference.
        """
        self._check_time(now_ms)
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
            name, payload = decoded["messageName"], decoded["payload"]
            if name not in {"WORK_RESULT", "RESULT_QUERY_REPLY"}:
                return False
            values = uart.decode_payload(name, payload)
        except (ValueError, TypeError):
            return False
        if name == "RESULT_QUERY_REPLY":
            if not self._query.accept_frame(frame, now_ms):
                return False
            if values["status"] == "HELD":
                saved = self._store.get_native_mcu_result(self._key["mcuBootId"], self._key["resultSequence"])
                if saved is not None:
                    original = bytes(saved["payload"])
                    if original[:60] != self._identity:
                        raise ValueError("stored result does not match the queried identity")
                    self._commit_and_confirm(original, now_ms)
            return True
        if payload[:60] != self._identity:
            if (values["mcuBootId"], values["resultSequence"]) == (self._key["mcuBootId"], self._key["resultSequence"]):
                if self._store.get_native_mcu_result(values["mcuBootId"], values["resultSequence"]) is not None:
                    # Keep same-number conflicting evidence, but never confirm
                    # a body that differs from this transfer's exact identity.
                    self._store.save_native_mcu_result(payload)
            return False
        self._commit_and_confirm(payload, now_ms)
        return True

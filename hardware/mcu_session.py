"""Native UART 2 candidate; not enabled by main or the legacy adapter.

One foreground owner and one exclusive bounded writer. The writer must not
queue, copy or retry requests, or synchronously reenter the receiver. A boot
observation is not admission and does not release work or permanent-ledger locks.
"""
from collections.abc import Callable
from time import monotonic_ns
import secrets

from edge_store import EdgeStore
import uart2_protocol as uart
from mcu_work_query import McuCommandQuery
from job_safety import JobPermit, JobSafetyError, PermanentJobSafety, PhysicalAction, action_digest


def _write_once(write: Callable[[bytes], int], frame: bytes) -> str | None:
    try:
        count = write(frame)
        return None if type(count) is int and count == len(frame) else "SHORT_WRITE"
    except OSError:
        return "WRITE_FAILED"


def _frame(name: str, identity: int, values: dict) -> bytes:
    return uart.encode_frame(name, (identity - 1) % 0xFFFFFFFF + 1, uart.encode_payload(name, values))


class McuBootSession:
    """Consume each probe/boot offer once, recover by fresh probes only.

    MCU reset between a reply and any later action is always possible. Every
    action still needs an exact target boot checked by the MCU, and its own
    business/ledger authorization. A new instance never trusts persisted 'ready'.
    """
    def __init__(self, store: EdgeStore, write: Callable[[bytes], int], *, interval_ms: int = 1000):
        if type(interval_ms) is not int or interval_ms < 1:
            raise ValueError("probe interval must be a positive integer")
        self._store, self._write, self._interval = store, write, interval_ms
        self._last_now = -1
        self._deadline = 0
        self._probe = self._proposed = 0
        self._boot: int | None = None
        self._stage = "UNKNOWN"
        self.last_write_error: str | None = None

    def _time(self, now_ms: int) -> None:
        if type(now_ms) is not int or now_ms < 0 or now_ms < self._last_now:
            raise ValueError("session clock must be non-negative monotonic milliseconds")
        self._last_now = now_ms

    def current_boot(self, now_ms: int) -> int | None:
        self._time(now_ms)
        return self._boot if now_ms < self._deadline else None

    def poll(self, now_ms: int) -> int | None:
        self._time(now_ms)
        if now_ms < self._deadline:
            return None
        self._boot = None
        self._stage = "UNKNOWN"
        self._probe = self._store.reserve_native_query_id()
        self._proposed = 0
        self._deadline = now_ms + self._interval
        self._stage = "PROBING"
        self.last_write_error = _write_once(self._write, _frame("BOOT_PROBE", self._probe, {"probeId": self._probe}))
        return self._probe

    def _observe(self, name: str, payload: bytes, boot_id: int) -> None:
        self._stage = "CONSUMED"  # No exception permits the same reply to bind again.
        self._boot = None
        saved = bool(
            boot_id and self._store.save_native_boot_observation(name, payload)
        )
        if (
            boot_id
            and not saved
            and name == "BOOT_PROBE_REPLY"
        ):
            saved = self._store.import_factory_native_boot_observation(
                name,
                payload,
                expected_probe_id=self._probe,
            )
        if saved:
            self._boot, self._stage = boot_id, "BOUND"

    def accept_frame(self, frame: bytes, now_ms: int) -> bool:
        self._time(now_ms)
        if now_ms >= self._deadline or self._stage not in {"PROBING", "BINDING"}:
            return False
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
            name = decoded["messageName"]
            if name not in {"BOOT_PROBE_REPLY", "BIND_BOOT_REPLY"}:
                return False
            values = uart.decode_payload(name, decoded["payload"])
        except (ValueError, TypeError):
            return False
        if values["probeId"] != self._probe:
            return False
        if self._stage == "PROBING" and name == "BOOT_PROBE_REPLY":
            if values["mcuBootId"]:
                self._observe(name, decoded["payload"], values["mcuBootId"])
            else:
                self._stage = "CONSUMED"
                self._proposed = self._store.reserve_native_boot_id(self._probe)
                self._stage = "BINDING"
                self.last_write_error = _write_once(self._write, _frame("BIND_BOOT", self._probe,
                    {"probeId": self._probe, "proposedMcuBootId": self._proposed}))
            return True
        if self._stage == "BINDING" and name == "BIND_BOOT_REPLY" and values["proposedMcuBootId"] == self._proposed:
            self._observe(name, decoded["payload"], values["mcuBootId"])
            return True
        return False


class McuCommandDispatcher:
    """Persisted single dispatch, followed only by original-command queries.

    'arm' is REQUIRED: its production adapter revalidates original business
    deadlines and permanent authority before returning a final validation
    callback. The simplified START uses the original ACTIVE job permit; it
    does not recreate individual actuator permissions. That callback runs
    after SQLite's dispatch claim commits, immediately before the serial write.
    It must never be a disabled compatibility no-op. NativePhysicalActionGate
    remains the old per-action candidate; configuration uses a scoped gate.
    It never translates command acceptance into physical completion/admission.
    """
    def __init__(self, store: EdgeStore, boot: McuBootSession, write: Callable[[bytes], int], *,
                 arm: Callable[[dict], Callable[[], None]], clock: Callable[[], int] = lambda: monotonic_ns() // 1000000):
        if not callable(arm):
            raise ValueError("permanent action authorization is required")
        self._store, self._boot, self._write, self._arm, self._clock = store, boot, write, arm, clock
        self._query: McuCommandQuery | None = None
        self._query_uid: str | None = None
        self.last_write_error: str | None = None

    def send_once(self, command_uid: str) -> bool:
        record = self._store.get_native_command(command_uid)
        if record is None:
            raise ValueError("unknown native command")
        if record["write_claimed"] or record["boot_retired"] or record["conflict"] or record["decision_outcome"] is not None:
            return False
        if self._boot.current_boot(self._clock()) != record["mcu_boot_id"]:
            raise RuntimeError("native boot not freshly confirmed")
        final_check = self._arm(dict(record))
        if not callable(final_check):
            raise ValueError("action authorization must provide final pre-write validation")
        # A slow permission RPC cannot silently extend freshness/deadlines.
        if self._boot.current_boot(self._clock()) != record["mcu_boot_id"]:
            raise RuntimeError("native boot expired during action authorization")
        frame = uart.encode_frame(record["message_name"], record["command_sequence"], record["payload"])
        if not self._store.claim_native_command_write(command_uid):
            return False
        final_check()
        if self._boot.current_boot(self._clock()) != record["mcu_boot_id"]:
            raise RuntimeError("native boot expired before serial write")
        self.last_write_error = _write_once(self._write, frame)
        return True  # Means one write attempted, NOT accepted or executed.

    def poll(self, command_uid: str, now_ms: int) -> int | None:
        record = self._store.get_native_command(command_uid)
        if record is None or not record["write_claimed"]:
            return None
        query = self._query if self._query_uid == command_uid else None
        if query is None:
            identity = uart.decode_payload("QUERY_COMMAND", (1).to_bytes(8, "big") + record["payload"][:60])
            del identity["queryId"]
            query = self._query = McuCommandQuery(self._store, self._write, identity)
            self._query_uid = command_uid
        return query.poll(now_ms)

    def accept_frame(self, frame: bytes, now_ms: int) -> bool:
        try:
            decoded = uart.decode_frame(frame, sender_role="MCU")
            name = decoded["messageName"]
            if name not in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}:
                return False
            values = uart.decode_payload(name, decoded["payload"])
        except (ValueError, TypeError):
            return False
        if name == "COMMAND_QUERY_RESULT":
            query = self._query if self._query_uid == values["mcuCommandUid"] else None
            if query is None:
                return False
            accepted = query.accept_frame(frame, now_ms)
            if not accepted and query.conflict_payload != decoded["payload"]:
                return False
        return self._store.save_native_command_observation(name, decoded["payload"])


class NativePhysicalActionGate:
    """Bind exact native bytes to the existing permanent two-phase action gate.

    The business owner supplies its ALREADY durable permit, stable logical
    action key/receipt and final monotonic deadline; never regenerate a receipt
    or reset a deadline during recovery. revalidate must check current safety,
    original authorization and prerequisites such as saved pre-open evidence.
    This adapter does not invent those business facts. The live dispatch token
    is intentionally not persisted/reused by a restarted process.
    """
    def __init__(self, safety: PermanentJobSafety, permit: JobPermit, action: PhysicalAction, *,
                 store: EdgeStore,
                 revalidate: Callable[[dict], None], deadline_ms: int,
                 clock: Callable[[], int] = lambda: monotonic_ns() // 1000000):
        if not isinstance(safety, PermanentJobSafety) or safety.enabled is not True:
            raise ValueError("native actions require the enabled permanent ledger")
        if not callable(revalidate) or type(deadline_ms) is not int or deadline_ms <= 0:
            raise ValueError("business validation and an original dispatch deadline are required")
        self._store, self._safety, self._permit, self._action = store, safety, permit, action
        self._revalidate, self._deadline, self._clock = revalidate, deadline_ms, clock
        self._token = secrets.token_hex(32)

    def _check(self, record: dict) -> None:
        if self._clock() >= self._deadline:
            raise JobSafetyError("COMMAND_EXPIRED", "native dispatch authorization expired")
        self._revalidate(dict(record))
        if self._clock() >= self._deadline:
            raise JobSafetyError("COMMAND_EXPIRED", "native business validation exceeded authorization")

    def __call__(self, record: dict) -> Callable[[], None]:
        permit, action = self._permit, self._action
        values = uart.decode_payload(record["message_name"], record["payload"])
        key = {"DELIVERY": "sessionUid", "CLEAN": "operationUid", "FULLNESS": "detectionUid", "BASELINE": "measurementUid"}.get(permit.work_type)
        digest = action_digest(work_uid=permit.work_uid, command_uid=permit.command_uid,
            action_key=action.action_key, action_kind=record["message_name"],
            payload={"nativeUartPayloadHex": record["payload"].hex()})
        if (key is None or values.get(key) != permit.work_uid or action.action_uid != record["command_uid"]
                or action.action_kind != record["message_name"] or action.action_digest_sha256 != digest):
            raise JobSafetyError("PHYSICAL_ACTION_IDENTITY_MISMATCH", "native command does not match its durable permit/action")
        self._check(record)
        self._store.bind_native_action(permit, action)
        self._check(record)
        self._safety.prepare_physical_action(permit, action=action, dispatch_attempt_token=self._token)
        self._check(record)
        self._safety.arm_physical_action(action, dispatch_attempt_token=self._token)
        self._check(record)  # No UART dispatch if the RPC outlives the original deadline.
        return lambda: self._check(record)

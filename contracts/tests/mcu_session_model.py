"""P0 protocol state model ONLY: no wire numbers, codecs, GPIO or runtime imports.

The authoritative wire source remains uart-registry.yaml. These candidate state
transitions are exercised before changing that source or the production runtime.
The channel may lose/delay frames, including across endpoint restarts, but MUST
NOT duplicate a complete valid request. Frame integrity is an explicit premise;
this is not protection against an active attacker or arbitrary valid replays.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3
import hashlib
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class BindingRequest:
    probe_id: int
    boot_id: int


@dataclass
class Binding:
    boot_id: int = 0
    pending_probe_id: int = 0

    def probe(self, probe_id: int) -> int:
        if probe_id <= 0:
            raise ValueError("probe identity must be nonzero")
        if self.boot_id == 0:
            self.pending_probe_id = probe_id
        return self.boot_id

    def bind(self, request: BindingRequest) -> bool:
        if (self.boot_id != 0 or request.probe_id <= 0 or request.boot_id <= 0
                or request.probe_id != self.pending_probe_id):
            return False
        self.boot_id = request.boot_id
        self.pending_probe_id = 0
        return True


class ModelStore:
    """Isolated, disposable SQLite model; NOT an EdgeStore migration/adapter.

    Allocation commits before the caller can attempt a send. An unused number
    is deliberately burned by a crash; restored/rolled-back DBs are not valid
    live-session continuity and require an explicit future migration policy.
    """
    def __init__(self, path: Path, checkpoint: Callable[[str], None] | None = None):
        self._checkpoint = checkpoint or (lambda _stage: None)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=0.1)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("CREATE TABLE IF NOT EXISTS ids (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS results (boot_id INTEGER, sequence INTEGER, work_uid TEXT, digest TEXT, payload BLOB, PRIMARY KEY (boot_id, sequence))")
        self._db.execute("CREATE TABLE IF NOT EXISTS outbox (boot_id INTEGER, sequence INTEGER, PRIMARY KEY (boot_id, sequence))")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._db.close()

    def _commit(self) -> None:
        # Fault injection belongs to this disposable model, never release code.
        self._checkpoint("before_commit")
        self._db.execute("COMMIT")
        self._checkpoint("after_commit")

    def allocate(self, name: str, maximum: int = 9007199254740991) -> int:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT value FROM ids WHERE name=?", (name,)).fetchone()
            value = (row[0] if row else 0) + 1
            if value > maximum:
                raise OverflowError("identity exhausted; must not wrap or reuse")
            self._db.execute("INSERT INTO ids VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                             (name, value))
            self._commit()
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return value

    def save_result(self, result: FrozenResult) -> tuple:
        result.validate()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._db.execute("SELECT boot_id, sequence, work_uid, digest, payload FROM results WHERE boot_id=? AND sequence=?",
                                        (result.boot_id, result.sequence)).fetchone()
            values = (*result.identity, result.payload)
            if existing is not None and existing != values:
                raise ValueError("immutable result identity conflict across all receive generations")
            self._db.execute("INSERT OR IGNORE INTO results VALUES (?, ?, ?, ?, ?)", values)
            self._db.execute("INSERT OR IGNORE INTO outbox VALUES (?, ?)", (result.boot_id, result.sequence))
            self._commit()
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return result.identity  # No caller can obtain a saved ACK before COMMIT.

    def saved_result(self, boot_id: int, sequence: int) -> FrozenResult | None:
        row = self._db.execute("SELECT boot_id, sequence, work_uid, digest, payload FROM results WHERE boot_id=? AND sequence=?",
                               (boot_id, sequence)).fetchone()
        return None if row is None else FrozenResult(*row)

    def pending_results(self) -> list[FrozenResult]:
        rows = self._db.execute("SELECT r.boot_id, r.sequence, r.work_uid, r.digest, r.payload FROM results r JOIN outbox o USING (boot_id, sequence) ORDER BY r.boot_id, r.sequence")
        return [FrozenResult(*row) for row in rows]


class PiBinding:
    """Each returned control request is eligible for ONE write attempt only.

    There is deliberately no retry/read-unsent API. A new object abandons all
    previous waiting replies, but keeps the durable allocator. Physical commands
    still require MCU-side target-boot fencing, even after a matching reply.
    """
    def __init__(self, store: ModelStore):
        self.store = store
        self.pending_probe_id = 0
        self.observed_boot_id: int | None = None

    def new_probe(self) -> int:
        probe_id = self.store.allocate("probe")
        self.pending_probe_id = probe_id
        self.observed_boot_id = None
        return probe_id

    def receive_probe(self, probe_id: int, boot_id: int) -> BindingRequest | None:
        if probe_id <= 0 or probe_id != self.pending_probe_id:
            return None
        self.pending_probe_id = 0  # Even a repeated zero reply is consumed once.
        self.observed_boot_id = boot_id
        if boot_id == 0:
            return BindingRequest(probe_id, self.store.allocate("boot"))
        return None


@dataclass(frozen=True)
class Command:
    target_boot_id: int
    sequence: int
    uid: str
    digest: str


@dataclass(frozen=True)
class CommandReceipt:
    command: Command
    outcome: str
    execute: bool = False

    def matches(self, command: Command) -> bool:
        return self.command == command


class CommandFence:
    """Bounded MCU decision memory, separate from the business state machine.

    A validated command above the high-water mark consumes its sequence even if
    business rejects it. Gaps are allowed (a reserved intent may never be sent),
    but are irreversibly retired, never later accepted. Bad boot/sequence and
    malformed wire data do not consume a sequence. Only the latest receipt
    is cached; retiring details never retires the monotonic high-water mark.
    Pi must persist immutable sequence/UID/digest mappings and allow one command
    in flight. Semantic payload validation precedes this model's interface.
    """
    def __init__(self, boot_id: int):
        self.boot_id = boot_id
        self.last_sequence = 0
        self.last_receipt: CommandReceipt | None = None

    def receive(self, command: Command, business_outcome: str = "ACCEPTED") -> CommandReceipt:
        def reject(reason: str) -> CommandReceipt:
            return CommandReceipt(command, reason)

        if self.boot_id == 0 or command.target_boot_id != self.boot_id:
            return reject("BOOT_MISMATCH")
        if command.sequence <= 0 or command.sequence > 4294967295:
            return reject("INVALID_SEQUENCE")
        if command.sequence == self.last_sequence and self.last_receipt is not None:
            if not self.last_receipt.matches(command):
                return reject("IDENTITY_CONFLICT")
            return replace(self.last_receipt, execute=False)
        if command.sequence <= self.last_sequence:
            return reject("OLD_DETAILS_UNAVAILABLE")
        self.last_sequence = command.sequence
        self.last_receipt = CommandReceipt(command, business_outcome,
                                           execute=business_outcome == "ACCEPTED")
        return self.last_receipt


@dataclass(frozen=True)
class FrozenResult:
    boot_id: int
    sequence: int
    work_uid: str
    digest: str
    payload: bytes

    @classmethod
    def make(cls, boot_id: int, sequence: int, work_uid: str, payload: bytes):
        return cls(boot_id, sequence, work_uid, hashlib.sha256(payload).hexdigest(), payload)

    @property
    def identity(self) -> tuple:
        return (self.boot_id, self.sequence, self.work_uid, self.digest)

    def validate(self) -> None:
        if (self.boot_id <= 0 or self.sequence <= 0 or not self.work_uid
                or hashlib.sha256(self.payload).hexdigest() != self.digest):
            raise ValueError("invalid complete-result evidence")


class ResultSlot:
    """One RAM-only completed result; this is not a business admission gate.

    Transport/fragments are intentionally absent: only a fully assembled,
    validated result enters this model. The later wire design must not map a
    transport ACK or partial fragment ACK to acknowledge().
    """
    def __init__(self, boot_id: int, work_uid: str):
        self.boot_id = boot_id
        self.work_uid = work_uid
        self.result: FrozenResult | None = None
        self._released: tuple | None = None

    @property
    def state(self) -> str:
        return "RELEASED" if self._released is not None else "RESULT_HELD" if self.result else "WAITING_RESULT"

    def freeze(self, result: FrozenResult) -> None:
        result.validate()
        if (result.boot_id != self.boot_id or result.work_uid != self.work_uid
                or self._released is not None or self.result not in (None, result)):
            raise ValueError("cannot overwrite a frozen result or change work identity")
        self.result = result

    def acknowledge(self, identity: tuple | None) -> bool:
        if self._released is not None and self._released == identity:
            return True
        if self.result is None or self.result.identity != identity:
            return False
        self._released = identity
        self.result = None
        return True


class ResultAssembly:
    """Candidate bounded collection, not the existing snapshot wire layout.

    Four parts / 1024 bytes are model bounds, not a promise that actual payloads
    fit. Registry layouts and target MCU RAM must be budgeted before enabling.
    Partial data yields no FrozenResult and thus no complete-result saved ACK.
    """
    def __init__(self, identity: tuple, part_count: int, byte_count: int):
        if not 1 <= part_count <= 4 or not 1 <= byte_count <= 1024:
            raise ValueError("result collection exceeds model bounds")
        self.identity = identity
        self.part_count = part_count
        self.byte_count = byte_count
        self._parts: dict[int, bytes] = {}

    def add(self, identity: tuple, index: int, payload: bytes) -> None:
        if identity != self.identity or not 0 <= index < self.part_count:
            raise ValueError("foreign result fragment")
        if index in self._parts:
            if self._parts[index] != payload:
                raise ValueError("conflicting result fragment")
            return
        if not payload or sum(map(len, self._parts.values())) + len(payload) > self.byte_count:
            raise ValueError("result collection length overflow")
        self._parts[index] = payload

    def complete(self) -> FrozenResult | None:
        if len(self._parts) != self.part_count:
            return None
        payload = b"".join(self._parts[index] for index in range(self.part_count))
        if len(payload) != self.byte_count:
            raise ValueError("result collection length mismatch")
        result = FrozenResult(*self.identity, payload)
        result.validate()
        return result

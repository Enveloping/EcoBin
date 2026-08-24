"""Private durable state for the independent remote-support agent.

This database is deliberately separate from ``edge.db``.  The hardware
gateway remains the only writer of the business command/event store, while
the maintenance agent is the only owner of the desired tunnel and OpenSSH
process state.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trusted_clock import ClockSample, local_deadline_reference, sample_clock


REMOTE_PORTS = frozenset(range(22011, 22015))
TERMINAL_STATES = frozenset({"CLOSED", "FAILED", "EXPIRED"})
SESSION_STATES = frozenset({
    "CONNECTING",
    "OPEN",
    "CLOSING",
    *TERMINAL_STATES,
})
FAILURE_CODES = frozenset({
    "CREDENTIALS_INVALID",
    "SSH_NOT_AVAILABLE",
    "SSH_START_FAILED",
    "SSH_EXITED",
    "PROCESS_SUPERVISION_FAILED",
})


class RemoteSupportStore:
    """Single-process SQLite store with a durable status handoff queue."""

    def __init__(
        self,
        path: str | Path,
        *,
        utc_now: Callable[[], datetime] | None = None,
        clock_sampler: Callable[[], ClockSample] | None = None,
    ) -> None:
        self.path = Path(path)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        if clock_sampler is not None:
            self._clock_sampler = clock_sampler
        elif utc_now is not None:
            self._clock_sampler = self._injected_clock_sample
        else:
            self._clock_sampler = sample_clock
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            INSERT OR IGNORE INTO schema_version(version) VALUES (1);

            CREATE TABLE IF NOT EXISTS remote_support_session (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                session_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                device_name TEXT NOT NULL,
                remote_port INTEGER NOT NULL
                    CHECK (remote_port BETWEEN 22011 AND 22014),
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'CONNECTING', 'OPEN', 'CLOSING', 'CLOSED',
                    'FAILED', 'EXPIRED'
                )),
                failure_code TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS status_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uid TEXT NOT NULL UNIQUE,
                session_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                device_name TEXT NOT NULL,
                remote_port INTEGER NOT NULL
                    CHECK (remote_port BETWEEN 22011 AND 22014),
                state TEXT NOT NULL CHECK (state IN (
                    'CONNECTING', 'OPEN', 'CLOSED', 'FAILED', 'EXPIRED'
                )),
                failure_code TEXT,
                occurred_at TEXT NOT NULL
            );
            """
        )
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        if version == 1:
            self._migrate_v2(connection)
            connection.execute(
                "INSERT INTO schema_version(version) VALUES (2)"
            )
        elif version != 2:
            connection.close()
            raise RuntimeError(
                "remote support database schema is incompatible"
            )
        connection.commit()
        self._connection = connection

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('status_outbox')"
            ).fetchall()
        }
        required = {
            "sequence", "event_uid", "session_uid", "command_uid",
            "device_name", "remote_port", "state", "failure_code",
            "occurred_at", "raw_occurred_at", "clock_quality",
        }
        if columns == required:
            return
        legacy = required - {"raw_occurred_at", "clock_quality"}
        if columns != legacy:
            raise RuntimeError(
                "remote support status outbox shape is incompatible"
            )
        connection.execute(
            "ALTER TABLE status_outbox RENAME TO status_outbox_v1"
        )
        connection.execute(
            """CREATE TABLE status_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uid TEXT NOT NULL UNIQUE,
                session_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                device_name TEXT NOT NULL,
                remote_port INTEGER NOT NULL
                    CHECK (remote_port BETWEEN 22011 AND 22014),
                state TEXT NOT NULL CHECK (state IN (
                    'CONNECTING', 'OPEN', 'CLOSED', 'FAILED', 'EXPIRED'
                )),
                failure_code TEXT,
                occurred_at TEXT,
                raw_occurred_at TEXT NOT NULL,
                clock_quality TEXT NOT NULL CHECK (clock_quality IN (
                    'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                ))
            )"""
        )
        connection.execute(
            """INSERT INTO status_outbox
               (sequence, event_uid, session_uid, command_uid,
                device_name, remote_port, state, failure_code,
                occurred_at, raw_occurred_at, clock_quality)
               SELECT sequence, event_uid, session_uid, command_uid,
                      device_name, remote_port, state, failure_code,
                      occurred_at, occurred_at, 'SYNCED'
               FROM status_outbox_v1"""
        )
        connection.execute("DROP TABLE status_outbox_v1")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def request_remote_support_open(
        self,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        expires_at: str,
    ) -> str:
        _require_uuid4(session_uid, "session_uid")
        _require_uuid4(command_uid, "command_uid")
        _require_device_name(device_name)
        _require_remote_port(remote_port)
        expiry = _parse_utc(expires_at, "expires_at")
        deadline_reference = self._deadline_reference()
        if deadline_reference is not None and expiry <= deadline_reference:
            raise ValueError("remote support session is already expired")

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if existing is not None:
                same_session = existing["session_uid"] == session_uid
                same_request = (
                    same_session
                    and existing["remote_port"] == remote_port
                    and existing["expires_at"] == expires_at
                    and existing["device_name"] == device_name
                )
                if same_session:
                    return "DUPLICATE" if same_request else "CONFLICT"
                if existing["state"] not in TERMINAL_STATES:
                    return "CONFLICT"

            now = self._now_text()
            connection.execute(
                """INSERT INTO remote_support_session
                   (singleton_id, session_uid, command_uid, device_name,
                    remote_port, expires_at, state, failure_code,
                    attempt_count, next_attempt_at, created_at, updated_at)
                   VALUES (1,?,?,?,?,?,'CONNECTING',NULL,0,NULL,?,?)
                   ON CONFLICT(singleton_id) DO UPDATE SET
                     session_uid=excluded.session_uid,
                     command_uid=excluded.command_uid,
                     device_name=excluded.device_name,
                     remote_port=excluded.remote_port,
                     expires_at=excluded.expires_at,
                     state='CONNECTING', failure_code=NULL,
                     attempt_count=0, next_attempt_at=NULL,
                     created_at=excluded.created_at,
                     updated_at=excluded.updated_at""",
                (
                    session_uid,
                    command_uid,
                    device_name,
                    remote_port,
                    expires_at,
                    now,
                    now,
                ),
            )
            self._append_status(
                connection,
                session_uid=session_uid,
                command_uid=command_uid,
                device_name=device_name,
                remote_port=remote_port,
                state="CONNECTING",
                failure_code=None,
            )
            return "ACCEPTED"

    def request_remote_support_close(
        self,
        session_uid: str,
        command_uid: str,
    ) -> str:
        _require_uuid4(session_uid, "session_uid")
        _require_uuid4(command_uid, "command_uid")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "NOT_FOUND"
            if row["state"] in TERMINAL_STATES:
                return "ALREADY_TERMINAL"
            if row["state"] == "CLOSING":
                return "DUPLICATE"
            connection.execute(
                """UPDATE remote_support_session
                   SET command_uid=?, state='CLOSING', failure_code=NULL,
                       next_attempt_at=NULL, updated_at=?
                   WHERE singleton_id=1""",
                (command_uid, self._now_text()),
            )
            return "ACCEPTED"

    def get_remote_support_session(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
        return dict(row) if row is not None else None

    def transition_remote_support_session(
        self,
        session_uid: str,
        state: str,
        *,
        failure_code: str | None = None,
    ) -> str:
        if state not in SESSION_STATES - {"CLOSING"}:
            raise ValueError("invalid remote support status state")
        if failure_code is not None and failure_code not in FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        if state == "FAILED" and failure_code is None:
            raise ValueError("FAILED remote support state requires failure_code")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "STALE"
            if row["state"] in TERMINAL_STATES:
                return "TERMINAL"
            if row["state"] == state and row["failure_code"] == failure_code:
                return "DUPLICATE"
            connection.execute(
                """UPDATE remote_support_session
                   SET state=?, failure_code=?, next_attempt_at=NULL,
                       attempt_count=CASE
                           WHEN ?='OPEN' THEN 0 ELSE attempt_count END,
                       updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (
                    state,
                    failure_code,
                    state,
                    self._now_text(),
                    session_uid,
                ),
            )
            self._append_status(
                connection,
                session_uid=session_uid,
                command_uid=row["command_uid"],
                device_name=row["device_name"],
                remote_port=row["remote_port"],
                state=state,
                failure_code=failure_code,
            )
            return "ACCEPTED"

    def record_remote_support_retry(
        self,
        session_uid: str,
        *,
        next_attempt_at: float,
        failure_code: str,
    ) -> int:
        if failure_code not in FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if (
                row is None
                or row["session_uid"] != session_uid
                or row["state"] in TERMINAL_STATES
                or row["state"] == "CLOSING"
            ):
                return 0
            attempts = int(row["attempt_count"]) + 1
            connection.execute(
                """UPDATE remote_support_session
                   SET state='CONNECTING', failure_code=?, attempt_count=?,
                       next_attempt_at=?, updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (
                    failure_code,
                    attempts,
                    float(next_attempt_at),
                    self._now_text(),
                    session_uid,
                ),
            )
            if row["state"] != "CONNECTING" or row["failure_code"] != failure_code:
                self._append_status(
                    connection,
                    session_uid=session_uid,
                    command_uid=row["command_uid"],
                    device_name=row["device_name"],
                    remote_port=row["remote_port"],
                    state="CONNECTING",
                    failure_code=failure_code,
                )
            return attempts

    def list_status_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("status event limit must be 1..100")
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT * FROM status_outbox
                   ORDER BY sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "eventUid": row["event_uid"],
                "sessionUid": row["session_uid"],
                "commandUid": row["command_uid"],
                "deviceName": row["device_name"],
                "remotePort": row["remote_port"],
                "state": row["state"],
                "failureCode": row["failure_code"],
                "occurredAt": row["occurred_at"],
                "clockQuality": row["clock_quality"],
            }
            for row in rows
        ]

    def ack_status_event(self, event_uid: str) -> str:
        _require_uuid4(event_uid, "event_uid")
        with self.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM status_outbox WHERE event_uid=?",
                (event_uid,),
            )
            return "ACCEPTED" if deleted.rowcount == 1 else "DUPLICATE"

    def import_legacy_edge_store(self, path: str | Path) -> str:
        """Adopt one active v10 session during the one-time service split."""

        with self._lock:
            existing = self._require_connection().execute(
                "SELECT 1 FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
        if existing is not None:
            return "ALREADY_INITIALIZED"
        legacy_path = Path(path)
        if not legacy_path.is_file():
            return "NO_LEGACY_STORE"
        legacy = sqlite3.connect(str(legacy_path))
        legacy.row_factory = sqlite3.Row
        try:
            table = legacy.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='remote_support_session'"""
            ).fetchone()
            if table is None:
                return "NO_LEGACY_SESSION"
            row = legacy.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
        finally:
            legacy.close()
        if row is None:
            return "NO_LEGACY_SESSION"
        if row["state"] in TERMINAL_STATES:
            return "TERMINAL_IGNORED"
        deadline_reference = self._deadline_reference()
        if (
            deadline_reference is not None
            and _parse_utc(row["expires_at"], "expires_at")
            <= deadline_reference
        ):
            return "EXPIRED_IGNORED"
        result = self.request_remote_support_open(
            session_uid=row["session_uid"],
            command_uid=row["command_uid"],
            device_name=row["device_name"],
            remote_port=int(row["remote_port"]),
            expires_at=row["expires_at"],
        )
        if row["state"] == "CLOSING":
            self.request_remote_support_close(
                row["session_uid"],
                row["command_uid"],
            )
        if result not in {"ACCEPTED", "DUPLICATE"}:
            raise RuntimeError("legacy remote support session conflicts")
        return "IMPORTED"

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _append_status(
        self,
        connection: sqlite3.Connection,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        state: str,
        failure_code: str | None,
    ) -> None:
        sampled = self._clock_sampler()
        raw_occurred_at = sampled.raw_observed_at or self._now_text()
        connection.execute(
            """INSERT INTO status_outbox
               (event_uid, session_uid, command_uid, device_name,
                remote_port, state, failure_code, occurred_at,
                raw_occurred_at, clock_quality)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                session_uid,
                command_uid,
                device_name,
                remote_port,
                state,
                failure_code,
                sampled.occurred_at,
                raw_occurred_at,
                sampled.quality,
            ),
        )

    def _now_datetime(self) -> datetime:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("remote support clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _now_text(self) -> str:
        value = self._now_datetime().isoformat(timespec="milliseconds")
        return value.replace("+00:00", "Z")

    def _deadline_reference(self) -> datetime | None:
        if self._clock_sampler is sample_clock:
            return local_deadline_reference()
        sampled = self._clock_sampler()
        if not sampled.trusted or sampled.occurred_at is None:
            return None
        return _parse_utc(sampled.occurred_at, "clock_sample")

    def _injected_clock_sample(self) -> ClockSample:
        raw = self._now_text()
        return ClockSample("SYNCED", raw, None, raw)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("remote support store is not initialized")
        return self._connection


def _require_uuid4(value: str, field: str) -> None:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")


def _require_device_name(value: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError("device_name is invalid")


def _require_remote_port(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value not in REMOTE_PORTS:
        raise ValueError("remote_port must be one of 22011..22014")


def _parse_utc(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an RFC3339 instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC3339 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)

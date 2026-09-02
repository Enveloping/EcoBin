"""Private durable state owned only by the permanent communication agent.

The stage-four candidate adds the permanent transport ledger while keeping
OneNet ownership and remote routing disabled.  The ledger is intentionally
usable without a network client so migration and rollback behaviour can be
qualified before the later ownership cut-over.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
MAX_RELEASE_VERSION_LENGTH = 32

INBOUND_RECEIVED = "RECEIVED"
INBOUND_BUSINESS_ACCEPTED = "BUSINESS_ACCEPTED"
OUTBOUND_PENDING = "PENDING"
OUTBOUND_SENDING = "SENDING"
OUTBOUND_PLATFORM_CONFIRMED = "PLATFORM_CONFIRMED"
SEND_ATTEMPT_STARTED = "STARTED"
SEND_ATTEMPT_OUTCOMES = frozenset(
    {
        "TRANSPORT_ACCEPTED",
        "RETRYABLE_FAILURE",
        "PERMANENT_FAILURE",
        "RESULT_UNKNOWN",
    }
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class CommunicationStore:
    """Single-process SQLite owner for permanent communication facts."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        utc_now: Callable[[], datetime] | None = None,
        process_id: Callable[[], int] = os.getpid,
    ) -> None:
        self.path = Path(path)
        if self.path.name.casefold() == "edge.db":
            raise ValueError("communication store must not use the business edge.db")
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._process_id = process_id
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        """Open, check, and transactionally migrate the private database."""

        with self._lock:
            if self._connection is not None:
                return
            self._verify_paths()
            connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            try:
                os.chmod(self.path, 0o600)
                self._require_quick_check(connection)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._create_v1_schema(connection)
                version = self._read_or_initialize_schema_version(connection)
                self._verify_v1_schema(connection)
                if version == 1:
                    self._migrate_v1_to_v2(connection)
                elif version != SCHEMA_VERSION:
                    raise RuntimeError(
                        "communication database schema is incompatible"
                    )
                self._verify_v2_schema(connection)
                self._require_quick_check(connection)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
                raise
            self._connection = connection

    def _verify_paths(self) -> None:
        try:
            parent = self.path.parent.lstat()
        except FileNotFoundError as error:
            raise PermissionError(
                "communication database parent does not exist"
            ) from error
        if self.path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode):
            raise PermissionError(
                "communication database parent is not a real directory"
            )
        if os.name == "posix":
            if parent.st_uid != os.getuid():
                raise PermissionError(
                    "communication database parent has an unexpected owner"
                )
            if stat.S_IMODE(parent.st_mode) & 0o077:
                raise PermissionError(
                    "communication database parent must have private mode 0700"
                )
        if self.path.is_symlink():
            raise PermissionError(
                "communication database path must not be a symlink"
            )
        if not self.path.exists():
            return
        metadata = self.path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError(
                "communication database path is not a regular file"
            )
        if os.name == "posix":
            if metadata.st_nlink != 1:
                raise PermissionError(
                    "communication database must not have hard links"
                )
            if metadata.st_uid != os.getuid():
                raise PermissionError(
                    "communication database has an unexpected owner"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise PermissionError(
                    "communication database permissions are too broad"
                )

    @staticmethod
    def _require_quick_check(connection: sqlite3.Connection) -> None:
        try:
            rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
        except sqlite3.DatabaseError as error:
            raise RuntimeError(
                "communication database quick_check failed"
            ) from error
        if rows != ["ok"]:
            raise RuntimeError("communication database quick_check failed")

    @staticmethod
    def _create_v1_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                version INTEGER NOT NULL CHECK (version > 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS process_start_fact (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                start_uid TEXT NOT NULL UNIQUE,
                release_version TEXT NOT NULL,
                process_id INTEGER NOT NULL CHECK (process_id > 0),
                started_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _read_or_initialize_schema_version(
        connection: sqlite3.Connection,
    ) -> int:
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version"
        ).fetchall()
        if not rows:
            connection.execute(
                "INSERT INTO schema_version(singleton_id, version) VALUES (1, 1)"
            )
            return 1
        if len(rows) != 1 or tuple(rows[0]) not in {
            (1, 1),
            (1, SCHEMA_VERSION),
        }:
            raise RuntimeError("communication database schema is incompatible")
        return int(rows[0]["version"])

    @staticmethod
    def _verify_v1_schema(connection: sqlite3.Connection) -> None:
        _require_exact_columns(
            connection,
            "process_start_fact",
            (
                "sequence",
                "start_uid",
                "release_version",
                "process_id",
                "started_at",
            ),
        )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        """Add permanent ledgers in the caller's one migration transaction."""

        statements = (
            """CREATE TABLE inbound_command_ledger (
                command_uid TEXT PRIMARY KEY CHECK (
                    length(command_uid) = 36
                    AND length(replace(command_uid, '-', '')) = 32
                    AND substr(command_uid, 9, 1) = '-'
                    AND substr(command_uid, 14, 1) = '-'
                    AND substr(command_uid, 15, 1) = '4'
                    AND substr(command_uid, 19, 1) = '-'
                    AND substr(command_uid, 20, 1) IN ('8', '9', 'a', 'b')
                    AND substr(command_uid, 24, 1) = '-'
                    AND replace(command_uid, '-', '')
                        NOT GLOB '*[^0-9a-f]*'
                ),
                content_sha256 TEXT NOT NULL
                    CHECK (
                        length(content_sha256) = 64
                        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                state TEXT NOT NULL CHECK (
                    state IN ('RECEIVED', 'BUSINESS_ACCEPTED')
                ),
                received_at TEXT NOT NULL,
                business_accepted_at TEXT,
                CHECK (
                    (state = 'RECEIVED' AND business_accepted_at IS NULL)
                    OR
                    (state = 'BUSINESS_ACCEPTED'
                     AND business_accepted_at IS NOT NULL)
                )
            )""",
            """CREATE TABLE outbound_business_event_ledger (
                event_uid TEXT PRIMARY KEY CHECK (
                    length(event_uid) = 36
                    AND length(replace(event_uid, '-', '')) = 32
                    AND substr(event_uid, 9, 1) = '-'
                    AND substr(event_uid, 14, 1) = '-'
                    AND substr(event_uid, 15, 1) = '4'
                    AND substr(event_uid, 19, 1) = '-'
                    AND substr(event_uid, 20, 1) IN ('8', '9', 'a', 'b')
                    AND substr(event_uid, 24, 1) = '-'
                    AND replace(event_uid, '-', '')
                        NOT GLOB '*[^0-9a-f]*'
                ),
                content_sha256 TEXT NOT NULL
                    CHECK (
                        length(content_sha256) = 64
                        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                state TEXT NOT NULL CHECK (
                    state IN ('PENDING', 'SENDING', 'PLATFORM_CONFIRMED')
                ),
                created_at TEXT NOT NULL,
                last_send_attempt_at TEXT,
                platform_confirmed_at TEXT,
                CHECK (
                    (state = 'PENDING'
                     AND last_send_attempt_at IS NULL
                     AND platform_confirmed_at IS NULL)
                    OR
                    (state = 'SENDING'
                     AND last_send_attempt_at IS NOT NULL
                     AND platform_confirmed_at IS NULL)
                    OR
                    (state = 'PLATFORM_CONFIRMED'
                     AND platform_confirmed_at IS NOT NULL)
                )
            )""",
            """CREATE TABLE outbound_send_attempt (
                attempt_uid TEXT PRIMARY KEY CHECK (
                    length(attempt_uid) = 36
                    AND length(replace(attempt_uid, '-', '')) = 32
                    AND substr(attempt_uid, 9, 1) = '-'
                    AND substr(attempt_uid, 14, 1) = '-'
                    AND substr(attempt_uid, 15, 1) = '4'
                    AND substr(attempt_uid, 19, 1) = '-'
                    AND substr(attempt_uid, 20, 1) IN ('8', '9', 'a', 'b')
                    AND substr(attempt_uid, 24, 1) = '-'
                    AND replace(attempt_uid, '-', '')
                        NOT GLOB '*[^0-9a-f]*'
                ),
                event_uid TEXT NOT NULL,
                attempt_sequence INTEGER NOT NULL
                    CHECK (attempt_sequence > 0),
                content_sha256 TEXT NOT NULL
                    CHECK (
                        length(content_sha256) = 64
                        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                outcome TEXT NOT NULL CHECK (
                    outcome IN (
                        'STARTED', 'TRANSPORT_ACCEPTED',
                        'RETRYABLE_FAILURE', 'PERMANENT_FAILURE',
                        'RESULT_UNKNOWN'
                    )
                ),
                started_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE (event_uid, attempt_sequence),
                FOREIGN KEY (event_uid)
                    REFERENCES outbound_business_event_ledger(event_uid),
                CHECK (
                    (outcome = 'STARTED' AND completed_at IS NULL)
                    OR
                    (outcome <> 'STARTED' AND completed_at IS NOT NULL)
                )
            )""",
            """CREATE TABLE outbound_platform_confirmation (
                confirmation_uid TEXT PRIMARY KEY CHECK (
                    length(confirmation_uid) = 36
                    AND length(replace(confirmation_uid, '-', '')) = 32
                    AND substr(confirmation_uid, 9, 1) = '-'
                    AND substr(confirmation_uid, 14, 1) = '-'
                    AND substr(confirmation_uid, 15, 1) = '4'
                    AND substr(confirmation_uid, 19, 1) = '-'
                    AND substr(confirmation_uid, 20, 1)
                        IN ('8', '9', 'a', 'b')
                    AND substr(confirmation_uid, 24, 1) = '-'
                    AND replace(confirmation_uid, '-', '')
                        NOT GLOB '*[^0-9a-f]*'
                ),
                event_uid TEXT NOT NULL UNIQUE,
                confirmation_sha256 TEXT NOT NULL
                    CHECK (
                        length(confirmation_sha256) = 64
                        AND confirmation_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                received_at TEXT NOT NULL,
                FOREIGN KEY (event_uid)
                    REFERENCES outbound_business_event_ledger(event_uid)
            )""",
            """CREATE TRIGGER inbound_command_no_delete
               BEFORE DELETE ON inbound_command_ledger
               BEGIN SELECT RAISE(ABORT, 'inbound command ledger is append-only'); END""",
            """CREATE TRIGGER inbound_command_monotonic_update
               BEFORE UPDATE ON inbound_command_ledger
               WHEN OLD.command_uid <> NEW.command_uid
                 OR OLD.content_sha256 <> NEW.content_sha256
                 OR OLD.received_at <> NEW.received_at
                 OR OLD.state <> 'RECEIVED'
                 OR NEW.state <> 'BUSINESS_ACCEPTED'
                 OR OLD.business_accepted_at IS NOT NULL
                 OR NEW.business_accepted_at IS NULL
               BEGIN SELECT RAISE(ABORT, 'inbound command ledger update is not monotonic'); END""",
            """CREATE TRIGGER outbound_event_no_delete
               BEFORE DELETE ON outbound_business_event_ledger
               BEGIN SELECT RAISE(ABORT, 'outbound event ledger is append-only'); END""",
            """CREATE TRIGGER outbound_event_monotonic_update
               BEFORE UPDATE ON outbound_business_event_ledger
               WHEN OLD.event_uid <> NEW.event_uid
                 OR OLD.content_sha256 <> NEW.content_sha256
                 OR OLD.created_at <> NEW.created_at
                 OR NOT (
                    (OLD.state = 'PENDING' AND NEW.state IN ('SENDING', 'PLATFORM_CONFIRMED'))
                    OR (OLD.state = 'SENDING' AND NEW.state IN ('SENDING', 'PLATFORM_CONFIRMED'))
                 )
                 OR (OLD.last_send_attempt_at IS NOT NULL
                     AND NEW.last_send_attempt_at IS NULL)
                 OR (OLD.platform_confirmed_at IS NOT NULL
                     AND NEW.platform_confirmed_at <> OLD.platform_confirmed_at)
               BEGIN SELECT RAISE(ABORT, 'outbound event ledger update is not monotonic'); END""",
            """CREATE TRIGGER outbound_attempt_no_delete
               BEFORE DELETE ON outbound_send_attempt
               BEGIN SELECT RAISE(ABORT, 'outbound send attempt is append-only'); END""",
            """CREATE TRIGGER outbound_attempt_monotonic_update
               BEFORE UPDATE ON outbound_send_attempt
               WHEN OLD.attempt_uid <> NEW.attempt_uid
                 OR OLD.event_uid <> NEW.event_uid
                 OR OLD.attempt_sequence <> NEW.attempt_sequence
                 OR OLD.content_sha256 <> NEW.content_sha256
                 OR OLD.started_at <> NEW.started_at
                 OR OLD.outcome <> 'STARTED'
                 OR NEW.outcome = 'STARTED'
                 OR OLD.completed_at IS NOT NULL
                 OR NEW.completed_at IS NULL
               BEGIN SELECT RAISE(ABORT, 'outbound send attempt update is not monotonic'); END""",
            """CREATE TRIGGER outbound_confirmation_no_delete
               BEFORE DELETE ON outbound_platform_confirmation
               BEGIN SELECT RAISE(ABORT, 'platform confirmation is append-only'); END""",
            """CREATE TRIGGER outbound_confirmation_no_update
               BEFORE UPDATE ON outbound_platform_confirmation
               BEGIN SELECT RAISE(ABORT, 'platform confirmation is immutable'); END""",
        )
        for statement in statements:
            connection.execute(statement)
        updated = connection.execute(
            "UPDATE schema_version SET version=2 WHERE singleton_id=1 AND version=1"
        )
        if updated.rowcount != 1:
            raise RuntimeError("communication database migration lost its version row")

    @staticmethod
    def _verify_v2_schema(connection: sqlite3.Connection) -> None:
        expected = {
            "inbound_command_ledger": (
                "command_uid",
                "content_sha256",
                "state",
                "received_at",
                "business_accepted_at",
            ),
            "outbound_business_event_ledger": (
                "event_uid",
                "content_sha256",
                "state",
                "created_at",
                "last_send_attempt_at",
                "platform_confirmed_at",
            ),
            "outbound_send_attempt": (
                "attempt_uid",
                "event_uid",
                "attempt_sequence",
                "content_sha256",
                "outcome",
                "started_at",
                "completed_at",
            ),
            "outbound_platform_confirmation": (
                "confirmation_uid",
                "event_uid",
                "confirmation_sha256",
                "received_at",
            ),
        }
        for table, columns in expected.items():
            _require_exact_columns(connection, table, columns)
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        required_triggers = {
            "inbound_command_no_delete",
            "inbound_command_monotonic_update",
            "outbound_event_no_delete",
            "outbound_event_monotonic_update",
            "outbound_attempt_no_delete",
            "outbound_attempt_monotonic_update",
            "outbound_confirmation_no_delete",
            "outbound_confirmation_no_update",
        }
        if not required_triggers.issubset(trigger_names):
            raise RuntimeError("communication database schema is incompatible")

    def record_process_start(self, release_version: str) -> dict[str, Any]:
        version = _require_release_version(release_version)
        start_uid = str(uuid.uuid4())
        process_id = self._process_id()
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            raise ValueError("communication process ID must be positive")
        started_at = self._now_text()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO process_start_fact
                   (start_uid, release_version, process_id, started_at)
                   VALUES (?, ?, ?, ?)""",
                (start_uid, version, process_id, started_at),
            )
        return {
            "startUid": start_uid,
            "releaseVersion": version,
            "processId": process_id,
            "startedAt": started_at,
        }

    def receive_inbound_command(
        self,
        command_uid: str,
        content_sha256: str,
    ) -> str:
        command_uid = _require_uuid4(command_uid, "command UID")
        content_sha256 = _require_sha256(content_sha256, "command content SHA-256")
        now = self._now_text()
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT content_sha256, state
                   FROM inbound_command_ledger WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is not None:
                if row["content_sha256"] != content_sha256:
                    return "CONFLICT"
                return (
                    "DUPLICATE_BUSINESS_ACCEPTED"
                    if row["state"] == INBOUND_BUSINESS_ACCEPTED
                    else "DUPLICATE_RECEIVED"
                )
            connection.execute(
                """INSERT INTO inbound_command_ledger
                   (command_uid, content_sha256, state, received_at)
                   VALUES (?, ?, 'RECEIVED', ?)""",
                (command_uid, content_sha256, now),
            )
            return "ACCEPTED"

    def mark_inbound_business_accepted(
        self,
        command_uid: str,
        content_sha256: str,
    ) -> str:
        command_uid = _require_uuid4(command_uid, "command UID")
        content_sha256 = _require_sha256(content_sha256, "command content SHA-256")
        now = self._now_text()
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT content_sha256, state
                   FROM inbound_command_ledger WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is None:
                return "UNKNOWN"
            if row["content_sha256"] != content_sha256:
                return "CONFLICT"
            if row["state"] == INBOUND_BUSINESS_ACCEPTED:
                return "DUPLICATE"
            updated = connection.execute(
                """UPDATE inbound_command_ledger
                   SET state='BUSINESS_ACCEPTED', business_accepted_at=?
                   WHERE command_uid=? AND state='RECEIVED'""",
                (now, command_uid),
            )
            if updated.rowcount != 1:
                raise RuntimeError("inbound command ledger did not advance")
            return "ACCEPTED"

    def get_inbound_command(self, command_uid: str) -> dict[str, Any] | None:
        command_uid = _require_uuid4(command_uid, "command UID")
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM inbound_command_ledger WHERE command_uid=?",
                (command_uid,),
            ).fetchone()
        return dict(row) if row is not None else None

    def receive_outbound_business_event(
        self,
        event_uid: str,
        content_sha256: str,
    ) -> str:
        event_uid = _require_uuid4(event_uid, "event UID")
        content_sha256 = _require_sha256(content_sha256, "event content SHA-256")
        now = self._now_text()
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT content_sha256, state
                   FROM outbound_business_event_ledger WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if row is not None:
                if row["content_sha256"] != content_sha256:
                    return "CONFLICT"
                return (
                    "DUPLICATE_PLATFORM_CONFIRMED"
                    if row["state"] == OUTBOUND_PLATFORM_CONFIRMED
                    else "DUPLICATE"
                )
            connection.execute(
                """INSERT INTO outbound_business_event_ledger
                   (event_uid, content_sha256, state, created_at)
                   VALUES (?, ?, 'PENDING', ?)""",
                (event_uid, content_sha256, now),
            )
            return "ACCEPTED"

    def begin_outbound_send_attempt(
        self,
        event_uid: str,
        content_sha256: str,
        attempt_uid: str,
    ) -> dict[str, Any]:
        event_uid = _require_uuid4(event_uid, "event UID")
        content_sha256 = _require_sha256(content_sha256, "event content SHA-256")
        attempt_uid = _require_uuid4(attempt_uid, "attempt UID")
        now = self._now_text()
        with self.transaction() as connection:
            event = connection.execute(
                """SELECT content_sha256, state
                   FROM outbound_business_event_ledger WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if event is None:
                return {"disposition": "UNKNOWN"}
            if event["content_sha256"] != content_sha256:
                return {"disposition": "CONFLICT"}
            existing = connection.execute(
                "SELECT * FROM outbound_send_attempt WHERE attempt_uid=?",
                (attempt_uid,),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["event_uid"] == event_uid
                    and existing["content_sha256"] == content_sha256
                )
                return {
                    "disposition": "DUPLICATE" if same else "CONFLICT",
                    "attemptSequence": existing["attempt_sequence"],
                    "outcome": existing["outcome"],
                }
            if event["state"] == OUTBOUND_PLATFORM_CONFIRMED:
                return {"disposition": "ALREADY_CONFIRMED"}
            sequence = int(
                connection.execute(
                    """SELECT COALESCE(MAX(attempt_sequence), 0) + 1
                       FROM outbound_send_attempt WHERE event_uid=?""",
                    (event_uid,),
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO outbound_send_attempt
                   (attempt_uid, event_uid, attempt_sequence,
                    content_sha256, outcome, started_at)
                   VALUES (?, ?, ?, ?, 'STARTED', ?)""",
                (attempt_uid, event_uid, sequence, content_sha256, now),
            )
            updated = connection.execute(
                """UPDATE outbound_business_event_ledger
                   SET state='SENDING', last_send_attempt_at=?
                   WHERE event_uid=? AND state IN ('PENDING', 'SENDING')""",
                (now, event_uid),
            )
            if updated.rowcount != 1:
                raise RuntimeError("outbound event ledger did not advance")
            return {
                "disposition": "ACCEPTED",
                "attemptSequence": sequence,
                "outcome": SEND_ATTEMPT_STARTED,
            }

    def complete_outbound_send_attempt(
        self,
        event_uid: str,
        content_sha256: str,
        attempt_uid: str,
        outcome: str,
    ) -> str:
        event_uid = _require_uuid4(event_uid, "event UID")
        content_sha256 = _require_sha256(content_sha256, "event content SHA-256")
        attempt_uid = _require_uuid4(attempt_uid, "attempt UID")
        if outcome not in SEND_ATTEMPT_OUTCOMES:
            raise ValueError("send attempt outcome is invalid")
        now = self._now_text()
        with self.transaction() as connection:
            attempt = connection.execute(
                "SELECT * FROM outbound_send_attempt WHERE attempt_uid=?",
                (attempt_uid,),
            ).fetchone()
            if attempt is None:
                return "UNKNOWN"
            if (
                attempt["event_uid"] != event_uid
                or attempt["content_sha256"] != content_sha256
            ):
                return "CONFLICT"
            if attempt["outcome"] != SEND_ATTEMPT_STARTED:
                return "DUPLICATE" if attempt["outcome"] == outcome else "CONFLICT"
            updated = connection.execute(
                """UPDATE outbound_send_attempt
                   SET outcome=?, completed_at=?
                   WHERE attempt_uid=? AND outcome='STARTED'""",
                (outcome, now, attempt_uid),
            )
            if updated.rowcount != 1:
                raise RuntimeError("outbound send attempt did not advance")
            return "ACCEPTED"

    def confirm_outbound_business_event(
        self,
        event_uid: str,
        content_sha256: str,
        confirmation_uid: str,
        confirmation_sha256: str,
    ) -> str:
        event_uid = _require_uuid4(event_uid, "event UID")
        content_sha256 = _require_sha256(content_sha256, "event content SHA-256")
        confirmation_uid = _require_uuid4(confirmation_uid, "confirmation UID")
        confirmation_sha256 = _require_sha256(
            confirmation_sha256,
            "confirmation content SHA-256",
        )
        now = self._now_text()
        with self.transaction() as connection:
            event = connection.execute(
                """SELECT content_sha256, state
                   FROM outbound_business_event_ledger WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if event is None:
                return "UNKNOWN"
            if event["content_sha256"] != content_sha256:
                return "CONFLICT"
            by_uid = connection.execute(
                """SELECT event_uid, confirmation_sha256
                   FROM outbound_platform_confirmation
                   WHERE confirmation_uid=?""",
                (confirmation_uid,),
            ).fetchone()
            by_event = connection.execute(
                """SELECT confirmation_uid, confirmation_sha256
                   FROM outbound_platform_confirmation WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if by_uid is not None or by_event is not None:
                same = bool(
                    by_uid is not None
                    and by_event is not None
                    and by_uid["event_uid"] == event_uid
                    and by_uid["confirmation_sha256"] == confirmation_sha256
                    and by_event["confirmation_uid"] == confirmation_uid
                    and by_event["confirmation_sha256"] == confirmation_sha256
                )
                return "DUPLICATE" if same else "CONFLICT"
            if event["state"] == OUTBOUND_PLATFORM_CONFIRMED:
                raise RuntimeError(
                    "outbound event is confirmed without confirmation evidence"
                )
            connection.execute(
                """INSERT INTO outbound_platform_confirmation
                   (confirmation_uid, event_uid,
                    confirmation_sha256, received_at)
                   VALUES (?, ?, ?, ?)""",
                (confirmation_uid, event_uid, confirmation_sha256, now),
            )
            updated = connection.execute(
                """UPDATE outbound_business_event_ledger
                   SET state='PLATFORM_CONFIRMED', platform_confirmed_at=?
                   WHERE event_uid=? AND state IN ('PENDING', 'SENDING')""",
                (now, event_uid),
            )
            if updated.rowcount != 1:
                raise RuntimeError("outbound event ledger did not confirm")
            return "ACCEPTED"

    def get_outbound_business_event(
        self,
        event_uid: str,
    ) -> dict[str, Any] | None:
        event_uid = _require_uuid4(event_uid, "event UID")
        with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                """SELECT * FROM outbound_business_event_ledger
                   WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if row is None:
                return None
            attempts = connection.execute(
                """SELECT attempt_uid, attempt_sequence, outcome,
                          started_at, completed_at
                   FROM outbound_send_attempt WHERE event_uid=?
                   ORDER BY attempt_sequence""",
                (event_uid,),
            ).fetchall()
            confirmation = connection.execute(
                """SELECT confirmation_uid, confirmation_sha256, received_at
                   FROM outbound_platform_confirmation WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
        result = dict(row)
        result["attempts"] = [dict(attempt) for attempt in attempts]
        result["platform_confirmation"] = (
            dict(confirmation) if confirmation is not None else None
        )
        return result

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            connection = self._require_connection()
            schema_version = int(
                connection.execute(
                    "SELECT version FROM schema_version WHERE singleton_id=1"
                ).fetchone()[0]
            )
            row = connection.execute(
                """SELECT start_uid, release_version, process_id, started_at
                   FROM process_start_fact ORDER BY sequence DESC LIMIT 1"""
            ).fetchone()
            counts = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM process_start_fact),
                    (SELECT COUNT(*) FROM inbound_command_ledger),
                    (SELECT COUNT(*) FROM inbound_command_ledger
                     WHERE state='BUSINESS_ACCEPTED'),
                    (SELECT COUNT(*) FROM outbound_business_event_ledger),
                    (SELECT COUNT(*) FROM outbound_send_attempt),
                    (SELECT COUNT(*) FROM outbound_business_event_ledger
                     WHERE state='PLATFORM_CONFIRMED')"""
            ).fetchone()
        latest = None
        if row is not None:
            latest = {
                "startUid": row["start_uid"],
                "releaseVersion": row["release_version"],
                "processId": int(row["process_id"]),
                "startedAt": row["started_at"],
            }
        return {
            "schemaVersion": schema_version,
            "processStartCount": int(counts[0]),
            "latestProcessStart": latest,
            "inboundCommandCount": int(counts[1]),
            "inboundBusinessAcceptedCount": int(counts[2]),
            "outboundBusinessEventCount": int(counts[3]),
            "outboundSendAttemptCount": int(counts[4]),
            "outboundPlatformConfirmedCount": int(counts[5]),
        }

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("communication store is not initialized")
        return self._connection

    def _now_text(self) -> str:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("communication clock must be timezone-aware")
        rendered = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        return rendered.replace("+00:00", "Z")


def _require_exact_columns(
    connection: sqlite3.Connection,
    table: str,
    expected: tuple[str, ...],
) -> None:
    actual = tuple(
        row["name"]
        for row in connection.execute(f"PRAGMA table_info('{table}')")
    )
    if actual != expected:
        raise RuntimeError("communication database schema is incompatible")


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError(f"{field} must be a lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _require_release_version(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_RELEASE_VERSION_LENGTH
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError("communication release version is invalid")
    return value

"""Private durable state owned only by the permanent communication agent.

The stage-four candidate adds the permanent transport ledger while keeping
OneNet ownership and remote routing disabled.  The ledger is intentionally
usable without a network client so migration and rollback behaviour can be
qualified before the later ownership cut-over.
"""

from __future__ import annotations

import hashlib
import json
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
PROXY_EXTENSION_VERSION = 1
MAX_RELEASE_VERSION_LENGTH = 32
MAX_PROXY_JSON_BYTES = 192 * 1024
MANAGEMENT_EVENT_SEQUENCE_MIN = 9_000_000_000_000
MAX_EDGE_EVENT_SEQUENCE = 9_999_999_999_999

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
                self._ensure_proxy_extension(connection)
                self._verify_proxy_extension(connection)
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

    def _ensure_proxy_extension(self, connection: sqlite3.Connection) -> None:
        """Install an additive proxy ledger that old stage-three code ignores.

        The base schema deliberately remains version two.  A device can carry
        this default-off candidate and still boot the previous status-only
        communication agent before OneNet ownership has been switched.
        """

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        extension_tables = {
            "communication_proxy_extension",
            "inbound_proxy_payload",
            "outbound_proxy_event",
            "outbound_proxy_platform_result",
            "outbound_proxy_result_delivery",
        }
        present = tables & extension_tables
        if present:
            if present != extension_tables:
                raise RuntimeError(
                    "communication proxy extension is incomplete"
                )
            return

        statements = (
            """CREATE TABLE communication_proxy_extension (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                extension_version INTEGER NOT NULL
                    CHECK (extension_version = 1),
                installed_at TEXT NOT NULL
            )""",
            """CREATE TABLE inbound_proxy_payload (
                command_uid TEXT PRIMARY KEY,
                service_id TEXT NOT NULL CHECK (
                    length(service_id) BETWEEN 1 AND 128
                ),
                params_json TEXT NOT NULL CHECK (
                    length(params_json) BETWEEN 2 AND 196608
                ),
                response_json TEXT CHECK (
                    response_json IS NULL
                    OR length(response_json) BETWEEN 2 AND 196608
                ),
                FOREIGN KEY (command_uid)
                    REFERENCES inbound_command_ledger(command_uid)
            )""",
            """CREATE TABLE outbound_proxy_event (
                event_uid TEXT PRIMARY KEY,
                event_type TEXT NOT NULL CHECK (
                    length(event_type) BETWEEN 1 AND 128
                ),
                params_json TEXT NOT NULL CHECK (
                    length(params_json) BETWEEN 2 AND 196608
                ),
                edge_event_sequence INTEGER NOT NULL UNIQUE CHECK (
                    edge_event_sequence BETWEEN 1 AND 9999999999999
                ),
                requested_generation INTEGER NOT NULL DEFAULT 1 CHECK (
                    requested_generation > 0
                ),
                completed_generation INTEGER NOT NULL DEFAULT 0 CHECK (
                    completed_generation >= 0
                    AND completed_generation <= requested_generation
                ),
                active_generation INTEGER CHECK (
                    active_generation IS NULL
                    OR (
                        active_generation > completed_generation
                        AND active_generation <= requested_generation
                    )
                ),
                active_attempt_uid TEXT,
                retry_not_before TEXT,
                CHECK (
                    (active_generation IS NULL
                     AND active_attempt_uid IS NULL)
                    OR
                    (active_generation IS NOT NULL
                     AND active_attempt_uid IS NOT NULL)
                ),
                FOREIGN KEY (event_uid)
                    REFERENCES outbound_business_event_ledger(event_uid),
                FOREIGN KEY (active_attempt_uid)
                    REFERENCES outbound_send_attempt(attempt_uid)
            )""",
            """CREATE TABLE outbound_proxy_platform_result (
                result_uid TEXT PRIMARY KEY,
                event_uid TEXT NOT NULL,
                dispatch_generation INTEGER NOT NULL CHECK (
                    dispatch_generation > 0
                ),
                edge_event_sequence INTEGER NOT NULL CHECK (
                    edge_event_sequence BETWEEN 1 AND 9999999999999
                ),
                result_code INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                FOREIGN KEY (event_uid)
                    REFERENCES outbound_business_event_ledger(event_uid)
            )""",
            """CREATE TABLE outbound_proxy_result_delivery (
                result_uid TEXT PRIMARY KEY,
                delivered_at TEXT NOT NULL,
                FOREIGN KEY (result_uid)
                    REFERENCES outbound_proxy_platform_result(result_uid)
            )""",
            """CREATE TRIGGER communication_proxy_extension_no_update
               BEFORE UPDATE ON communication_proxy_extension
               BEGIN SELECT RAISE(ABORT, 'proxy extension marker is immutable'); END""",
            """CREATE TRIGGER communication_proxy_extension_no_delete
               BEFORE DELETE ON communication_proxy_extension
               BEGIN SELECT RAISE(ABORT, 'proxy extension marker is immutable'); END""",
            """CREATE TRIGGER inbound_proxy_payload_no_delete
               BEFORE DELETE ON inbound_proxy_payload
               BEGIN SELECT RAISE(ABORT, 'inbound proxy payload is append-only'); END""",
            """CREATE TRIGGER inbound_proxy_payload_monotonic_update
               BEFORE UPDATE ON inbound_proxy_payload
               WHEN OLD.command_uid <> NEW.command_uid
                 OR OLD.service_id <> NEW.service_id
                 OR OLD.params_json <> NEW.params_json
                 OR OLD.response_json IS NOT NULL
                 OR NEW.response_json IS NULL
               BEGIN SELECT RAISE(ABORT, 'inbound proxy payload update is not monotonic'); END""",
            """CREATE TRIGGER outbound_proxy_event_no_delete
               BEFORE DELETE ON outbound_proxy_event
               BEGIN SELECT RAISE(ABORT, 'outbound proxy event is append-only'); END""",
            """CREATE TRIGGER outbound_proxy_event_monotonic_update
               BEFORE UPDATE ON outbound_proxy_event
               WHEN OLD.event_uid <> NEW.event_uid
                 OR OLD.event_type <> NEW.event_type
                 OR OLD.params_json <> NEW.params_json
                 OR OLD.edge_event_sequence <> NEW.edge_event_sequence
                 OR NEW.requested_generation < OLD.requested_generation
                 OR NEW.completed_generation < OLD.completed_generation
               BEGIN SELECT RAISE(ABORT, 'outbound proxy event update is not monotonic'); END""",
            """CREATE TRIGGER outbound_proxy_result_no_update
               BEFORE UPDATE ON outbound_proxy_platform_result
               BEGIN SELECT RAISE(ABORT, 'proxy platform result is immutable'); END""",
            """CREATE TRIGGER outbound_proxy_result_no_delete
               BEFORE DELETE ON outbound_proxy_platform_result
               BEGIN SELECT RAISE(ABORT, 'proxy platform result is append-only'); END""",
            """CREATE TRIGGER outbound_proxy_delivery_no_update
               BEFORE UPDATE ON outbound_proxy_result_delivery
               BEGIN SELECT RAISE(ABORT, 'proxy result delivery is immutable'); END""",
            """CREATE TRIGGER outbound_proxy_delivery_no_delete
               BEFORE DELETE ON outbound_proxy_result_delivery
               BEGIN SELECT RAISE(ABORT, 'proxy result delivery is append-only'); END""",
        )
        for statement in statements:
            connection.execute(statement)
        connection.execute(
            """INSERT INTO communication_proxy_extension
               (singleton_id, extension_version, installed_at)
               VALUES (
                   1, ?,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               )""",
            (PROXY_EXTENSION_VERSION,),
        )

    @staticmethod
    def _verify_proxy_extension(connection: sqlite3.Connection) -> None:
        expected = {
            "communication_proxy_extension": (
                "singleton_id",
                "extension_version",
                "installed_at",
            ),
            "inbound_proxy_payload": (
                "command_uid",
                "service_id",
                "params_json",
                "response_json",
            ),
            "outbound_proxy_event": (
                "event_uid",
                "event_type",
                "params_json",
                "edge_event_sequence",
                "requested_generation",
                "completed_generation",
                "active_generation",
                "active_attempt_uid",
                "retry_not_before",
            ),
            "outbound_proxy_platform_result": (
                "result_uid",
                "event_uid",
                "dispatch_generation",
                "edge_event_sequence",
                "result_code",
                "received_at",
            ),
            "outbound_proxy_result_delivery": (
                "result_uid",
                "delivered_at",
            ),
        }
        for table, columns in expected.items():
            _require_exact_columns(connection, table, columns)
        marker = connection.execute(
            """SELECT singleton_id, extension_version
               FROM communication_proxy_extension"""
        ).fetchall()
        if [tuple(row) for row in marker] != [(1, PROXY_EXTENSION_VERSION)]:
            raise RuntimeError(
                "communication proxy extension is incompatible"
            )
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        required = {
            "communication_proxy_extension_no_update",
            "communication_proxy_extension_no_delete",
            "inbound_proxy_payload_no_delete",
            "inbound_proxy_payload_monotonic_update",
            "outbound_proxy_event_no_delete",
            "outbound_proxy_event_monotonic_update",
            "outbound_proxy_result_no_update",
            "outbound_proxy_result_no_delete",
            "outbound_proxy_delivery_no_update",
            "outbound_proxy_delivery_no_delete",
        }
        if not required.issubset(trigger_names):
            raise RuntimeError(
                "communication proxy extension is incompatible"
            )

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

    def receive_proxy_inbound_command(
        self,
        command_uid: str,
        service_id: str,
        params: dict[str, Any],
        *,
        identity_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one cloud command before contacting its local owner.

        ``identity_params`` is the credential-free stable projection used for
        the durable digest and payload.  The caller may still forward the
        original in-memory parameters to a maintenance owner.  In particular,
        a presigned download URL must never enter this database and refreshing
        that URL must not create an idempotency conflict.
        """

        command_uid = _require_uuid4(command_uid, "command UID")
        service_id = _require_proxy_identifier(service_id, "service ID")
        stable_params = params if identity_params is None else identity_params
        params_json, content_sha256 = _canonical_proxy_document(
            "ecobin.communication.inbound-command",
            {"serviceId": service_id, "params": stable_params},
        )
        # Store only the raw params in the payload row.  The digest above also
        # binds the service identifier so the same command UID cannot cross
        # service routes without producing a conflict.
        raw_params_json = _canonical_proxy_json(
            stable_params, "stable service params"
        )
        now = self._now_text()
        with self.transaction() as connection:
            ledger = connection.execute(
                """SELECT content_sha256, state
                   FROM inbound_command_ledger WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if ledger is None:
                connection.execute(
                    """INSERT INTO inbound_command_ledger
                       (command_uid, content_sha256, state, received_at)
                       VALUES (?, ?, 'RECEIVED', ?)""",
                    (command_uid, content_sha256, now),
                )
                connection.execute(
                    """INSERT INTO inbound_proxy_payload
                       (command_uid, service_id, params_json)
                       VALUES (?, ?, ?)""",
                    (command_uid, service_id, raw_params_json),
                )
                return {
                    "disposition": "ACCEPTED",
                    "contentSha256": content_sha256,
                    "responseData": None,
                }

            payload = connection.execute(
                """SELECT service_id, params_json, response_json
                   FROM inbound_proxy_payload WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if (
                ledger["content_sha256"] != content_sha256
                or payload is None
                or payload["service_id"] != service_id
                or payload["params_json"] != raw_params_json
            ):
                return {
                    "disposition": "CONFLICT",
                    "contentSha256": content_sha256,
                    "responseData": None,
                }
            response = (
                json.loads(payload["response_json"])
                if payload["response_json"] is not None
                else None
            )
            return {
                "disposition": (
                    "DUPLICATE_BUSINESS_ACCEPTED"
                    if ledger["state"] == INBOUND_BUSINESS_ACCEPTED
                    else "DUPLICATE_RECEIVED"
                ),
                "contentSha256": content_sha256,
                "responseData": response,
            }

    def mark_proxy_inbound_business_accepted(
        self,
        command_uid: str,
        content_sha256: str,
        response_data: dict[str, Any],
    ) -> str:
        """Atomically bind the business response to permanent acceptance."""

        command_uid = _require_uuid4(command_uid, "command UID")
        content_sha256 = _require_sha256(content_sha256, "command content SHA-256")
        response_json = _canonical_proxy_json(
            response_data,
            "business response",
        )
        now = self._now_text()
        with self.transaction() as connection:
            ledger = connection.execute(
                """SELECT content_sha256, state
                   FROM inbound_command_ledger WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            payload = connection.execute(
                """SELECT response_json FROM inbound_proxy_payload
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if ledger is None or payload is None:
                return "UNKNOWN"
            if ledger["content_sha256"] != content_sha256:
                return "CONFLICT"
            if ledger["state"] == INBOUND_BUSINESS_ACCEPTED:
                return (
                    "DUPLICATE"
                    if payload["response_json"] == response_json
                    else "CONFLICT"
                )
            if payload["response_json"] is not None:
                raise RuntimeError(
                    "proxy response exists before business acceptance"
                )
            response_updated = connection.execute(
                """UPDATE inbound_proxy_payload SET response_json=?
                   WHERE command_uid=? AND response_json IS NULL""",
                (response_json, command_uid),
            )
            ledger_updated = connection.execute(
                """UPDATE inbound_command_ledger
                   SET state='BUSINESS_ACCEPTED', business_accepted_at=?
                   WHERE command_uid=? AND state='RECEIVED'""",
                (now, command_uid),
            )
            if response_updated.rowcount != 1 or ledger_updated.rowcount != 1:
                raise RuntimeError(
                    "proxy inbound command did not advance atomically"
                )
            return "ACCEPTED"

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

    def submit_proxy_outbound_event(
        self,
        event_uid: str,
        event_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Durably accept one business-to-cloud delivery request.

        At most one generation remains outstanding.  Repeating an RPC whose
        response was lost therefore does not grow an unbounded queue.  Once a
        cloud result completed that generation, a later business retry opens
        exactly one new generation for the same immutable event.
        """

        event_uid = _require_uuid4(event_uid, "event UID")
        event_type = _require_proxy_identifier(event_type, "event type")
        params_json = _canonical_proxy_json(params, "event params")
        _document_json, content_sha256 = _canonical_proxy_document(
            "ecobin.communication.outbound-event",
            {"eventType": event_type, "params": params},
        )
        edge_sequence = params.get("edgeEventSequence")
        if (
            isinstance(edge_sequence, bool)
            or not isinstance(edge_sequence, int)
            or not 1 <= edge_sequence < MANAGEMENT_EVENT_SEQUENCE_MIN
        ):
            raise ValueError(
                "business event edgeEventSequence is outside its reserved range"
            )
        now = self._now_text()
        with self.transaction() as connection:
            ledger = connection.execute(
                """SELECT content_sha256, state
                   FROM outbound_business_event_ledger WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if ledger is None:
                connection.execute(
                    """INSERT INTO outbound_business_event_ledger
                       (event_uid, content_sha256, state, created_at)
                       VALUES (?, ?, 'PENDING', ?)""",
                    (event_uid, content_sha256, now),
                )
                connection.execute(
                    """INSERT INTO outbound_proxy_event
                       (event_uid, event_type, params_json,
                        edge_event_sequence)
                       VALUES (?, ?, ?, ?)""",
                    (event_uid, event_type, params_json, edge_sequence),
                )
                return {
                    "disposition": "ACCEPTED",
                    "contentSha256": content_sha256,
                    "dispatchGeneration": 1,
                }

            proxy = connection.execute(
                "SELECT * FROM outbound_proxy_event WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if (
                ledger["content_sha256"] != content_sha256
                or proxy is None
                or proxy["event_type"] != event_type
                or proxy["params_json"] != params_json
                or proxy["edge_event_sequence"] != edge_sequence
            ):
                return {
                    "disposition": "CONFLICT",
                    "contentSha256": content_sha256,
                    "dispatchGeneration": None,
                }
            requested = int(proxy["requested_generation"])
            completed = int(proxy["completed_generation"])
            if requested == completed and proxy["active_generation"] is None:
                requested += 1
                updated = connection.execute(
                    """UPDATE outbound_proxy_event
                       SET requested_generation=?, retry_not_before=NULL
                       WHERE event_uid=?
                         AND requested_generation=completed_generation
                         AND active_generation IS NULL""",
                    (requested, event_uid),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        "proxy outbound generation did not advance"
                    )
            return {
                "disposition": "DUPLICATE",
                "contentSha256": content_sha256,
                "dispatchGeneration": requested,
            }

    def submit_proxy_management_event(
        self,
        event_uid: str,
        event_type: str,
        params_without_sequence: dict[str, Any],
    ) -> dict[str, Any]:
        """Durably assign and enqueue one updater-owned management event.

        The permanent communication process is the only allocator for the
        reserved high sequence range.  Callers repeat the same immutable
        envelope without a sequence; the first durable acceptance freezes the
        assigned sequence and later retries reuse it.
        """

        event_uid = _require_uuid4(event_uid, "event UID")
        event_type = _require_proxy_identifier(event_type, "event type")
        stable = dict(params_without_sequence)
        if "edgeEventSequence" in stable:
            raise ValueError(
                "management event sequence must be assigned by communication"
            )
        if stable.get("eventUid") != event_uid:
            raise ValueError("management event UID differs from its envelope")
        if stable.get("eventType") != event_type:
            raise ValueError("management event type differs from its envelope")
        stable_json = _canonical_proxy_json(stable, "management event params")
        now = self._now_text()
        with self.transaction() as connection:
            ledger = connection.execute(
                """SELECT content_sha256, state
                   FROM outbound_business_event_ledger WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if ledger is None:
                largest = connection.execute(
                    "SELECT MAX(edge_event_sequence) FROM outbound_proxy_event"
                ).fetchone()[0]
                edge_sequence = max(
                    MANAGEMENT_EVENT_SEQUENCE_MIN - 1,
                    int(largest) if largest is not None else 0,
                ) + 1
                if edge_sequence > MAX_EDGE_EVENT_SEQUENCE:
                    raise RuntimeError(
                        "management edge event sequence range is exhausted"
                    )
                params = {**stable, "edgeEventSequence": edge_sequence}
                params_json = _canonical_proxy_json(params, "event params")
                _document_json, content_sha256 = _canonical_proxy_document(
                    "ecobin.communication.outbound-event",
                    {"eventType": event_type, "params": params},
                )
                connection.execute(
                    """INSERT INTO outbound_business_event_ledger
                       (event_uid, content_sha256, state, created_at)
                       VALUES (?, ?, 'PENDING', ?)""",
                    (event_uid, content_sha256, now),
                )
                connection.execute(
                    """INSERT INTO outbound_proxy_event
                       (event_uid, event_type, params_json,
                        edge_event_sequence)
                       VALUES (?, ?, ?, ?)""",
                    (event_uid, event_type, params_json, edge_sequence),
                )
                return {
                    "disposition": "ACCEPTED",
                    "contentSha256": content_sha256,
                    "dispatchGeneration": 1,
                    "edgeEventSequence": edge_sequence,
                }

            proxy = connection.execute(
                "SELECT * FROM outbound_proxy_event WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if proxy is None or proxy["event_type"] != event_type:
                return {
                    "disposition": "CONFLICT",
                    "contentSha256": None,
                    "dispatchGeneration": None,
                    "edgeEventSequence": None,
                }
            persisted = json.loads(proxy["params_json"])
            persisted.pop("edgeEventSequence", None)
            if _canonical_proxy_json(
                persisted, "persisted management event params"
            ) != stable_json:
                return {
                    "disposition": "CONFLICT",
                    "contentSha256": None,
                    "dispatchGeneration": None,
                    "edgeEventSequence": None,
                }
            requested = int(proxy["requested_generation"])
            completed = int(proxy["completed_generation"])
            if requested == completed and proxy["active_generation"] is None:
                requested += 1
                updated = connection.execute(
                    """UPDATE outbound_proxy_event
                       SET requested_generation=?, retry_not_before=NULL
                       WHERE event_uid=?
                         AND requested_generation=completed_generation
                         AND active_generation IS NULL""",
                    (requested, event_uid),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        "proxy management generation did not advance"
                    )
            return {
                "disposition": "DUPLICATE",
                "contentSha256": ledger["content_sha256"],
                "dispatchGeneration": requested,
                "edgeEventSequence": int(proxy["edge_event_sequence"]),
            }

    def claim_next_proxy_outbound_event(self) -> dict[str, Any] | None:
        """Claim one pending generation and append its durable send attempt."""

        now = self._now_text()
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT proxy.*, ledger.content_sha256, ledger.state
                   FROM outbound_proxy_event AS proxy
                   JOIN outbound_business_event_ledger AS ledger
                     ON ledger.event_uid=proxy.event_uid
                   WHERE proxy.completed_generation
                         < proxy.requested_generation
                     AND proxy.active_generation IS NULL
                     AND (proxy.retry_not_before IS NULL
                          OR proxy.retry_not_before <= ?)
                   ORDER BY proxy.rowid
                   LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            attempt_uid = str(uuid.uuid4())
            sequence = int(
                connection.execute(
                    """SELECT COALESCE(MAX(attempt_sequence), 0) + 1
                       FROM outbound_send_attempt WHERE event_uid=?""",
                    (row["event_uid"],),
                ).fetchone()[0]
            )
            generation = int(row["completed_generation"]) + 1
            connection.execute(
                """INSERT INTO outbound_send_attempt
                   (attempt_uid, event_uid, attempt_sequence,
                    content_sha256, outcome, started_at)
                   VALUES (?, ?, ?, ?, 'STARTED', ?)""",
                (
                    attempt_uid,
                    row["event_uid"],
                    sequence,
                    row["content_sha256"],
                    now,
                ),
            )
            if row["state"] in {OUTBOUND_PENDING, OUTBOUND_SENDING}:
                updated = connection.execute(
                    """UPDATE outbound_business_event_ledger
                       SET state='SENDING', last_send_attempt_at=?
                       WHERE event_uid=?
                         AND state IN ('PENDING', 'SENDING')""",
                    (now, row["event_uid"]),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        "proxy outbound ledger did not enter sending"
                    )
            connection.execute(
                """UPDATE outbound_proxy_event
                   SET active_generation=?, active_attempt_uid=?,
                       retry_not_before=NULL
                   WHERE event_uid=? AND active_generation IS NULL""",
                (generation, attempt_uid, row["event_uid"]),
            )
            return {
                "eventUid": row["event_uid"],
                "eventType": row["event_type"],
                "params": json.loads(row["params_json"]),
                "contentSha256": row["content_sha256"],
                "dispatchGeneration": generation,
                "attemptUid": attempt_uid,
                "attemptSequence": sequence,
            }

    def acknowledge_proxy_transport(self, event_uid: str) -> str:
        """Record a QoS transport acknowledgement without completing delivery."""

        event_uid = _require_uuid4(event_uid, "event UID")
        now = self._now_text()
        with self.transaction() as connection:
            proxy = connection.execute(
                """SELECT active_attempt_uid FROM outbound_proxy_event
                   WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if proxy is None:
                return "UNKNOWN"
            attempt_uid = proxy["active_attempt_uid"]
            if attempt_uid is None:
                return "DUPLICATE"
            attempt = connection.execute(
                """SELECT outcome FROM outbound_send_attempt
                   WHERE attempt_uid=?""",
                (attempt_uid,),
            ).fetchone()
            if attempt is None:
                raise RuntimeError("proxy active send attempt is absent")
            if attempt["outcome"] == SEND_ATTEMPT_STARTED:
                connection.execute(
                    """UPDATE outbound_send_attempt
                       SET outcome='TRANSPORT_ACCEPTED', completed_at=?
                       WHERE attempt_uid=? AND outcome='STARTED'""",
                    (now, attempt_uid),
                )
                return "ACCEPTED"
            return (
                "DUPLICATE"
                if attempt["outcome"] == "TRANSPORT_ACCEPTED"
                else "CONFLICT"
            )

    def fail_proxy_send(
        self,
        event_uid: str,
        *,
        retry_not_before: datetime,
        outcome: str = "RETRYABLE_FAILURE",
    ) -> str:
        """Close one local send attempt and leave its generation pending."""

        event_uid = _require_uuid4(event_uid, "event UID")
        if outcome not in {"RETRYABLE_FAILURE", "RESULT_UNKNOWN"}:
            raise ValueError("proxy retry outcome is invalid")
        retry_text = self._format_clock_value(retry_not_before)
        now = self._now_text()
        with self.transaction() as connection:
            proxy = connection.execute(
                """SELECT active_attempt_uid FROM outbound_proxy_event
                   WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
            if proxy is None:
                return "UNKNOWN"
            attempt_uid = proxy["active_attempt_uid"]
            if attempt_uid is None:
                return "DUPLICATE"
            attempt = connection.execute(
                "SELECT outcome FROM outbound_send_attempt WHERE attempt_uid=?",
                (attempt_uid,),
            ).fetchone()
            if attempt is None:
                raise RuntimeError("proxy active send attempt is absent")
            if attempt["outcome"] == SEND_ATTEMPT_STARTED:
                connection.execute(
                    """UPDATE outbound_send_attempt
                       SET outcome=?, completed_at=?
                       WHERE attempt_uid=? AND outcome='STARTED'""",
                    (outcome, now, attempt_uid),
                )
            connection.execute(
                """UPDATE outbound_proxy_event
                   SET active_generation=NULL, active_attempt_uid=NULL,
                       retry_not_before=?
                   WHERE event_uid=? AND active_attempt_uid=?""",
                (retry_text, event_uid, attempt_uid),
            )
            return "ACCEPTED"

    def recover_proxy_sends(self) -> int:
        """Turn process-interrupted active attempts into retryable uncertainty."""

        now = self._now_text()
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT event_uid, active_attempt_uid
                   FROM outbound_proxy_event
                   WHERE active_attempt_uid IS NOT NULL"""
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE outbound_send_attempt
                       SET outcome='RESULT_UNKNOWN', completed_at=?
                       WHERE attempt_uid=? AND outcome='STARTED'""",
                    (now, row["active_attempt_uid"]),
                )
                connection.execute(
                    """UPDATE outbound_proxy_event
                       SET active_generation=NULL, active_attempt_uid=NULL,
                           retry_not_before=NULL
                       WHERE event_uid=?
                         AND active_attempt_uid=?""",
                    (row["event_uid"], row["active_attempt_uid"]),
                )
            return len(rows)

    def record_proxy_platform_result(
        self,
        edge_event_sequence: int,
        code: int,
    ) -> dict[str, Any] | None:
        """Persist one OneNet result and complete its current send generation."""

        if (
            isinstance(edge_event_sequence, bool)
            or not isinstance(edge_event_sequence, int)
            or not 1 <= edge_event_sequence <= 9_999_999_999_999
        ):
            raise ValueError("edge event sequence is invalid")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("platform result code is invalid")
        now = self._now_text()
        result_uid = str(uuid.uuid4())
        with self.transaction() as connection:
            proxy = connection.execute(
                """SELECT * FROM outbound_proxy_event
                   WHERE edge_event_sequence=?""",
                (edge_event_sequence,),
            ).fetchone()
            if proxy is None:
                return None
            generation = (
                int(proxy["active_generation"])
                if proxy["active_generation"] is not None
                else max(
                    int(proxy["completed_generation"]),
                    min(
                        int(proxy["requested_generation"]),
                        int(proxy["completed_generation"]) + 1,
                    ),
                )
            )
            attempt_uid = proxy["active_attempt_uid"]
            if attempt_uid is not None:
                connection.execute(
                    """UPDATE outbound_send_attempt
                       SET outcome='TRANSPORT_ACCEPTED', completed_at=?
                       WHERE attempt_uid=? AND outcome='STARTED'""",
                    (now, attempt_uid),
                )
            connection.execute(
                """INSERT INTO outbound_proxy_platform_result
                   (result_uid, event_uid, dispatch_generation,
                    edge_event_sequence, result_code, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    result_uid,
                    proxy["event_uid"],
                    generation,
                    edge_event_sequence,
                    code,
                    now,
                ),
            )
            connection.execute(
                """UPDATE outbound_proxy_event
                   SET completed_generation=MAX(completed_generation, ?),
                       active_generation=NULL, active_attempt_uid=NULL,
                       retry_not_before=NULL
                   WHERE event_uid=?""",
                (generation, proxy["event_uid"]),
            )
            if code in {0, 200}:
                ledger = connection.execute(
                    """SELECT state, content_sha256
                       FROM outbound_business_event_ledger
                       WHERE event_uid=?""",
                    (proxy["event_uid"],),
                ).fetchone()
                if ledger is None:
                    raise RuntimeError("proxy outbound ledger is absent")
                if ledger["state"] != OUTBOUND_PLATFORM_CONFIRMED:
                    confirmation_sha256 = hashlib.sha256(
                        (
                            "ecobin.communication.platform-result\n"
                            f"{edge_event_sequence}\n{code}"
                        ).encode("ascii")
                    ).hexdigest()
                    connection.execute(
                        """INSERT INTO outbound_platform_confirmation
                           (confirmation_uid, event_uid,
                            confirmation_sha256, received_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            result_uid,
                            proxy["event_uid"],
                            confirmation_sha256,
                            now,
                        ),
                    )
                    connection.execute(
                        """UPDATE outbound_business_event_ledger
                           SET state='PLATFORM_CONFIRMED',
                               platform_confirmed_at=?
                           WHERE event_uid=?
                             AND state IN ('PENDING', 'SENDING')""",
                        (now, proxy["event_uid"]),
                    )
            return {
                "resultUid": result_uid,
                "eventUid": proxy["event_uid"],
                "dispatchGeneration": generation,
                "edgeEventSequence": edge_event_sequence,
                "code": code,
                "receivedAt": now,
            }

    def list_undelivered_proxy_platform_results(
        self,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("platform result limit is invalid")
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT result.result_uid, result.event_uid,
                          result.dispatch_generation,
                          result.edge_event_sequence, result.result_code,
                          result.received_at
                   FROM outbound_proxy_platform_result AS result
                   LEFT JOIN outbound_proxy_result_delivery AS delivery
                     ON delivery.result_uid=result.result_uid
                   WHERE delivery.result_uid IS NULL
                   ORDER BY result.rowid
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "resultUid": row["result_uid"],
                "eventUid": row["event_uid"],
                "dispatchGeneration": int(row["dispatch_generation"]),
                "edgeEventSequence": int(row["edge_event_sequence"]),
                "code": int(row["result_code"]),
                "receivedAt": row["received_at"],
            }
            for row in rows
        ]

    def mark_proxy_platform_result_delivered(self, result_uid: str) -> str:
        result_uid = _require_uuid4(result_uid, "platform result UID")
        now = self._now_text()
        with self.transaction() as connection:
            result = connection.execute(
                """SELECT 1 FROM outbound_proxy_platform_result
                   WHERE result_uid=?""",
                (result_uid,),
            ).fetchone()
            if result is None:
                return "UNKNOWN"
            existing = connection.execute(
                """SELECT 1 FROM outbound_proxy_result_delivery
                   WHERE result_uid=?""",
                (result_uid,),
            ).fetchone()
            if existing is not None:
                return "DUPLICATE"
            connection.execute(
                """INSERT INTO outbound_proxy_result_delivery
                   (result_uid, delivered_at) VALUES (?, ?)""",
                (result_uid, now),
            )
            return "ACCEPTED"

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
                     WHERE state='PLATFORM_CONFIRMED'),
                    (SELECT COUNT(*) FROM outbound_proxy_event
                     WHERE completed_generation < requested_generation),
                    (SELECT COUNT(*)
                     FROM outbound_proxy_platform_result AS result
                     LEFT JOIN outbound_proxy_result_delivery AS delivery
                       ON delivery.result_uid=result.result_uid
                     WHERE delivery.result_uid IS NULL)"""
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
            "proxyExtensionVersion": PROXY_EXTENSION_VERSION,
            "proxyPendingOutboundCount": int(counts[6]),
            "proxyUndeliveredPlatformResultCount": int(counts[7]),
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
        return self._format_clock_value(value)

    @staticmethod
    def _format_clock_value(value: datetime) -> str:
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


def _require_proxy_identifier(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or not value.isprintable()
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _canonical_proxy_json(value: Any, field: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must contain JSON values") from error
    if not 2 <= len(encoded) <= MAX_PROXY_JSON_BYTES:
        raise ValueError(f"{field} size is invalid")
    return encoded.decode("utf-8")


def _canonical_proxy_document(
    domain: str,
    value: dict[str, Any],
) -> tuple[str, str]:
    document = {"domain": domain, **value}
    encoded = _canonical_proxy_json(document, "proxy content")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

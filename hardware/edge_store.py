"""edge_store.py —— 香橙派边缘 SQLite 存储。

SQLite 是香橙派的唯一持久化真相源。
"""

from __future__ import annotations

import json as _json
import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from onenet_wire import (
    build_business_confirmation_receipt,
    build_configuration_progress_event,
    build_event_envelope,
    canonical_payload_sha256,
    validate_factory_seal_envelope_at_acceptance,
)
from factory_seal.errors import FactorySealError
from factory_seal.validation import authorization_binding_sha256
from trusted_clock import local_deadline_reference, sample_clock

logger = logging.getLogger("edge-store")

CURRENT_SCHEMA_VERSION = 40
WORK_TYPE_NONE = "NONE"
WORK_TYPE_DELIVERY = "DELIVERY"
WORK_TYPE_CLEAN = "CLEAN"
WORK_TYPE_FULLNESS = "FULLNESS"
WORK_TYPE_BASELINE = "BASELINE"
EVENT_PENDING = "PENDING"
EVENT_SENDING = "SENDING"
EVENT_CONFIRMED = "CONFIRMED"
EVENT_DEAD = "DEAD"
PHOTO_CAPTURE_PENDING = "CAPTURE_PENDING"
PHOTO_PENDING = "PENDING"
PHOTO_UPLOADING = "UPLOADING"
PHOTO_UPLOADED = "UPLOADED"
PHOTO_DEAD = "DEAD"
FAULT_OBSERVED = "OBSERVED"
FAULT_RECOVERED = "RECOVERED"
REMOTE_SUPPORT_TERMINAL_STATES = frozenset({
    "CLOSED",
    "FAILED",
    "EXPIRED",
})
REMOTE_SUPPORT_FAILURE_CODES = frozenset({
    "CREDENTIALS_INVALID",
    "SSH_NOT_AVAILABLE",
    "SSH_START_FAILED",
    "SSH_EXITED",
    "PROCESS_SUPERVISION_FAILED",
})

# A factory-seal authorization has no physical side effect until the local
# authorization row, command completion and ACCEPTED observation commit in one
# SQLite transaction.  These local prerequisites may be repaired safely and
# retried only after OneNet redelivers the same immutable command.  Keep this
# list beside the persistence transition so a caller cannot requeue an
# arbitrary FAILED command by mistake.
FACTORY_SEAL_RETRYABLE_ERROR_CODES = frozenset({
    "EDGE_RESTARTED",
    "FACTORY_SEAL_NOT_AVAILABLE",
    "IMAGE_RELEASE_INVALID",
    "FACTORY_REPORT_INVALID",
    "DEVICE_CREDENTIALS_INVALID",
    "FACTORY_SEAL_RETRYABLE_FAILURE",
})

# These failures prove that the frozen cloud authority cannot be reconciled
# with this EdgeStore.  Retrying the same command bytes cannot change that
# fact, so the command processor may emit the terminal REJECTED observation.
FACTORY_SEAL_TERMINAL_ERROR_CODES = frozenset({
    "FACTORY_ALREADY_SEALED",
    "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID",
    "FACTORY_SEAL_COMMAND_CONFLICT",
    "FACTORY_SEAL_COMMAND_STATE_INVALID",
    "ACCEPTANCE_EVIDENCE_NOT_FOUND",
    "ACCEPTANCE_EVIDENCE_NOT_LATEST",
    "ACCEPTANCE_EVIDENCE_INVALID",
    "ACCEPTANCE_EVIDENCE_MISMATCH",
    "FACTORY_SEAL_GENERATION_STALE",
    "FACTORY_SEAL_GENERATION_CONFLICT",
    "FACTORY_SEAL_OBSERVATION_CONFLICT",
})
FACTORY_SEAL_ACCEPTANCE_EVIDENCE_SCHEMA_VERSIONS = frozenset({3, 4, 5})
MCU_UPDATE_ACTIVE_STATES = frozenset({
    "QUEUED",
    "PACKAGE_FETCH_FAILED",
    "PREFLIGHT",
    "PREPARED",
    "FLASHING_TARGET",
    "VERIFYING_TARGET",
    "ROLLING_BACK",
    "VERIFYING_ROLLBACK",
    "FAILED_LOCKED",
})
MCU_UPDATE_TERMINAL_STATES = frozenset({
    "SUCCEEDED",
    "ROLLED_BACK",
    "REJECTED",
})


def _canonical_mcu_prepare_identity(identity: dict) -> str:
    """Validate and canonically encode the application identity before F2."""

    if not isinstance(identity, dict):
        raise ValueError("MCU prepare identity must be an object")
    snapshot = {
        field: identity.get(field)
        for field in (
            "protocolRevision",
            "firmwareVersionCode",
            "firmwareVersion",
            "firmwareIdentityHex",
        )
    }
    if (
        not isinstance(snapshot["protocolRevision"], int)
        or isinstance(snapshot["protocolRevision"], bool)
        or snapshot["protocolRevision"] != 2
        or not isinstance(snapshot["firmwareVersionCode"], int)
        or isinstance(snapshot["firmwareVersionCode"], bool)
        or snapshot["firmwareVersionCode"] <= 0
        or not isinstance(snapshot["firmwareVersion"], str)
        or not snapshot["firmwareVersion"]
        or not isinstance(snapshot["firmwareIdentityHex"], str)
        or len(snapshot["firmwareIdentityHex"]) != 16
        or any(
            character not in "0123456789abcdef"
            for character in snapshot["firmwareIdentityHex"]
        )
    ):
        raise ValueError("MCU prepare identity is invalid")
    encoded = _json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return encoded


def _parse_utc_instant(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an RFC3339 instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC3339 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _require_uuid4_local(value: str, field: str) -> str:
    try:
        parsed = _uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _validate_device_entry_url(url: str, sha256: str) -> None:
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 192
        or not url.startswith("https://")
        or any(
            ord(character) < 0x21
            or ord(character) > 0x7E
            or character in {'"', "\\"}
            for character in url
        )
    ):
        raise ValueError(
            "device entry URL must be printable ASCII HTTPS within 192 bytes"
        )
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ValueError("device entry URL SHA-256 is invalid")
    if hashlib.sha256(url.encode("ascii")).hexdigest() != sha256:
        raise ValueError("device entry URL SHA-256 mismatch")


class EdgeStore:
    """香橙派边缘 SQLite 存储。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self._open_connection()
        self._migrate()
        recovered_events = self.recover_sending_events()
        if recovered_events:
            logger.info(
                "Recovered %d in-flight events for retransmission",
                recovered_events,
            )
        logger.info("EdgeStore 初始化: %s (v%d)", self.db_path, CURRENT_SCHEMA_VERSION)

    def initialize_existing_recovery(self) -> None:
        """Open existing native custody only; no empty DB or cloud-send recovery."""
        from pathlib import Path
        if self._conn is not None:
            raise RuntimeError("native recovery requires a closed existing store")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=rw", uri=True, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] if table else None
            if type(version) is not int or not 36 <= version <= CURRENT_SCHEMA_VERSION:
                raise ValueError("native recovery requires an existing native recovery schema")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            self._conn = conn
            self._migrate()
            if not self.integrity_check():
                raise ValueError("native recovery store integrity failed")
        except BaseException:
            self._conn = None
            conn.close()
            raise

    def prepare_schema(self) -> None:
        """Migrate and verify the store without starting runtime recovery.

        The first-boot coordinator calls this through an early, networkless
        oneshot before it reads the factory-seal table.  This breaks the boot
        dependency cycle for an empty database, a v14 database, or a database
        whose previous migration transaction was interrupted by power loss.
        """

        if self._conn is not None:
            raise RuntimeError("EdgeStore schema preparation requires a closed store")
        try:
            self._open_connection()
            self._migrate()
            version = self._conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            table = self._conn.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='factory_seal_authorization'"""
            ).fetchone()
            quick_check = self._conn.execute("PRAGMA quick_check").fetchone()[0]
            if (
                version != CURRENT_SCHEMA_VERSION
                or table is None
                or quick_check != "ok"
            ):
                raise RuntimeError("EdgeStore schema preparation verification failed")
        finally:
            self.close()

    def _open_connection(self) -> None:
        if self._conn is not None:
            return
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        self._conn = conn

    def _migrate(self) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._apply_migrations(conn)
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply every pending schema step inside one explicit transaction."""

        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        if current == CURRENT_SCHEMA_VERSION:
            # Re-check the latest durable shapes even when their version rows
            # are present.  A copied or historically partially-applied
            # database must fail closed instead of silently weakening either
            # clock evidence or command-observation identity.
            self._migrate_v17()
            self._migrate_v18()
            self._migrate_v19()
            self._migrate_v20()
            self._migrate_v21()
            self._migrate_v22()
            self._migrate_v23()
            self._migrate_v24()
            self._migrate_v25()
            self._migrate_v26()
            self._migrate_v27()
            self._migrate_v28()
            self._migrate_v29()
            self._migrate_v30()
            self._migrate_v31()
            self._migrate_v32()
            self._migrate_v33()
            self._migrate_v34()
            self._migrate_v35()
            self._migrate_v36()
            self._migrate_v37()
            self._migrate_v38()
            self._migrate_v39()
            self._migrate_v40()
            return
        if current not in {0, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39}:
            raise RuntimeError(
                "EdgeStore 数据库时代不兼容；永久资产 v9 不读取旧设备数据库"
            )
        logger.info("EdgeStore 创建永久资产数据库 v%d", CURRENT_SCHEMA_VERSION)
        if current < 1:
            self._create_tables()
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            current = 1
        if current < 2:
            self._migrate_v2()
            conn.execute("INSERT INTO schema_version (version) VALUES (2)")
            current = 2
        if current < 3:
            self._migrate_v3()
            conn.execute("INSERT INTO schema_version (version) VALUES (3)")
            current = 3
        if current < 4:
            self._migrate_v4()
            conn.execute("INSERT INTO schema_version (version) VALUES (4)")
            current = 4
        if current < 5:
            self._migrate_v5()
            conn.execute("INSERT INTO schema_version (version) VALUES (5)")
            current = 5
        if current < 6:
            self._migrate_v6()
            conn.execute("INSERT INTO schema_version (version) VALUES (6)")
            current = 6
        if current < 7:
            self._migrate_v7()
            conn.execute("INSERT INTO schema_version (version) VALUES (7)")
            current = 7
        if current < 8:
            self._migrate_v8()
            conn.execute("INSERT INTO schema_version (version) VALUES (8)")
            current = 8
        if current < 9:
            conn.execute("INSERT INTO schema_version (version) VALUES (9)")
            current = 9
        if current < 10:
            self._migrate_v10()
            conn.execute("INSERT INTO schema_version (version) VALUES (10)")
            current = 10
        if current < 11:
            self._migrate_v11()
            conn.execute("INSERT INTO schema_version (version) VALUES (11)")
            current = 11
        if current < 12:
            self._migrate_v12()
            conn.execute("INSERT INTO schema_version (version) VALUES (12)")
            current = 12
        if current < 13:
            self._migrate_v13()
            conn.execute("INSERT INTO schema_version (version) VALUES (13)")
            current = 13
        if current < 14:
            self._migrate_v14()
            conn.execute("INSERT INTO schema_version (version) VALUES (14)")
            current = 14
        if current < 15:
            self._migrate_v15()
            conn.execute("INSERT INTO schema_version (version) VALUES (15)")
            current = 15
        if current < 16:
            self._migrate_v16()
            conn.execute("INSERT INTO schema_version (version) VALUES (16)")
            current = 16
        if current < 17:
            self._migrate_v17()
            conn.execute("INSERT INTO schema_version (version) VALUES (17)")
            current = 17
        if current < 18:
            self._migrate_v18()
            conn.execute("INSERT INTO schema_version (version) VALUES (18)")
        if current < 19:
            if current == 18:
                # Preserve the integrity rechecks previously performed on v18 startup.
                self._migrate_v17()
                self._migrate_v18()
            self._migrate_v19()
            conn.execute("INSERT INTO schema_version (version) VALUES (19)")
        if current < 20:
            if current == 19:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
            self._migrate_v20()
            conn.execute("INSERT INTO schema_version (version) VALUES (20)")
        if current < 21:
            if current == 20:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
            self._migrate_v21()
            conn.execute("INSERT INTO schema_version (version) VALUES (21)")
        if current < 22:
            if current == 21:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
            self._migrate_v22()
            conn.execute("INSERT INTO schema_version (version) VALUES (22)")

        if current < 23:
            if current == 22:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
                self._migrate_v22()
            self._migrate_v23()
            conn.execute("INSERT INTO schema_version (version) VALUES (23)")

        if current < 24:
            if current == 23:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
                self._migrate_v22()
                self._migrate_v23()
            self._migrate_v24()
            conn.execute("INSERT INTO schema_version (version) VALUES (24)")

        if current < 25:
            if current == 24:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
                self._migrate_v22()
                self._migrate_v23()
                self._migrate_v24()
            self._migrate_v25()
            conn.execute("INSERT INTO schema_version (version) VALUES (25)")

        if current < 26:
            if current == 25:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
                self._migrate_v22()
                self._migrate_v23()
                self._migrate_v24()
                self._migrate_v25()
            self._migrate_v26()
            conn.execute("INSERT INTO schema_version (version) VALUES (26)")

        if current < 27:
            if current == 26:
                self._migrate_v17()
                self._migrate_v18()
                self._migrate_v19()
                self._migrate_v20()
                self._migrate_v21()
                self._migrate_v22()
                self._migrate_v23()
                self._migrate_v24()
                self._migrate_v25()
                self._migrate_v26()
            self._migrate_v27()
            conn.execute("INSERT INTO schema_version (version) VALUES (27)")

        if current < 28:
            if current == 27:
                for version in range(17, 28):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v28()
            conn.execute("INSERT INTO schema_version (version) VALUES (28)")

        if current < 29:
            if current == 28:
                for version in range(17, 29):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v29()
            conn.execute("INSERT INTO schema_version (version) VALUES (29)")

        if current < 30:
            if current == 29:
                for version in range(17, 30):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v30()
            conn.execute("INSERT INTO schema_version (version) VALUES (30)")

        if current < 31:
            if current == 30:
                for version in range(17, 31):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v31()
            conn.execute("INSERT INTO schema_version (version) VALUES (31)")

        if current < 32:
            if current == 31:
                for version in range(17, 32):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v32()
            conn.execute("INSERT INTO schema_version (version) VALUES (32)")

        if current < 33:
            if current == 32:
                for version in range(17, 33):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v33()
            conn.execute("INSERT INTO schema_version (version) VALUES (33)")

        if current < 34:
            if current == 33:
                for version in range(17, 34):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v34()
            conn.execute("INSERT INTO schema_version (version) VALUES (34)")

        if current < 35:
            if current == 34:
                for version in range(17, 35):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v35()
            conn.execute("INSERT INTO schema_version (version) VALUES (35)")

        if current < 36:
            if current == 35:
                for version in range(17, 36):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v36()
            conn.execute("INSERT INTO schema_version (version) VALUES (36)")

        if current < 37:
            if current == 36:
                for version in range(17, 37):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v37()
            conn.execute("INSERT INTO schema_version (version) VALUES (37)")

        if current < 38:
            if current == 37:
                for version in range(17, 38):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v38()
            conn.execute("INSERT INTO schema_version (version) VALUES (38)")

        if current < 39:
            if current == 38:
                for version in range(17, 39):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v39()
            conn.execute("INSERT INTO schema_version (version) VALUES (39)")

        if current < 40:
            if current == 39:
                for version in range(17, 40):
                    getattr(self, f"_migrate_v{version}")()
            self._migrate_v40()
            conn.execute("INSERT INTO schema_version (version) VALUES (40)")

    def _migrate_v40(self):
        from native_recovery_close_isolation import checked
        ddl = """CREATE TABLE native_recovery_close_isolation (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_recovery_close(action_uid),
            bundle_json TEXT NOT NULL CHECK(length(bundle_json) BETWEEN 1 AND 65536),
            evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
            state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','ISOLATED'))
        )"""
        schema = self._conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_recovery_close_isolation'").fetchone()
        if schema is None:
            if self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 40:
                raise ValueError("native recovery close isolation table missing")
            self._conn.execute(ddl)
        elif ("".join(schema[0].split()).lower() != "".join(ddl.split()).lower()
                or self._conn.execute("""SELECT 1 FROM sqlite_master WHERE tbl_name='native_recovery_close_isolation'
                    AND (type='trigger' OR (type='index' AND sql IS NOT NULL))""").fetchone()):
            raise ValueError("native recovery close isolation schema is incompatible")
        for row in self._conn.execute("SELECT * FROM native_recovery_close_isolation").fetchall():
            checked(self, self._conn, row)

    def get_native_recovery_close_isolation(self, action_uid):
        from native_recovery_close_isolation import checked
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_recovery_close_isolation WHERE action_uid=?", (action_uid,)).fetchone()
            return checked(self, self._conn, row) if row else None

    def prepare_native_recovery_close_isolation(self, action_uid, *, current_boot):
        from native_recovery_close_isolation import bundle, digest
        from native_delivery_recovery_close import close_effect_bundle, check_occupancy
        from work_recovery import canonical
        with self._standalone_native_transaction() as conn:
            old = self.get_native_recovery_close_isolation(action_uid)
            if old is not None:
                return old
            binding = self.get_native_delivery_recovery_close(action_uid)
            if binding is None:
                raise ValueError("recovery close isolation binding missing")
            issue = self.get_native_delivery_issue(binding["permit"].work_uid)
            check_occupancy(self, issue, binding["evidence"]["portNo"])
            if (self.get_native_recovery_close_confirmation(action_uid) is not None
                    or close_effect_bundle(self, conn, action_uid) is not None):
                return None
            boot = current_boot()
            command = self.get_native_command(action_uid)
            if (type(boot) is not int or boot <= command["mcu_boot_id"]
                    or boot != self._native_counter(conn, "native_current_boot")):
                raise ValueError("recovery close isolation requires a fresh newer owned boot")
            witness = self.get_native_boot_observation(boot)
            if witness is None:
                raise ValueError("recovery close isolation boot witness missing")
            value = bundle(self, conn, action_uid, binding=binding, boot_observation=dict(observedMcuBootId=boot,
                bootObservationMessageName=witness["message_name"], bootObservationPayloadHex=witness["payload"].hex()))
            raw = canonical(value)
            proof = dict(action_uid=action_uid, bundle_json=raw, evidence_sha256=digest(raw), state="PENDING")
            conn.execute("INSERT INTO native_recovery_close_isolation(action_uid,bundle_json,evidence_sha256) VALUES(?,?,?)",
                (action_uid, raw, proof["evidence_sha256"]))
            if current_boot() != boot:
                raise ValueError("recovery close isolation requires fresh boot through commit")
            return proof

    def confirm_native_recovery_close_isolation(self, action_uid, ledger, disposition):
        from native_recovery_close_isolation import check_disposition
        with self._standalone_native_transaction() as conn:
            proof = self.get_native_recovery_close_isolation(action_uid)
            if proof is None:
                raise ValueError("recovery close isolation proof missing")
            check_disposition(self.get_native_delivery_recovery_close(action_uid), proof, ledger, disposition)
            conn.execute("UPDATE native_recovery_close_isolation SET state='ISOLATED' WHERE action_uid=?", (action_uid,))
            return proof | {"state": "ISOLATED"}

    def _migrate_v39(self):
        self._migrate_native_close_ancestry()
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(native_mcu_command)")}
        if "dispatch_retired" not in columns:
            if self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 39:
                raise ValueError("native dispatch retirement marker missing")
            self._conn.execute("""ALTER TABLE native_mcu_command ADD COLUMN dispatch_retired
                INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_retired IN (0,1))""")
        self._conn.execute("""CREATE INDEX IF NOT EXISTS idx_cmd_inbox_mcu_command_state
            ON command_inbox(mcu_command_uid, state)
            WHERE result_json IS NOT NULL""")
        self._conn.execute("""CREATE INDEX IF NOT EXISTS idx_cmd_inbox_native_failure_start
            ON command_inbox(
                json_extract(
                    result_json,
                    '$.nativeControlFailure.evidence.startCommandUid'
                ),
                state
            )
            WHERE result_json IS NOT NULL
              AND state IN ('FAILED','REJECTED')""")
        # Old software could permanently apply a local control failure or
        # baseline completion and release its work slot before the explicit
        # dispatch fence column existed.  Rebuild that derived fence on every
        # open from the exact terminal proof; the operation is idempotent and
        # cannot retire an unrelated or merely PREPARED command.
        self._conn.execute("""UPDATE native_mcu_command SET dispatch_retired=1 WHERE command_uid IN
            (SELECT action_uid FROM native_recovery_close_retirement WHERE state='RETIRED')""")
        for raw in self._conn.execute(
            """SELECT * FROM native_mcu_command
               WHERE dispatch_retired=0
                 AND decision_outcome IS NULL
                 AND boot_retired=0"""
        ).fetchall():
            record = self._checked_native_command(raw)
            if (
                self._native_control_failure_proves_dispatch_retirement(
                    self._conn,
                    record,
                )
                or self._native_baseline_completion_proves_dispatch_retirement(
                    self._conn,
                    record,
                )
            ):
                self._conn.execute(
                    """UPDATE native_mcu_command SET dispatch_retired=1
                       WHERE command_uid=? AND dispatch_retired=0""",
                    (record["command_uid"],),
                )
        self._verify_native_dispatch_retirements(self._conn)
        self._conn.execute("DROP INDEX IF EXISTS native_mcu_one_pending_command")
        self._conn.execute("""CREATE UNIQUE INDEX native_mcu_one_pending_command
            ON native_mcu_command ((1)) WHERE decision_outcome IS NULL AND boot_retired=0 AND dispatch_retired=0""")

    def _migrate_native_close_ancestry(self):
        """Preserve exact old proof bytes while replacing their referenced parent."""
        conn = self._conn
        old_sql = """CREATE TABLE native_delivery_recovery_close (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_mcu_command(command_uid),
            issue_uid TEXT NOT NULL UNIQUE REFERENCES native_delivery_issue(issue_uid),
            binding_json TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL CHECK(length(binding_sha256)=64)
        )"""
        new_sql = """CREATE TABLE native_delivery_recovery_close (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_mcu_command(command_uid),
            issue_uid TEXT NOT NULL REFERENCES native_delivery_issue(issue_uid),
            binding_json TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL CHECK(length(binding_sha256)=64),
            predecessor_action_uid TEXT NOT NULL UNIQUE REFERENCES native_mcu_command(command_uid)
        )"""
        def normalized(sql):
            return "".join(sql.replace('"', '').replace("IF NOT EXISTS", "").split()).lower()
        stored = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_delivery_recovery_close'").fetchone()
        if stored is None or normalized(stored[0]) not in {normalized(old_sql), normalized(new_sql)}:
            raise ValueError("native recovery close ancestry schema is unsupported")
        tables = ("native_delivery_recovery_close", "native_recovery_close_confirmation", "native_recovery_close_retirement")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE tbl_name IN (?,?,?) AND type IN ('index','trigger') AND sql IS NOT NULL",
                        tables).fetchone():
            raise ValueError("native recovery close ancestry has unsupported schema extensions")
        if normalized(stored[0]) == normalized(new_sql):
            return
        if conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 39:
            raise ValueError("native recovery close ancestry schema regressed")
        children = []
        for table, terminal in zip(tables[1:], ("CONFIRMED", "RETIRED")):
            expected = f"""CREATE TABLE {table} (
                action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_recovery_close(action_uid),
                bundle_json TEXT NOT NULL CHECK(length(bundle_json) BETWEEN 1 AND 8192),
                evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
                state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','{terminal}'))
            )"""
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if ddl is None or normalized(ddl[0]) != normalized(expected):
                raise ValueError("native recovery close child proof schema is unsupported")
            children.append((table, ddl[0], [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]))
        from native_delivery_recovery_close import checked_binding
        parents = []
        for row in conn.execute("SELECT * FROM native_delivery_recovery_close").fetchall():
            binding = checked_binding(self, row)
            parents.append((*tuple(row), binding["evidence"]["sourceActionUid"]))
        # Child tables are copied and rebuilt inside the same startup transaction.
        # Renaming the old parent would redirect their FK definitions to its old name.
        for table, _, _ in children:
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DROP TABLE native_delivery_recovery_close")
        conn.execute(new_sql)
        conn.executemany("INSERT INTO native_delivery_recovery_close VALUES(?,?,?,?,?)", parents)
        for table, ddl, rows in children:
            conn.execute(ddl)
            conn.executemany(f"INSERT INTO {table} VALUES(?,?,?,?)", rows)
        self._migrate_v36()
        self._migrate_v37()
        self._migrate_v38()
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("native recovery close ancestry migration broke foreign keys")

    def _verify_native_dispatch_retirements(self, conn):
        rows = conn.execute("""SELECT c.*, r.state AS recovery_retirement_state
            FROM native_mcu_command c
            LEFT JOIN native_recovery_close_retirement r
              ON r.action_uid=c.command_uid""").fetchall()
        for raw in rows:
            record = self._checked_native_command(raw)
            recovery_retired = raw["recovery_retirement_state"] == "RETIRED"
            locally_retired = bool(record["dispatch_retired"])
            if record["dispatch_retired"] not in {0, 1}:
                raise ValueError(
                    "native dispatch retirement marker differs from durable proof"
                )
            if recovery_retired and not locally_retired:
                raise ValueError(
                    "native dispatch retirement marker differs from durable proof"
                )
            if locally_retired and not recovery_retired and not (
                self._native_control_failure_proves_dispatch_retirement(
                    conn,
                    record,
                )
                or self._native_baseline_completion_proves_dispatch_retirement(
                    conn,
                    record,
                )
            ):
                raise ValueError(
                    "native dispatch retirement marker differs from durable proof"
                )
        for row in conn.execute("SELECT action_uid FROM native_recovery_close_retirement WHERE state='RETIRED'").fetchall():
            self.get_native_recovery_close_retirement(row[0])

    def _native_control_failure_proves_dispatch_retirement(self, conn, record):
        """Accept only an exact applied local failure as a dispatch fence.

        This does not claim that the MCU rejected or observed the command.  It
        proves only that the immutable bytes reached their permanent local
        disposition and no longer own the one-pending-command gate.
        """
        from job_safety import JobPermit
        from native_control_failure import MARKER, _checked_marker
        import uart2_protocol as uart2

        matches = []
        for raw_command in conn.execute(
            """SELECT * FROM command_inbox
               WHERE state IN ('FAILED','REJECTED')
                 AND result_json IS NOT NULL
                 AND json_extract(
                       result_json,
                       '$.nativeControlFailure.evidence.startCommandUid'
                     )=?""",
            (record["command_uid"],),
        ).fetchall():
            command = self._decode_command_row(raw_command)
            result = command.get("result")
            marker = result.get(MARKER) if isinstance(result, dict) else None
            evidence = marker.get("evidence") if isinstance(marker, dict) else None
            if (
                not isinstance(evidence, dict)
                or evidence.get("startCommandUid") != record["command_uid"]
            ):
                continue
            try:
                saved_permit = evidence["permit"]
                permit = JobPermit(
                    permit_uid=saved_permit["permit_uid"],
                    work_uid=saved_permit["work_uid"],
                    command_uid=saved_permit["command_uid"],
                    work_type=saved_permit["work_type"],
                    request_digest_sha256=saved_permit[
                        "request_digest_sha256"
                    ],
                )
                start = uart2.decode_payload(
                    record["message_name"],
                    record["payload"],
                )
                _, checked = _checked_marker(
                    self,
                    permit,
                    record,
                    start,
                    command,
                    evidence["deviceName"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "native control failure dispatch retirement proof is corrupt"
                ) from error
            if checked is not None and checked.get("state") == "APPLIED":
                matches.append(command["command_uid"])
        if len(matches) > 1:
            raise ValueError(
                "native command has multiple control failure retirement proofs"
            )
        return len(matches) == 1

    def _native_baseline_completion_proves_dispatch_retirement(
        self,
        conn,
        record,
    ):
        """Validate the applied exact-result receipt used as a local fence."""
        from job_safety import command_request_digest
        import uart2_protocol as uart2

        if record["message_name"] != "MEASURE_BASELINE":
            return False
        native = uart2.decode_payload("MEASURE_BASELINE", record["payload"])
        matches = []
        for raw_command in conn.execute(
            """SELECT * FROM command_inbox
               WHERE state='COMPLETED' AND result_json IS NOT NULL
                 AND mcu_command_uid=?""",
            (record["command_uid"],),
        ).fetchall():
            command = self._decode_command_row(raw_command)
            result = command.get("result")
            marker = (
                result.get("nativeBaselineCompletion")
                if isinstance(result, dict)
                else None
            )
            evidence = marker.get("evidence") if isinstance(marker, dict) else None
            if (
                not isinstance(evidence, dict)
                or evidence.get("nativeCommandUid") != record["command_uid"]
            ):
                continue
            if marker.get("state") != "APPLIED":
                continue
            cloud = command["payload"]
            payload = cloud.get("payload") if isinstance(cloud, dict) else None
            saved_permit = evidence.get("permit")
            event_row = self.get_event(evidence.get("eventUid"))
            event = (
                _json.loads(event_row["payload_json"])
                if event_row is not None
                else None
            )
            scope = self._native_baseline_scope(record)
            raw_scope = uart2.encode_payload(
                "QUERY_PROCESS_EVENT",
                scope | {"queryId": 1},
            )[8:]
            receipt = self.get_native_process_receipt(raw_scope)
            measured = (
                uart2.decode_payload(
                    "BASELINE_MEASUREMENT_RESULT",
                    bytes(receipt["payload"]),
                )
                if receipt is not None
                else None
            )
            if (
                marker.get("evidenceSha256")
                != canonical_payload_sha256(evidence)
                or not isinstance(payload, dict)
                or not isinstance(saved_permit, dict)
                or saved_permit
                != {
                    "permit_uid": command["command_uid"],
                    "work_uid": payload.get("measurementUid"),
                    "command_uid": command["command_uid"],
                    "work_type": WORK_TYPE_BASELINE,
                    "request_digest_sha256": command_request_digest(cloud),
                }
                or command["mcu_command_uid"] != record["command_uid"]
                or cloud.get("commandType")
                != "MEASURE_EMPTY_BAG_BASELINE"
                or payload.get("measurementUid") != native["measurementUid"]
                or payload.get("portNo") != native["portNo"]
                or evidence.get("sourceMcuBootId")
                != native["targetMcuBootId"]
                or measured is None
                or measured["mcuCommandUid"] != record["command_uid"]
                or measured["measurementUid"] != native["measurementUid"]
                or evidence.get("sourceMcuEventSequence")
                != measured["mcuEventSequence"]
                or evidence.get("sourceEventDigestSha256")
                != uart2.compute_process_event_digest(
                    "BASELINE_MEASUREMENT_RESULT",
                    bytes(receipt["payload"]),
                )
                or not isinstance(event, dict)
                or event.get("eventType")
                != "BASELINE_MEASUREMENT_COMPLETE"
                or event.get("eventUid") != evidence.get("eventUid")
                or event.get("commandUid") != command["command_uid"]
                or event.get("target")
                != {
                    "type": "BASELINE_MEASUREMENT",
                    "uid": native["measurementUid"],
                }
                or event.get("payloadSha256")
                != evidence.get("eventPayloadSha256")
            ):
                raise ValueError(
                    "native baseline dispatch retirement proof is corrupt"
                )
            matches.append(command["command_uid"])
        if len(matches) > 1:
            raise ValueError(
                "native baseline has multiple completion retirement proofs"
            )
        return len(matches) == 1

    def _migrate_v38(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_recovery_close_retirement (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_recovery_close(action_uid),
            bundle_json TEXT NOT NULL CHECK(length(bundle_json) BETWEEN 1 AND 8192),
            evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
            state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','RETIRED'))
        )""")
        from native_delivery_recovery_close import checked_retirement
        for row in self._conn.execute("SELECT * FROM native_recovery_close_retirement"):
            checked_retirement(self, self._conn, row)

    def get_native_recovery_close_retirement(self, action_uid):
        from native_delivery_recovery_close import checked_retirement
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_recovery_close_retirement WHERE action_uid=?", (action_uid,)).fetchone()
            return checked_retirement(self, self._conn, row) if row else None

    def _prepare_native_recovery_close_retirement(self, conn, action_uid):
        from native_delivery_recovery_close import retirement_bundle, retirement_digest, check_occupancy
        from work_recovery import canonical
        bundle = retirement_bundle(self, conn, action_uid)
        old = self.get_native_recovery_close_retirement(action_uid)
        if old is not None and old["state"] == "RETIRED":
            return old
        issue = self.get_native_delivery_issue(bundle["permit"]["work_uid"])
        check_occupancy(self, issue, bundle["recovery"]["portNo"])
        if old is not None:
            return old
        raw = canonical(bundle)
        digest = retirement_digest(raw)
        conn.execute("INSERT INTO native_recovery_close_retirement(action_uid,bundle_json,evidence_sha256) VALUES(?,?,?)",
            (action_uid, raw, digest))
        return dict(action_uid=action_uid, bundle_json=raw, evidence_sha256=digest, state="PENDING")

    def prepare_native_recovery_close_retirement(self, action_uid):
        with self._standalone_native_transaction() as conn:
            return self._prepare_native_recovery_close_retirement(conn, action_uid)

    def list_pending_native_recovery_close_retirements(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("recovery close retirement batch must contain 1..32 actions")
        with self._lock:
            return [row[0] for row in self._conn.execute("""SELECT r.action_uid
                FROM native_recovery_close_retirement r
                JOIN native_delivery_recovery_close b ON b.action_uid=r.action_uid
                JOIN native_delivery_issue i ON i.issue_uid=b.issue_uid
                JOIN work_slot w ON w.slot_id=1 AND w.work_uid=i.work_uid AND w.work_type='DELIVERY'
                WHERE r.state='PENDING' ORDER BY r.action_uid LIMIT ?""", (limit,))]

    def confirm_native_recovery_close_retirement(self, action_uid, ledger):
        from native_delivery_recovery_close import check_retirement_ledger
        with self._standalone_native_transaction() as conn:
            proof = self._prepare_native_recovery_close_retirement(conn, action_uid)
            check_retirement_ledger(self.get_native_delivery_recovery_close(action_uid), proof, ledger)
            conn.execute("UPDATE native_recovery_close_retirement SET state='RETIRED' WHERE action_uid=?", (action_uid,))
            conn.execute("UPDATE native_mcu_command SET dispatch_retired=1 WHERE command_uid=?", (action_uid,))
            return proof | {"state": "RETIRED"}

    def confirm_native_recovery_close_withdrawal(self, action_uid, ledger, disposition):
        from native_delivery_recovery_close import check_withdrawal_disposition
        with self._standalone_native_transaction() as conn:
            proof = self._prepare_native_recovery_close_retirement(conn, action_uid)
            check_withdrawal_disposition(self.get_native_delivery_recovery_close(action_uid), proof, ledger, disposition)
            conn.execute("UPDATE native_recovery_close_retirement SET state='RETIRED' WHERE action_uid=?", (action_uid,))
            conn.execute("UPDATE native_mcu_command SET dispatch_retired=1 WHERE command_uid=?", (action_uid,))
            return proof | {"state": "RETIRED"}

    def _migrate_v37(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_recovery_close_confirmation (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_recovery_close(action_uid),
            bundle_json TEXT NOT NULL CHECK(length(bundle_json) BETWEEN 1 AND 8192),
            evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
            state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','CONFIRMED'))
        )""")
        from native_delivery_recovery_close import checked_close_confirmation
        for row in self._conn.execute("SELECT * FROM native_recovery_close_confirmation"):
            checked_close_confirmation(self, self._conn, row)

    def get_native_recovery_close_confirmation(self, action_uid):
        from native_delivery_recovery_close import checked_close_confirmation
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_recovery_close_confirmation WHERE action_uid=?", (action_uid,)).fetchone()
            return checked_close_confirmation(self, self._conn, row) if row else None

    def _prepare_native_recovery_close_confirmation(self, conn, action_uid):
        from native_delivery_recovery_close import close_effect_bundle, check_occupancy
        from mcu_action_evidence import bundle_digest
        from work_recovery import canonical
        if self.get_native_recovery_close_isolation(action_uid) is not None:
            return None
        binding = self.get_native_delivery_recovery_close(action_uid)
        if binding is None:
            raise ValueError("native recovery close binding missing")
        issue = self.get_native_delivery_issue(binding["permit"].work_uid)
        check_occupancy(self, issue, binding["evidence"]["portNo"])
        old = self.get_native_recovery_close_confirmation(action_uid)
        if old is not None:
            return old
        bundle = close_effect_bundle(self, conn, action_uid)
        if bundle is None:
            return None
        encoded = canonical(bundle)
        digest = bundle_digest(encoded)
        conn.execute("INSERT INTO native_recovery_close_confirmation(action_uid,bundle_json,evidence_sha256) VALUES(?,?,?)",
            (action_uid, encoded, digest))
        return dict(action_uid=action_uid, bundle_json=encoded, evidence_sha256=digest, state="PENDING")

    def prepare_native_recovery_close_confirmation(self, action_uid):
        with self._standalone_native_transaction() as conn:
            return self._prepare_native_recovery_close_confirmation(conn, action_uid)

    def list_pending_native_recovery_close_uids(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("native recovery close batch must contain 1..32 actions")
        with self._lock:
            return [row[0] for row in self._conn.execute("""SELECT r.action_uid
                FROM native_delivery_recovery_close r
                JOIN native_delivery_issue i ON i.issue_uid=r.issue_uid
                JOIN native_mcu_command c ON c.command_uid=r.action_uid
                JOIN work_slot w ON w.slot_id=1 AND w.work_uid=i.work_uid AND w.work_type='DELIVERY'
                LEFT JOIN native_recovery_close_confirmation f ON f.action_uid=r.action_uid
                WHERE c.write_claimed=1 AND (f.state IS NULL OR f.state='PENDING')
                ORDER BY c.mcu_boot_id,c.command_sequence LIMIT ?""", (limit,))]

    def confirm_native_recovery_close_effect(self, action_uid, ledger):
        from mcu_action_evidence import check_ledger
        with self._standalone_native_transaction() as conn:
            proof = self._prepare_native_recovery_close_confirmation(conn, action_uid)
            if proof is None:
                raise ValueError("native recovery close output proof missing")
            check_ledger(self.get_native_delivery_recovery_close(action_uid), ledger, proof)
            conn.execute("UPDATE native_recovery_close_confirmation SET state='CONFIRMED' WHERE action_uid=?", (action_uid,))
            return proof | {"state": "CONFIRMED"}

    def _migrate_v36(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_recovery_close (
            action_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_mcu_command(command_uid),
            issue_uid TEXT NOT NULL UNIQUE REFERENCES native_delivery_issue(issue_uid),
            binding_json TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL CHECK(length(binding_sha256)=64)
        )""")
        from native_delivery_recovery_close import checked_binding
        if self._conn.execute("""SELECT 1 FROM native_mcu_command c
                LEFT JOIN native_delivery_recovery_close r ON r.action_uid=c.command_uid
                WHERE c.message_name='SAFE_CLOSE' AND r.action_uid IS NULL LIMIT 1""").fetchone():
            raise ValueError("delivery recovery close lost its durable authority")
        for row in self._conn.execute("SELECT * FROM native_delivery_recovery_close"):
            checked_binding(self, row)

    def get_native_delivery_recovery_close(self, action_uid):
        from native_delivery_recovery_close import checked_binding
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_delivery_recovery_close WHERE action_uid=?", (action_uid,)).fetchone()
            return checked_binding(self, row) if row else None

    def snapshot_native_recovery_close_startup(self):
        """One original occupied issue and its existing preparations, not new work."""
        from native_delivery_recovery_close import check_occupancy, checked_binding
        with self._standalone_native_transaction() as conn:
            slot = self.get_work_slot()
            if slot is None or slot["work_type"] != "DELIVERY":
                return None
            issue = self.get_native_delivery_issue(slot["work_uid"])
            if issue is None:
                return None
            rows = conn.execute("""SELECT b.* FROM native_delivery_recovery_close b
                JOIN native_mcu_command c ON c.command_uid=b.action_uid
                WHERE b.issue_uid=? ORDER BY c.command_sequence""", (issue["issueUid"],)).fetchall()
            bindings = [checked_binding(self, row) for row in rows]
            for binding in bindings:
                check_occupancy(self, issue, binding["evidence"]["portNo"])
            return dict(issue=issue, bindings=bindings, slot=dict(slot))

    def prepare_native_delivery_recovery_close(self, work_uid, source_ledger, *, current_boot, execution_window_ms):
        from native_delivery_recovery_close import prepare_in_transaction
        with self._standalone_native_transaction() as conn:
            return prepare_in_transaction(self, conn, work_uid, source_ledger, current_boot, execution_window_ms)

    def prepare_native_delivery_recovery_close_successor(self, predecessor_action_uid, source_ledger,
            predecessor_ledger, *, current_boot, execution_window_ms, predecessor_disposition=None):
        from native_delivery_recovery_close import prepare_successor_in_transaction
        with self._standalone_native_transaction() as conn:
            return prepare_successor_in_transaction(self, conn, predecessor_action_uid, source_ledger,
                predecessor_ledger, current_boot, execution_window_ms, predecessor_disposition)

    def validate_native_recovery_close_source(self, action_uid, source_ledger):
        """One local custody snapshot; caller must finish external reads first."""
        from native_delivery_recovery_close import check_occupancy, check_source_ledger
        with self._standalone_native_transaction():
            binding = self.get_native_delivery_recovery_close(action_uid)
            if binding is None:
                raise ValueError("delivery recovery close binding missing")
            issue = self.get_native_delivery_issue(binding["permit"].work_uid)
            check_occupancy(self, issue, binding["evidence"]["portNo"])
            first = self.get_native_action_by_key(binding["permit"].work_uid, "delivery:first-open")
            check_source_ledger(self, first, source_ledger)
            return binding

    def _migrate_v35(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_issue_confirmation (
            event_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_issue_report(event_uid),
            confirmation_uid TEXT NOT NULL UNIQUE REFERENCES confirmation_inbox(confirmation_uid),
            command_json TEXT NOT NULL,
            command_sha256 TEXT NOT NULL CHECK(length(command_sha256)=64),
            receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64)
        )""")
        from native_delivery_issue_report import checked_issue_confirmation
        for row in self._conn.execute("SELECT * FROM native_delivery_issue_confirmation"):
            checked_issue_confirmation(self, self._conn, row)

    def get_native_delivery_issue_confirmation(self, work_uid, *, device_name, event_uid=None):
        from native_delivery_issue_report import checked_issue_confirmation
        with self._lock:
            issue = self.get_native_delivery_issue(work_uid)
            if issue is None:
                return None
            if issue["deviceName"] != device_name:
                raise ValueError("native issue confirmation belongs to another device")
            uid = issue["issueUid"] if event_uid is None else event_uid
            task = self._conn.execute("SELECT * FROM native_delivery_issue_report WHERE event_uid=?", (uid,)).fetchone()
            if task is None:
                return None
            if task["issue_uid"] != issue["issueUid"]:
                raise ValueError("native issue confirmation belongs to another work")
            row = self._conn.execute("SELECT * FROM native_delivery_issue_confirmation WHERE event_uid=?", (uid,)).fetchone()
            return checked_issue_confirmation(self, self._conn, row) if row else None

    def _save_native_delivery_issue_confirmation(self, conn, task, stable_command):
        if task is None:
            return
        from native_delivery_issue_report import checked_issue_confirmation
        uid = stable_command["payload"]["confirmationUid"]
        digest = canonical_payload_sha256(stable_command)
        receipt = conn.execute("""SELECT e.payload_json FROM confirmation_inbox c
            JOIN event_outbox e ON e.event_uid=c.receipt_event_uid WHERE c.confirmation_uid=?""", (uid,)).fetchone()
        if receipt is None:
            raise ValueError("native issue confirmation lacks its original receipt")
        conn.execute("""INSERT OR IGNORE INTO native_delivery_issue_confirmation
            (event_uid,confirmation_uid,command_json,command_sha256,receipt_sha256) VALUES(?,?,?,?,?)""",
            (task["event_uid"], uid, _json.dumps(stable_command, ensure_ascii=False, sort_keys=True), digest,
             canonical_payload_sha256(_json.loads(receipt["payload_json"]))))
        row = conn.execute("SELECT * FROM native_delivery_issue_confirmation WHERE event_uid=?", (task["event_uid"],)).fetchone()
        if row is None or row["confirmation_uid"] != uid or row["command_sha256"] != digest:
            raise ValueError("native issue confirmation identity conflicts")
        checked_issue_confirmation(self, conn, row)

    def _migrate_v34(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_issue_report (
            event_uid TEXT PRIMARY KEY NOT NULL REFERENCES event_outbox(event_uid),
            issue_uid TEXT NOT NULL REFERENCES native_delivery_issue(issue_uid),
            device_name TEXT NOT NULL,
            evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('ARCHIVE','ARCHIVE_CONTEXT','PROCESS_FACT','FINAL_RESULT')),
            evidence_index INTEGER NOT NULL CHECK(evidence_index>=0),
            part_index INTEGER NOT NULL CHECK(part_index>=0),
            event_sha256 TEXT NOT NULL CHECK(length(event_sha256)=64),
            UNIQUE(issue_uid,evidence_kind,evidence_index,part_index)
        )""")
        for row in self._conn.execute("""SELECT DISTINCT i.work_uid FROM native_delivery_issue_report r
            JOIN native_delivery_issue i USING(issue_uid)"""):
            self.list_native_delivery_issue_reports(row["work_uid"])

    def list_native_delivery_issue_reports(self, work_uid):
        from native_delivery_issue_report import report_sources, checked_report
        with self._lock:
            issue = self.get_native_delivery_issue(work_uid)
            if issue is None:
                return []
            expected = {key:(key, kind, payload) for key,kind,payload in report_sources(self, issue)}
            rows = self._conn.execute("SELECT * FROM native_delivery_issue_report WHERE issue_uid=? ORDER BY rowid",
                (issue["issueUid"],)).fetchall()
            for row in rows:
                key = (row["evidence_kind"], row["evidence_index"], row["part_index"])
                if key not in expected:
                    raise ValueError("native issue report no longer has original evidence")
                checked_report(self, row, issue, expected[key])
            return [dict(row) for row in rows]

    def prepare_native_delivery_issue_reports(self, work_uid, *, device_name, limit=50):
        from native_delivery_issue_report import report_sources, checked_report, ARCHIVE_EVENT
        from onenet_wire import build_event_envelope, encode_event_post
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("native issue report batch must contain 1..100 new events")
        with self._standalone_native_transaction() as conn:
            issue = self.get_native_delivery_issue(work_uid)
            if issue is None or issue["deviceName"] != device_name:
                raise ValueError("native issue report requires the original archived device/work")
            created = []
            for key, event_type, payload in report_sources(self, issue):
                existing = conn.execute("""SELECT * FROM native_delivery_issue_report
                    WHERE issue_uid=? AND evidence_kind=? AND evidence_index=? AND part_index=?""",
                    (issue["issueUid"], *key)).fetchone()
                if existing:
                    checked_report(self, existing, issue, (key, event_type, payload))
                    continue
                uid = issue["issueUid"] if event_type == ARCHIVE_EVENT else self._new_uid()
                event = build_event_envelope(device_name=device_name, event_uid=uid,
                    edge_event_sequence=self._next_seq(conn), event_type=event_type,
                    target_type="DELIVERY_SESSION", target_uid=work_uid, command_uid=issue["permit"]["command_uid"], payload=payload)
                encode_event_post(event_type, event)
                self._insert_event(conn, event, event_type)
                conn.execute("""INSERT INTO native_delivery_issue_report
                    (event_uid,issue_uid,device_name,evidence_kind,evidence_index,part_index,event_sha256)
                    VALUES (?,?,?,?,?,?,?)""", (uid, issue["issueUid"], device_name, *key, canonical_payload_sha256(event)))
                created.append(uid)
                if len(created) == limit:
                    break
            return created

    def _migrate_v33(self) -> None:
        """Irreversible delivery issue verdict; not business completion/admission."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_issue (
            issue_uid TEXT PRIMARY KEY NOT NULL,
            work_uid TEXT NOT NULL UNIQUE,
            recovery_uid TEXT NOT NULL REFERENCES native_work_recovery_intent(recovery_uid),
            evidence_json TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_issue_fact (
            issue_uid TEXT NOT NULL REFERENCES native_delivery_issue(issue_uid),
            fact_sequence INTEGER NOT NULL CHECK(fact_sequence>0),
            source_kind TEXT NOT NULL,
            source_key TEXT NOT NULL,
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK(length(payload) BETWEEN 1 AND 242),
            metadata_json TEXT NOT NULL,
            PRIMARY KEY(issue_uid,fact_sequence),
            UNIQUE(issue_uid,source_kind,source_key)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_issue_result (
            issue_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_delivery_issue(issue_uid),
            mcu_boot_id INTEGER NOT NULL,
            result_sequence INTEGER NOT NULL,
            payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
            UNIQUE(mcu_boot_id,result_sequence),
            FOREIGN KEY(mcu_boot_id,result_sequence) REFERENCES native_mcu_result(mcu_boot_id,result_sequence)
        )""")
        from work_recovery import checked_delivery_issue
        for row in self._conn.execute("SELECT * FROM native_delivery_issue"):
            checked_delivery_issue(self, self._conn, row)
        for row in self._conn.execute("SELECT issue_uid FROM native_delivery_issue_result"):
            self.list_native_delivery_issue_results(row["issue_uid"])

    def get_native_delivery_issue(self, work_uid):
        from work_recovery import checked_delivery_issue
        with self._lock:
            # Earlier migrations revalidate normal reports before v33 exists.
            if not self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_delivery_issue'").fetchone():
                return None
            row = self._conn.execute("SELECT * FROM native_delivery_issue WHERE work_uid=?", (work_uid,)).fetchone()
            return checked_delivery_issue(self, self._conn, row)

    def list_native_delivery_issue_work_uids(self, *, after_work_uid="", limit=50):
        """Page existing archives for report recovery, independently of the live slot.

        This is an index only. The reporter revalidates original evidence before
        using it. No new queue, archive decision or current business is created.
        """
        if (not isinstance(after_work_uid, str) or type(limit) is not int
                or not 1 <= limit <= 100):
            raise ValueError("native issue page requires a string cursor and 1..100 rows")
        with self._lock:
            return [row["work_uid"] for row in self._conn.execute(
                "SELECT work_uid FROM native_delivery_issue WHERE work_uid>? ORDER BY work_uid LIMIT ?",
                (after_work_uid, limit))]

    def list_native_delivery_issue_results(self, issue_uid):
        from work_recovery import checked_delivery_issue, match_issue_result
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_delivery_issue WHERE issue_uid=?", (issue_uid,)).fetchone()
            issue = checked_delivery_issue(self, self._conn, row)
            if issue is None:
                raise ValueError("unknown native delivery issue")
            rows = self._conn.execute("""SELECT e.*, r.payload FROM native_delivery_issue_result e
                LEFT JOIN native_mcu_result r USING(mcu_boot_id,result_sequence) WHERE e.issue_uid=?""", (issue_uid,)).fetchall()
            for row in rows:
                if row["payload"] is None or hashlib.sha256(row["payload"]).hexdigest() != row["payload_sha256"]:
                    raise ValueError("native delivery issue late result custody is corrupt")
                match_issue_result(self, issue, bytes(row["payload"]))
            return [dict(row) for row in rows]

    def _append_native_delivery_issue_result(self, conn, payload, values):
        from work_recovery import match_issue_result
        # The result is already durably staged in this transaction. A failed
        # identity check retains those bytes but never links trusted evidence.
        related = conn.execute("""SELECT i.work_uid FROM native_delivery_issue i
            JOIN native_work_recovery_intent r USING(recovery_uid)
            WHERE i.work_uid=? OR r.start_command_uid=?""", (values["workUid"], values["originCommandUid"])).fetchall()
        if not related:
            return
        if len(related) != 1:
            raise ValueError("native late result identity names conflicting archived deliveries")
        issue = self.get_native_delivery_issue(related[0]["work_uid"])
        match_issue_result(self, issue, payload)
        rows = self.list_native_delivery_issue_results(issue["issueUid"])
        if rows:
            if rows[0]["payload"] != payload:
                raise ValueError("native delivery issue has conflicting late result identities")
            return
        conn.execute("""INSERT INTO native_delivery_issue_result
            (issue_uid,mcu_boot_id,result_sequence,payload_sha256) VALUES (?,?,?,?)""",
            (issue["issueUid"], values["mcuBootId"], values["resultSequence"], hashlib.sha256(payload).hexdigest()))

    def list_native_delivery_issue_facts(self, issue_uid, *, after_sequence=0, limit=100):
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("native issue facts require a nonnegative cursor and 1..1000 rows")
        from work_recovery import checked_delivery_issue
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_delivery_issue WHERE issue_uid=?", (issue_uid,)).fetchone()
            if checked_delivery_issue(self, self._conn, row) is None:
                raise ValueError("unknown native delivery issue")
            return [dict(r) for r in self._conn.execute("""SELECT * FROM native_delivery_issue_fact
                WHERE issue_uid=? AND fact_sequence>? ORDER BY fact_sequence LIMIT ?""", (issue_uid, after_sequence, limit))]

    def archive_native_delivery_issue(self, permit, start_command_uid, *, device_name, current_boot):
        from dataclasses import asdict
        from job_safety import JobPermit
        from work_recovery import (original_work, complete_result, delivery_issue_authority,
            known_facts, facts_fingerprint, canonical)
        if not isinstance(permit, JobPermit) or permit.work_type != "DELIVERY":
            raise ValueError("native issue archive applies only to delivery")
        def existing_decision():
            issue = self.get_native_delivery_issue(permit.work_uid)
            if issue is not None:
                if (issue["permit"] != asdict(permit) or issue["startCommandUid"] != start_command_uid
                        or issue["deviceName"] != device_name):
                    raise ValueError("native delivery issue differs from requested original identity")
                return dict(status="DELIVERY_ISSUE_ARCHIVED", issue=issue)
            return None
        existing = existing_decision()
        if existing is not None:
            return existing
        decision = self.evaluate_native_work_recovery(permit, start_command_uid, current_boot=current_boot)
        if decision["status"] != "RECOVERY_INTENT_RECORDED":
            return decision
        # The neutral intent is not terminal. Arbitrate final packet vs archive
        # again in the SAME write transaction that freezes the terminal verdict.
        with self._standalone_native_transaction() as conn:
            existing = existing_decision()
            if existing is not None:
                return existing
            record, start = original_work(self, permit, start_command_uid)
            result = complete_result(self, conn, permit, record, start)
            if result is not None:
                return result
            intent = self.get_native_work_recovery_intent(decision["intent"]["recovery_uid"])
            boot = intent["target_mcu_boot_id"]
            if current_boot() != boot or boot != self._native_counter(conn, "native_current_boot"):
                raise RuntimeError("native boot observation expired before issue archive")
            delivery_issue_authority(self, permit, record, start, device_name)
            facts = list(known_facts(self, conn, permit, start))
            count, digest = facts_fingerprint(facts)
            uid = self._new_uid()
            issue = dict(profile="ecobin-native-delivery-issue-v1", issueUid=uid,
                workUid=permit.work_uid, permit=asdict(permit), startCommandUid=start_command_uid,
                recoveryUid=intent["recovery_uid"], deviceName=device_name,
                originalWorkContext=self.get_work_slot()["context"],
                sourceMcuBootId=start["targetMcuBootId"], targetMcuBootId=boot,
                reason="MCU_RESTART_FINAL_RESULT_UNAVAILABLE", reasonText="单片机重启，未取得最终结果包",
                settlementAllowed=False, finalResultAtArchive="ABSENT", knownFactCount=count, knownFactsSha256=digest)
            encoded = canonical(issue)
            conn.execute("""INSERT INTO native_delivery_issue
                (issue_uid,work_uid,recovery_uid,evidence_json,evidence_sha256) VALUES (?,?,?,?,?)""",
                (uid, permit.work_uid, intent["recovery_uid"], encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()))
            for sequence, fact in enumerate(facts, 1):
                conn.execute("""INSERT INTO native_delivery_issue_fact
                    (issue_uid,fact_sequence,source_kind,source_key,message_name,payload,metadata_json)
                    VALUES (?,?,?,?,?,?,?)""", (uid, sequence, fact["source_kind"], fact["source_key"],
                    fact["message_name"], fact["payload"], fact["metadata_json"]))
            if current_boot() != boot:
                raise RuntimeError("native boot observation expired during issue archive")
            return existing_decision()

    def _migrate_v32(self) -> None:
        """Bind native reports to their original accepted backend decisions."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_result_confirmation (
            event_uid TEXT PRIMARY KEY NOT NULL REFERENCES native_result_report_outbox(event_uid),
            confirmation_uid TEXT NOT NULL UNIQUE REFERENCES confirmation_inbox(confirmation_uid),
            command_json TEXT NOT NULL,
            command_sha256 TEXT NOT NULL CHECK (length(command_sha256)=64),
            receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256)=64)
        )""")
        for row in self._conn.execute("SELECT * FROM native_result_confirmation"):
            self._native_result_confirmation_binding(self._conn, row)

    def _native_result_confirmation_binding(self, conn, row):
        from native_result_report import confirmation_for_report
        task = conn.execute("SELECT * FROM native_result_report_outbox WHERE event_uid=?", (row["event_uid"],)).fetchone()
        accepted = conn.execute("SELECT * FROM confirmation_inbox WHERE confirmation_uid=?", (row["confirmation_uid"],)).fetchone()
        command = _json.loads(row["command_json"])
        if (task is None or accepted is None or canonical_payload_sha256(command) != row["command_sha256"]
                or accepted["canonical_sha256"] != row["command_sha256"]
                or accepted["command_uid"] != command.get("commandUid")):
            raise ValueError("native confirmation command custody is corrupt")
        stable, binding = confirmation_for_report(self, conn, task, command, command.get("targetDeviceName"), persisted=True)
        payload = stable["payload"]
        if (accepted["event_uid"] != row["event_uid"] or accepted["outcome"] != payload["outcome"]
                or row["confirmation_uid"] != payload["confirmationUid"]
                or _json.loads(accepted["payload_json"]) != payload):
            raise ValueError("native confirmation inbox differs from original decision")
        receipt = self.get_event(accepted["receipt_event_uid"])
        event = self.get_event(row["event_uid"])
        if receipt is None or event["state"] != EVENT_CONFIRMED or event["confirmed_at"] is None:
            raise ValueError("native confirmation lost its atomic receipt")
        body = _json.loads(receipt["payload_json"])
        if canonical_payload_sha256(body) != row["receipt_sha256"]:
            raise ValueError("native confirmation original receipt digest is corrupt")
        expected = {key: payload[key] for key in ("confirmationUid", "originalEventUid", "originalPayloadSha256", "outcome")}
        if (body.get("payload") != expected or body.get("payloadSha256") != canonical_payload_sha256(expected)
                or body.get("commandUid") != stable["commandUid"] or body.get("eventUid") != accepted["receipt_event_uid"]
                or body.get("eventType") != "BUSINESS_CONFIRMATION_RECEIPT"
                or receipt["event_type"] != body["eventType"] or receipt["work_uid"] != row["event_uid"]
                or body.get("edgeEventSequence") != receipt["edge_event_sequence"]
                or body.get("target") != {"type": "BUSINESS_CONFIRMATION", "uid": row["confirmation_uid"]}):
            raise ValueError("native confirmation receipt differs from original decision")
        return dict(eventUid=row["event_uid"], confirmationUid=row["confirmation_uid"],
            receiptEventUid=accepted["receipt_event_uid"], workUid=binding["permit"]["work_uid"], **{
                key: payload[key] for key in ("outcome", "effectKind", "resultReferences", "processedAt", "errorCode", "quarantineUid")})

    def get_native_result_confirmation(self, permit, start_command_uid, *, device_name):
        with self._lock:
            report = self.get_native_result_report(permit, start_command_uid, device_name=device_name)
            if report is None:
                return None
            row = self._conn.execute("SELECT * FROM native_result_confirmation WHERE event_uid=?", (report["eventUid"],)).fetchone()
            return self._native_result_confirmation_binding(self._conn, row) if row else None

    def _save_native_result_confirmation(self, conn, task, stable_command):
        if task is None:
            return
        uid = stable_command["payload"]["confirmationUid"]
        serialized = _json.dumps(stable_command, ensure_ascii=False, sort_keys=True)
        digest = canonical_payload_sha256(stable_command)
        receipt = conn.execute("""SELECT e.payload_json FROM confirmation_inbox c
            JOIN event_outbox e ON e.event_uid=c.receipt_event_uid WHERE c.confirmation_uid=?""", (uid,)).fetchone()
        if receipt is None:
            raise ValueError("native confirmation lacks its original receipt")
        receipt_digest = canonical_payload_sha256(_json.loads(receipt["payload_json"]))
        conn.execute("""INSERT OR IGNORE INTO native_result_confirmation
            (event_uid, confirmation_uid, command_json, command_sha256, receipt_sha256) VALUES (?,?,?,?,?)""",
            (task["event_uid"], uid, serialized, digest, receipt_digest))
        row = conn.execute("SELECT * FROM native_result_confirmation WHERE event_uid=?", (task["event_uid"],)).fetchone()
        if row is None or row["confirmation_uid"] != uid or row["command_sha256"] != digest:
            raise ValueError("native confirmation identity conflicts")
        self._native_result_confirmation_binding(conn, row)

    def prepare_native_business_completion(self, permit, start_command_uid, *, device_name, permit_snapshot):
        from native_business_completion import prepare
        return prepare(self, permit, start_command_uid, device_name=device_name, permit_snapshot=permit_snapshot)

    def apply_native_business_completion(self, permit, start_command_uid, *, device_name, permit_snapshot):
        from native_business_completion import apply
        return apply(self, permit, start_command_uid, device_name=device_name, permit_snapshot=permit_snapshot)

    def prepare_native_issue_completion(self, permit, start_command_uid, *, device_name, permit_snapshot):
        from native_issue_completion import prepare
        return prepare(self, permit, start_command_uid, device_name=device_name, permit_snapshot=permit_snapshot)

    def apply_native_issue_completion(self, permit, start_command_uid, *, device_name, permit_snapshot):
        from native_issue_completion import apply
        return apply(self, permit, start_command_uid, device_name=device_name, permit_snapshot=permit_snapshot)

    def prepare_native_control_failure(self, permit, start_command_uid, *, device_name, stage, reason):
        from native_control_failure import prepare
        return prepare(self, permit, start_command_uid, device_name=device_name, stage=stage, reason=reason)

    def apply_native_control_failure(self, permit, start_command_uid, *, device_name, permit_snapshot):
        from native_control_failure import apply
        return apply(self, permit, start_command_uid, device_name=device_name, permit_snapshot=permit_snapshot)

    def _migrate_v31(self) -> None:
        """Consume native result custody into the existing reliable event outbox."""
        conn = self._conn
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(native_result_report_outbox)")}
        if "report_json" not in columns:
            conn.execute("ALTER TABLE native_result_report_outbox RENAME TO native_result_report_v30")
            conn.execute("""CREATE TABLE native_result_report_outbox (
                task_uid TEXT NOT NULL UNIQUE,
                mcu_boot_id INTEGER NOT NULL,
                result_sequence INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING_CLASSIFICATION',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                event_uid TEXT UNIQUE REFERENCES event_outbox(event_uid),
                report_json TEXT,
                CHECK ((state='PENDING_CLASSIFICATION' AND event_uid IS NULL AND report_json IS NULL)
                    OR (state='REPORT_CREATED' AND event_uid IS NOT NULL AND report_json IS NOT NULL)),
                PRIMARY KEY (mcu_boot_id, result_sequence),
                FOREIGN KEY (mcu_boot_id, result_sequence)
                    REFERENCES native_mcu_result (mcu_boot_id, result_sequence)
            )""")
            conn.execute("""INSERT INTO native_result_report_outbox
                (task_uid, mcu_boot_id, result_sequence, state, created_at)
                SELECT task_uid, mcu_boot_id, result_sequence, state, created_at FROM native_result_report_v30""")
            conn.execute("DROP TABLE native_result_report_v30")
        for row in conn.execute("SELECT * FROM native_result_report_outbox"):
            if row["state"] == "REPORT_CREATED":
                self._native_report_binding(conn, row)
            elif row["state"] != "PENDING_CLASSIFICATION" or row["event_uid"] is not None or row["report_json"] is not None:
                raise ValueError("native report task state is corrupt")

    def _native_report_binding(self, conn, row):
        from native_result_report import checked_report
        result = self.get_native_mcu_result(row["mcu_boot_id"], row["result_sequence"])
        event = self.get_event(row["event_uid"])
        body = _json.loads(event["payload_json"]) if event else None
        if event and any(event[column] != body.get(field) for column, field in (
                ("event_uid", "eventUid"), ("event_type", "eventType"), ("edge_event_sequence", "edgeEventSequence"))):
            raise ValueError("native report event projection is corrupt")
        if event and event["work_uid"] != body.get("target", {}).get("uid"):
            raise ValueError("native report event work identity is corrupt")
        return checked_report(self, conn, row, result, body)

    def get_native_result_report(self, permit, start_command_uid: str, *, device_name):
        from dataclasses import asdict
        with self._lock:
            rows = self._conn.execute("""SELECT t.* FROM native_result_report_outbox t
                JOIN native_mcu_result r USING(mcu_boot_id, result_sequence)
                WHERE r.work_uid=? AND t.state='REPORT_CREATED'""", (permit.work_uid,)).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise ValueError("native work has multiple business reports")
            binding = self._native_report_binding(self._conn, rows[0])
            if (binding["permit"] != asdict(permit) or binding["startCommandUid"] != start_command_uid
                    or binding["deviceName"] != device_name):
                raise ValueError("native report differs from original permit/START")
            return dict(state="REPORT_CREATED", eventUid=binding["eventUid"])

    def create_native_result_report(self, permit, start_command_uid: str, *, device_name,
                                   photo_manager=None, permit_snapshot=None, ledgers=None):
        from native_result_report import original_authority, report_payload, pending_photos, report_binding, supports_result_policy, check_job_permit
        from work_recovery import original_work, complete_result
        from onenet_wire import build_event_envelope, encode_event_post
        import uart2_protocol as uart
        with self._standalone_native_transaction() as conn:
            if self.get_native_delivery_issue(permit.work_uid) is not None:
                raise ValueError("native delivery is archived; late results are evidence only")
            existing = self.get_native_result_report(permit, start_command_uid, device_name=device_name)
            if existing is not None:
                return existing
            check_job_permit(permit, permit_snapshot)
            record, start = original_work(self, permit, start_command_uid)
            decision = complete_result(self, conn, permit, record, start)
            if decision is None or decision["evidence"]["state"] != "MATCHED":
                raise ValueError("native report lacks complete reconciled evidence")
            evidence, saved = decision["evidence"], decision["result"]
            value = uart.decode_payload("WORK_RESULT", saved["payload"])
            if not supports_result_policy(value):
                raise ValueError("native report requires an explicit supported result policy")
            command = original_authority(self, permit, start, evidence, device_name)
            if command is None:
                raise ValueError("native report lacks original cloud command custody")
            work_type = value["workType"]
            event_type = "CLEAN_COMPLETE" if work_type == "CLEAN_OPERATION" else "DELIVERY_COMPLETE"
            photos = (photo_manager.get_completion_photo_facts(permit.work_uid, work_type)
                if photo_manager is not None else pending_photos(work_type))
            event = build_event_envelope(device_name=device_name, event_uid=decision["task"]["task_uid"],
                edge_event_sequence=self._next_seq(conn), event_type=event_type,
                target_type=work_type, target_uid=permit.work_uid, command_uid=permit.command_uid,
                payload=report_payload(value, evidence, command, photos))
            encode_event_post(event_type, event)
            self._insert_event(conn, event, event_type)
            conn.execute("""UPDATE native_result_report_outbox SET state='REPORT_CREATED', event_uid=?, report_json=?
                WHERE mcu_boot_id=? AND result_sequence=? AND state='PENDING_CLASSIFICATION'""",
                (event["eventUid"], _json.dumps(report_binding(permit, start_command_uid, saved, event, device_name), sort_keys=True),
                    saved["mcu_boot_id"], saved["result_sequence"]))
            return dict(state="REPORT_CREATED", eventUid=event["eventUid"])

    def _migrate_v30(self) -> None:
        """Recovery preparation and frozen known facts; neither is a cloud result."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_work_recovery_intent (
            recovery_uid TEXT PRIMARY KEY,
            work_uid TEXT NOT NULL,
            start_command_uid TEXT NOT NULL REFERENCES native_mcu_command(command_uid),
            target_mcu_boot_id INTEGER NOT NULL REFERENCES native_mcu_boot_observation(boot_id),
            evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 1 AND 16384),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256)=64),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(work_uid, target_mcu_boot_id)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_work_recovery_fact (
            recovery_uid TEXT NOT NULL REFERENCES native_work_recovery_intent(recovery_uid),
            fact_sequence INTEGER NOT NULL CHECK (fact_sequence>0),
            source_kind TEXT NOT NULL,
            source_key TEXT NOT NULL,
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 242),
            metadata_json TEXT NOT NULL CHECK (length(metadata_json) BETWEEN 1 AND 4096),
            PRIMARY KEY(recovery_uid, fact_sequence),
            UNIQUE(recovery_uid, source_kind, source_key)
        )""")
        if self._conn.execute("""SELECT 1 FROM native_work_recovery_fact f
                LEFT JOIN native_work_recovery_intent i USING(recovery_uid)
                WHERE i.recovery_uid IS NULL LIMIT 1""").fetchone():
            raise ValueError("native recovery fact custody has no original intent")
        rows = self._conn.execute("SELECT * FROM native_work_recovery_intent")
        for row in rows:
            from work_recovery import checked_intent
            checked_intent(self, self._conn, row)

    def _migrate_v29(self) -> None:
        """One immutable positive handshake witness per owned MCU boot."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_boot_observation (
            boot_id INTEGER PRIMARY KEY REFERENCES native_mcu_boot(boot_id),
            probe_id INTEGER NOT NULL UNIQUE CHECK (probe_id BETWEEN 1 AND 9007199254740991),
            message_name TEXT NOT NULL CHECK (message_name IN ('BOOT_PROBE_REPLY','BIND_BOOT_REPLY')),
            payload BLOB NOT NULL CHECK (length(payload) IN (16,25)),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(native_mcu_command)")}
        if "boot_retired" not in columns:
            self._conn.execute("""ALTER TABLE native_mcu_command ADD COLUMN boot_retired
                INTEGER NOT NULL DEFAULT 0 CHECK (boot_retired IN (0,1))""")
            self._conn.execute("DROP INDEX IF EXISTS native_mcu_one_pending_command")
        predicate = "decision_outcome IS NULL AND boot_retired=0" + (" AND dispatch_retired=0" if "dispatch_retired" in columns else "")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS native_mcu_one_pending_command "
                           f"ON native_mcu_command ((1)) WHERE {predicate}")
        self._verify_native_boot_retirements(self._conn)

    def evaluate_native_work_recovery(self, permit, start_command_uid: str, *, current_boot) -> dict:
        """Serialize original-work inspection with complete-result custody."""
        from dataclasses import asdict
        from work_recovery import original_work, complete_result, recovery_evidence, known_facts, canonical, checked_intent
        if not callable(current_boot):
            raise ValueError("native recovery requires a fresh boot observation")
        with self._standalone_native_transaction() as conn:
            issue = self.get_native_delivery_issue(permit.work_uid)
            if issue is not None:
                if issue["permit"] != asdict(permit) or issue["startCommandUid"] != start_command_uid:
                    raise ValueError("native archived delivery differs from original identity")
                return dict(status="DELIVERY_ISSUE_ARCHIVED", issue=issue)
            record, start = original_work(self, permit, start_command_uid)
            result = complete_result(self, conn, permit, record, start)
            if result is not None:
                return result
            if not record["write_claimed"]:
                return {"status": "START_NOT_DISPATCHED"}
            if record["decision_outcome"] == "REJECTED":
                return {"status": "START_REJECTED"}
            boot = current_boot()
            if boot is None:
                return {"status": "WAIT_FOR_BOOT"}
            if type(boot) is not int or boot != self._native_counter(conn, "native_current_boot"):
                raise ValueError("native recovery boot owner is stale")
            if boot == start["targetMcuBootId"]:
                return {"status": "WAIT_FOR_ORIGINAL_WORK"}
            if boot < start["targetMcuBootId"]:
                raise ValueError("native recovery boot predates the original START")
            witness = self.get_native_boot_observation(boot)
            if witness is None:
                raise ValueError("native recovery requires a saved positive boot witness")
            row = conn.execute("SELECT * FROM native_work_recovery_intent WHERE work_uid=? AND target_mcu_boot_id=?",
                (permit.work_uid, boot)).fetchone()
            if row is None:
                uid = self._new_uid()
                evidence = recovery_evidence(self, conn, permit, record, start, witness, uid)
                encoded = canonical(evidence)
                conn.execute("""INSERT INTO native_work_recovery_intent
                    (recovery_uid, work_uid, start_command_uid, target_mcu_boot_id, evidence_json, evidence_sha256)
                    VALUES (?, ?, ?, ?, ?, ?)""", (uid, permit.work_uid, start_command_uid, boot,
                    encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()))
                for sequence, fact in enumerate(known_facts(self, conn, permit, start), 1):
                    conn.execute("""INSERT INTO native_work_recovery_fact
                        (recovery_uid,fact_sequence,source_kind,source_key,message_name,payload,metadata_json)
                        VALUES (?,?,?,?,?,?,?)""", (uid, sequence, fact["source_kind"], fact["source_key"],
                        fact["message_name"], fact["payload"], fact["metadata_json"]))
                row = conn.execute("SELECT * FROM native_work_recovery_intent WHERE recovery_uid=?", (uid,)).fetchone()
            intent = checked_intent(self, conn, row)
            if intent["start_command_uid"] != start_command_uid or intent["evidence"]["permit"] != asdict(permit):
                raise ValueError("native recovery intent differs from the original work")
            if current_boot() != boot:
                raise RuntimeError("native boot observation expired during recovery inspection")
            return {"status": "RECOVERY_INTENT_RECORDED", "intent": intent}

    def get_native_work_recovery_intent(self, recovery_uid: str) -> Optional[dict]:
        from work_recovery import checked_intent
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_work_recovery_intent WHERE recovery_uid=?", (recovery_uid,)).fetchone()
            return checked_intent(self, self._conn, row)

    def list_native_work_recovery_intents(self, work_uid: str) -> list[dict]:
        with self._lock:
            return [self.get_native_work_recovery_intent(row[0]) for row in self._conn.execute(
                "SELECT recovery_uid FROM native_work_recovery_intent WHERE work_uid=? ORDER BY target_mcu_boot_id", (work_uid,))]

    def list_native_work_recovery_facts(self, recovery_uid: str, *, after_sequence: int = 0, limit: int = 100) -> list[dict]:
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("native recovery fact page requires a nonnegative cursor and 1..1000 rows")
        with self._lock:
            if self.get_native_work_recovery_intent(recovery_uid) is None:
                raise ValueError("unknown native recovery intent")
            return [dict(row) for row in self._conn.execute("""SELECT * FROM native_work_recovery_fact
                WHERE recovery_uid=? AND fact_sequence>? ORDER BY fact_sequence LIMIT ?""", (recovery_uid, after_sequence, limit))]

    def _verify_native_boot_retirements(self, conn) -> None:
        current = self._native_counter(conn, "native_current_boot")
        highest = 0
        for row in conn.execute("SELECT * FROM native_mcu_boot_observation ORDER BY boot_id"):
            self._checked_native_boot_observation(conn, row)
            if row["boot_id"] > current:
                raise ValueError("native boot observation exceeds current identity")
            highest = row["boot_id"]
        if conn.execute("""SELECT 1 FROM native_mcu_command
                WHERE boot_retired=1 AND mcu_boot_id>=? LIMIT 1""", (highest,)).fetchone():
            raise ValueError("native command retirement has no newer boot witness")

    def _migrate_v28(self) -> None:
        """Original permit/action/receipt survives Pi restart independently of work UI."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_action_binding (
            action_uid TEXT PRIMARY KEY REFERENCES native_mcu_command(command_uid),
            receipt_uid TEXT NOT NULL UNIQUE,
            work_uid TEXT NOT NULL,
            platform_command_uid TEXT NOT NULL,
            action_key TEXT NOT NULL CHECK (length(action_key) BETWEEN 1 AND 160),
            permit_json TEXT NOT NULL,
            action_json TEXT NOT NULL,
            command_payload BLOB NOT NULL CHECK (length(command_payload) BETWEEN 60 AND 242),
            UNIQUE (work_uid, action_key),
            UNIQUE (platform_command_uid, action_key)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_action_confirmation (
            action_uid TEXT PRIMARY KEY REFERENCES native_action_binding(action_uid),
            bundle_json TEXT NOT NULL CHECK (length(bundle_json) BETWEEN 1 AND 8192),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            state TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'CONFIRMED'))
        )""")

    def bind_native_action(self, permit, action) -> dict:
        """Freeze identities BEFORE permanent ARM or any possible serial write.

        Exact replay reads the original; a restored caller cannot replace the
        receipt/key/permit or manufacture a binding for an already sent command.
        This is neither a permanent permission nor physical-result confirmation.
        """
        with self._standalone_native_transaction() as conn:
            return self._bind_native_action_in_tx(conn, permit, action)

    def _bind_native_action_in_tx(self, conn, permit, action):
        from mcu_action_evidence import binding_values, decode_binding, confirmed_first_clean_unlock, saved_clean_reopen_intent
        record = self._checked_native_command(conn.execute(
            "SELECT * FROM native_mcu_command WHERE command_uid=?", (action.action_uid,)).fetchone())
        values = binding_values(record, permit, action)
        if confirmed_first_clean_unlock(self, permit, record) is not None:
            import uart2_protocol as uart2
            grant = uart2.decode_payload("UNLOCK_CLEAN_DOOR", record["payload"])
            if saved_clean_reopen_intent(self, grant) is None:
                raise ValueError("native reopen requires its saved original intent")
        old = conn.execute("SELECT * FROM native_action_binding WHERE action_uid=?", (action.action_uid,)).fetchone()
        if old is not None:
            original = decode_binding(old, record)
            if any(old[key] != value for key, value in values.items()):
                raise ValueError("native action binding identity conflict")
            return original
        if record["write_claimed"] or record["decision_outcome"] is not None or record["conflict"]:
            raise ValueError("cannot add native action binding after dispatch or decision")
        conn.execute("""INSERT INTO native_action_binding (action_uid, receipt_uid, work_uid,
            platform_command_uid, action_key, permit_json, action_json, command_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", tuple(values.values()))
        return {"permit": permit, "action": action, "command_payload": record["payload"]}

    def get_native_action_binding(self, action_uid: str) -> Optional[dict]:
        from mcu_action_evidence import decode_binding
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_action_binding WHERE action_uid=?", (action_uid,)).fetchone()
            if row is None:
                return None
            record = self._checked_native_command(self._conn.execute(
                "SELECT * FROM native_mcu_command WHERE command_uid=?", (action_uid,)).fetchone())
            return decode_binding(row, record)

    def get_native_action_by_key(self, work_uid: str, action_key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT action_uid FROM native_action_binding WHERE work_uid=? AND action_key=?",
                (work_uid, action_key)).fetchone()
            return self.get_native_action_binding(row["action_uid"]) if row else None

    def prepare_native_clean_reopen(self, permit, intent_scope: bytes, *, remaining_operation_window_ms: int) -> dict:
        """One saved button -> one atomically frozen command/action/receipt.

        No RPC or UART is sent. Exact re-observation returns the original even
        if the caller now has a smaller remaining window; it never rewrites a
        queued command. The live dispatch gate must still check original time
        limits/readiness. It can never resend a command already claimed.
        """
        from job_safety import JobPermit, PhysicalAction, action_digest
        from mcu_action_evidence import clean_unlock_action_key, confirmed_first_clean_unlock, saved_clean_reopen_intent
        from mcu_process_handoff import decode_process_scope
        import uart2_protocol as uart2

        original = decode_process_scope(intent_scope)
        if (not isinstance(permit, JobPermit) or permit.work_type != "CLEAN"
                or original["workType"] != "CLEAN_OPERATION" or original["workUid"] != permit.work_uid
                or original["eventMessageType"] != "CLEAN_UNLOCK_REQUESTED" or original["stepSequence"] == 0):
            raise ValueError("native clean reopen requires original unlock intent and permit")
        if type(remaining_operation_window_ms) is not int or not 1 <= remaining_operation_window_ms <= 0xFFFFFFFF:
            raise ValueError("native clean remaining operation window is invalid")
        with self._standalone_native_transaction() as conn:
            slot = self.get_work_slot()
            if (slot is None or slot["work_type"] != "CLEAN" or slot["work_uid"] != permit.work_uid
                    or slot["port_no"] != original["portNo"]):
                raise ValueError("native clean reopen requires original occupied work")
            intent = self.get_native_process_receipt(intent_scope)
            first = self.get_native_action_by_key(permit.work_uid, "clean:first-unlock")
            if intent is None or first is None:
                raise ValueError("native clean reopen requires saved intent and first unlock")
            first_grant = uart2.decode_payload("UNLOCK_CLEAN_DOOR", first["command_payload"])
            if remaining_operation_window_ms > first_grant["remainingOperationWindowMs"]:
                raise ValueError("native clean reopen cannot extend original operation window")
            grant = dict(operationUid=permit.work_uid, portNo=original["portNo"],
                parentCommandUid=original["mcuCommandUid"], cleanActionSequence=original["stepSequence"], recoveryGeneration=0,
                unlockPulseMs=first_grant["unlockPulseMs"], remainingOperationWindowMs=remaining_operation_window_ms)
            key = clean_unlock_action_key(grant | {"targetMcuBootId": original["targetMcuBootId"]})
            matched = saved_clean_reopen_intent(self, grant | {"targetMcuBootId": original["targetMcuBootId"]})
            if matched is None or matched["scope"] != intent_scope or matched["payload"] != intent["payload"]:
                raise ValueError("native clean reopen intent differs from original command context")
            existing = self.get_native_action_by_key(permit.work_uid, key)
            if existing is not None:
                if existing["permit"] != permit:
                    raise ValueError("native clean reopen permit identity conflict")
                record = self.get_native_command(existing["action"].action_uid)
                confirmed_first_clean_unlock(self, permit, record)
                return existing
            uid, receipt_uid = str(_uuid.uuid4()), str(_uuid.uuid4())
            record = self._prepare_native_command_in_tx(conn, "UNLOCK_CLEAN_DOOR", uid, original["targetMcuBootId"], grant)
            digest = action_digest(work_uid=permit.work_uid, command_uid=permit.command_uid, action_key=key,
                action_kind="UNLOCK_CLEAN_DOOR", payload={"nativeUartPayloadHex": record["payload"].hex()})
            action = PhysicalAction(uid, receipt_uid, key, "UNLOCK_CLEAN_DOOR", digest)
            return self._bind_native_action_in_tx(conn, permit, action)

    def list_native_action_actuator_events(self, action_uid: str, message_name: str) -> list[dict]:
        """Bounded first-action proof candidates; third output is a contradiction.

        Later local-delivery rounds have their own message kind and cannot be
        treated as additional executions of the Pi's original first-open grant.
        """
        if message_name not in {"DELIVERY_DOOR_COMMAND_RESULT", "CLEAN_LOCK_POWER_CHANGED"}:
            raise ValueError("unsupported native action output kind")
        with self._lock:
            rows = self._conn.execute("""SELECT mcu_boot_id, event_sequence FROM native_actuator_event
                WHERE reported_command_uid=? AND message_name=? ORDER BY mcu_boot_id, event_sequence LIMIT 3""",
                (action_uid, message_name)).fetchall()
            return [self.get_native_actuator_event(row["mcu_boot_id"], row["event_sequence"]) for row in rows]

    def _native_action_confirmation_in_tx(self, conn, action_uid):
        from mcu_action_evidence import canonical, bundle_digest, checked_confirmation, executed_bundle
        row = conn.execute("SELECT * FROM native_action_confirmation WHERE action_uid=?", (action_uid,)).fetchone()
        original = checked_confirmation(row) if row else None
        pinned = _json.loads(original["bundle_json"]) if original else None
        bundle = executed_bundle(self, action_uid, pinned)
        if bundle is None:
            if original:
                raise ValueError("native pending proof lost its original evidence")
            return None
        binding = self.get_native_action_binding(action_uid)
        slot = self.get_work_slot()
        grant = bundle["command"]
        import uart2_protocol as uart2
        port = uart2.decode_payload(grant["messageName"], bytes.fromhex(grant["payloadHex"]))["portNo"]
        if (slot is None or slot["work_uid"] != binding["permit"].work_uid
                or slot["work_type"] != binding["permit"].work_type or slot["port_no"] != port):
            raise ValueError("native action requires original occupied work")
        encoded = canonical(bundle)
        if original:
            if encoded != original["bundle_json"]:
                raise ValueError("native action proof identity conflict")
            return original
        if len(encoded) > 8192:
            raise ValueError("native action proof exceeds bounded storage")
        digest = bundle_digest(encoded)
        conn.execute("INSERT INTO native_action_confirmation (action_uid, bundle_json, evidence_sha256) VALUES (?, ?, ?)",
            (action_uid, encoded, digest))
        return dict(action_uid=action_uid, bundle_json=encoded, evidence_sha256=digest, state="PENDING")

    def prepare_native_action_confirmation(self, action_uid: str) -> Optional[dict]:
        """Commit immutable full proof BEFORE a permanent-ledger confirmation RPC."""
        with self._standalone_native_transaction() as conn:
            return self._native_action_confirmation_in_tx(conn, action_uid)

    def list_pending_native_action_uids(self, *, limit: int = 32) -> list[str]:
        """Recover only the currently occupied work's original dispatched bindings."""
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("native reconciliation batch must contain 1..32 actions")
        with self._lock:
            return [row[0] for row in self._conn.execute("""SELECT b.action_uid
                FROM native_action_binding b JOIN native_mcu_command c ON c.command_uid=b.action_uid
                JOIN work_slot w ON w.slot_id=1 AND w.work_uid=b.work_uid AND w.work_type<>'NONE'
                LEFT JOIN native_action_confirmation f ON f.action_uid=b.action_uid
                WHERE c.write_claimed=1 AND (f.state IS NULL OR f.state='PENDING')
                ORDER BY c.mcu_boot_id,c.command_sequence LIMIT ?""", (limit,)).fetchall()]

    def get_native_action_confirmation(self, action_uid: str) -> Optional[dict]:
        """Historical outbox status; not a new admission/safety decision."""
        from mcu_action_evidence import checked_confirmation
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_action_confirmation WHERE action_uid=?", (action_uid,)).fetchone()
            return checked_confirmation(row) if row else None

    def confirm_native_action_effect(self, action_uid: str, ledger: dict) -> dict:
        """Match original permanent receipt and recheck custody before local confirmation."""
        from mcu_action_evidence import check_ledger
        with self._standalone_native_transaction() as conn:
            proof = self._native_action_confirmation_in_tx(conn, action_uid)
            if proof is None:
                raise ValueError("native output proof missing")
            check_ledger(self.get_native_action_binding(action_uid), ledger, proof)
            conn.execute("UPDATE native_action_confirmation SET state='CONFIRMED' WHERE action_uid=?", (action_uid,))
            return proof | {"state": "CONFIRMED"}

    def _migrate_v27(self) -> None:
        """Human confirmation is a separate fact from the same-step finish request."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_clean_confirmation (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            scope BLOB NOT NULL UNIQUE CHECK (length(scope) = 89),
            work_uid TEXT NOT NULL,
            clean_action_sequence INTEGER NOT NULL CHECK (clean_action_sequence BETWEEN 1 AND 65535),
            finish_event_sequence INTEGER NOT NULL CHECK (finish_event_sequence BETWEEN 1 AND 4294967295),
            final_event_sequence INTEGER NOT NULL CHECK (final_event_sequence BETWEEN 1 AND 4294967295),
            payload BLOB NOT NULL CHECK (length(payload) = 83),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence),
            UNIQUE (mcu_boot_id, work_uid)
        )""")

    def _native_clean_confirmation_record(self, scope: bytes, payload: bytes) -> dict:
        import uart2_protocol as uart2
        from mcu_process_handoff import decode_process_scope, process_event_receipt

        receipt = process_event_receipt(scope, "CLEAN_COMPLETION_CONFIRMED", payload)
        values = uart2.decode_payload("CLEAN_COMPLETION_CONFIRMED", payload)
        original = decode_process_scope(scope)
        predecessors = []
        for name in ("CLEAN_FINISH_REQUESTED", "CLEAN_FINAL_WEIGHT_READY"):
            prior_scope = uart2.encode_payload("QUERY_PROCESS_EVENT", original | {"queryId": 1, "eventMessageType": name})[8:]
            row = self.get_native_process_receipt(prior_scope)
            if row is None:
                raise ValueError("clean confirmation requires exactly saved finish and final weight")
            predecessors.append(uart2.decode_payload(name, bytes(row["payload"])))
        finish, final = predecessors
        if (final["measurementUid"] != values["finalMeasurementUid"]
                or not finish["mcuEventSequence"] < final["mcuEventSequence"] < values["mcuEventSequence"]
                or not finish["uptimeMs"] <= final["uptimeMs"] <= values["uptimeMs"]):
            raise ValueError("clean confirmation differs from its saved final candidate or order")
        return dict(mcu_boot_id=values["mcuBootId"], event_sequence=values["mcuEventSequence"], scope=scope,
            work_uid=values["operationUid"], clean_action_sequence=values["cleanActionSequence"],
            finish_event_sequence=finish["mcuEventSequence"], final_event_sequence=final["mcuEventSequence"],
            payload=payload, saved_payload=receipt)

    def _verify_native_clean_confirmation_row(self, row) -> None:
        expected = self._native_clean_confirmation_record(bytes(row["scope"]), bytes(row["payload"]))
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("native clean confirmation custody is corrupt")

    def _save_native_clean_confirmation_receipt(self, scope: bytes, payload: bytes) -> bytes:
        import uart2_protocol as uart2
        from mcu_process_handoff import process_event_receipt

        name = "CLEAN_COMPLETION_CONFIRMED"
        receipt = process_event_receipt(scope, name, payload)
        values = uart2.decode_payload(name, payload)
        key = values["mcuBootId"], values["mcuEventSequence"]
        with self._standalone_native_transaction() as conn:
            conflict = self._native_custody_conflicted(conn, key)
            for table in ("native_measurement_event", "native_actuator_event", "native_delivery_selection", "native_clean_intent"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone():
                    conflict = True
            matches = conn.execute("""SELECT * FROM native_clean_confirmation
                WHERE (mcu_boot_id=? AND event_sequence=?) OR scope=? OR (mcu_boot_id=? AND work_uid=?)""",
                (*key, scope, key[0], values["operationUid"])).fetchall()
            for old in matches:
                self._verify_native_clean_confirmation_row(old)
                original = old["mcu_boot_id"], old["event_sequence"]
                if bytes(old["scope"]) != scope or bytes(old["payload"]) != payload:
                    self._retain_native_custody_conflict(conn, original, name, payload)
                    conflict = True
                if self._native_custody_conflicted(conn, original):
                    conflict = True
            if conflict:
                self._retain_native_custody_conflict(conn, key, name, payload)
            else:
                record = self._native_clean_confirmation_record(scope, payload)
                if not matches:
                    conn.execute("""INSERT INTO native_clean_confirmation (mcu_boot_id,event_sequence,scope,work_uid,
                        clean_action_sequence,finish_event_sequence,final_event_sequence,payload,saved_payload)
                        VALUES (?,?,?,?,?,?,?,?,?)""", tuple(record.values()))
        if conflict:
            raise ValueError("native clean confirmation identity conflict; evidence retained, no receipt")
        return receipt

    def _migrate_v26(self) -> None:
        """Clean button intents are not measurements, actions or final results."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_clean_intent (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            message_name TEXT NOT NULL CHECK (message_name IN ('CLEAN_UNLOCK_REQUESTED','CLEAN_FINISH_REQUESTED')),
            scope BLOB NOT NULL UNIQUE CHECK (length(scope) = 89),
            work_uid TEXT NOT NULL,
            clean_action_sequence INTEGER NOT NULL CHECK (clean_action_sequence BETWEEN 1 AND 65535),
            payload BLOB NOT NULL CHECK (length(payload) = 63),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence),
            UNIQUE (mcu_boot_id, work_uid, clean_action_sequence)
        )""")

    @staticmethod
    def _native_clean_intent_record(scope: bytes, message_name: str, payload: bytes) -> dict:
        import uart2_protocol as uart2
        from mcu_process_handoff import process_event_receipt

        if message_name not in {"CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"}:
            raise ValueError("not a clean button intent")
        receipt = process_event_receipt(scope, message_name, payload)
        values = uart2.decode_payload(message_name, payload)
        return dict(mcu_boot_id=values["mcuBootId"], event_sequence=values["mcuEventSequence"],
            message_name=message_name, scope=scope, work_uid=values["operationUid"],
            clean_action_sequence=values["cleanActionSequence"], payload=payload, saved_payload=receipt)

    @classmethod
    def _verify_native_clean_intent_row(cls, row) -> None:
        expected = cls._native_clean_intent_record(bytes(row["scope"]), row["message_name"], bytes(row["payload"]))
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("native clean intent custody is corrupt")

    def _save_native_clean_intent_receipt(self, scope: bytes, message_name: str, payload: bytes) -> bytes:
        record = self._native_clean_intent_record(scope, message_name, payload)
        key = record["mcu_boot_id"], record["event_sequence"]
        with self._standalone_native_transaction() as conn:
            conflict = self._native_custody_conflicted(conn, key)
            for table in ("native_measurement_event", "native_actuator_event", "native_delivery_selection", "native_clean_confirmation"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone():
                    conflict = True
            matches = conn.execute("""SELECT * FROM native_clean_intent
                WHERE (mcu_boot_id=? AND event_sequence=?) OR scope=?
                OR (mcu_boot_id=? AND work_uid=? AND clean_action_sequence=?)""",
                (*key, scope, key[0], record["work_uid"], record["clean_action_sequence"])).fetchall()
            for old in matches:
                self._verify_native_clean_intent_row(old)
                original = old["mcu_boot_id"], old["event_sequence"]
                if old["message_name"] != message_name or bytes(old["scope"]) != scope or bytes(old["payload"]) != payload:
                    self._retain_native_custody_conflict(conn, original, message_name, payload)
                    conflict = True
                if self._native_custody_conflicted(conn, original):
                    conflict = True
            if conflict:
                self._retain_native_custody_conflict(conn, key, message_name, payload)
            elif not matches:
                conn.execute("""INSERT INTO native_clean_intent (mcu_boot_id,event_sequence,message_name,scope,
                    work_uid,clean_action_sequence,payload,saved_payload) VALUES (?,?,?,?,?,?,?,?)""", tuple(record.values()))
        if conflict:
            raise ValueError("native clean intent identity conflict; evidence retained, no receipt")
        return record["saved_payload"]

    def _migrate_v25(self) -> None:
        """Widen actuator custody to 64 bytes without rewriting prior evidence.

        The caller holds the single explicit migration transaction. Copy, swap
        and version advance commit together or roll back together. No ledger,
        business occupancy, conflict record or report task is cleared.
        """
        conn = self._conn
        shape = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_actuator_event'").fetchone()
        sql = " ".join(shape[0].split()).lower() if shape else ""
        if "check (length(payload) between 1 and 64)" in sql:
            return
        if "check (length(payload) between 1 and 60)" not in sql:
            raise RuntimeError("native actuator custody shape is incompatible")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE tbl_name='native_actuator_event' AND type IN ('index', 'trigger') AND sql IS NOT NULL").fetchone():
            raise RuntimeError("native actuator custody has unsupported additional schema")
        conn.execute("""CREATE TABLE native_actuator_event_v25 (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            message_name TEXT NOT NULL,
            reported_command_uid TEXT NOT NULL,
            reported_work_uid TEXT,
            port_no INTEGER NOT NULL CHECK (port_no BETWEEN 1 AND 6),
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 64),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence)
        )""")
        columns = "mcu_boot_id,event_sequence,message_name,reported_command_uid,reported_work_uid,port_no,payload,saved_payload,created_at"
        conn.execute(f"INSERT INTO native_actuator_event_v25 ({columns}) SELECT {columns} FROM native_actuator_event")
        conn.execute("DROP TABLE native_actuator_event")
        conn.execute("ALTER TABLE native_actuator_event_v25 RENAME TO native_actuator_event")

    def _migrate_v24(self) -> None:
        """Choices are not measurements; preserve the referenced saved weight."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_delivery_selection (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            scope BLOB NOT NULL UNIQUE CHECK (length(scope) = 89),
            work_uid TEXT NOT NULL,
            round_index INTEGER NOT NULL CHECK (round_index BETWEEN 1 AND 65535),
            postclose_event_sequence INTEGER NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) = 80),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence),
            UNIQUE (mcu_boot_id, work_uid, round_index),
            FOREIGN KEY (mcu_boot_id, postclose_event_sequence)
                REFERENCES native_process_receipt (mcu_boot_id, event_sequence)
        )""")

    def _native_selection_record(self, scope: bytes, payload: bytes) -> dict:
        import uart2_protocol as uart2
        from mcu_process_handoff import decode_process_scope, process_event_receipt

        receipt = process_event_receipt(scope, "DELIVERY_SELECTION", payload)
        values = uart2.decode_payload("DELIVERY_SELECTION", payload)
        original = decode_process_scope(scope)
        weight_scope = uart2.encode_payload("QUERY_PROCESS_EVENT", original | {
            "queryId": 1, "eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY"})[8:]
        weight_record = self.get_native_process_receipt(weight_scope)
        if weight_record is None:
            raise ValueError("selection requires an exactly saved original post-close weight")
        weight = uart2.decode_payload("WORK_POSTCLOSE_WEIGHT_READY", bytes(weight_record["payload"]))
        if (weight["measurementUid"] != values["postCloseMeasurementUid"]
                or weight["measurementKind"] not in {"STABLE_MEAN", "TIMEOUT_MEDIAN"}
                or weight["mcuEventSequence"] >= values["mcuEventSequence"]
                or weight["uptimeMs"] > values["uptimeMs"]):
            raise ValueError("selection differs from its saved available post-close weight or order")
        return dict(mcu_boot_id=values["mcuBootId"], event_sequence=values["mcuEventSequence"],
            scope=scope, work_uid=values["sessionUid"], round_index=values["roundIndex"],
            postclose_event_sequence=weight["mcuEventSequence"], payload=payload, saved_payload=receipt)

    def _verify_native_selection_row(self, row) -> None:
        expected = self._native_selection_record(bytes(row["scope"]), bytes(row["payload"]))
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("native selection custody is corrupt")

    def _save_native_selection_receipt(self, scope: bytes, payload: bytes) -> bytes:
        import uart2_protocol as uart2
        from mcu_process_handoff import process_event_receipt

        receipt = process_event_receipt(scope, "DELIVERY_SELECTION", payload)
        values = uart2.decode_payload("DELIVERY_SELECTION", payload)
        key = (values["mcuBootId"], values["mcuEventSequence"])
        with self._standalone_native_transaction() as conn:
            conflict = self._native_custody_conflicted(conn, key)
            if (conn.execute("SELECT 1 FROM native_measurement_event WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_clean_intent WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_clean_confirmation WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()):
                conflict = True
            matches = conn.execute("""SELECT * FROM native_delivery_selection
                WHERE (mcu_boot_id=? AND event_sequence=?) OR scope=?
                OR (mcu_boot_id=? AND work_uid=? AND round_index=?)""",
                (*key, scope, key[0], values["sessionUid"], values["roundIndex"])).fetchall()
            for old in matches:
                self._verify_native_selection_row(old)
                original = (old["mcu_boot_id"], old["event_sequence"])
                if bytes(old["scope"]) != scope or bytes(old["payload"]) != payload:
                    self._retain_native_custody_conflict(conn, original, "DELIVERY_SELECTION", payload)
                    conflict = True
                if self._native_custody_conflicted(conn, original):
                    conflict = True
            if conflict:
                self._retain_native_custody_conflict(conn, key, "DELIVERY_SELECTION", payload)
            else:
                record = self._native_selection_record(scope, payload)  # Same locked COMMIT as custody.
                if not matches:
                    conn.execute("""INSERT INTO native_delivery_selection (mcu_boot_id, event_sequence, scope,
                        work_uid, round_index, postclose_event_sequence, payload, saved_payload)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", tuple(record.values()))
        if conflict:
            raise ValueError("native selection identity conflict; evidence retained, no receipt")
        return receipt

    def get_native_process_event(self, message_name: str, mcu_boot_id: int, event_sequence: int) -> Optional[dict]:
        """Read typed process evidence without mislabelling a choice as weight."""
        if message_name == "CLEAN_COMPLETION_CONFIRMED":
            with self._lock:
                if self._native_custody_conflicted(self._conn, (mcu_boot_id, event_sequence)):
                    raise ValueError("native clean confirmation identity conflict")
                row = self._conn.execute("SELECT * FROM native_clean_confirmation WHERE mcu_boot_id=? AND event_sequence=?",
                    (mcu_boot_id, event_sequence)).fetchone()
                if row is None:
                    return None
                self._verify_native_clean_confirmation_row(row)
                return dict(row) | {"message_name": message_name}
        if message_name in {"CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"}:
            with self._lock:
                if self._native_custody_conflicted(self._conn, (mcu_boot_id, event_sequence)):
                    raise ValueError("native clean intent identity conflict")
                row = self._conn.execute("SELECT * FROM native_clean_intent WHERE mcu_boot_id=? AND event_sequence=?",
                    (mcu_boot_id, event_sequence)).fetchone()
                if row is None:
                    return None
                self._verify_native_clean_intent_row(row)
                if row["message_name"] != message_name:
                    raise ValueError("native clean intent message type differs")
                return dict(row)
        if message_name != "DELIVERY_SELECTION":
            return self.get_native_measurement_event(mcu_boot_id, event_sequence)
        with self._lock:
            if self._native_custody_conflicted(self._conn, (mcu_boot_id, event_sequence)):
                raise ValueError("native selection identity conflict")
            row = self._conn.execute("SELECT * FROM native_delivery_selection WHERE mcu_boot_id=? AND event_sequence=?",
                (mcu_boot_id, event_sequence)).fetchone()
            if row is None:
                return None
            self._verify_native_selection_row(row)
            return dict(row) | {"message_name": "DELIVERY_SELECTION"}

    def retain_native_process_query_conflict(self, message_name: str, payload: bytes, query_payload: bytes) -> None:
        """Persist contradictory process body/query claims without a receipt."""
        import uart2_protocol as uart2

        digest = uart2.compute_process_event_digest(message_name, payload)
        values = uart2.decode_payload(message_name, payload)
        query = uart2.decode_payload("PROCESS_EVENT_QUERY_REPLY", query_payload)
        key = (values["mcuBootId"], values["mcuEventSequence"])
        if query["status"] != "HELD" or key != (query["targetMcuBootId"], query["mcuEventSequence"]):
            raise ValueError("query and body do not claim the same process identity")
        if message_name == query["eventMessageType"] and digest == query["eventDigestSha256"]:
            raise ValueError("matching process claims are not a conflict")
        with self._standalone_native_transaction() as conn:
            self._retain_native_custody_conflict(conn, key, message_name, payload)
            self._retain_native_custody_conflict(conn, key, "PROCESS_EVENT_QUERY_REPLY", query_payload)

    def retain_native_process_query_disagreement(self, first_payload: bytes, second_payload: bytes) -> None:
        """Same fresh query, conflicting replies: retain every claimed event key."""
        import uart2_protocol as uart2

        first = uart2.decode_payload("PROCESS_EVENT_QUERY_REPLY", first_payload)
        second = uart2.decode_payload("PROCESS_EVENT_QUERY_REPLY", second_payload)
        size = uart2.MESSAGE_SPECS["QUERY_PROCESS_EVENT"]["maximumPayloadLength"]
        if first_payload == second_payload or first_payload[:size] != second_payload[:size]:
            raise ValueError("process query disagreement requires differing replies to the exact same query")
        keys = {(value["targetMcuBootId"], value["mcuEventSequence"]) for value in (first, second)
            if value["status"] in {"HELD", "RELEASED"}}
        with self._standalone_native_transaction() as conn:
            for key in keys:
                self._retain_native_custody_conflict(conn, key, "PROCESS_EVENT_QUERY_REPLY", first_payload)
                self._retain_native_custody_conflict(conn, key, "PROCESS_EVENT_QUERY_REPLY", second_payload)

    def _migrate_v23(self) -> None:
        """Actuator raw custody and REPORTED context, never an accepted command."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_actuator_event (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            message_name TEXT NOT NULL,
            reported_command_uid TEXT NOT NULL,
            reported_work_uid TEXT,
            port_no INTEGER NOT NULL CHECK (port_no BETWEEN 1 AND 6),
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 60),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_actuator_event_conflict (
            mcu_boot_id INTEGER NOT NULL,
            event_sequence INTEGER NOT NULL,
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 242),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence, message_name, payload)
        )""")

    @staticmethod
    def _native_custody_conflicted(conn, key) -> bool:
        return conn.execute("SELECT 1 FROM native_actuator_event_conflict WHERE mcu_boot_id=? AND event_sequence=? LIMIT 1", key).fetchone() is not None

    @staticmethod
    def _retain_native_custody_conflict(conn, key, message_name, payload) -> None:
        conn.execute("INSERT OR IGNORE INTO native_actuator_event_conflict (mcu_boot_id, event_sequence, message_name, payload) VALUES (?, ?, ?, ?)",
            (*key, message_name, payload))

    def list_native_actuator_event_conflicts(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM native_actuator_event_conflict ORDER BY mcu_boot_id, event_sequence, message_name, payload")]

    @staticmethod
    def _native_actuator_record(message_name: str, payload: bytes) -> dict:
        import uart2_protocol as uart2

        if message_name not in uart2.REGISTRY["sessionPolicy"]["actuatorEventMessages"] or not isinstance(payload, bytes):
            raise ValueError("actuator evidence requires immutable registered bytes")
        values = uart2.decode_payload(message_name, payload)
        receipt = uart2.encode_payload("ACTUATOR_EVENT_SAVED", dict(mcuBootId=values["mcuBootId"],
            mcuEventSequence=values["mcuEventSequence"], eventMessageType=message_name,
            eventDigestSha256=uart2.compute_actuator_event_digest(message_name, payload)))
        return dict(mcu_boot_id=values["mcuBootId"], event_sequence=values["mcuEventSequence"],
            message_name=message_name, reported_command_uid=values["mcuCommandUid"],
            reported_work_uid=values.get("sessionUid", values.get("operationUid")),
            port_no=values["portNo"], payload=payload, saved_payload=receipt)

    @classmethod
    def _verify_native_actuator_row(cls, row) -> None:
        expected = cls._native_actuator_record(row["message_name"], bytes(row["payload"]))
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("native actuator evidence is corrupt")

    def save_native_actuator_event(self, message_name: str, payload: bytes) -> bytes:
        """Return custody receipt only AFTER standalone full evidence COMMIT.

        Reported command/work IDs are copied from MCU bytes, NOT proof of an
        accepted command or matched business ledger. This does not advance work,
        clear occupancy, queue cloud events, authorize motion or produce money.
        """
        record = self._native_actuator_record(message_name, payload)
        key = (record["mcu_boot_id"], record["event_sequence"])
        with self._standalone_native_transaction() as conn:
            conflict = self._native_custody_conflicted(conn, key)
            if (conn.execute("SELECT 1 FROM native_measurement_event WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_delivery_selection WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_clean_intent WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                    or conn.execute("SELECT 1 FROM native_clean_confirmation WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()):
                self._retain_native_custody_conflict(conn, key, message_name, payload)
                conflict = True
            old = conn.execute("SELECT * FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
            if old is not None:
                self._verify_native_actuator_row(old)
                if old["message_name"] != message_name or bytes(old["payload"]) != payload:
                    self._retain_native_custody_conflict(conn, key, message_name, payload)
                    conflict = True
            elif not conflict:
                conn.execute("""INSERT INTO native_actuator_event (mcu_boot_id, event_sequence, message_name,
                    reported_command_uid, reported_work_uid, port_no, payload, saved_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", tuple(record.values()))
        if conflict:
            raise ValueError("native actuator identity conflict; evidence retained, no receipt")
        return record["saved_payload"]

    def get_native_actuator_event(self, mcu_boot_id: int, event_sequence: int) -> Optional[dict]:
        with self._lock:
            if self._native_custody_conflicted(self._conn, (mcu_boot_id, event_sequence)):
                raise ValueError("native actuator identity conflict")
            row = self._conn.execute("SELECT * FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?",
                (mcu_boot_id, event_sequence)).fetchone()
            if row is None:
                return None
            self._verify_native_actuator_row(row)
            return dict(row)

    def list_native_work_actuator_events(self, work_uid: str) -> list[dict]:
        """Original output/interrupt facts, including wire/index disagreements.

        Work-scoped bodies have criticalEventIdentity (20 bytes), command UUID
        (16 bytes), then work UUID. SAFE_CLOSE_RESULT has no work UUID. Do not
        let a corrupt copied work index hide a result's original output.
        Caller may hold the result's DB snapshot.
        """
        with self._lock:
            rows = self._conn.execute("""SELECT mcu_boot_id,event_sequence FROM native_actuator_event
                WHERE reported_work_uid=? OR substr(payload,37,16)=?
                ORDER BY mcu_boot_id,event_sequence""", (work_uid, _uuid.UUID(work_uid).bytes)).fetchall()
            return [self.get_native_actuator_event(row["mcu_boot_id"], row["event_sequence"]) for row in rows]

    def retain_native_actuator_query_conflict(self, message_name: str, payload: bytes, query_payload: bytes) -> None:
        """Persist both contradictory claims, with no sendable confirmation."""
        import uart2_protocol as uart2

        record = self._native_actuator_record(message_name, payload)
        query = uart2.decode_payload("ACTUATOR_EVENT_QUERY_REPLY", query_payload)
        key = (record["mcu_boot_id"], record["event_sequence"])
        if query["status"] != "HELD" or key != (query["targetMcuBootId"], query["mcuEventSequence"]):
            raise ValueError("query and body do not claim the same actuator identity")
        if (message_name == query["eventMessageType"]
                and uart2.compute_actuator_event_digest(message_name, payload) == query["eventDigestSha256"]):
            raise ValueError("matching actuator claims are not a conflict")
        with self._standalone_native_transaction() as conn:
            self._retain_native_custody_conflict(conn, key, message_name, payload)
            self._retain_native_custody_conflict(conn, key, "ACTUATOR_EVENT_QUERY_REPLY", query_payload)

    def _migrate_v22(self) -> None:
        """Process custody associations, separate from work and cloud tasks."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_process_receipt (
            mcu_boot_id INTEGER NOT NULL,
            event_sequence INTEGER NOT NULL,
            scope BLOB NOT NULL UNIQUE CHECK (length(scope) = 89),
            saved_payload BLOB NOT NULL CHECK (length(saved_payload) = 45),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence),
            FOREIGN KEY (mcu_boot_id, event_sequence)
                REFERENCES native_measurement_event (mcu_boot_id, event_sequence)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_process_receipt_conflict (
            original_boot_id INTEGER NOT NULL,
            original_event_sequence INTEGER NOT NULL,
            incoming_boot_id INTEGER NOT NULL,
            incoming_event_sequence INTEGER NOT NULL,
            incoming_scope BLOB NOT NULL CHECK (length(incoming_scope) = 89),
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 242),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (original_boot_id, original_event_sequence, incoming_boot_id,
                         incoming_event_sequence, incoming_scope, payload),
            FOREIGN KEY (original_boot_id, original_event_sequence)
                REFERENCES native_process_receipt (mcu_boot_id, event_sequence)
        )""")

    def save_native_process_receipt(self, scope: bytes, message_name: str, payload: bytes) -> bytes:
        """COMMIT full bytes + original scope before returning a precise receipt.

        Caller restores scope from the accepted business ledger; this does not
        prove acceptance, create a cloud task, advance work or authorize motion.
        Raw unscoped archives alone never produce a sendable saved confirmation.
        """
        from mcu_process_handoff import process_event_receipt

        if message_name == "DELIVERY_SELECTION":
            return self._save_native_selection_receipt(scope, payload)
        if message_name in {"CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"}:
            return self._save_native_clean_intent_receipt(scope, message_name, payload)
        if message_name == "CLEAN_COMPLETION_CONFIRMED":
            return self._save_native_clean_confirmation_receipt(scope, payload)
        receipt = process_event_receipt(scope, message_name, payload)
        values = self._native_measurement_values(message_name, payload)
        key = (values["mcuBootId"], values["mcuEventSequence"])
        with self._standalone_native_transaction() as conn:
            conflict, _ = self._save_native_measurement_in_tx(conn, message_name, payload, values)
            matches = conn.execute("""SELECT r.scope, r.saved_payload, e.* FROM native_process_receipt r
                JOIN native_measurement_event e USING (mcu_boot_id, event_sequence)
                WHERE (r.mcu_boot_id=? AND r.event_sequence=?) OR r.scope=?
                OR (r.mcu_boot_id, r.event_sequence) IN (
                    SELECT original_boot_id, original_event_sequence FROM native_process_receipt_conflict
                    WHERE (incoming_boot_id=? AND incoming_event_sequence=?) OR incoming_scope=?)""",
                (*key, scope, *key, scope)).fetchall()
            for row in matches:
                self._verify_native_process_receipt_row(row)
                original = (row["mcu_boot_id"], row["event_sequence"])
                if original != key or bytes(row["scope"]) != scope:
                    conn.execute("""INSERT OR IGNORE INTO native_process_receipt_conflict
                        (original_boot_id, original_event_sequence, incoming_boot_id,
                         incoming_event_sequence, incoming_scope, message_name, payload) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (*original, *key, scope, message_name, payload))
                    conflict = True
                if conn.execute("""SELECT 1 FROM native_process_receipt_conflict
                    WHERE original_boot_id=? AND original_event_sequence=? LIMIT 1""", original).fetchone():
                    conflict = True
            if not conflict and not matches:
                conn.execute("""INSERT INTO native_process_receipt
                    (mcu_boot_id, event_sequence, scope, saved_payload) VALUES (?, ?, ?, ?)""",
                    (*key, scope, receipt))
        if conflict:
            raise ValueError("native process identity conflict; evidence retained, no receipt")
        return receipt

    @classmethod
    def _verify_native_process_receipt_row(cls, row) -> None:
        from mcu_process_handoff import process_event_receipt

        cls._verify_native_measurement_row(row)
        expected = process_event_receipt(bytes(row["scope"]), row["message_name"], bytes(row["payload"]))
        if expected != bytes(row["saved_payload"]):
            raise ValueError("native process receipt is corrupt")

    def list_native_process_receipt_conflicts(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("""SELECT * FROM native_process_receipt_conflict
                ORDER BY original_boot_id, original_event_sequence, incoming_boot_id, incoming_event_sequence""").fetchall()]

    def get_native_process_receipt(self, scope: bytes) -> Optional[dict]:
        from mcu_process_handoff import decode_process_scope

        original = decode_process_scope(scope)
        with self._lock:
            if original["eventMessageType"] == "CLEAN_COMPLETION_CONFIRMED":
                row = self._conn.execute("SELECT mcu_boot_id,event_sequence FROM native_clean_confirmation WHERE scope=?", (scope,)).fetchone()
                return self.get_native_process_event(original["eventMessageType"], row["mcu_boot_id"], row["event_sequence"]) if row else None
            if original["eventMessageType"] in {"CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"}:
                row = self._conn.execute("SELECT mcu_boot_id,event_sequence FROM native_clean_intent WHERE scope=?", (scope,)).fetchone()
                return self.get_native_process_event(original["eventMessageType"], row["mcu_boot_id"], row["event_sequence"]) if row else None
            if original["eventMessageType"] == "DELIVERY_SELECTION":
                row = self._conn.execute("SELECT mcu_boot_id, event_sequence FROM native_delivery_selection WHERE scope=?", (scope,)).fetchone()
                return self.get_native_process_event("DELIVERY_SELECTION", row["mcu_boot_id"], row["event_sequence"]) if row else None
            row = self._conn.execute("""SELECT r.scope, r.saved_payload, e.* FROM native_process_receipt r
                JOIN native_measurement_event e USING (mcu_boot_id, event_sequence) WHERE r.scope=?""", (scope,)).fetchone()
            if row is None:
                return None
            if self._native_custody_conflicted(self._conn, (row["mcu_boot_id"], row["event_sequence"])):
                raise ValueError("native process identity conflict")
            self._verify_native_process_receipt_row(row)
            if self._conn.execute("""SELECT 1 FROM native_measurement_event_conflict
                WHERE original_boot_id=? AND original_event_sequence=? LIMIT 1""",
                (row["mcu_boot_id"], row["event_sequence"])).fetchone():
                raise ValueError("native process evidence has an unresolved identity conflict")
            if self._conn.execute("""SELECT 1 FROM native_process_receipt_conflict
                WHERE original_boot_id=? AND original_event_sequence=? LIMIT 1""",
                (row["mcu_boot_id"], row["event_sequence"])).fetchone():
                raise ValueError("native process receipt has an unresolved context conflict")
            return dict(row)

    @staticmethod
    def _native_baseline_scope(record: dict) -> dict:
        """Rebuild the sole query scope from the immutable MEASURE_BASELINE."""
        import uart2_protocol as uart2

        if (
            record is None
            or record.get("message_name") != "MEASURE_BASELINE"
            or record.get("conflict")
        ):
            raise ValueError("native baseline command is missing or conflicted")
        values = uart2.decode_payload("MEASURE_BASELINE", record["payload"])
        return {
            "mcuCommandUid": values["mcuCommandUid"],
            "commandDigestSha256": values["commandDigestSha256"],
            "targetMcuBootId": values["targetMcuBootId"],
            "commandSequence": values["commandSequence"],
            "workUid": values["measurementUid"],
            "workType": "BASELINE_MEASUREMENT",
            "portNo": values["portNo"],
            "eventMessageType": "BASELINE_MEASUREMENT_RESULT",
            "stepSequence": 0,
            "configVersion": values["configVersion"],
        }

    @staticmethod
    def _native_baseline_measurement_fact(values: dict) -> dict:
        """Project the richer UART result without turning a failed zero into weight."""
        available = values["measurementKind"] in {
            "STABLE_MEAN",
            "TIMEOUT_MEDIAN",
        }
        if values["measurementKind"] == "STABLE_MEAN":
            status, value_kind, health = (
                "STABLE",
                "STABLE_WINDOW_MEAN",
                "OK",
            )
        elif values["measurementKind"] == "TIMEOUT_MEDIAN":
            status, value_kind, health = "UNSTABLE", "TIMEOUT_MEDIAN", "OK"
        else:
            value_kind = "NONE"
            by_kind = {
                "OVERLOAD": ("OVERLOAD", "OVERLOAD"),
                "PROTOCOL_ERROR": ("PROTOCOL_ERROR", "PROTOCOL_ERROR"),
                "CONFIG_ERROR": ("CONFIG_ERROR", "CONFIG_ERROR"),
                "DISCONNECTED": ("DISCONNECTED", "DISCONNECTED"),
                "SENSOR_FAULT": ("SENSOR_FAULT", "SENSOR_FAULT"),
            }
            if values["measurementKind"] == "UNAVAILABLE" and values["faultCode"] == "WEIGHT_TIMEOUT":
                status, health = "TIMEOUT", "TIMEOUT"
            else:
                status, health = by_kind.get(
                    values["measurementKind"],
                    ("SENSOR_FAULT", "UNKNOWN"),
                )
        fault = values["faultCode"]
        # MEASUREMENT_INTERRUPTED has no OneNet fault symbol. The exact UART
        # bytes remain authoritative locally; do not invent another sensor
        # diagnosis merely to populate an optional cloud field.
        if fault in {"NONE", "MEASUREMENT_INTERRUPTED"}:
            fault = None
        return {
            "measurementUid": values["weightMeasurementUid"],
            "status": status,
            "weightValueAvailable": available,
            "reportedWeightGrams": (
                values["reportedWeightGrams"] if available else None
            ),
            "weightValueKind": value_kind,
            "measurementElapsedMs": values["measurementElapsedMs"],
            "sampleCount": values["sampleCount"],
            "calibrationVersion": values["calibrationVersion"],
            "sensorHealth": health,
            "faultCode": fault,
            "mcuBootId": values["mcuBootId"],
            "mcuEventSequence": values["mcuEventSequence"],
        }

    @staticmethod
    def _native_baseline_permit_dict(permit) -> dict:
        return {
            "permit_uid": permit.permit_uid,
            "work_uid": permit.work_uid,
            "command_uid": permit.command_uid,
            "work_type": permit.work_type,
            "request_digest_sha256": permit.request_digest_sha256,
        }

    def prepare_native_baseline_completion(
        self,
        permit,
        native_command_uid: str,
        *,
        device_name: str,
        event_uid: str,
    ) -> dict:
        """Freeze one exact native baseline result before permanent completion.

        The scoped MCU receipt, reliable event, cloud-command terminal state,
        optional non-negative local tare and COMPLETING slot are one commit.
        A restart can therefore only repeat the same permanent completion; it
        cannot manufacture another result event or dispatch the measurement.
        """
        from job_safety import JobPermit, command_request_digest
        import uart2_protocol as uart2

        marker_name = "nativeBaselineCompletion"
        if not isinstance(permit, JobPermit) or permit.work_type != WORK_TYPE_BASELINE:
            raise ValueError("native baseline completion requires its original permit")
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("native baseline completion requires the device identity")
        with self._standalone_native_transaction() as conn:
            record = self.get_native_command(native_command_uid)
            scope_values = self._native_baseline_scope(record)
            raw_scope = uart2.encode_payload(
                "QUERY_PROCESS_EVENT",
                scope_values | {"queryId": 1},
            )[8:]
            receipt = self.get_native_process_receipt(raw_scope)
            if receipt is None:
                return {"state": "WAITING_FOR_RESULT"}
            values = uart2.decode_payload(
                "BASELINE_MEASUREMENT_RESULT",
                bytes(receipt["payload"]),
            )
            native = uart2.decode_payload("MEASURE_BASELINE", record["payload"])
            command = self.get_command(permit.command_uid)
            if command is None:
                raise ValueError("native baseline lost its original cloud command")
            cloud = command["payload"]
            payload = cloud.get("payload") if isinstance(cloud, dict) else None
            if (
                cloud.get("commandUid") != permit.command_uid
                or cloud.get("commandType") != "MEASURE_EMPTY_BAG_BASELINE"
                or cloud.get("targetDeviceName") != device_name
                or not isinstance(payload, dict)
                or payload.get("measurementUid") != permit.work_uid
                or payload.get("measurementUid") != native["measurementUid"]
                or payload.get("portNo") != native["portNo"]
                or payload.get("emptyBagConfirmed") is not True
                or payload.get("measurementTimeoutMs") != 5000
                or payload.get("config", {}).get("version") != native["configVersion"]
                or payload.get("config", {}).get("contentSha256")
                != native["configContentSha256"]
                or command_request_digest(cloud) != permit.request_digest_sha256
                or values["mcuCommandUid"] != native_command_uid
                or values["measurementUid"] != permit.work_uid
                or values["portNo"] != native["portNo"]
                or values["configVersion"] != native["configVersion"]
                or values["mcuBootId"] != native["targetMcuBootId"]
                or not record["write_claimed"]
                or record["decision_outcome"] == "REJECTED"
            ):
                raise ValueError("native baseline result differs from original authority")
            slot_row = conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if (
                slot_row is None
                or (slot_row["work_type"], slot_row["work_uid"], slot_row["port_no"])
                != (WORK_TYPE_BASELINE, permit.work_uid, native["portNo"])
            ):
                raise ValueError("native baseline lost its original work slot")
            context = (
                _json.loads(slot_row["context_json"])
                if slot_row["context_json"]
                else {}
            )
            if (
                context.get("native_protocol") != 2
                or context.get("start_command_uid") != permit.command_uid
                or context.get("start_mcu_command_uid") != native_command_uid
                or context.get("job_safety")
                != (
                    self._native_baseline_permit_dict(permit)
                    | {"begin_uid": permit.work_uid}
                )
            ):
                raise ValueError("native baseline slot differs from its original permit")

            measurement = self._native_baseline_measurement_fact(values)
            event_payload = {
                "measurementUid": permit.work_uid,
                "portNo": payload["portNo"],
                "bagUid": payload["bagUid"],
                "emptyBagConfirmed": True,
                "totalWeightMeasurement": measurement,
                "frozenConfig": dict(payload["config"]),
            }
            baseline = None
            weight = measurement["reportedWeightGrams"]
            if measurement["weightValueAvailable"] and weight >= 0:
                baseline = {
                    "bag_uid": payload["bagUid"],
                    "weight_grams": weight,
                    "source_kind": "NATIVE_BASELINE",
                    "source_work_type": WORK_TYPE_BASELINE,
                    "source_work_uid": permit.work_uid,
                    "source_mcu_boot_id": values["mcuBootId"],
                    "source_mcu_event_sequence": values["mcuEventSequence"],
                    "source_observed_at": None,
                    "measurement_uid": values["weightMeasurementUid"],
                }
                saved_baseline = conn.execute(
                    "SELECT * FROM bag_baseline WHERE bag_uid=?",
                    (payload["bagUid"],),
                ).fetchone()
                stable_fields = tuple(baseline)
                baseline["updated_at"] = (
                    saved_baseline["updated_at"]
                    if saved_baseline is not None
                    and all(
                        saved_baseline[field] == baseline[field]
                        for field in stable_fields
                    )
                    else self._now()
                )
            evidence = {
                "profile": "ecobin-native-baseline-completion-v1",
                "deviceName": device_name,
                "permit": self._native_baseline_permit_dict(permit),
                "nativeCommandUid": native_command_uid,
                "sourceMcuBootId": values["mcuBootId"],
                "sourceMcuEventSequence": values["mcuEventSequence"],
                "sourceEventDigestSha256": uart2.compute_process_event_digest(
                    "BASELINE_MEASUREMENT_RESULT",
                    bytes(receipt["payload"]),
                ),
                "eventUid": event_uid,
                "eventPayloadSha256": canonical_payload_sha256(event_payload),
                "baseline": baseline,
            }
            digest = canonical_payload_sha256(evidence)
            result = {} if command["result"] is None else dict(command["result"])
            old = result.get(marker_name)
            if old is not None:
                if (
                    not isinstance(old, dict)
                    or old.get("state") not in {"PREPARED", "APPLIED"}
                    or old.get("evidence") != evidence
                    or old.get("evidenceSha256") != digest
                    or command["state"] != "COMPLETED"
                    or slot_row["work_state"] != "COMPLETING"
                    or context.get(marker_name)
                    != {
                        "eventUid": event_uid,
                        "evidenceSha256": digest,
                    }
                ):
                    raise ValueError("native baseline completion receipt conflicts")
                event_row = self.get_event(event_uid)
                event = (
                    _json.loads(event_row["payload_json"])
                    if event_row is not None
                    else None
                )
                if (
                    event is None
                    or event.get("eventType") != "BASELINE_MEASUREMENT_COMPLETE"
                    or event.get("commandUid") != permit.command_uid
                    or event.get("target")
                    != {"type": "BASELINE_MEASUREMENT", "uid": permit.work_uid}
                    or event.get("payload") != event_payload
                    or event.get("payloadSha256") != evidence["eventPayloadSha256"]
                ):
                    raise ValueError("native baseline reliable event conflicts")
                return {
                    "state": "PREPARED",
                    "eventUid": event_uid,
                    "evidenceSha256": digest,
                }
            if command["state"] not in {"PROCESSING", "WAITING_MCU_RESULT"}:
                raise ValueError("native baseline cannot replace a terminal command")
            sequence = self._next_seq(conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="BASELINE_MEASUREMENT_COMPLETE",
                target_type="BASELINE_MEASUREMENT",
                target_uid=permit.work_uid,
                command_uid=permit.command_uid,
                payload=event_payload,
            )
            self._insert_event(conn, event, "BASELINE_MEASUREMENT_COMPLETE")
            if baseline is not None:
                self._upsert_bag_baseline_in_tx(conn, baseline)
            marker = {
                "state": "PREPARED",
                "evidence": evidence,
                "evidenceSha256": digest,
            }
            result.update(
                measurementStatus=measurement["status"],
                weightValuePresent=measurement["weightValueAvailable"],
                reportedWeightGrams=measurement["reportedWeightGrams"],
            )
            result[marker_name] = marker
            context = dict(context)
            context["phase"] = "NATIVE_BASELINE_COMPLETING"
            context[marker_name] = {
                "eventUid": event_uid,
                "evidenceSha256": digest,
            }
            updated = conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, mcu_command_uid=?,
                       result_json=?, last_error=NULL
                   WHERE command_uid=? AND state IN ('PROCESSING','WAITING_MCU_RESULT')""",
                (
                    self._now(),
                    native_command_uid,
                    _json.dumps(result, ensure_ascii=False, sort_keys=True),
                    permit.command_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("native baseline cloud command changed")
            updated = conn.execute(
                """UPDATE work_slot
                   SET work_state='COMPLETING', context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=? AND port_no=?""",
                (
                    _json.dumps(context, ensure_ascii=False, sort_keys=True),
                    self._now(),
                    WORK_TYPE_BASELINE,
                    permit.work_uid,
                    native["portNo"],
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("native baseline work slot changed")
            return {
                "state": "PREPARED",
                "eventUid": event_uid,
                "evidenceSha256": digest,
            }

    def apply_native_baseline_completion(
        self,
        permit,
        native_command_uid: str,
        *,
        device_name: str,
        permit_snapshot: dict,
    ) -> dict:
        """Release only after reading the exact permanent completion receipt."""
        marker_name = "nativeBaselineCompletion"
        expected_permit = {
            "permitUid": permit.permit_uid,
            "commandUid": permit.command_uid,
            "workUid": permit.work_uid,
            "workType": permit.work_type,
            "requestDigestSha256": permit.request_digest_sha256,
        }
        if not isinstance(permit_snapshot, dict) or any(
            permit_snapshot.get(key) != value
            for key, value in expected_permit.items()
        ):
            raise ValueError("native baseline permanent permit identity conflicts")
        with self._standalone_native_transaction() as conn:
            command = self.get_command(permit.command_uid)
            result = command.get("result") if command is not None else None
            marker = result.get(marker_name) if isinstance(result, dict) else None
            if (
                command is None
                or command["state"] != "COMPLETED"
                or not isinstance(marker, dict)
                or marker.get("state") not in {"PREPARED", "APPLIED"}
                or marker.get("evidence", {}).get("nativeCommandUid")
                != native_command_uid
            ):
                raise ValueError("native baseline completion was not prepared")
            expected_completion = {
                "state": "COMPLETED",
                "completionUid": permit.command_uid,
                "completionOutcome": "SUCCEEDED",
                "completionDigestSha256": marker["evidenceSha256"],
            }
            if any(
                permit_snapshot.get(key) != value
                for key, value in expected_completion.items()
            ):
                raise ValueError("native baseline lacks permanent completion receipt")
            if marker["state"] == "APPLIED":
                return {
                    "state": "COMPLETED",
                    "eventUid": marker["evidence"]["eventUid"],
                }
            slot = conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            context = (
                _json.loads(slot["context_json"])
                if slot is not None and slot["context_json"]
                else {}
            )
            release = context.get("nativeBaselineMcuRelease")
            if (
                slot is None
                or (slot["work_type"], slot["work_uid"], slot["work_state"])
                != (WORK_TYPE_BASELINE, permit.work_uid, "COMPLETING")
                or context.get(marker_name)
                != {
                    "eventUid": marker["evidence"]["eventUid"],
                    "evidenceSha256": marker["evidenceSha256"],
                }
                or not isinstance(release, dict)
                or release.get("profile")
                != "native-baseline-process-release-v1"
                or release.get("basis")
                not in {
                    "FRESH_PROCESS_EVENT_QUERY_RELEASED",
                    "RECOGNIZED_MCU_RESTART",
                }
                or release.get("nativeCommandUid") != native_command_uid
                or release.get("sourceMcuBootId")
                != marker["evidence"]["sourceMcuBootId"]
                or release.get("sourceMcuEventSequence")
                != marker["evidence"]["sourceMcuEventSequence"]
                or release.get("sourceEventDigestSha256")
                != marker["evidence"]["sourceEventDigestSha256"]
            ):
                raise ValueError(
                    "native baseline completion lacks exact MCU release proof"
                )
            marker["state"] = "APPLIED"
            result[marker_name] = marker
            updated = conn.execute(
                "UPDATE command_inbox SET result_json=? WHERE command_uid=? AND state='COMPLETED'",
                (
                    _json.dumps(result, ensure_ascii=False, sort_keys=True),
                    permit.command_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("native baseline completion command changed")
            retired = conn.execute(
                """UPDATE native_mcu_command SET dispatch_retired=1
                   WHERE command_uid=? AND dispatch_retired=0""",
                (native_command_uid,),
            )
            if retired.rowcount != 1:
                raise ValueError(
                    "native baseline completion dispatch fence changed"
                )
            released = conn.execute(
                """UPDATE work_slot
                   SET work_type='NONE', work_uid=NULL, work_state=NULL,
                       port_no=NULL, context_json=NULL, updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?
                     AND work_state='COMPLETING'""",
                (self._now(), WORK_TYPE_BASELINE, permit.work_uid),
            )
            if released.rowcount != 1:
                raise ValueError("native baseline completion slot was not released")
            return {
                "state": "COMPLETED",
                "eventUid": marker["evidence"]["eventUid"],
            }

    def confirm_native_baseline_mcu_release(
        self,
        permit,
        native_command_uid: str,
        *,
        release_observation: Optional[dict] = None,
        replacement_mcu_boot_id: Optional[int] = None,
    ) -> dict:
        """Persist exact proof that the old MCU process slot cannot stay held."""
        from job_safety import JobPermit
        import uart2_protocol as uart2

        key = "nativeBaselineMcuRelease"
        if not isinstance(permit, JobPermit) or permit.work_type != WORK_TYPE_BASELINE:
            raise ValueError("native baseline release requires its original permit")
        if (release_observation is None) == (replacement_mcu_boot_id is None):
            raise ValueError("native baseline release requires exactly one proof")
        with self._standalone_native_transaction() as conn:
            record = self.get_native_command(native_command_uid)
            native = uart2.decode_payload("MEASURE_BASELINE", record["payload"])
            scope = self._native_baseline_scope(record)
            raw_scope = uart2.encode_payload(
                "QUERY_PROCESS_EVENT",
                scope | {"queryId": 1},
            )[8:]
            receipt = self.get_native_process_receipt(raw_scope)
            if receipt is None:
                raise ValueError("native baseline release lacks local result custody")
            measured = uart2.decode_payload(
                "BASELINE_MEASUREMENT_RESULT",
                bytes(receipt["payload"]),
            )
            source_digest = uart2.compute_process_event_digest(
                "BASELINE_MEASUREMENT_RESULT",
                bytes(receipt["payload"]),
            )
            slot = conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            context = (
                _json.loads(slot["context_json"])
                if slot is not None and slot["context_json"]
                else {}
            )
            command = self.get_command(permit.command_uid)
            marker = (
                command.get("result", {}).get("nativeBaselineCompletion")
                if command is not None and isinstance(command.get("result"), dict)
                else None
            )
            if (
                slot is None
                or (slot["work_type"], slot["work_uid"], slot["work_state"])
                != (WORK_TYPE_BASELINE, permit.work_uid, "COMPLETING")
                or not isinstance(marker, dict)
                or marker.get("state") != "PREPARED"
                or context.get("nativeBaselineCompletion")
                != {
                    "eventUid": marker["evidence"]["eventUid"],
                    "evidenceSha256": marker["evidenceSha256"],
                }
                or native["measurementUid"] != permit.work_uid
                or measured["mcuCommandUid"] != native_command_uid
            ):
                raise ValueError("native baseline release lost its prepared completion")

            if release_observation is not None:
                try:
                    encoded = uart2.encode_payload(
                        "PROCESS_EVENT_QUERY_REPLY",
                        release_observation,
                    )
                    observed = uart2.decode_payload(
                        "PROCESS_EVENT_QUERY_REPLY",
                        encoded,
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError("native baseline release observation is invalid") from error
                if (
                    any(observed.get(field) != value for field, value in scope.items())
                    or observed["status"] != "RELEASED"
                    or observed["currentMcuBootId"] != native["targetMcuBootId"]
                    or observed["mcuEventSequence"] != measured["mcuEventSequence"]
                    or observed["eventDigestSha256"] != source_digest
                ):
                    raise ValueError("native baseline release observation differs from custody")
                proof = {
                    "profile": "native-baseline-process-release-v1",
                    "basis": "FRESH_PROCESS_EVENT_QUERY_RELEASED",
                    "nativeCommandUid": native_command_uid,
                    "sourceMcuBootId": measured["mcuBootId"],
                    "sourceMcuEventSequence": measured["mcuEventSequence"],
                    "sourceEventDigestSha256": source_digest,
                    "queryId": observed["queryId"],
                    "queryPayloadSha256": canonical_payload_sha256(observed),
                }
            else:
                if (
                    type(replacement_mcu_boot_id) is not int
                    or replacement_mcu_boot_id == native["targetMcuBootId"]
                    or replacement_mcu_boot_id
                    != self._native_counter(conn, "native_current_boot")
                    or conn.execute(
                        "SELECT 1 FROM native_mcu_boot WHERE boot_id=?",
                        (replacement_mcu_boot_id,),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("native baseline replacement boot is not recognized")
                proof = {
                    "profile": "native-baseline-process-release-v1",
                    "basis": "RECOGNIZED_MCU_RESTART",
                    "nativeCommandUid": native_command_uid,
                    "sourceMcuBootId": measured["mcuBootId"],
                    "sourceMcuEventSequence": measured["mcuEventSequence"],
                    "sourceEventDigestSha256": source_digest,
                    "replacementMcuBootId": replacement_mcu_boot_id,
                }
            existing = context.get(key)
            if existing is not None:
                if (
                    not isinstance(existing, dict)
                    or existing.get("profile")
                    != "native-baseline-process-release-v1"
                    or existing.get("nativeCommandUid") != native_command_uid
                    or existing.get("sourceMcuBootId") != measured["mcuBootId"]
                    or existing.get("sourceMcuEventSequence")
                    != measured["mcuEventSequence"]
                    or existing.get("sourceEventDigestSha256") != source_digest
                ):
                    raise ValueError("native baseline release proof conflicts")
                return existing
            context[key] = proof
            updated = conn.execute(
                """UPDATE work_slot SET context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?
                     AND work_state='COMPLETING'""",
                (
                    _json.dumps(context, ensure_ascii=False, sort_keys=True),
                    self._now(),
                    WORK_TYPE_BASELINE,
                    permit.work_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("native baseline release slot changed")
            return proof

    def _migrate_v21(self) -> None:
        """Native terminal process evidence, not a business/OneNet outbox."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_measurement_event (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            event_sequence INTEGER NOT NULL CHECK (event_sequence BETWEEN 1 AND 4294967295),
            message_name TEXT NOT NULL,
            measurement_uid TEXT NOT NULL UNIQUE,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 242),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, event_sequence)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_measurement_event_conflict (
            original_boot_id INTEGER NOT NULL,
            original_event_sequence INTEGER NOT NULL,
            incoming_boot_id INTEGER NOT NULL,
            incoming_event_sequence INTEGER NOT NULL,
            incoming_measurement_uid TEXT NOT NULL,
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 1 AND 242),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (original_boot_id, original_event_sequence, message_name, payload),
            FOREIGN KEY (original_boot_id, original_event_sequence)
                REFERENCES native_measurement_event (mcu_boot_id, event_sequence)
        )""")

    @staticmethod
    def _native_measurement_values(message_name: str, payload: bytes) -> dict:
        import uart2_protocol as uart2

        if (message_name not in uart2.REGISTRY["sessionPolicy"]["processMeasurementMessages"]
                or not isinstance(payload, bytes)):
            raise ValueError("native process evidence requires a registered measurement and immutable bytes")
        return uart2.decode_payload(message_name, payload)

    @classmethod
    def _verify_native_measurement_row(cls, row) -> None:
        values = cls._native_measurement_values(row["message_name"], bytes(row["payload"]))
        uid = values.get("weightMeasurementUid", values["measurementUid"])
        if ((values["mcuBootId"], values["mcuEventSequence"], uid)
                != (row["mcu_boot_id"], row["event_sequence"], row["measurement_uid"])
                or hashlib.sha256(row["payload"]).hexdigest() != row["payload_sha256"]):
            raise ValueError("native process evidence is corrupt")

    def save_native_measurement_event(self, message_name: str, payload: bytes) -> str:
        """Full validation -> standalone COMMIT; no transport receipt is returned.

        This archives process evidence only. It does NOT prove current boot,
        work/round/clean action or authorization, and does not ACK, advance work,
        clear occupancy, queue OneNet events or produce financial value. A later
        business owner must reconcile that context before using/acknowledging it.
        Both event-identity and actual-measurement-UID collisions are sticky;
        retain originals and conflicting incoming bytes, never last-write-wins.
        """
        values = self._native_measurement_values(message_name, payload)
        with self._standalone_native_transaction() as conn:
            conflict, disposition = self._save_native_measurement_in_tx(conn, message_name, payload, values)
        if conflict:
            raise ValueError("native process identity conflict; evidence retained, no receipt")
        return disposition

    def _save_native_measurement_in_tx(self, conn, message_name, payload, values):
        key = (values["mcuBootId"], values["mcuEventSequence"])
        if (self._native_custody_conflicted(conn, key)
                or conn.execute("SELECT 1 FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                or conn.execute("SELECT 1 FROM native_delivery_selection WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                or conn.execute("SELECT 1 FROM native_clean_intent WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()
                or conn.execute("SELECT 1 FROM native_clean_confirmation WHERE mcu_boot_id=? AND event_sequence=?", key).fetchone()):
            self._retain_native_custody_conflict(conn, key, message_name, payload)
            return True, "CONFLICT"
        uid = values.get("weightMeasurementUid", values["measurementUid"])
        conflict, disposition = False, "STORED"
        matches = conn.execute("""SELECT * FROM native_measurement_event
                WHERE (mcu_boot_id=? AND event_sequence=?) OR measurement_uid=?
                OR (mcu_boot_id, event_sequence) IN (
                    SELECT original_boot_id, original_event_sequence FROM native_measurement_event_conflict
                    WHERE (incoming_boot_id=? AND incoming_event_sequence=?) OR incoming_measurement_uid=?)""",
                (*key, uid, *key, uid)).fetchall()
        for existing in matches:
            self._verify_native_measurement_row(existing)
            original = (existing["mcu_boot_id"], existing["event_sequence"])
            if existing["message_name"] != message_name or bytes(existing["payload"]) != payload:
                conn.execute("""INSERT OR IGNORE INTO native_measurement_event_conflict
                        (original_boot_id, original_event_sequence, incoming_boot_id,
                         incoming_event_sequence, incoming_measurement_uid, message_name, payload) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (*original, *key, uid, message_name, payload))
                conflict = True
            if conn.execute("""SELECT 1 FROM native_measurement_event_conflict
                    WHERE original_boot_id=? AND original_event_sequence=? LIMIT 1""", original).fetchone():
                conflict = True
        if matches:
            disposition = "DUPLICATE"
        else:
            conn.execute("""INSERT INTO native_measurement_event
                    (mcu_boot_id, event_sequence, message_name, measurement_uid, payload, payload_sha256)
                    VALUES (?, ?, ?, ?, ?, ?)""", (*key, message_name, uid, payload, hashlib.sha256(payload).hexdigest()))
        return conflict, disposition

    def get_native_measurement_event(self, mcu_boot_id: int, event_sequence: int) -> Optional[dict]:
        """Revalidate stored bytes; retrieval alone is not authorization or freshness."""
        with self._lock:
            if self._native_custody_conflicted(self._conn, (mcu_boot_id, event_sequence)):
                raise ValueError("native process identity conflict")
            row = self._conn.execute("""SELECT * FROM native_measurement_event
                WHERE mcu_boot_id=? AND event_sequence=?""", (mcu_boot_id, event_sequence)).fetchone()
            if row is None:
                return None
            self._verify_native_measurement_row(row)
            if self._conn.execute("""SELECT 1 FROM native_measurement_event_conflict
                WHERE original_boot_id=? AND original_event_sequence=? LIMIT 1""",
                (mcu_boot_id, event_sequence)).fetchone():
                raise ValueError("native process evidence has an unresolved identity conflict")
            return dict(row)

    def list_native_measurement_event_conflicts(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("""SELECT * FROM native_measurement_event_conflict
                ORDER BY original_boot_id, original_event_sequence, incoming_boot_id, incoming_event_sequence""").fetchall()]

    def _migrate_v20(self) -> None:
        """Native boot/dispatch identities; no change to legacy work admission."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_boot (
            boot_id INTEGER PRIMARY KEY CHECK (boot_id BETWEEN 1 AND 9007199254740991),
            probe_id INTEGER NOT NULL UNIQUE CHECK (probe_id BETWEEN 1 AND 9007199254740991),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_command (
            command_uid TEXT PRIMARY KEY,
            mcu_boot_id INTEGER NOT NULL REFERENCES native_mcu_boot(boot_id),
            command_sequence INTEGER NOT NULL CHECK (command_sequence BETWEEN 1 AND 4294967295),
            message_name TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) BETWEEN 60 AND 242),
            write_claimed INTEGER NOT NULL DEFAULT 0 CHECK (write_claimed IN (0,1)),
            decision_outcome TEXT CHECK (decision_outcome IN ('ACCEPTED','REJECTED')),
            decision_error TEXT,
            conflict INTEGER NOT NULL DEFAULT 0 CHECK (conflict IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(mcu_boot_id, command_sequence)
        )""")
        # This older migration is also rechecked on current-schema startup.
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(native_mcu_command)")}
        predicate = "decision_outcome IS NULL" + (" AND boot_retired=0" if "boot_retired" in columns else "")
        predicate += " AND dispatch_retired=0" if "dispatch_retired" in columns else ""
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS native_mcu_one_pending_command "
                           f"ON native_mcu_command ((1)) WHERE {predicate}")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_command_observation (
            command_uid TEXT NOT NULL REFERENCES native_mcu_command(command_uid),
            outcome TEXT NOT NULL, current_boot_id INTEGER NOT NULL,
            error_code TEXT NOT NULL, highest_sequence INTEGER NOT NULL,
            message_name TEXT NOT NULL, payload BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(command_uid, outcome, current_boot_id, error_code, highest_sequence)
        )""")

    @staticmethod
    def _native_counter(conn, key: str, maximum: int = 9007199254740991) -> int:
        row = conn.execute("SELECT state_value FROM device_state WHERE state_key=?", (key,)).fetchone()
        raw = row[0] if row is not None else "0"
        if (not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal()
                or len(raw) > 16 or (len(raw) > 1 and raw[0] == "0") or int(raw) > maximum):
            raise ValueError("native counter is corrupt")
        return int(raw)

    def _set_native_counter(self, conn, key: str, value: int) -> None:
        conn.execute("""INSERT INTO device_state (state_key, state_value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value, updated_at=excluded.updated_at""",
            (key, str(value), self._now()))

    def reserve_native_boot_id(self, probe_id: int) -> int:
        """Consume a fresh boot identity for ONE pending probe, before one bind write.

        Only a coordinator with a fresh zero-boot reply may call this. Never
        reuse an offer after a restart or a missing bind reply. Gaps are legal.
        """
        with self._standalone_native_transaction() as conn:
            if (type(probe_id) is not int or not 1 <= probe_id <= self._native_counter(conn, "native_query_sequence")
                    or conn.execute("SELECT 1 FROM native_mcu_boot WHERE probe_id=?", (probe_id,)).fetchone()):
                raise ValueError("native boot probe is not fresh")
            previous = self._native_counter(conn, "native_boot_sequence")
            maximum = conn.execute("SELECT COALESCE(MAX(boot_id),0) FROM native_mcu_boot").fetchone()[0]
            if previous < maximum:
                raise ValueError("native boot counter regressed")
            if previous == 9007199254740991:
                raise ValueError("native boot counter is exhausted")
            boot_id = previous + 1
            self._set_native_counter(conn, "native_boot_sequence", boot_id)
            conn.execute("INSERT INTO native_mcu_boot (boot_id, probe_id) VALUES (?, ?)", (boot_id, probe_id))
        return boot_id

    def get_native_boot(self, boot_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_mcu_boot WHERE boot_id=?", (boot_id,)).fetchone()
            return dict(row) if row else None

    def recognize_native_boot_id(self, boot_id: int) -> bool:
        """Legacy candidate identity selection; does not create a restart witness.

        The live boot session uses save_native_boot_observation instead. Neither
        method grants admission, mechanical permission or physical completion.
        """
        if type(boot_id) is not int or not 1 <= boot_id <= 9007199254740991:
            return False
        with self._standalone_native_transaction() as conn:
            return self._recognize_native_boot_in_tx(conn, boot_id)

    def _recognize_native_boot_in_tx(self, conn, boot_id):
        current = self._native_counter(conn, "native_current_boot")
        reserved = self._native_counter(conn, "native_boot_sequence")
        if current > reserved:
            raise ValueError("native boot counters are inconsistent")
        if not 1 <= boot_id <= reserved or boot_id < current or not conn.execute(
                "SELECT 1 FROM native_mcu_boot WHERE boot_id=?", (boot_id,)).fetchone():
            return False
        self._set_native_counter(conn, "native_current_boot", boot_id)
        return True

    @staticmethod
    def _native_boot_reply_values(name, payload):
        import uart2_protocol as uart2
        if name not in {"BOOT_PROBE_REPLY", "BIND_BOOT_REPLY"} or not isinstance(payload, bytes):
            raise ValueError("native boot observation requires an immutable reply")
        return uart2.decode_payload(name, payload)

    @classmethod
    def _native_boot_observation_values(cls, conn, name, payload):
        values = cls._native_boot_reply_values(name, payload)
        if (values["mcuBootId"] == 0 or values["probeId"] > EdgeStore._native_counter(conn, "native_query_sequence")
                or not conn.execute("SELECT 1 FROM native_mcu_boot WHERE boot_id=?", (values["mcuBootId"],)).fetchone()):
            raise ValueError("native boot observation is not an owned positive reply")
        if name == "BIND_BOOT_REPLY" and not conn.execute(
                "SELECT 1 FROM native_mcu_boot WHERE boot_id=? AND probe_id=?",
                (values["proposedMcuBootId"], values["probeId"])).fetchone():
            raise ValueError("native boot observation does not match the original offer")
        return values

    def save_native_boot_observation(self, message_name: str, payload: bytes) -> bool:
        """Called only by the fresh correlated boot session; commit before ready.

        Keep the first positive raw reply, not an unbounded row for each probe.
        The caller owns request freshness; stored history never makes a new Pi
        instance ready. A bind refusal records the actual boot, not the offer.
        """
        with self._standalone_native_transaction() as conn:
            values = self._native_boot_reply_values(message_name, payload)
            boot = values["mcuBootId"]
            if not self._recognize_native_boot_in_tx(conn, boot):
                return False
            self._native_boot_observation_values(conn, message_name, payload)
            existing = conn.execute("SELECT * FROM native_mcu_boot_observation WHERE boot_id=?", (boot,)).fetchone()
            if existing is not None:
                self._checked_native_boot_observation(conn, existing)
            else:
                conn.execute("""INSERT INTO native_mcu_boot_observation
                    (boot_id, probe_id, message_name, payload) VALUES (?, ?, ?, ?)""",
                    (boot, values["probeId"], message_name, payload))
            # Transport retirement is NOT a command decision, an action result,
            # work completion or a grant to send anything. Keep every old byte.
            conn.execute("UPDATE native_mcu_command SET boot_retired=1 WHERE mcu_boot_id<? AND boot_retired=0", (boot,))
        return True

    @classmethod
    def _checked_native_boot_observation(cls, conn, row):
        values = cls._native_boot_observation_values(conn, row["message_name"], bytes(row["payload"]))
        if (row["boot_id"], row["probe_id"]) != (values["mcuBootId"], values["probeId"]):
            raise ValueError("native boot observation is corrupt")
        return dict(row)

    def get_native_boot_observation(self, boot_id: int) -> Optional[dict]:
        """Return checked historical evidence, never current freshness or admission."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM native_mcu_boot_observation WHERE boot_id=?", (boot_id,)).fetchone()
            return self._checked_native_boot_observation(self._conn, row) if row is not None else None

    @staticmethod
    def _native_command_payload(name: str, uid: str, boot_id: int, sequence: int, fields: dict) -> bytes:
        import uart2_protocol as uart2
        spec = uart2.MESSAGE_SPECS.get(name)
        if (not spec or (spec["category"] not in {"COMMAND", "CONFIGURATION"} and name != "SAFE_CLOSE")
                or spec["direction"] != "EDGE_TO_MCU"):
            raise ValueError("not a native business command")
        identity = {"mcuCommandUid": uid, "targetMcuBootId": boot_id, "commandSequence": sequence,
                    "commandDigestSha256": "00" * 32}
        if identity.keys() & fields.keys():
            raise ValueError("caller cannot override allocated command identity")
        values = identity | fields
        values["commandDigestSha256"] = uart2.compute_command_digest(name, values)
        return uart2.encode_payload(name, values)

    @staticmethod
    def _checked_native_command(row) -> Optional[dict]:
        import uart2_protocol as uart2
        if row is None:
            return None
        record = dict(row)
        spec = uart2.MESSAGE_SPECS.get(record["message_name"])
        if (not spec or (spec["category"] not in {"COMMAND", "CONFIGURATION"} and record["message_name"] != "SAFE_CLOSE")
                or spec["direction"] != "EDGE_TO_MCU"):
            raise ValueError("native command record is corrupt")
        values = uart2.decode_payload(record["message_name"], record["payload"])
        if (values["mcuCommandUid"] != record["command_uid"] or values["targetMcuBootId"] != record["mcu_boot_id"]
                or values["commandSequence"] != record["command_sequence"]):
            raise ValueError("native command record identity is corrupt")
        return record

    def prepare_native_command(self, name: str, command_uid: str, boot_id: int, fields: dict) -> dict:
        """Freeze one intention/sequence before dispatch; never a work permit.

        command_uid is a caller's already durable logical action identity. A
        duplicate returns the original bytes, not a new sequence or new action.
        The caller must retain/revalidate original authorization deadlines.
        """
        if name == "SAFE_CLOSE":
            raise ValueError("SAFE_CLOSE requires durable delivery recovery authority")
        command_uid = str(_uuid.UUID(command_uid))
        with self._standalone_native_transaction() as conn:
            return self._prepare_native_command_in_tx(conn, name, command_uid, boot_id, fields)

    def prepare_native_baseline_work(
        self,
        command: dict,
        permit,
        native_command_uid: str,
        boot_id: int,
        *,
        runtime_instance_uid: str,
    ) -> dict:
        """Atomically bind one baseline cloud command, MCU intent and slot.

        The volatile runtime token that permits the first write is deliberately
        created only after this commit.  A process death at the return boundary
        therefore leaves a complete query/technical-failure identity, never an
        unresolved native row without its owning work slot.
        """
        from dataclasses import asdict
        from job_safety import JobPermit, command_request_digest

        stable = dict(command) if isinstance(command, dict) else None
        if stable is None:
            raise ValueError("native baseline cloud command must be an object")
        stable.pop("cosGrant", None)
        payload = stable.get("payload")
        target = stable.get("target")
        if (
            not isinstance(permit, JobPermit)
            or permit.work_type != WORK_TYPE_BASELINE
            or not isinstance(payload, dict)
            or not isinstance(target, dict)
            or stable.get("commandType") != "MEASURE_EMPTY_BAG_BASELINE"
            or stable.get("commandUid") != permit.command_uid
            or target.get("type") != "BASELINE_MEASUREMENT"
            or target.get("uid") != permit.work_uid
            or payload.get("measurementUid") != permit.work_uid
            or payload.get("emptyBagConfirmed") is not True
            or payload.get("measurementTimeoutMs") != 5000
            or payload.get("portNo") != 1
            or not isinstance(payload.get("bagUid"), str)
            or not payload["bagUid"]
            or not isinstance(payload.get("config"), dict)
            or command_request_digest(stable)
            != permit.request_digest_sha256
            or permit.permit_uid != permit.command_uid
        ):
            raise ValueError("native baseline authority is inconsistent")
        try:
            parsed_runtime_uid = _uuid.UUID(runtime_instance_uid)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("native baseline runtime identity is invalid") from error
        if (
            parsed_runtime_uid.version != 4
            or str(parsed_runtime_uid) != runtime_instance_uid
        ):
            raise ValueError("native baseline runtime identity is invalid")
        config = payload["config"]
        fields = {
            "measurementUid": permit.work_uid,
            "portNo": payload["portNo"],
            "configVersion": config.get("version"),
            "configContentSha256": config.get("contentSha256"),
            "startExecutionWindowMs": 5000,
            "measurementTimeoutMs": 5000,
        }
        context = {
            "native_protocol": 2,
            "phase": "NATIVE_BASELINE_RUNNING",
            "start_command_uid": permit.command_uid,
            "start_mcu_command_uid": native_command_uid,
            "start_runtime_instance_uid": runtime_instance_uid,
            "bag_uid": payload["bagUid"],
            "empty_bag_confirmed": True,
            "config": dict(config),
            "job_safety": asdict(permit) | {"begin_uid": permit.work_uid},
        }
        pending_result = {
            "native_pending": True,
            "mcu_command_uid": native_command_uid,
        }
        with self._standalone_native_transaction() as conn:
            row = conn.execute(
                "SELECT * FROM command_inbox WHERE command_uid=?",
                (permit.command_uid,),
            ).fetchone()
            if row is None:
                raise ValueError("native baseline cloud command is missing")
            stored = _json.loads(row["payload_json"])
            if stored.get("cosGrant") is None:
                stored.pop("cosGrant", None)
            if (
                row["state"] != "PROCESSING"
                or row["command_type"] != "MEASURE_EMPTY_BAG_BASELINE"
                or row["canonical_sha256"]
                != canonical_payload_sha256(stable)
                or stored != stable
            ):
                raise ValueError("native baseline cloud command changed")
            if conn.execute(
                "SELECT 1 FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone():
                return None
            slot = conn.execute(
                "SELECT work_type FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if slot is None or slot["work_type"] != WORK_TYPE_NONE:
                return None
            record = self._prepare_native_command_in_tx(
                conn,
                "MEASURE_BASELINE",
                native_command_uid,
                boot_id,
                fields,
            )
            acquired = conn.execute(
                """UPDATE work_slot
                   SET work_type=?, work_uid=?, work_state='ACTIVE',
                       port_no=?, context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_type=?""",
                (
                    WORK_TYPE_BASELINE,
                    permit.work_uid,
                    payload["portNo"],
                    _json.dumps(context, ensure_ascii=False, sort_keys=True),
                    self._now(),
                    WORK_TYPE_NONE,
                ),
            )
            if acquired.rowcount != 1:
                raise ValueError("native baseline work slot changed")
            waiting = conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    native_command_uid,
                    _json.dumps(
                        pending_result,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    permit.command_uid,
                ),
            )
            if waiting.rowcount != 1:
                raise ValueError("native baseline cloud command changed")
            return record

    def _prepare_native_command_in_tx(self, conn, name, command_uid, boot_id, fields):
        self._verify_native_dispatch_retirements(conn)
        existing = self._checked_native_command(conn.execute(
            "SELECT * FROM native_mcu_command WHERE command_uid=?", (command_uid,)).fetchone())
        if existing:
            candidate = self._native_command_payload(name, command_uid, boot_id, existing["command_sequence"], fields)
            if name != existing["message_name"] or candidate != existing["payload"]:
                raise ValueError("native command identity conflict")
            return existing
        if conn.execute("""SELECT 1 FROM native_mcu_command
                WHERE boot_retired=0 AND dispatch_retired=0 AND (decision_outcome IS NULL OR conflict=1)""").fetchone():
            raise RuntimeError("native command unresolved; no additional action")
        if (type(boot_id) is not int or boot_id == 0 or boot_id != self._native_counter(conn, "native_current_boot")
                or not conn.execute("SELECT 1 FROM native_mcu_boot WHERE boot_id=?", (boot_id,)).fetchone()):
            raise ValueError("native command requires a recognized boot")
        key = f"native_command_sequence:{boot_id}"
        previous = self._native_counter(conn, key, 4294967295)
        highest = conn.execute("SELECT COALESCE(MAX(command_sequence),0) FROM native_mcu_command WHERE mcu_boot_id=?", (boot_id,)).fetchone()[0]
        if previous < highest:
            raise ValueError("native command counter regressed")
        if previous == 4294967295:
            raise ValueError("native command counter exhausted")
        sequence = previous + 1
        payload = self._native_command_payload(name, command_uid, boot_id, sequence, fields)
        self._set_native_counter(conn, key, sequence)
        conn.execute("""INSERT INTO native_mcu_command
            (command_uid, mcu_boot_id, command_sequence, message_name, payload) VALUES (?, ?, ?, ?, ?)""",
            (command_uid, boot_id, sequence, name, payload))
        result = self._checked_native_command(conn.execute(
            "SELECT * FROM native_mcu_command WHERE command_uid=?", (command_uid,)).fetchone())
        return result

    def claim_native_command_write(self, command_uid: str) -> bool:
        """Commit 'may be sent' before ONE write. It can never be unclaimed.

        Caller must first acquire/revalidate the permanent action gate. A crash
        after this commit, even before the actual write, permits only queries.
        """
        with self._standalone_native_transaction() as conn:
            self._verify_native_dispatch_retirements(conn)
            record = self._checked_native_command(conn.execute(
                "SELECT * FROM native_mcu_command WHERE command_uid=?", (command_uid,)).fetchone())
            if record is None:
                raise ValueError("unknown native command")
            if conn.execute("SELECT 1 FROM native_recovery_close_retirement WHERE action_uid=?", (command_uid,)).fetchone():
                return False
            if record["write_claimed"] or record["boot_retired"] or record["dispatch_retired"] or record["conflict"] or record["decision_outcome"] is not None:
                return False
            if record["mcu_boot_id"] != self._native_counter(conn, "native_current_boot"):
                raise ValueError("native command boot is retired")
            conn.execute("UPDATE native_mcu_command SET write_claimed=1 WHERE command_uid=?", (command_uid,))
        return True

    def get_native_command(self, command_uid: str) -> Optional[dict]:
        with self._lock:
            return self._checked_native_command(self._conn.execute(
                "SELECT * FROM native_mcu_command WHERE command_uid=?", (command_uid,)).fetchone())

    def list_native_commands(self) -> list[dict]:
        with self._lock:
            return [self._checked_native_command(row) for row in self._conn.execute(
                "SELECT * FROM native_mcu_command ORDER BY mcu_boot_id, command_sequence").fetchall()]

    def save_native_command_observation(self, message_name: str, payload: bytes) -> bool:
        """Exact original identity only. Caller also checks query freshness.

        Decisions close only the 'awaiting acceptance' slot, not a business job,
        actuator effect, or permanent receipt. Non-decisions never release it.
        Repeated unchanged queries retain their first evidence, not one row/sec.
        """
        import uart2_protocol as uart2
        if message_name not in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"} or type(payload) is not bytes:
            raise ValueError("expected native command observation")
        values = uart2.decode_payload(message_name, payload)
        uid = values["mcuCommandUid"]
        offset = 8 if message_name == "COMMAND_QUERY_RESULT" else 0
        with self._standalone_native_transaction() as conn:
            record = self._checked_native_command(conn.execute(
                "SELECT * FROM native_mcu_command WHERE command_uid=?", (uid,)).fetchone())
            if record is None or not record["write_claimed"] or payload[offset:offset + 60] != record["payload"][:60]:
                return False
            outcome, error = values["outcome"], values["errorCode"]
            conn.execute("""INSERT OR IGNORE INTO native_mcu_command_observation
                (command_uid, outcome, current_boot_id, error_code, highest_sequence, message_name, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", (uid, outcome, values["currentMcuBootId"], error,
                    values.get("highestCommandSequence", -1), message_name, payload))
            decision = outcome in {"ACCEPTED", "REJECTED"}
            conflict = (outcome == "IDENTITY_CONFLICT" or (record["decision_outcome"] is not None
                and (outcome == "NOT_SEEN" or (decision and (outcome != record["decision_outcome"]
                    or error != record["decision_error"])))))
            if conflict:
                conn.execute("UPDATE native_mcu_command SET conflict=1 WHERE command_uid=?", (uid,))
            elif decision and record["decision_outcome"] is None:
                conn.execute("UPDATE native_mcu_command SET decision_outcome=?, decision_error=? WHERE command_uid=?",
                             (outcome, error, uid))
        return True

    def list_native_command_observations(self, command_uid: str) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(
                "SELECT * FROM native_mcu_command_observation WHERE command_uid=? ORDER BY created_at, outcome",
                (command_uid,)).fetchall()]

    def native_baseline_result_deadline_status(
        self,
        command_uid: str,
        *,
        communication_timeout_ms: int,
    ) -> dict:
        """Compare against the first durable ACCEPTED fact, never process age.

        The MCU's immutable command contributes its fixed five-second
        measurement bound.  The configured communication timeout is the
        remaining query/handoff allowance.  Because the acceptance row is
        committed before this read and survives a Pi restart, reopening the
        runtime cannot reset an accepted measurement's deadline.
        """
        import uart2_protocol as uart2

        if (
            type(communication_timeout_ms) is not int
            or communication_timeout_ms < 1000
        ):
            raise ValueError("native baseline communication timeout is invalid")
        with self._lock:
            record = self._checked_native_command(
                self._conn.execute(
                    "SELECT * FROM native_mcu_command WHERE command_uid=?",
                    (command_uid,),
                ).fetchone()
            )
            if (
                record is None
                or record["message_name"] != "MEASURE_BASELINE"
                or not record["write_claimed"]
                or record["conflict"]
            ):
                raise ValueError("native baseline deadline lost its original command")
            values = uart2.decode_payload("MEASURE_BASELINE", record["payload"])
            if values["measurementTimeoutMs"] != 5000:
                raise ValueError("native baseline deadline is not five seconds")
            if record["decision_outcome"] != "ACCEPTED":
                return {"state": "WAITING_FOR_ACCEPTANCE", "expired": False}
            if record["decision_error"] != "NONE":
                raise ValueError("accepted native baseline carries a rejection error")
            row = self._conn.execute(
                """SELECT MIN(created_at) AS accepted_at,
                          CAST((julianday('now') - julianday(MIN(created_at)))
                               * 86400000 AS INTEGER) AS elapsed_ms
                   FROM native_mcu_command_observation
                   WHERE command_uid=? AND outcome='ACCEPTED'
                     AND current_boot_id=? AND error_code='NONE'""",
                (command_uid, values["targetMcuBootId"]),
            ).fetchone()
            if row is None or row["accepted_at"] is None or row["elapsed_ms"] is None:
                raise ValueError("accepted native baseline lacks its durable observation")
            # SQLite's historical observation column has whole-second
            # precision.  Treat the unknown sub-second fraction
            # conservatively so an ACCEPTED fact can never expire almost one
            # second before the MCU's five-second measurement bound.
            allowance = (
                values["measurementTimeoutMs"]
                + communication_timeout_ms
                + 1000
            )
            elapsed = int(row["elapsed_ms"])
            return {
                "state": "ACCEPTED",
                "acceptedAt": row["accepted_at"],
                "deadlineAfterMs": allowance,
                # A backwards wall-clock discontinuity must not create an
                # unbounded permit. It fails closed as an elapsed deadline.
                "expired": elapsed < 0 or elapsed >= allowance,
            }

    def native_baseline_release_deadline_status(
        self,
        command_uid: str,
        *,
        communication_timeout_ms: int,
    ) -> dict:
        """Bound exact-result handoff without discarding the saved result."""
        import uart2_protocol as uart2

        if (
            type(communication_timeout_ms) is not int
            or communication_timeout_ms < 1000
        ):
            raise ValueError("native baseline release timeout is invalid")
        with self._lock:
            record = self._checked_native_command(
                self._conn.execute(
                    "SELECT * FROM native_mcu_command WHERE command_uid=?",
                    (command_uid,),
                ).fetchone()
            )
            scope = self._native_baseline_scope(record)
            raw_scope = uart2.encode_payload(
                "QUERY_PROCESS_EVENT",
                scope | {"queryId": 1},
            )[8:]
            row = self._conn.execute(
                """SELECT created_at,
                          CAST((julianday('now') - julianday(created_at))
                               * 86400000 AS INTEGER) AS elapsed_ms
                   FROM native_process_receipt WHERE scope=?""",
                (raw_scope,),
            ).fetchone()
            if row is None or row["elapsed_ms"] is None:
                raise ValueError("native baseline release lacks exact result receipt")
            elapsed = int(row["elapsed_ms"])
            allowance = communication_timeout_ms + 1000
            return {
                "state": "WAITING_FOR_MCU_RELEASE",
                "resultSavedAt": row["created_at"],
                "deadlineAfterMs": allowance,
                "expired": elapsed < 0 or elapsed >= allowance,
            }

    def _migrate_v19(self) -> None:
        """Native result handoff is durable but isolated from business relay."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_result (
            mcu_boot_id INTEGER NOT NULL CHECK (mcu_boot_id BETWEEN 1 AND 9007199254740991),
            result_sequence INTEGER NOT NULL CHECK (result_sequence BETWEEN 1 AND 4294967295),
            work_uid TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) = 199),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, result_sequence)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_result_report_outbox (
            task_uid TEXT NOT NULL UNIQUE,
            mcu_boot_id INTEGER NOT NULL,
            result_sequence INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING_CLASSIFICATION'
                CHECK (state = 'PENDING_CLASSIFICATION'),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, result_sequence),
            FOREIGN KEY (mcu_boot_id, result_sequence)
                REFERENCES native_mcu_result (mcu_boot_id, result_sequence)
        )""")

        self._conn.execute("""CREATE TABLE IF NOT EXISTS native_mcu_result_conflict (
            mcu_boot_id INTEGER NOT NULL,
            result_sequence INTEGER NOT NULL,
            payload BLOB NOT NULL CHECK (length(payload) = 199),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (mcu_boot_id, result_sequence, payload),
            FOREIGN KEY (mcu_boot_id, result_sequence)
                REFERENCES native_mcu_result (mcu_boot_id, result_sequence)
        )""")

    @contextmanager
    def _standalone_native_transaction(self):
        # Reject nesting before transaction() can roll back its caller's work.
        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("native persistence requires a standalone transaction")
            with self.transaction(immediate=True) as conn:
                yield conn

    def reserve_native_query_id(self) -> int:
        """Candidate read-only request identity: COMMIT before a single write.

        Reconnect/restart never resets it. Abandoned reservations leave gaps.
        Corrupt/exhausted counters fail closed; never wrap to an old query ID.
        Existing device_state table keeps schema 19 unchanged. This is not an
        MCU boot ID, action sequence or transport txSequence allocator.
        """
        with self._standalone_native_transaction() as conn:
            row = conn.execute(
                "SELECT state_value FROM device_state WHERE state_key='native_query_sequence'"
            ).fetchone()
            raw = row[0] if row is not None else "0"
            if (not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal()
                    or len(raw) > 16 or (len(raw) > 1 and raw[0] == "0")):
                raise ValueError("native query counter is corrupt")
            previous = int(raw)
            if previous >= 9007199254740991:
                raise ValueError("native query counter is exhausted")
            reserved = previous + 1
            conn.execute(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES ('native_query_sequence', ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET
                       state_value=excluded.state_value, updated_at=excluded.updated_at""",
                (str(reserved), self._now()),
            )
        return reserved

    def save_native_mcu_result(self, payload: bytes) -> dict:
        """Candidate-only: complete validation -> one COMMIT -> saved receipt.

        The task is NOT a OneNet event and is invisible to the money/business
        relay. A later classifier must merge Pi photos/context and choose the
        approved normal/exception/restart policy. No work slot is released here.
        Never call inside another EdgeStore transaction or acknowledge on error.
        """
        import uart2_protocol as uart2

        if not isinstance(payload, bytes):
            raise ValueError("native result must be immutable bytes")
        values = uart2.decode_payload("WORK_RESULT", payload)
        key = (values["mcuBootId"], values["resultSequence"])
        conflict = False
        issue_error = None
        with self._standalone_native_transaction() as conn:
            existing = conn.execute(
                "SELECT payload FROM native_mcu_result WHERE mcu_boot_id=? AND result_sequence=?", key
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    conn.execute(
                        """INSERT OR IGNORE INTO native_mcu_result_conflict
                           (mcu_boot_id, result_sequence, payload) VALUES (?, ?, ?)""",
                        (*key, payload),
                    )
                    conflict = True
                task = conn.execute(
                    "SELECT task_uid FROM native_result_report_outbox WHERE mcu_boot_id=? AND result_sequence=?", key
                ).fetchone()
                if task is None:
                    raise RuntimeError("native result has no durable report task")
                task_uid = task["task_uid"]
            else:
                task_uid = self._new_uid()
                conn.execute(
                    """INSERT INTO native_mcu_result
                       (mcu_boot_id, result_sequence, work_uid, result_digest, payload)
                       VALUES (?, ?, ?, ?, ?)""",
                    (*key, values["workUid"], values["resultDigestSha256"], payload),
                )
                conn.execute(
                    """INSERT INTO native_result_report_outbox
                       (task_uid, mcu_boot_id, result_sequence) VALUES (?, ?, ?)""",
                    (task_uid, *key),
                )
            if not conflict:
                try:
                    self._append_native_delivery_issue_result(conn, payload, values)
                except ValueError as exc:
                    issue_error = exc
        # This line is deliberately after the transaction context has committed.
        if conflict:
            raise ValueError("native result identity conflict; evidence saved, no saved acknowledgement")
        if issue_error is not None:
            raise issue_error
        return {"savedPayload": payload[:60], "taskUid": task_uid}

    def get_native_mcu_result(self, mcu_boot_id: int, result_sequence: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM native_mcu_result WHERE mcu_boot_id=? AND result_sequence=?",
                (mcu_boot_id, result_sequence),
            ).fetchone()
            return dict(row) if row else None

    def list_native_result_report_tasks(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(
                """SELECT t.* FROM native_result_report_outbox t
                JOIN native_mcu_result r USING(mcu_boot_id,result_sequence)
                WHERE NOT EXISTS (SELECT 1 FROM native_delivery_issue i
                    JOIN native_work_recovery_intent a USING(recovery_uid)
                    WHERE i.work_uid=r.work_uid OR replace(a.start_command_uid,'-','')=lower(hex(substr(r.payload,71,16))))
                ORDER BY t.mcu_boot_id, t.result_sequence"""
            ).fetchall()]

    def list_native_mcu_result_conflicts(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(
                "SELECT * FROM native_mcu_result_conflict ORDER BY mcu_boot_id, result_sequence"
            ).fetchall()]

    def _migrate_v10(self) -> None:
        """Add the independent, reboot-safe remote-support control slot."""

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS remote_support_session (
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
            )"""
        )

    def _migrate_v11(self) -> None:
        """Add an exclusive, reboot-safe STM32 firmware update journal."""

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS maintenance_lock (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                lock_type TEXT NOT NULL CHECK (
                    lock_type = 'MCU_FIRMWARE_UPDATE'
                ),
                owner_uid TEXT NOT NULL,
                detail_json TEXT,
                acquired_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_update (
                update_uid TEXT PRIMARY KEY,
                deployment_uid TEXT NOT NULL UNIQUE,
                command_uid TEXT UNIQUE,
                source TEXT NOT NULL CHECK (source IN ('CLOUD', 'LOCAL')),
                package_path TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'QUEUED', 'PREFLIGHT', 'PREPARED',
                    'FLASHING_TARGET', 'VERIFYING_TARGET',
                    'ROLLING_BACK', 'VERIFYING_ROLLBACK',
                    'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED',
                    'REJECTED'
                )),
                legacy_preflight INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_preflight IN (0, 1)),
                allow_downgrade INTEGER NOT NULL DEFAULT 0
                    CHECK (allow_downgrade IN (0, 1)),
                requested_reason TEXT,
                target_attempt_count INTEGER NOT NULL DEFAULT 0,
                rollback_attempt_count INTEGER NOT NULL DEFAULT 0,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_mcu_update_state
               ON mcu_firmware_update(state, requested_at)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_progress (
                update_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                target_attempt_count INTEGER NOT NULL,
                rollback_attempt_count INTEGER NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (
                    update_uid, stage,
                    target_attempt_count, rollback_attempt_count
                )
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                current_package_path TEXT,
                current_package_sha256 TEXT,
                current_manifest_json TEXT,
                current_installed_at TEXT,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                previous_installed_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        now = self._now()
        self._conn.execute(
            """INSERT OR IGNORE INTO mcu_firmware_state
               (singleton_id, updated_at) VALUES (1, ?)""",
            (now,),
        )

    def _migrate_v12(self) -> None:
        """Reserve cloud updates before package I/O and widen the journal state."""

        self._conn.execute(
            "ALTER TABLE mcu_firmware_update RENAME TO mcu_firmware_update_v11"
        )
        self._conn.execute(
            """CREATE TABLE mcu_firmware_update (
                update_uid TEXT PRIMARY KEY,
                deployment_uid TEXT NOT NULL UNIQUE,
                command_uid TEXT UNIQUE,
                source TEXT NOT NULL CHECK (source IN ('CLOUD', 'LOCAL')),
                package_path TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                package_ready INTEGER NOT NULL DEFAULT 1
                    CHECK (package_ready IN (0, 1)),
                state TEXT NOT NULL CHECK (state IN (
                    'QUEUED', 'PACKAGE_FETCH_FAILED',
                    'PREFLIGHT', 'PREPARED',
                    'FLASHING_TARGET', 'VERIFYING_TARGET',
                    'ROLLING_BACK', 'VERIFYING_ROLLBACK',
                    'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED',
                    'REJECTED'
                )),
                legacy_preflight INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_preflight IN (0, 1)),
                allow_downgrade INTEGER NOT NULL DEFAULT 0
                    CHECK (allow_downgrade IN (0, 1)),
                requested_reason TEXT,
                target_attempt_count INTEGER NOT NULL DEFAULT 0,
                rollback_attempt_count INTEGER NOT NULL DEFAULT 0,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )
        self._conn.execute(
            """INSERT INTO mcu_firmware_update (
                 update_uid, deployment_uid, command_uid, source,
                 package_path, package_sha256, manifest_json,
                 package_ready, state, legacy_preflight, allow_downgrade,
                 requested_reason, target_attempt_count,
                 rollback_attempt_count, previous_package_path,
                 previous_package_sha256, previous_manifest_json,
                 last_error_code, last_error_message, requested_at,
                 started_at, updated_at, completed_at
               )
               SELECT update_uid, deployment_uid, command_uid, source,
                      package_path, package_sha256, manifest_json,
                      1, state, legacy_preflight, allow_downgrade,
                      requested_reason, target_attempt_count,
                      rollback_attempt_count, previous_package_path,
                      previous_package_sha256, previous_manifest_json,
                      last_error_code, last_error_message, requested_at,
                      started_at, updated_at, completed_at
               FROM mcu_firmware_update_v11"""
        )
        self._conn.execute("DROP TABLE mcu_firmware_update_v11")
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_mcu_update_state
               ON mcu_firmware_update(state, requested_at)"""
        )

    def _migrate_v13(self) -> None:
        """Bound cloud package acquisition across task wake generations."""

        self._conn.execute(
            """ALTER TABLE mcu_firmware_update
               ADD COLUMN package_acquisition_attempt_count INTEGER
                   NOT NULL DEFAULT 0
                   CHECK (package_acquisition_attempt_count >= 0)"""
        )

    def _migrate_v14(self) -> None:
        """Persist the point after which an F2 stop command may have run."""

        # V14 was first released before migrations used an explicit
        # transaction. A power loss may therefore leave either column on a
        # database whose recorded version is still 13. Resume from the actual
        # table shape instead of replaying already-persisted DDL.
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('mcu_firmware_update')"
            ).fetchall()
        }
        if "prepare_recovery_required" not in columns:
            self._conn.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_recovery_required INTEGER
                       NOT NULL DEFAULT 0
                       CHECK (prepare_recovery_required IN (0, 1))"""
            )
        if "prepare_identity_json" not in columns:
            self._conn.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_identity_json TEXT"""
            )
        uncertain_updates = self._conn.execute(
            """SELECT update_uid, previous_manifest_json
               FROM mcu_firmware_update
               WHERE legacy_preflight=0
                 AND state IN ('PREFLIGHT', 'PREPARED')
                 AND target_attempt_count=0
                 AND rollback_attempt_count=0"""
        ).fetchall()
        for update in uncertain_updates:
            identity_json = None
            try:
                manifest = _json.loads(update["previous_manifest_json"])
                identity_json = _canonical_mcu_prepare_identity(
                    {
                        "protocolRevision": manifest.get(
                            "fixedFrameRevision"
                        ),
                        "firmwareVersionCode": manifest.get(
                            "firmwareVersionCode"
                        ),
                        "firmwareVersion": manifest.get("firmwareVersion"),
                        "firmwareIdentityHex": manifest.get(
                            "firmwareIdentityHex"
                        ),
                    }
                )

            except (AttributeError, TypeError, ValueError):
                # v13 did not persist the exact pre-F2 F3 snapshot.  Missing
                # or corrupt stable identity must migrate fail-closed.
                pass
            self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET prepare_recovery_required=1,
                       prepare_identity_json=?
                   WHERE update_uid=?""",
                (identity_json, update["update_uid"]),
            )

    def _migrate_v15(self) -> None:
        """Add the monotonic, reboot-safe factory-seal authorization journal.

        Every DDL statement is shape-aware and idempotent.  This is required
        even though current migrations run in one explicit transaction:
        deployed v14 databases may be left with durable DDL but no recorded
        v15 version by older SQLite wrappers or a process kill at the boundary.
        """

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS factory_seal_authorization (
                command_uid TEXT PRIMARY KEY,
                command_sha256 TEXT NOT NULL,
                hardware_sn TEXT NOT NULL,
                acceptance_generation INTEGER NOT NULL
                    CHECK (acceptance_generation > 0),
                evidence_event_uid TEXT NOT NULL,
                acceptance_challenge_uid TEXT NOT NULL,
                acceptance_evidence_sha256 TEXT NOT NULL,
                factory_bag_revision INTEGER NOT NULL
                    CHECK (factory_bag_revision >= 0),
                factory_bag_set_sha256 TEXT NOT NULL,
                image_release_id TEXT NOT NULL,
                image_release_sha256 TEXT NOT NULL,
                factory_report_sha256 TEXT NOT NULL,
                authorization_binding_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'AUTHORIZED', 'SUPERSEDED', 'SEALING', 'SEALED'
                )),
                operator_confirmation_uid TEXT,
                authorized_at TEXT NOT NULL,
                confirmed_at TEXT,
                completed_at TEXT,
                last_error TEXT
            )"""
        )
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        required = {
            "command_uid",
            "command_sha256",
            "hardware_sn",
            "acceptance_generation",
            "evidence_event_uid",
            "acceptance_challenge_uid",
            "acceptance_evidence_sha256",
            "factory_bag_revision",
            "factory_bag_set_sha256",
            "image_release_id",
            "image_release_sha256",
            "factory_report_sha256",
            "authorization_binding_sha256",
            "state",
            "operator_confirmation_uid",
            "authorized_at",
            "confirmed_at",
            "completed_at",
            "last_error",
        }
        forward_columns = {
            "cleanup_completed_at",
            "completion_event_uid",
            "completion_clock_quality",
        }
        if (
            not required.issubset(columns)
            or not columns.issubset(required | forward_columns)
        ):
            raise RuntimeError(
                "factory seal authorization table shape is incompatible"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_acceptance_generation
               ON factory_seal_authorization(acceptance_generation)"""
        )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_operator_confirmation
               ON factory_seal_authorization(operator_confirmation_uid)
               WHERE operator_confirmation_uid IS NOT NULL"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_factory_seal_state
               ON factory_seal_authorization(state, acceptance_generation)"""
        )

    def _migrate_v16(self) -> None:
        """Persist the atomic factory-seal completion fact and outbox link.

        The first release of this migration may be interrupted after either
        ``ALTER TABLE`` has reached durable storage but before schema version
        16 is recorded.  Inspecting the real table shape makes every restart
        safe: an existing column is retained, a missing column is added, and
        the complete final shape is verified before the version advances.
        """

        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        if "cleanup_completed_at" not in columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN cleanup_completed_at TEXT"""
            )
        if "completion_event_uid" not in columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN completion_event_uid TEXT"""
            )

        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        required = {
            "command_uid",
            "command_sha256",
            "hardware_sn",
            "acceptance_generation",
            "evidence_event_uid",
            "acceptance_challenge_uid",
            "acceptance_evidence_sha256",
            "factory_bag_revision",
            "factory_bag_set_sha256",
            "image_release_id",
            "image_release_sha256",
            "factory_report_sha256",
            "authorization_binding_sha256",
            "state",
            "operator_confirmation_uid",
            "authorized_at",
            "confirmed_at",
            "completed_at",
            "last_error",
            "cleanup_completed_at",
            "completion_event_uid",
        }
        if not required.issubset(columns) or not columns.issubset(
            required | {"completion_clock_quality"}
        ):
            raise RuntimeError(
                "factory seal authorization v16 table shape is incompatible"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_completion_event
               ON factory_seal_authorization(completion_event_uid)
               WHERE completion_event_uid IS NOT NULL"""
        )
        index = next(
            (
                row
                for row in self._conn.execute(
                    "PRAGMA index_list('factory_seal_authorization')"
                ).fetchall()
                if row["name"] == "idx_factory_seal_completion_event"
            ),
            None,
        )
        indexed_columns = [
            row["name"]
            for row in self._conn.execute(
                "PRAGMA index_info('idx_factory_seal_completion_event')"
            ).fetchall()
        ]
        if (
            index is None
            or index["unique"] != 1
            or index["partial"] != 1
            or indexed_columns != ["completion_event_uid"]
        ):
            raise RuntimeError(
                "factory seal completion index shape is incompatible"
            )

    def _migrate_v17(self) -> None:
        """Persist clock trust beside raw device wall-clock observations."""

        command_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('command_inbox')"
            ).fetchall()
        }
        if "received_clock_quality" not in command_columns:
            self._conn.execute(
                """ALTER TABLE command_inbox
                   ADD COLUMN received_clock_quality TEXT NOT NULL
                       DEFAULT 'ESTIMATED'
                       CHECK (received_clock_quality IN (
                           'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                       ))"""
            )

        photo_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('photo_outbox')"
            ).fetchall()
        }
        if "captured_clock_quality" not in photo_columns:
            self._conn.execute(
                """ALTER TABLE photo_outbox
                   ADD COLUMN captured_clock_quality TEXT
                       CHECK (captured_clock_quality IS NULL OR
                              captured_clock_quality IN (
                                  'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                              ))"""
            )
        self._conn.execute(
            """UPDATE photo_outbox
               SET captured_clock_quality='ESTIMATED'
               WHERE captured_at IS NOT NULL
                 AND captured_clock_quality IS NULL"""
        )

        seal_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        if "completion_clock_quality" not in seal_columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN completion_clock_quality TEXT
                       CHECK (completion_clock_quality IS NULL OR
                              completion_clock_quality IN (
                                  'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                              ))"""
            )
        self._conn.execute(
            """UPDATE factory_seal_authorization
               SET completion_clock_quality='SYNCED'
               WHERE state='SEALED'
                 AND completion_clock_quality IS NULL"""
        )

        required_shapes = {
            "command_inbox": "received_clock_quality",
            "photo_outbox": "captured_clock_quality",
            "factory_seal_authorization": "completion_clock_quality",
        }
        for table, column in required_shapes.items():
            columns = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info('{table}')"
                ).fetchall()
            }
            if column not in columns:
                raise RuntimeError(
                    f"EdgeStore v17 {table} shape is incompatible"
                )

    def _migrate_v18(self) -> None:
        """Qualify one command stage by its stable error identity.

        A clean operation can first time out and later be interrupted by an
        edge restart.  Both are reliable facts with stage ``FAILED``; the
        second fact must not overwrite the first.  Empty ``error_code`` is
        the durable identity for stages whose wire ``errorCode`` is null.
        """

        table_info = self._conn.execute(
            "PRAGMA table_info('command_observation')"
        ).fetchall()
        columns = {row["name"] for row in table_info}
        primary_key = [
            row["name"]
            for row in sorted(
                (row for row in table_info if row["pk"]),
                key=lambda row: row["pk"],
            )
        ]
        v18_columns = {
            "command_uid",
            "stage",
            "error_code",
            "event_uid",
            "canonical_sha256",
            "created_at",
        }
        if columns == v18_columns:
            if primary_key != ["command_uid", "stage", "error_code"]:
                raise RuntimeError(
                    "EdgeStore v18 command observation key is incompatible"
                )
            event_uid_unique = any(
                index["unique"] == 1
                and [
                    row["name"]
                    for row in self._conn.execute(
                        f"PRAGMA index_info('{index['name']}')"
                    ).fetchall()
                ] == ["event_uid"]
                for index in self._conn.execute(
                    "PRAGMA index_list('command_observation')"
                ).fetchall()
            )
            if not event_uid_unique:
                raise RuntimeError(
                    "EdgeStore v18 command event identity is not unique"
                )
            return

        v17_columns = v18_columns - {"error_code"}
        if (
            columns != v17_columns
            or primary_key != ["command_uid", "stage"]
        ):
            raise RuntimeError(
                "EdgeStore v17 command observation shape is incompatible"
            )
        temporary = self._conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                   'command_observation_v17',
                   'command_observation_v18'
               )"""
        ).fetchall()
        if temporary:
            raise RuntimeError(
                "EdgeStore v18 command observation migration is incomplete"
            )

        rows = self._conn.execute(
            """SELECT observation.command_uid, observation.stage,
                      observation.event_uid,
                      observation.canonical_sha256,
                      observation.created_at,
                      event_row.payload_json
               FROM command_observation observation
               LEFT JOIN event_outbox event_row
                 ON event_row.event_uid = observation.event_uid
               ORDER BY observation.created_at, observation.event_uid"""
        ).fetchall()
        migrated_rows: list[tuple[str, str, str, str, str, str]] = []
        for row in rows:
            try:
                event = _json.loads(row["payload_json"])
                payload = event["payload"]
                error_code = payload.get("errorCode")
            except (KeyError, TypeError, _json.JSONDecodeError) as error:
                raise RuntimeError(
                    "EdgeStore v18 command observation event is invalid"
                ) from error
            if (
                event.get("eventType") != "DEVICE_COMMAND_OBSERVED"
                or event.get("commandUid") != row["command_uid"]
                or payload.get("stage") != row["stage"]
                or (
                    error_code is not None
                    and (
                        not isinstance(error_code, str)
                        or not error_code
                    )
                )
                or canonical_payload_sha256(payload)
                != row["canonical_sha256"]
            ):
                raise RuntimeError(
                    "EdgeStore v18 command observation event conflicts "
                    "with its identity"
                )
            migrated_rows.append((
                row["command_uid"],
                row["stage"],
                error_code if error_code is not None else "",
                row["event_uid"],
                row["canonical_sha256"],
                row["created_at"],
            ))

        self._conn.execute(
            """CREATE TABLE command_observation_v18 (
                command_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                error_code TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                canonical_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (command_uid, stage, error_code)
            )"""
        )
        self._conn.executemany(
            """INSERT INTO command_observation_v18
               (command_uid, stage, error_code, event_uid,
                canonical_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            migrated_rows,
        )
        self._conn.execute(
            "ALTER TABLE command_observation RENAME TO command_observation_v17"
        )
        self._conn.execute(
            "ALTER TABLE command_observation_v18 RENAME TO command_observation"
        )
        self._conn.execute("DROP TABLE command_observation_v17")

    def _create_tables(self) -> None:
        conn = self._conn
        conn.execute("""CREATE TABLE IF NOT EXISTS command_inbox (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            command_uid TEXT NOT NULL UNIQUE,
            command_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cmd_inbox_state ON command_inbox(state)")
        conn.execute("""CREATE TABLE IF NOT EXISTS work_slot (
            slot_id INTEGER PRIMARY KEY CHECK (slot_id = 1),
            work_type TEXT NOT NULL DEFAULT 'NONE',
            work_uid TEXT,
            work_state TEXT,
            port_no INTEGER,
            context_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT OR IGNORE INTO work_slot (slot_id) VALUES (1)")
        conn.execute("""CREATE TABLE IF NOT EXISTS event_outbox (
            event_uid TEXT NOT NULL PRIMARY KEY,
            edge_event_sequence INTEGER NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            mqtt_msg_id INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT,
            last_platform_code INTEGER,
            platform_accepted_at TEXT,
            last_platform_reply_at TEXT,
            work_uid TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_state ON event_outbox(state, tombstoned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_work ON event_outbox(work_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_retry ON event_outbox(state, next_retry_at) WHERE state='PENDING' AND tombstoned=0")
        conn.execute("""CREATE TABLE IF NOT EXISTS photo_outbox (
            photo_uid TEXT NOT NULL PRIMARY KEY,
            slot_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            cos_key TEXT,
            url TEXT,
            state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            uploaded_at TEXT,
            work_uid TEXT,
            work_type TEXT,
            device_name TEXT,
            content_sha256 TEXT,
            size_bytes INTEGER,
            captured_at TEXT,
            grant_request_event_uid TEXT,
            grant_generation INTEGER NOT NULL DEFAULT 0,
            status_event_uid TEXT,
            last_error TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_state ON photo_outbox(state, tombstoned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_work ON photo_outbox(work_uid)")
        conn.execute("""CREATE TABLE IF NOT EXISTS confirmation_inbox (
            confirmation_uid TEXT NOT NULL PRIMARY KEY,
            event_uid TEXT NOT NULL,
            outcome TEXT NOT NULL,
            payload_json TEXT,
            received_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conf_event ON confirmation_inbox(event_uid)")
        conn.execute("""CREATE TABLE IF NOT EXISTS device_state (
            state_key TEXT NOT NULL PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.executemany(
            "INSERT OR IGNORE INTO device_state (state_key, state_value) VALUES (?, ?)",
            [
                ("edge_boot_id", ""),
                ("edge_event_sequence", "0"),
                ("mqtt_session_present", "false"),
                ("mqtt_last_rc", "0"),
            ],
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS faults (
            fault_uid TEXT NOT NULL PRIMARY KEY,
            component TEXT NOT NULL,
            fault_code INTEGER NOT NULL,
            severity TEXT NOT NULL,
            lifecycle TEXT NOT NULL DEFAULT 'OBSERVED',
            detail_json TEXT,
            observed_at TEXT NOT NULL DEFAULT (datetime('now')),
            recovered_at TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faults_lifecycle ON faults(lifecycle)")
        conn.execute("""CREATE TABLE IF NOT EXISTS tombstones (
            tombstone_uid TEXT NOT NULL PRIMARY KEY,
            original_type TEXT NOT NULL,
            original_uid TEXT NOT NULL,
            cleared_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")

    def _migrate_v2(self) -> None:
        conn = self._conn
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(command_inbox)").fetchall()
        }
        additions = {
            "canonical_sha256": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "processing_started_at": "TEXT",
            "last_error": "TEXT",
            "result_json": "TEXT",
            "mcu_command_uid": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE command_inbox ADD COLUMN {name} {declaration}"
                )
        rows = conn.execute(
            "SELECT rowid, payload_json FROM command_inbox WHERE canonical_sha256 IS NULL"
        ).fetchall()
        for row in rows:
            payload = _json.loads(row["payload_json"])
            stable = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(stable, dict):
                stable.pop("cosGrant", None)
            conn.execute(
                "UPDATE command_inbox SET canonical_sha256=? WHERE rowid=?",
                (canonical_payload_sha256(stable), row["rowid"]),
            )

        conn.execute("""CREATE TABLE IF NOT EXISTS configuration_state (
            application_uid TEXT NOT NULL PRIMARY KEY,
            command_uid TEXT NOT NULL UNIQUE,
            device_name TEXT NOT NULL,
            config_version INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            mcu_payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            part_command_uids_json TEXT NOT NULL,
            state TEXT NOT NULL,
            commit_mcu_command_uid TEXT,
            error_code TEXT,
            edge_saved_at TEXT NOT NULL,
            applied_at TEXT
        )""")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_config_version "
            "ON configuration_state(config_version)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS mcu_event_inbox (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            mcu_boot_id INTEGER NOT NULL,
            mcu_event_sequence INTEGER NOT NULL,
            message_name TEXT NOT NULL,
            message_type INTEGER NOT NULL,
            source_tx_sequence INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT,
            last_error TEXT,
            UNIQUE(mcu_boot_id, mcu_event_sequence)
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcu_event_state "
            "ON mcu_event_inbox(state, rowid)"
        )

    def _migrate_v3(self) -> None:
        """Add an Edge-owned receive generation for fixed MCU boot IDs."""
        conn = self._conn
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(mcu_event_inbox)"
            ).fetchall()
        }
        if "mcu_receive_generation" not in columns:
            conn.execute(
                "ALTER TABLE mcu_event_inbox RENAME TO mcu_event_inbox_v2"
            )
            conn.execute("DROP INDEX IF EXISTS idx_mcu_event_state")
            conn.execute("""CREATE TABLE mcu_event_inbox (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                mcu_receive_generation INTEGER NOT NULL,
                mcu_boot_id INTEGER NOT NULL,
                mcu_event_sequence INTEGER NOT NULL,
                message_name TEXT NOT NULL,
                message_type INTEGER NOT NULL,
                source_tx_sequence INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING',
                received_at TEXT NOT NULL DEFAULT (datetime('now')),
                processed_at TEXT,
                last_error TEXT,
                UNIQUE(
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence
                )
            )""")
            conn.execute(
                """INSERT INTO mcu_event_inbox
                   (rowid, mcu_receive_generation, mcu_boot_id,
                    mcu_event_sequence, message_name, message_type,
                    source_tx_sequence, content_sha256, payload_json,
                    state, received_at, processed_at, last_error)
                   SELECT rowid, 0, mcu_boot_id, mcu_event_sequence,
                          message_name, message_type, source_tx_sequence,
                          content_sha256, payload_json, state, received_at,
                          processed_at, last_error
                   FROM mcu_event_inbox_v2"""
            )
            conn.execute("DROP TABLE mcu_event_inbox_v2")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcu_event_state "
            "ON mcu_event_inbox(state, rowid)"
        )
        conn.executemany(
            """INSERT OR IGNORE INTO device_state
               (state_key, state_value) VALUES (?, ?)""",
            [
                ("mcu_receive_generation", "0"),
                ("active_mcu_boot_id", ""),
            ],
        )

    def _migrate_v4(self) -> None:
        """Add persistent photo metadata without persisting temporary grants."""
        conn = self._conn
        conn.execute("""CREATE TABLE IF NOT EXISTS photo_outbox (
            photo_uid TEXT NOT NULL PRIMARY KEY,
            slot_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            cos_key TEXT,
            url TEXT,
            state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            uploaded_at TEXT,
            work_uid TEXT,
            work_type TEXT,
            device_name TEXT,
            content_sha256 TEXT,
            size_bytes INTEGER,
            captured_at TEXT,
            grant_request_event_uid TEXT,
            grant_generation INTEGER NOT NULL DEFAULT 0,
            status_event_uid TEXT,
            last_error TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(photo_outbox)"
            ).fetchall()
        }
        additions = {
            "url": "TEXT",
            "work_type": "TEXT",
            "device_name": "TEXT",
            "content_sha256": "TEXT",
            "size_bytes": "INTEGER",
            "captured_at": "TEXT",
            "grant_request_event_uid": "TEXT",
            "grant_generation": "INTEGER NOT NULL DEFAULT 0",
            "status_event_uid": "TEXT",
            "last_error": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE photo_outbox ADD COLUMN {name} {declaration}"
                )

        # Temporary COS credentials are execution-only data. Scrub any command
        # rows created by earlier versions that persisted the transport grant.
        command_table = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='command_inbox'"""
        ).fetchone()
        if command_table:
            for row in conn.execute(
                "SELECT rowid, payload_json FROM command_inbox"
            ).fetchall():
                payload = _json.loads(row["payload_json"])
                if (
                    isinstance(payload, dict)
                    and payload.get("cosGrant") is not None
                ):
                    payload["cosGrant"] = None
                    conn.execute(
                        """UPDATE command_inbox SET payload_json=?
                           WHERE rowid=?""",
                        (
                            _json.dumps(payload, ensure_ascii=False),
                            row["rowid"],
                        ),
                    )

        self._backfill_photo_metadata(conn)
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_photo_state
               ON photo_outbox(state, tombstoned)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_photo_work
               ON photo_outbox(work_uid)"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_photo_work_slot
               ON photo_outbox(work_type, work_uid, slot_name)
               WHERE tombstoned=0 AND work_type IS NOT NULL"""
        )

    def _migrate_v5(self) -> None:
        """Add fixed-frame compatibility results and reliable control identity."""
        conn = self._conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS confirmation_inbox (
                confirmation_uid TEXT NOT NULL PRIMARY KEY,
                event_uid TEXT NOT NULL,
                outcome TEXT NOT NULL,
                payload_json TEXT,
                received_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_conf_event
               ON confirmation_inbox(event_uid)"""
        )
        confirmation_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(confirmation_inbox)"
            ).fetchall()
        }
        for name, declaration in {
            "command_uid": "TEXT",
            "canonical_sha256": "TEXT",
            "receipt_event_uid": "TEXT",
        }.items():
            if name not in confirmation_columns:
                conn.execute(
                    "ALTER TABLE confirmation_inbox "
                    f"ADD COLUMN {name} {declaration}"
                )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_conf_receipt_event
               ON confirmation_inbox(receipt_event_uid)
               WHERE receipt_event_uid IS NOT NULL"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS command_observation (
                command_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                canonical_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (command_uid, stage)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fixed_frame_local_result (
                result_type TEXT NOT NULL,
                result_key TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (result_type, result_key)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS bag_baseline (
                bag_uid TEXT NOT NULL PRIMARY KEY,
                weight_grams INTEGER NOT NULL,
                source_kind TEXT NOT NULL,
                source_work_type TEXT,
                source_work_uid TEXT,
                source_mcu_boot_id INTEGER,
                source_mcu_event_sequence INTEGER,
                source_observed_at TEXT,
                measurement_uid TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS edge_fault_state (
                fault_uid TEXT NOT NULL PRIMARY KEY,
                scope_key TEXT NOT NULL,
                port_no INTEGER,
                component TEXT NOT NULL,
                fault_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                discovery_count INTEGER NOT NULL DEFAULT 1,
                first_detected_at TEXT NOT NULL,
                last_detected_at TEXT NOT NULL,
                recovered_at TEXT,
                detail_json TEXT,
                recovery_evidence TEXT
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_edge_fault_active
               ON edge_fault_state(
                   lifecycle, scope_key, component, fault_code
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_derived_event (
                mcu_receive_generation INTEGER NOT NULL,
                mcu_boot_id INTEGER NOT NULL,
                mcu_event_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                PRIMARY KEY (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    event_type
                )
            )"""
        )

    def _migrate_v6(self) -> None:
        """Keep URLs as evidence of successful COS upload only."""
        self._conn.execute(
            """UPDATE photo_outbox
               SET url=NULL
               WHERE state<>'UPLOADED' AND url IS NOT NULL"""
        )

    def _migrate_v7(self) -> None:
        """Persist sanitized OneNet event-post reply evidence."""
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(event_outbox)"
            ).fetchall()
        }
        if not columns:
            return
        for name, declaration in {
            "last_platform_code": "INTEGER",
            "platform_accepted_at": "TEXT",
            "last_platform_reply_at": "TEXT",
        }.items():
            if name not in columns:
                self._conn.execute(
                    "ALTER TABLE event_outbox "
                    f"ADD COLUMN {name} {declaration}"
                )

    def _migrate_v8(self) -> None:
        """Persist the current-bag fullness state owned by the edge."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS port_fullness_state (
                port_no INTEGER NOT NULL PRIMARY KEY,
                bag_uid TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('FULL', 'NOT_FULL')),
                last_state_change_uid TEXT,
                last_event_uid TEXT,
                updated_at TEXT NOT NULL
            )"""
        )

    def _backfill_photo_metadata(self, conn) -> None:
        delivery_slots = {
            "OPEN_INNER": "BEFORE_INNER",
            "OPEN_OUTSIDE": "BEFORE_OUTER",
            "CLOSE_INNER": "AFTER_INNER",
            "CLOSE_OUTSIDE": "AFTER_OUTER",
        }
        clean_slots = {
            "OPEN_INNER": "FIRST_OPEN_INNER",
            "OPEN_OUTSIDE": "FIRST_OPEN_OUTER",
            "CLOSE_INNER": "FINAL_CLOSE_INNER",
            "CLOSE_OUTSIDE": "FINAL_CLOSE_OUTER",
        }
        rows = conn.execute(
            """SELECT * FROM photo_outbox
               WHERE work_type IS NULL OR content_sha256 IS NULL
                  OR size_bytes IS NULL OR captured_at IS NULL"""
        ).fetchall()
        for row in rows:
            work_type = row["work_type"]
            device_name = row["device_name"]
            slot_name = row["slot_name"]
            event = conn.execute(
                """SELECT event_type, payload_json FROM event_outbox
                   WHERE work_uid=? AND event_type IN (
                       'DELIVERY_COMPLETE', 'CLEAN_COMPLETE'
                   )
                   ORDER BY edge_event_sequence LIMIT 1""",
                (row["work_uid"],),
            ).fetchone()
            if event:
                envelope = _json.loads(event["payload_json"])
                device_name = (
                    device_name or envelope.get("targetDeviceName")
                )
                if event["event_type"] == "DELIVERY_COMPLETE":
                    work_type = "DELIVERY_SESSION"
                    slot_name = delivery_slots.get(slot_name, slot_name)
                else:
                    work_type = "CLEAN_OPERATION"
                    slot_name = clean_slots.get(slot_name, slot_name)

            local_path = row["local_path"]
            content_sha256 = row["content_sha256"]
            size_bytes = row["size_bytes"]
            captured_at = row["captured_at"]
            if os.path.isfile(local_path):
                if content_sha256 is None:
                    digest = hashlib.sha256()
                    with open(local_path, "rb") as source:
                        for chunk in iter(lambda: source.read(64 * 1024), b""):
                            digest.update(chunk)
                    content_sha256 = digest.hexdigest()
                stat = os.stat(local_path)
                size_bytes = size_bytes or stat.st_size
                captured_at = captured_at or time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z",
                    time.gmtime(stat.st_mtime),
                )
            conn.execute(
                """UPDATE photo_outbox
                   SET slot_name=?, work_type=?, device_name=?,
                       content_sha256=?, size_bytes=?, captured_at=?
                   WHERE photo_uid=?""",
                (
                    slot_name,
                    work_type,
                    device_name,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    row["photo_uid"],
                ),
            )

    # ── 事务辅助 ──

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        with self._lock:
            try:
                if immediate:
                    self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    @staticmethod
    def _command_received_at_now() -> tuple[str, datetime, str]:
        sampled = sample_clock()
        encoded = sampled.raw_observed_at
        if encoded is None:
            received_at = datetime.now(timezone.utc)
            encoded = received_at.isoformat(timespec="milliseconds").replace(
                "+00:00",
                "Z",
            )
        else:
            received_at = datetime.fromisoformat(
                encoded.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        return encoded, received_at, sampled.quality

    @staticmethod
    def _parse_command_received_at(value: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError("command received_at is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("command received_at is invalid") from error
        # Rows created before the factory-seal path stored SQLite's UTC
        # CURRENT_TIMESTAMP form without an explicit offset.  That field is
        # database-owned, so interpreting that legacy shape as UTC is safe.
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _new_uid(self) -> str:
        return str(_uuid.uuid4())

    def _next_seq(self, conn) -> int:
        row = conn.execute(
            "SELECT state_value FROM device_state WHERE state_key='edge_event_sequence'"
        ).fetchone()
        seq = int(row["state_value"] or 0) + 1
        conn.execute(
            "UPDATE device_state SET state_value=?, updated_at=? WHERE state_key='edge_event_sequence'",
            (str(seq), self._now()),
        )
        return seq

    # ── 原子事务 1: 接收命令 ──

    def receive_command(self, command_uid: str, command_type: str, payload: dict) -> str:
        stable = dict(payload)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(payload)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        received_at, _received_datetime, clock_quality = (
            self._command_received_at_now()
        )
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT canonical_sha256 FROM command_inbox WHERE command_uid=?",
                (command_uid,),
            ).fetchone()
            if existing:
                if existing["canonical_sha256"] != canonical_sha256:
                    logger.error("命令幂等冲突: %s", command_uid)
                    return "CONFLICT"
                logger.info("命令去重: %s", command_uid)
                return "DUPLICATE"
            conn.execute(
                """INSERT INTO command_inbox
                   (command_uid, command_type, payload_json,
                    canonical_sha256, received_at,
                    received_clock_quality)
                   VALUES (?,?,?,?,?,?)""",
                (
                    command_uid,
                    command_type,
                    _json.dumps(stored, ensure_ascii=False),
                    canonical_sha256,
                    received_at,
                    clock_quality,
                ),
            )
            return "ACCEPTED"

    def receive_factory_seal_command(self, command: dict) -> str:
        """Validate and durably accept a seal command at one trusted instant.

        A first delivery must still be valid when this transaction accepts
        it.  An exact duplicate may be validated against the original
        database-owned receipt time, allowing queue/restart recovery after
        ``expiresAt`` without accepting a newly expired command.
        """

        command_uid = command.get("commandUid")
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(command)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        with self.transaction(immediate=True):
            existing = self._conn.execute(
                """SELECT command_type, canonical_sha256, received_at,
                          received_clock_quality
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["command_type"] != "AUTHORIZE_FACTORY_SEAL"
                    or existing["canonical_sha256"] != canonical_sha256
                ):
                    logger.error("命令幂等冲突: %s", command_uid)
                    return "CONFLICT"
                acceptance_time = self._parse_command_received_at(
                    existing["received_at"]
                )
                validate_factory_seal_envelope_at_acceptance(
                    command,
                    acceptance_time,
                    acceptance_clock_quality=existing[
                        "received_clock_quality"
                    ],
                )
                logger.info("命令去重: %s", command_uid)
                return "DUPLICATE"

            received_at, acceptance_time, clock_quality = (
                self._command_received_at_now()
            )
            validate_factory_seal_envelope_at_acceptance(
                command,
                acceptance_time,
                acceptance_clock_quality=clock_quality,
            )
            self._conn.execute(
                """INSERT INTO command_inbox
                   (command_uid, command_type, payload_json,
                    canonical_sha256, received_at,
                    received_clock_quality)
                   VALUES (?, 'AUTHORIZE_FACTORY_SEAL', ?, ?, ?, ?)""",
                (
                    command_uid,
                    _json.dumps(stored, ensure_ascii=False),
                    canonical_sha256,
                    received_at,
                    clock_quality,
                ),
            )
            return "ACCEPTED"

    def validate_claimed_factory_seal_command(self, command: dict) -> None:
        """Revalidate a claimed seal using only its durable receipt fact."""

        command_uid = command.get("commandUid")
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        with self._lock:
            row = self._conn.execute(
                """SELECT command_type, canonical_sha256, state, received_at,
                          received_clock_quality
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
        if row is None or row["command_type"] != "AUTHORIZE_FACTORY_SEAL":
            raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")
        if row["canonical_sha256"] != canonical_sha256:
            raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")
        if row["state"] != "PROCESSING":
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        try:
            acceptance_time = self._parse_command_received_at(
                row["received_at"]
            )
            validate_factory_seal_envelope_at_acceptance(
                command,
                acceptance_time,
                acceptance_clock_quality=row[
                    "received_clock_quality"
                ],
            )
        except (TypeError, ValueError) as error:
            raise FactorySealError(
                "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID"
            ) from error

    def receive_rejected_command(
        self,
        command: dict,
        error_code: str,
    ) -> str:
        """Persist a valid, capability-rejected command without queueing it."""
        command_uid = command["commandUid"]
        command_type = command["commandType"]
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(command)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        received_at, _received_datetime, clock_quality = (
            self._command_received_at_now()
        )
        with self.transaction():
            existing = self._conn.execute(
                """SELECT command_type, canonical_sha256, state, last_error
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["command_type"] != command_type
                    or existing["canonical_sha256"] != canonical_sha256
                ):
                    return "CONFLICT"
                if existing["state"] == "PENDING":
                    changed = self._conn.execute(
                        """UPDATE command_inbox
                           SET state='REJECTED', processed_at=?,
                               processing_started_at=NULL, last_error=?
                           WHERE command_uid=? AND state='PENDING'""",
                        (self._now(), error_code, command_uid),
                    )
                    if changed.rowcount != 1:
                        return "CONFLICT"
                elif (
                    existing["state"] != "REJECTED"
                    or existing["last_error"] != error_code
                ):
                    return "CONFLICT"
            else:
                self._conn.execute(
                    """INSERT INTO command_inbox
                       (command_uid, command_type, payload_json,
                        canonical_sha256, state, received_at,
                        received_clock_quality, processed_at, last_error)
                       VALUES (?, ?, ?, ?, 'REJECTED', ?, ?, ?, ?)""",
                    (
                        command_uid,
                        command_type,
                        _json.dumps(stored, ensure_ascii=False),
                        canonical_sha256,
                        received_at,
                        clock_quality,
                        self._now(),
                        error_code,
                    ),
                )
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                return "CONFLICT"
            return "REJECTED"

    def accept_factory_seal_authorization(
        self,
        command: dict,
        local_facts: dict[str, str],
    ) -> dict[str, Any]:
        """Atomically authorize, complete, and emit the reliable ACCEPTED fact."""

        command_uid = command["commandUid"]
        payload = command["payload"]
        generation = payload["acceptanceGeneration"]
        command_sha256 = canonical_payload_sha256(
            {key: value for key, value in command.items() if key != "cosGrant"}
        )
        # BEGIN IMMEDIATE freezes the latest local acceptance evidence before
        # the first read and keeps another SQLite writer from inserting a
        # newer challenge between that check and authorization commit.
        with self.transaction(immediate=True):
            inbox = self._conn.execute(
                """SELECT command_type, canonical_sha256, state
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if (
                inbox is None
                or inbox["command_type"] != "AUTHORIZE_FACTORY_SEAL"
                or inbox["canonical_sha256"] != command_sha256
            ):
                raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")

            existing = self._conn.execute(
                """SELECT authorization_binding_sha256, state
                   FROM factory_seal_authorization WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing is not None:
                self._complete_factory_seal_command_in_tx(command)
                return {
                    "disposition": "DUPLICATE_AUTHORIZED",
                    "acceptanceGeneration": generation,
                    "authorizationBindingSha256": existing[
                        "authorization_binding_sha256"
                    ],
                    "state": existing["state"],
                }
            if inbox["state"] != "PROCESSING":
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")

            evidence_row = self._conn.execute(
                """SELECT event_uid, payload_json
                   FROM event_outbox
                   WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                     AND work_uid=?
                   ORDER BY edge_event_sequence DESC
                   LIMIT 1""",
                (payload["hardwareSn"],),
            ).fetchone()
            if evidence_row is None:
                raise FactorySealError("ACCEPTANCE_EVIDENCE_NOT_FOUND")
            if evidence_row["event_uid"] != payload["acceptanceEvidenceUid"]:
                raise FactorySealError("ACCEPTANCE_EVIDENCE_NOT_LATEST")
            try:
                evidence_event = _json.loads(evidence_row["payload_json"])
                evidence = evidence_event["payload"]
            except (KeyError, TypeError, ValueError):
                raise FactorySealError("ACCEPTANCE_EVIDENCE_INVALID") from None
            if (
                evidence_event.get("eventUid")
                != payload["acceptanceEvidenceUid"]
                or evidence_event.get("eventType")
                != "DEVICE_ACCEPTANCE_EVIDENCE"
                or evidence_event.get("payloadSha256")
                != payload["acceptanceEvidenceSha256"]
                or canonical_payload_sha256(evidence)
                != payload["acceptanceEvidenceSha256"]
                or evidence_event.get("target")
                != {"type": "DEVICE_ASSET", "uid": payload["hardwareSn"]}
                or evidence.get("evidenceSchemaVersion")
                not in FACTORY_SEAL_ACCEPTANCE_EVIDENCE_SCHEMA_VERSIONS
                or evidence.get("challengeUid")
                != payload["acceptanceChallengeUid"]
                or evidence.get("factoryBagRevision")
                != payload["factoryBagRevision"]
                or evidence.get("factoryBagSetSha256")
                != payload["factoryBagSetSha256"]
            ):
                raise FactorySealError("ACCEPTANCE_EVIDENCE_MISMATCH")

            highest = self._conn.execute(
                """SELECT command_uid, acceptance_generation
                   FROM factory_seal_authorization
                   ORDER BY acceptance_generation DESC LIMIT 1"""
            ).fetchone()
            if highest is not None and generation < highest["acceptance_generation"]:
                raise FactorySealError("FACTORY_SEAL_GENERATION_STALE")
            if highest is not None and generation == highest["acceptance_generation"]:
                raise FactorySealError("FACTORY_SEAL_GENERATION_CONFLICT")

            binding_values = {
                "commandUid": command_uid,
                "hardwareSn": payload["hardwareSn"],
                "acceptanceGeneration": generation,
                "acceptanceEvidenceUid": payload["acceptanceEvidenceUid"],
                "acceptanceChallengeUid": payload["acceptanceChallengeUid"],
                "acceptanceEvidenceSha256": payload[
                    "acceptanceEvidenceSha256"
                ],
                "factoryBagRevision": payload["factoryBagRevision"],
                "factoryBagSetSha256": payload["factoryBagSetSha256"],
                **local_facts,
            }
            binding = authorization_binding_sha256(binding_values)
            now = self._now()
            self._conn.execute(
                """UPDATE factory_seal_authorization
                   SET state='SUPERSEDED', last_error='NEWER_GENERATION'
                   WHERE state='AUTHORIZED'"""
            )
            self._conn.execute(
                """INSERT INTO factory_seal_authorization (
                       command_uid, command_sha256, hardware_sn,
                       acceptance_generation, evidence_event_uid,
                       acceptance_challenge_uid,
                       acceptance_evidence_sha256,
                       factory_bag_revision, factory_bag_set_sha256,
                       image_release_id, image_release_sha256,
                       factory_report_sha256,
                       authorization_binding_sha256, state, authorized_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             'AUTHORIZED', ?)""",
                (
                    command_uid,
                    command_sha256,
                    payload["hardwareSn"],
                    generation,
                    payload["acceptanceEvidenceUid"],
                    payload["acceptanceChallengeUid"],
                    payload["acceptanceEvidenceSha256"],
                    payload["factoryBagRevision"],
                    payload["factoryBagSetSha256"],
                    local_facts["imageReleaseId"],
                    local_facts["imageReleaseSha256"],
                    local_facts["factoryReportSha256"],
                    binding,
                    now,
                ),
            )
            self._complete_factory_seal_command_in_tx(command)
            return {
                "disposition": "AUTHORIZED",
                "acceptanceGeneration": generation,
                "authorizationBindingSha256": binding,
                "state": "AUTHORIZED",
            }

    def reject_factory_seal_command(
        self,
        command: dict,
        error_code: str,
    ) -> bool:
        """Atomically reject the command and retain its reliable observation."""

        if error_code not in FACTORY_SEAL_TERMINAL_ERROR_CODES:
            raise ValueError(
                "factory seal terminal error is not allow-listed"
            )
        with self.transaction():
            row = self._conn.execute(
                """SELECT command_type, state FROM command_inbox
                   WHERE command_uid=?""",
                (command["commandUid"],),
            ).fetchone()
            if row is None or row["command_type"] != "AUTHORIZE_FACTORY_SEAL":
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            if row["state"] not in {"PROCESSING", "REJECTED"}:
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            if row["state"] == "PROCESSING":
                updated = self._conn.execute(
                    """UPDATE command_inbox
                       SET state='REJECTED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (self._now(), error_code, command["commandUid"]),
                )
                if updated.rowcount != 1:
                    raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise FactorySealError("FACTORY_SEAL_OBSERVATION_CONFLICT")
            return row["state"] == "PROCESSING"

    def _complete_factory_seal_command_in_tx(self, command: dict) -> None:
        row = self._conn.execute(
            "SELECT state FROM command_inbox WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchone()
        if row is None:
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        if row["state"] == "PROCESSING":
            result = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    _json.dumps(
                        {
                            "acceptanceGeneration": command["payload"][
                                "acceptanceGeneration"
                            ],
                            "disposition": "FACTORY_SEAL_AUTHORIZED",
                        },
                        ensure_ascii=False,
                    ),
                    command["commandUid"],
                ),
            )
            if result.rowcount != 1:
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        elif row["state"] != "COMPLETED":
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        observation = self._record_command_observation_in_tx(
            self._conn,
            command,
            "ACCEPTED",
        )
        if observation == "CONFLICT":
            raise FactorySealError("FACTORY_SEAL_OBSERVATION_CONFLICT")

    def get_command(self, command_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM command_inbox WHERE command_uid=?", (command_uid,)
            ).fetchone()
        return self._decode_command_row(row)

    def list_orphan_permit_reconciliation_commands(self) -> list[dict]:
        """List restart-failed physical commands that never gained a slot."""

        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM command_inbox
                   WHERE state='FAILED' AND last_error='EDGE_RESTARTED'
                     AND command_type IN (
                       'START_DELIVERY_SESSION', 'START_CLEAN_OPERATION',
                       'SAMPLE_FULLNESS', 'MEASURE_EMPTY_BAG_BASELINE'
                     )
                   ORDER BY rowid"""
            ).fetchall()
        return [self._decode_command_row(row) for row in rows]

    def mark_orphan_permit_reconciled(self, command_uid: str) -> bool:
        with self.transaction():
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET last_error='EDGE_RESTARTED_BEFORE_PHYSICAL_START'
                   WHERE command_uid=? AND state='FAILED'
                     AND last_error='EDGE_RESTARTED'""",
                (command_uid,),
            )
            return updated.rowcount == 1

    def claim_next_command(self) -> Optional[dict]:
        with self.transaction():
            maintenance = self._conn.execute(
                """SELECT lock_type, owner_uid
                   FROM maintenance_lock WHERE singleton_id=1"""
            ).fetchone()
            if maintenance:
                # A firmware package fetch deliberately keeps maintenance
                # locked. Only the command owning that exact journal may be
                # reclaimed with fresh execution-only COS credentials; every
                # unrelated physical command remains blocked.
                if maintenance["lock_type"] != "MCU_FIRMWARE_UPDATE":
                    return None
                row = self._conn.execute(
                    """SELECT command_row.*
                       FROM command_inbox command_row
                       JOIN mcu_firmware_update firmware_update
                         ON firmware_update.command_uid=command_row.command_uid
                       WHERE firmware_update.update_uid=?
                         AND firmware_update.package_ready=0
                         AND firmware_update.state IN (
                           'QUEUED', 'PACKAGE_FETCH_FAILED'
                         )
                         AND command_row.command_type=
                           'START_MCU_FIRMWARE_UPDATE'
                         AND command_row.state='PENDING'
                       LIMIT 1""",
                    (maintenance["owner_uid"],),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT * FROM command_inbox
                       WHERE state='PENDING' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            if not row:
                return None
            now = self._now()
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PROCESSING', processing_started_at=?,
                       attempt_count=attempt_count+1, last_error=NULL
                   WHERE rowid=? AND state='PENDING'""",
                (now, row["rowid"]),
            )
            if updated.rowcount != 1:
                return None
            claimed = self._conn.execute(
                "SELECT * FROM command_inbox WHERE rowid=?", (row["rowid"],)
            ).fetchone()
            return self._decode_command_row(claimed)

    def mark_command_waiting_mcu(
        self,
        command_uid: str,
        mcu_command_uid: Optional[str],
        result: Optional[dict] = None,
    ) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    mcu_command_uid,
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def mark_command_recovery_required(
        self,
        command_uid: str,
        error_code: str,
        mcu_command_uid: Optional[str] = None,
        result: Optional[dict] = None,
    ) -> bool:
        """Keep an indeterminate physical command locked for reconciliation."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL,
                       processed_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    mcu_command_uid,
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    error_code,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def complete_command(self, command_uid: str, result: Optional[dict] = None) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?, processing_started_at=NULL,
                       result_json=?, last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def complete_command_with_work_context(
        self,
        command_uid: str,
        result: Optional[dict],
        *,
        work_uid: str,
        work_context: dict,
    ) -> bool:
        """Complete a control command and freeze job receipt facts atomically."""

        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(result, ensure_ascii=False)
                    if result
                    else None,
                    command_uid,
                ),
            )
            slot = self._conn.execute(
                """UPDATE work_slot SET context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_uid=?""",
                (
                    _json.dumps(work_context, ensure_ascii=False),
                    self._now(),
                    work_uid,
                ),
            )
            if slot.rowcount != 1:
                raise ValueError("active work context changed")
            return cur.rowcount == 1

    def complete_device_acceptance(
        self,
        command: dict,
        evidence_payload: dict,
    ) -> dict:
        """Atomically retain acceptance evidence and complete its command.

        The camera upload/readback happens before this transaction because it
        talks to COS.  Once those external checks finish, the reliable event
        and command terminal state must commit together so a process crash can
        never leave a completed command without its evidence event.
        """
        command_uid = command["commandUid"]
        with self.transaction():
            row = self._conn.execute(
                """SELECT state, command_type, result_json FROM command_inbox
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is None:
                raise ValueError("acceptance command is not persisted")
            if row["command_type"] != "REQUEST_DEVICE_ACCEPTANCE":
                raise ValueError("command is not a device acceptance request")
            if row["state"] == "COMPLETED":
                existing = self._conn.execute(
                    """SELECT payload_json FROM event_outbox
                       WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                         AND json_extract(payload_json, '$.commandUid')=?
                       ORDER BY edge_event_sequence DESC LIMIT 1""",
                    (command_uid,),
                ).fetchone()
                if existing is None:
                    raise ValueError(
                        "completed acceptance command has no evidence"
                    )
                return _json.loads(existing["payload_json"])
            if row["state"] != "PROCESSING":
                raise ValueError("acceptance command is not processing")

            event_uid = self._new_uid()
            sequence = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=command["targetDeviceName"],
                edge_event_sequence=sequence,
                event_type="DEVICE_ACCEPTANCE_EVIDENCE",
                target_type="DEVICE_ASSET",
                target_uid=command["targetDeviceName"],
                command_uid=command_uid,
                payload=evidence_payload,
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_ACCEPTANCE_EVIDENCE",
            )
            completion_result = {
                "challengeUid": evidence_payload["challengeUid"],
                "evidenceEventUid": event_uid,
                "disposition": "EVIDENCE_RECORDED",
            }
            # UART-v2 acceptance first parks behind a durable URL application
            # continuation. Keep that raw proof after the acceptance event is
            # committed; replacing result_json here would sever the evidence
            # from its original cloud command.
            if row["result_json"]:
                from native_device_entry_url import JOURNAL_KEY, validate_journal

                prior_result = _json.loads(row["result_json"])
                if isinstance(prior_result, dict) and JOURNAL_KEY in prior_result:
                    journal = validate_journal(prior_result[JOURNAL_KEY])
                    if (
                        journal["continuation"]
                        != "REQUEST_DEVICE_ACCEPTANCE"
                        or journal["sourceCommandUid"] != command_uid
                        or journal["state"] != "APPLIED"
                    ):
                        raise ValueError(
                            "acceptance URL application proof is not applied"
                        )
                    completion_result[JOURNAL_KEY] = journal
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    _json.dumps(completion_result, ensure_ascii=False),
                    command_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("acceptance command state changed")
            return event

    def fail_command(
        self,
        command_uid: str,
        error_code: str,
        *,
        retryable: bool = False,
    ) -> bool:
        state = "PENDING" if retryable else "FAILED"
        processed_at = None if retryable else self._now()
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state=?, processed_at=?, processing_started_at=NULL,
                       last_error=?
                   WHERE command_uid=?""",
                (state, processed_at, error_code, command_uid),
            )
            return cur.rowcount == 1

    def fail_command_and_observe(
        self,
        command: dict,
        error_code: str,
        *,
        stage: str,
        mcu_command_uid: Optional[str] = None,
        work_uid: Optional[str] = None,
        work_context: Optional[dict] = None,
    ) -> bool:
        """Atomically fail a command and create its stable observation."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    stage,
                    mcu_command_uid=mcu_command_uid,
                    error_code=error_code,
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            if work_context is not None:
                if not work_uid:
                    raise ValueError("work_uid is required with work_context")
                updated = self._conn.execute(
                    """UPDATE work_slot SET context_json=?, updated_at=?
                       WHERE slot_id=1 AND work_uid=?""",
                    (
                        _json.dumps(work_context, ensure_ascii=False),
                        self._now(),
                        work_uid,
                    ),
                )
                if updated.rowcount != 1:
                    raise ValueError("active work context changed")
            return cur.rowcount == 1

    def reject_claimed_command_and_observe(
        self,
        command: dict,
        error_code: str,
    ) -> bool:
        """Atomically reject a claimed command before any physical action."""

        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='REJECTED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if cur.rowcount != 1:
                return False
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise ValueError("command observation conflict")
            return True

    def fail_factory_seal_command_for_retry(
        self,
        command_uid: str,
        error_code: str,
    ) -> bool:
        """Park a locally repairable seal command until cloud redelivery.

        No terminal command observation is created here.  The backend keeps
        the immutable reliable task pending and redelivers the same command;
        only that duplicate delivery may move this allow-listed failure back
        to PENDING.  Restricting both command type and error code prevents this
        recovery seam from replaying physical or deterministically rejected
        commands.
        """

        if error_code not in FACTORY_SEAL_RETRYABLE_ERROR_CODES:
            raise ValueError(
                "factory seal retry error is not allow-listed"
            )
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?
                     AND command_type='AUTHORIZE_FACTORY_SEAL'
                     AND state='PROCESSING'""",
                (self._now(), error_code, command_uid),
            )
            return cur.rowcount == 1

    def requeue_failed_command(
        self,
        command_uid: str,
        expected_error: str,
    ) -> bool:
        """Requeue a stable command when fresh execution-only data arrives."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='FAILED'
                     AND last_error=?""",
                (command_uid, expected_error),
            )
            return cur.rowcount == 1

    def requeue_failed_factory_seal_command(
        self,
        command_uid: str,
    ) -> bool:
        """Requeue only a repairable seal failure on duplicate delivery."""

        placeholders = ",".join(
            "?" for _ in FACTORY_SEAL_RETRYABLE_ERROR_CODES
        )
        retryable_errors = tuple(
            sorted(FACTORY_SEAL_RETRYABLE_ERROR_CODES)
        )
        with self.transaction():
            cur = self._conn.execute(
                f"""UPDATE command_inbox
                    SET state='PENDING', processed_at=NULL,
                        processing_started_at=NULL, last_error=NULL
                    WHERE command_uid=? AND state='FAILED'
                      AND command_type='AUTHORIZE_FACTORY_SEAL'
                      AND last_error IN ({placeholders})""",
                (command_uid, *retryable_errors),
            )
            return cur.rowcount == 1

    def requeue_unknown_end_clean_before_unlock(
        self,
        command: dict[str, Any],
    ) -> bool:
        """Retry only the exact non-actuating clean cancellation intent.

        Physical commands in RECOVERY_REQUIRED must never be replayed.  This
        one command is a narrow exception because it only tells the MCU to
        abandon a clean state before any unlock was claimed.  Requeue it only
        while the inbox identity, active work slot, durable END intent and
        original MCU command identity all still agree inside one write
        transaction.
        """

        if command.get("commandType") != "END_CLEAN_BEFORE_UNLOCK":
            return False
        command_uid = command.get("commandUid")
        payload = command.get("payload")
        if not isinstance(command_uid, str) or not isinstance(payload, dict):
            return False
        stable = dict(command)
        stable.pop("cosGrant", None)
        expected_digest = canonical_payload_sha256(stable)
        with self.transaction(immediate=True):
            inbox = self._conn.execute(
                """SELECT command_type, canonical_sha256, state, last_error,
                          mcu_command_uid
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            slot = self._conn.execute(
                """SELECT work_type, work_uid, port_no, context_json
                   FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            if (
                inbox is None
                or inbox["command_type"] != "END_CLEAN_BEFORE_UNLOCK"
                or inbox["canonical_sha256"] != expected_digest
                or inbox["state"] != "RECOVERY_REQUIRED"
                or inbox["last_error"] != "UART_ACK_RESULT_UNKNOWN"
                or slot is None
                or slot["work_type"] != WORK_TYPE_CLEAN
                or slot["work_uid"] != payload.get("operationUid")
                or slot["port_no"] != payload.get("portNo")
                or not slot["context_json"]
            ):
                return False
            context = _json.loads(slot["context_json"])
            if not isinstance(context, dict):
                raise ValueError("active clean context is invalid")
            end_intent = context.get("end_before_unlock")
            exact_unknown_intent = (
                isinstance(end_intent, dict)
                and end_intent.get("command_uid") == command_uid
                and end_intent.get("reason") == payload.get("reason")
                and end_intent.get("state") == "RESULT_UNKNOWN"
                and isinstance(end_intent.get("mcu_command_uid"), str)
                and end_intent.get("mcu_command_uid")
                == inbox["mcu_command_uid"]
                and context.get("operation_uid")
                == payload.get("operationUid")
                and context.get("port_no") == payload.get("portNo")
                and context.get("phase")
                in {
                    "ENDING_BEFORE_UNLOCK",
                    "END_BEFORE_UNLOCK_RESULT_UNKNOWN",
                }
            )
            if not exact_unknown_intent:
                return False
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=?
                     AND command_type='END_CLEAN_BEFORE_UNLOCK'
                     AND canonical_sha256=?
                     AND state='RECOVERY_REQUIRED'
                     AND last_error='UART_ACK_RESULT_UNKNOWN'
                     AND mcu_command_uid=?""",
                (
                    command_uid,
                    expected_digest,
                    end_intent["mcu_command_uid"],
                ),
            )
            return updated.rowcount == 1

    def requeue_completed_photo_grant_command(
        self,
        command_uid: str,
    ) -> bool:
        """Allow fresh execution-only STS credentials for one stable request."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='COMPLETED'
                     AND command_type='PROVIDE_PHOTO_UPLOAD_GRANT'""",
                (command_uid,),
            )
            return cur.rowcount == 1

    def requeue_failed_mcu_firmware_command(
        self,
        command_uid: str,
    ) -> bool:
        """Retry package acquisition only when a fresh COS grant can help."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='FAILED'
                     AND command_type='START_MCU_FIRMWARE_UPDATE'
                     AND (
                       last_error='FIRMWARE_GRANT_NOT_AVAILABLE'
                       OR EXISTS (
                         SELECT 1 FROM mcu_firmware_update firmware_update
                         WHERE firmware_update.command_uid=command_inbox.command_uid
                           AND firmware_update.package_ready=0
                           AND firmware_update.state IN (
                             'QUEUED', 'PACKAGE_FETCH_FAILED'
                           )
                       )
                     )""",
                (command_uid,),
            )
            return cur.rowcount == 1

    @staticmethod
    def _valid_local_physical_action(
        record: Any,
        *,
        action_key: str,
        action_kind: str,
        action_uid: Optional[str],
    ) -> bool:
        if not isinstance(record, dict):
            return False
        digest = record.get("action_digest_sha256")
        return bool(
            isinstance(action_uid, str)
            and action_uid
            and record.get("action_uid") == action_uid
            and record.get("receipt_uid") == action_uid
            and record.get("action_key") == action_key
            and record.get("action_kind") == action_kind
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )

    @classmethod
    def _protected_command_matches_work_slot(
        cls,
        row: sqlite3.Row,
        command: Any,
        slot: Optional[sqlite3.Row],
        context: Any,
        native_command: Optional[dict] = None,
    ) -> bool:
        """Prove an interrupted command belongs to the retained safe lock.

        The permanent updater owns whether a protected physical action may run
        twice, while ``work_slot`` owns the current business identity.  Startup
        may retain a command only when the immutable inbox payload, slot,
        safety permit and local action receipt all name the same work.  A
        missing or malformed binding deliberately falls back to the historical
        FAILED/EDGE_RESTARTED handling; the occupied slot itself is never
        released here.
        """

        if not isinstance(command, dict) or slot is None:
            return False
        command_uid = row["command_uid"]
        command_type = row["command_type"]
        payload = command.get("payload")
        target = command.get("target")
        if not (
            command.get("commandUid") == command_uid
            and command.get("commandType") == command_type
            and isinstance(payload, dict)
            and isinstance(target, dict)
            and isinstance(context, dict)
        ):
            return False
        stable_command = dict(command)
        stable_command.pop("cosGrant", None)
        if canonical_payload_sha256(stable_command) != row["canonical_sha256"]:
            return False

        if (
            command_type == "MEASURE_EMPTY_BAG_BASELINE"
            and context.get("native_protocol") == 2
        ):
            # Native-v2 baseline owns no per-action permit map.  Its sole
            # permanent JobPermit and immutable MCU command are instead bound
            # directly into the active slot.  Keep exactly that shape in its
            # original PROCESSING/WAITING state so NativeBusinessRuntime can
            # continue query-only recovery after a Pi restart.
            work_uid = payload.get("measurementUid")
            native_uid = context.get("start_mcu_command_uid")
            safety = context.get("job_safety")
            if (
                not isinstance(native_command, dict)
                or native_command.get("message_name") != "MEASURE_BASELINE"
                or native_command.get("command_uid") != native_uid
                or native_command.get("conflict")
                or not isinstance(safety, dict)
            ):
                return False
            try:
                import uart2_protocol as uart2

                native = uart2.decode_payload(
                    "MEASURE_BASELINE",
                    native_command["payload"],
                )
            except (KeyError, TypeError, ValueError):
                return False
            stable_digest = canonical_payload_sha256(stable_command)
            return bool(
                isinstance(work_uid, str)
                and work_uid
                and slot["work_type"] == WORK_TYPE_BASELINE
                and slot["work_uid"] == work_uid
                and slot["port_no"] == payload.get("portNo")
                and slot["work_state"] in {"ACTIVE", "RECOVERY_REQUIRED"}
                and target.get("type") == "BASELINE_MEASUREMENT"
                and target.get("uid") == work_uid
                and payload.get("emptyBagConfirmed") is True
                and payload.get("measurementTimeoutMs") == 5000
                and context.get("phase") == "NATIVE_BASELINE_RUNNING"
                and context.get("start_command_uid") == command_uid
                and context.get("bag_uid") == payload.get("bagUid")
                and context.get("empty_bag_confirmed") is True
                and context.get("config") == payload.get("config")
                and safety
                == {
                    "permit_uid": command_uid,
                    "work_uid": work_uid,
                    "command_uid": command_uid,
                    "work_type": WORK_TYPE_BASELINE,
                    "request_digest_sha256": stable_digest,
                    "begin_uid": work_uid,
                }
                and native["mcuCommandUid"] == native_uid
                and native["measurementUid"] == work_uid
                and native["portNo"] == payload.get("portNo")
                and native["configVersion"]
                == payload.get("config", {}).get("version")
                and native["configContentSha256"]
                == payload.get("config", {}).get("contentSha256")
                and native["startExecutionWindowMs"] == 5000
                and native["measurementTimeoutMs"] == 5000
                and (
                    row["mcu_command_uid"] == native_uid
                    or (
                        row["state"] == "PROCESSING"
                        and row["mcu_command_uid"] is None
                    )
                )
                and (
                    native_command["write_claimed"]
                    or native_command["decision_outcome"] is None
                )
            )

        start_bindings = {
            "START_DELIVERY_SESSION": (
                WORK_TYPE_DELIVERY,
                "sessionUid",
                "session_uid",
                "DELIVERY:START:0",
                "START_DELIVERY_SESSION",
                "start_mcu_command_uid",
            ),
            "START_CLEAN_OPERATION": (
                WORK_TYPE_CLEAN,
                "operationUid",
                "operation_uid",
                "CLEAN:START:0",
                "START_CLEAN_OPERATION",
                "start_mcu_command_uid",
            ),
            "SAMPLE_FULLNESS": (
                WORK_TYPE_FULLNESS,
                "detectionUid",
                "detection_uid",
                "FULLNESS:SAMPLE:0",
                "SAMPLE_FULLNESS",
                "mcu_command_uid",
            ),
            "MEASURE_EMPTY_BAG_BASELINE": (
                WORK_TYPE_BASELINE,
                "measurementUid",
                "measurement_uid",
                "BASELINE:MEASURE:0",
                "MEASURE_EMPTY_BAG_BASELINE",
                "mcu_command_uid",
            ),
        }
        binding = start_bindings.get(command_type)
        if binding is not None:
            (
                work_type,
                payload_uid_key,
                context_uid_key,
                action_key,
                action_kind,
                action_uid_key,
            ) = binding
            work_uid = payload.get(payload_uid_key)
            if not (
                isinstance(work_uid, str)
                and work_uid
                and slot["work_type"] == work_type
                and slot["work_uid"] == work_uid
                and slot["port_no"] == payload.get("portNo")
                and target.get("uid") == work_uid
                and context.get(context_uid_key) == work_uid
                and context.get("port_no") == payload.get("portNo")
                and context.get(
                    "start_command_uid"
                    if work_type in {WORK_TYPE_DELIVERY, WORK_TYPE_CLEAN}
                    else "command_uid"
                )
                == command_uid
            ):
                return False
            safety = context.get("job_safety")
            if not isinstance(safety, dict):
                return False
            stable_digest = canonical_payload_sha256(stable_command)
            if not (
                safety.get("permit_uid") == command_uid
                and safety.get("work_uid") == work_uid
                and safety.get("command_uid") == command_uid
                and safety.get("work_type") == work_type
                and safety.get("begin_uid") == work_uid
                and safety.get("completion_uid") == command_uid
                and safety.get("request_digest_sha256") == stable_digest
                and isinstance(safety.get("actions"), dict)
            ):
                return False
            actions = safety["actions"]
            action_uid = context.get(action_uid_key)
            if (
                command_type == "MEASURE_EMPTY_BAG_BASELINE"
                and action_key not in actions
                and "BASELINE:FIXED_FRAME_QUERY:0" in actions
            ):
                action_key = "BASELINE:FIXED_FRAME_QUERY:0"
                action_uid = work_uid
            return cls._valid_local_physical_action(
                actions.get(action_key),
                action_key=action_key,
                action_kind=action_kind,
                action_uid=action_uid,
            )

        if not (
            slot["work_type"] == WORK_TYPE_CLEAN
            and slot["work_uid"] == payload.get("operationUid")
            and slot["port_no"] == payload.get("portNo")
            and target.get("uid") == slot["work_uid"]
            and context.get("operation_uid") == slot["work_uid"]
            and context.get("port_no") == slot["port_no"]
            and isinstance(context.get("job_safety"), dict)
        ):
            return False
        if command_type == "RESUME_CLEAN_OPERATION":
            action_key = context.get("resume_action_key")
            action_uid = context.get("resume_mcu_command_uid")
            actions = context["job_safety"].get("actions")
            return bool(
                context.get("resume_command_uid") == command_uid
                and payload.get("recoveryGeneration")
                == context.get("recovery_generation")
                and isinstance(action_key, str)
                and isinstance(actions, dict)
                and cls._valid_local_physical_action(
                    actions.get(action_key),
                    action_key=action_key,
                    action_kind="RESUME_CLEAN_OPERATION",
                    action_uid=action_uid,
                )
            )
        if command_type == "END_CLEAN_BEFORE_UNLOCK":
            intent = context.get("end_before_unlock")
            return bool(
                isinstance(intent, dict)
                and intent.get("command_uid") == command_uid
                and isinstance(intent.get("mcu_command_uid"), str)
                and intent.get("mcu_command_uid")
                and intent.get("reason") == payload.get("reason")
                and intent.get("state")
                in {"REQUESTED", "RESULT_UNKNOWN", "ACKED"}
                and context.get("phase")
                in {
                    "ENDING_BEFORE_UNLOCK",
                    "END_BEFORE_UNLOCK_RESULT_UNKNOWN",
                    "END_BEFORE_UNLOCK_ACKED",
                    "END_BEFORE_UNLOCK_CONFIRMED",
                }
            )
        return False

    def recover_interrupted_commands(
        self,
        *,
        physical_recovery_required: bool = True,
    ) -> dict[str, int]:
        """Requeue controls and, when enabled, lock exact physical work."""
        with self.transaction():
            config = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='APPLY_CONFIGURATION'"""
            ).rowcount
            remote_support = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type IN (
                       'OPEN_REMOTE_SUPPORT_TUNNEL',
                       'CLOSE_REMOTE_SUPPORT_TUNNEL'
                     )"""
            ).rowcount
            delivery_recovery = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type=
                       'QUARANTINE_DELIVERY_RECOVERY'"""
            ).rowcount
            acceptance = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL,
                       last_error='ACCEPTANCE_GRANT_NOT_AVAILABLE'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='REQUEST_DEVICE_ACCEPTANCE'""",
                (self._now(),),
            ).rowcount
            firmware = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL,
                       last_error='FIRMWARE_GRANT_NOT_AVAILABLE'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='START_MCU_FIRMWARE_UPDATE'""",
                (self._now(),),
            ).rowcount
            factory_seal = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='AUTHORIZE_FACTORY_SEAL'"""
            ).rowcount
            rows = self._conn.execute(
                """SELECT command_uid, command_type, canonical_sha256,
                          state, last_error, mcu_command_uid, payload_json
                   FROM command_inbox
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type NOT IN (
                         'APPLY_CONFIGURATION',
                         'REQUEST_DEVICE_ACCEPTANCE',
                          'START_MCU_FIRMWARE_UPDATE',
                          'OPEN_REMOTE_SUPPORT_TUNNEL',
                          'CLOSE_REMOTE_SUPPORT_TUNNEL',
                          'QUARANTINE_DELIVERY_RECOVERY',
                          'AUTHORIZE_FACTORY_SEAL'
                      )"""
            ).fetchall()
            slot = self._conn.execute(
                """SELECT work_type, work_uid, work_state, port_no,
                          context_json
                   FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            context = None
            if slot is not None and slot["context_json"]:
                try:
                    decoded_context = _json.loads(slot["context_json"])
                except (TypeError, ValueError):
                    decoded_context = None
                if isinstance(decoded_context, dict):
                    context = decoded_context
            physical_locked = 0
            physical_failed = 0
            for row in rows:
                command = _json.loads(row["payload_json"])
                native_command = None
                if (
                    row["command_type"] == "MEASURE_EMPTY_BAG_BASELINE"
                    and isinstance(context, dict)
                    and context.get("native_protocol") == 2
                    and isinstance(context.get("start_mcu_command_uid"), str)
                ):
                    try:
                        native_command = self._checked_native_command(
                            self._conn.execute(
                                "SELECT * FROM native_mcu_command WHERE command_uid=?",
                                (context["start_mcu_command_uid"],),
                            ).fetchone()
                        )
                    except ValueError:
                        native_command = None
                if (
                    physical_recovery_required
                    and self._protected_command_matches_work_slot(
                        row,
                        command,
                        slot,
                        context,
                        native_command,
                    )
                ):
                    if (
                        row["command_type"]
                        == "MEASURE_EMPTY_BAG_BASELINE"
                        and context.get("native_protocol") == 2
                    ):
                        # The native runtime's durable write fence, result
                        # receipt and permanent permit already define recovery.
                        # Re-labeling either row would destroy that state
                        # machine, so startup only counts and preserves it.
                        physical_locked += 1
                        continue
                    last_error = (
                        row["last_error"]
                        if row["state"] == "RECOVERY_REQUIRED"
                        and row["last_error"]
                        else "EDGE_RESTARTED_RESULT_UNKNOWN"
                    )
                    locked = self._conn.execute(
                        """UPDATE command_inbox
                           SET state='RECOVERY_REQUIRED', processed_at=NULL,
                               processing_started_at=NULL, last_error=?
                           WHERE command_uid=?
                             AND state IN (
                               'PROCESSING', 'WAITING_MCU_RESULT',
                               'RECOVERY_REQUIRED'
                             )""",
                        (last_error, row["command_uid"]),
                    ).rowcount
                    if locked:
                        self._conn.execute(
                            """UPDATE work_slot
                               SET work_state='RECOVERY_REQUIRED', updated_at=?
                               WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                            (
                                self._now(),
                                slot["work_type"],
                                slot["work_uid"],
                            ),
                        )
                    physical_locked += locked
                    continue
                if command.get("targetDeviceName"):
                    observation = self._record_command_observation_in_tx(
                        self._conn,
                        command,
                        "FAILED",
                        error_code="EDGE_RESTARTED",
                    )
                    if observation == "CONFLICT":
                        raise ValueError("command observation conflict")
                physical_failed += self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL,
                           last_error='EDGE_RESTARTED'
                       WHERE command_uid=?
                         AND state IN (
                             'PROCESSING', 'WAITING_MCU_RESULT',
                             'RECOVERY_REQUIRED'
                         )""",
                    (self._now(), row["command_uid"]),
                ).rowcount
            return {
                "configuration_requeued": config,
                "remote_support_requeued": remote_support,
                "delivery_recovery_requeued": delivery_recovery,
                "acceptance_grant_lost": acceptance,
                "firmware_grant_lost": firmware,
                "factory_seal_requeued": factory_seal,
                "physical_locked": physical_locked,
                "physical_failed": physical_failed,
            }

    @staticmethod
    def _decode_command_row(row) -> Optional[dict]:
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        if result.get("result_json"):
            result["result"] = _json.loads(result["result_json"])
        else:
            result["result"] = None
        return result

    # ── 配置应用与进度 ──

    def save_configuration_edge(
        self,
        command: dict,
        part_command_uids: list[str],
    ) -> str:
        payload = command["payload"]
        config = payload["config"]
        application_uid = payload["applicationUid"]
        version = config["version"]
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["config_version"] == version
                    and existing["content_sha256"] == config["contentSha256"]
                    and existing["mcu_payload_sha256"] == config["mcuPayloadSha256"]
                ):
                    return "DUPLICATE"
                return "CONFLICT"
            highest = self._conn.execute(
                """SELECT config_version, content_sha256, mcu_payload_sha256
                   FROM configuration_state
                   ORDER BY config_version DESC LIMIT 1"""
            ).fetchone()
            if highest is not None:
                if version < highest["config_version"]:
                    return "OUTDATED"
                if version == highest["config_version"]:
                    return "CONFLICT"

            now = self._now()
            self._conn.execute(
                """INSERT INTO configuration_state
                   (application_uid, command_uid, device_name, config_version,
                    content_sha256, mcu_payload_sha256, payload_json,
                    part_command_uids_json, state, edge_saved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    application_uid,
                    command["commandUid"],
                    command["targetDeviceName"],
                    version,
                    config["contentSha256"],
                    config["mcuPayloadSha256"],
                    _json.dumps(payload, ensure_ascii=False),
                    _json.dumps(part_command_uids),
                    "EDGE_SAVED",
                    now,
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=command["targetDeviceName"],
                command_uid=command["commandUid"],
                application_uid=application_uid,
                stage="EDGE_SAVED",
                version=version,
                content_sha256=config["contentSha256"],
                mcu_payload_sha256=config["mcuPayloadSha256"],
                edge_event_sequence=seq,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            self._upsert_state(self._conn, "saved_config_version", str(version), now)
            self._upsert_state(
                self._conn, "saved_config_content_sha256", config["contentSha256"], now
            )
            return "ACCEPTED"

    def get_configuration(self, application_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        result["part_command_uids"] = _json.loads(
            result.pop("part_command_uids_json")
        )
        return result

    def list_pending_configurations(self) -> list[dict]:
        """Read original configuration custody; do not requeue physical commands."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT application_uid FROM configuration_state
                   WHERE state IN ('EDGE_SAVED', 'WAITING_MCU_RESULT', 'RECOVERY_REQUIRED')
                   ORDER BY config_version, application_uid"""
            ).fetchall()
            return [self.get_configuration(row["application_uid"]) for row in rows]

    def get_latest_applied_configuration(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM configuration_state
                   WHERE state='APPLIED'
                   ORDER BY config_version DESC LIMIT 1"""
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        result["part_command_uids"] = _json.loads(
            result.pop("part_command_uids_json")
        )
        return result

    def apply_configuration_result(self, payload: dict) -> str:
        application_uid = payload.get("applicationUid")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row:
                return "UNKNOWN"
            if (
                row["config_version"] != payload.get("configVersion")
                or row["content_sha256"] != payload.get("contentSha256")
                or row["mcu_payload_sha256"] != payload.get("mcuPayloadSha256")
            ):
                return "CONFLICT"
            part_command_uids = _json.loads(row["part_command_uids_json"])
            if payload.get("mcuCommandUid") != part_command_uids[-1]:
                return "CONFLICT"
            desired_state = "APPLIED" if payload.get("status") == "APPLIED" else "FAILED"
            if row["state"] == desired_state:
                return "DUPLICATE"
            if row["state"] not in (
                "EDGE_SAVED",
                "WAITING_MCU_RESULT",
                "RECOVERY_REQUIRED",
            ):
                return "CONFLICT"

            error_code = None
            if desired_state == "FAILED":
                error_code = payload.get("faultCode") or "MCU_INTERNAL"
                if error_code == "NONE":
                    error_code = "MCU_INTERNAL"
            now = self._now()
            self._conn.execute(
                """UPDATE configuration_state
                   SET state=?, commit_mcu_command_uid=?, error_code=?, applied_at=?
                   WHERE application_uid=?""",
                (
                    desired_state,
                    payload.get("mcuCommandUid"),
                    error_code,
                    now,
                    application_uid,
                ),
            )
            command_state = "COMPLETED" if desired_state == "APPLIED" else "FAILED"
            self._conn.execute(
                """UPDATE command_inbox
                   SET state=?, processed_at=?, processing_started_at=NULL,
                       mcu_command_uid=?, last_error=?
                   WHERE command_uid=?""",
                (
                    command_state,
                    now,
                    payload.get("mcuCommandUid"),
                    error_code,
                    row["command_uid"],
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=row["device_name"],
                command_uid=row["command_uid"],
                application_uid=application_uid,
                stage=desired_state,
                version=row["config_version"],
                content_sha256=row["content_sha256"],
                mcu_payload_sha256=row["mcu_payload_sha256"],
                edge_event_sequence=seq,
                mcu_command_uid=payload.get("mcuCommandUid"),
                error_code=error_code,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            if desired_state == "APPLIED":
                self._upsert_state(
                    self._conn, "applied_config_version", str(row["config_version"]), now
                )
                self._upsert_state(
                    self._conn,
                    "applied_config_content_sha256",
                    row["content_sha256"],
                    now,
                )
                self._upsert_state(
                    self._conn,
                    "applied_config_mcu_payload_sha256",
                    row["mcu_payload_sha256"],
                    now,
                )
            return "ACCEPTED"

    def mark_configuration_waiting_mcu(
        self,
        application_uid: str,
        commit_mcu_command_uid: str,
    ) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE configuration_state
                   SET state='WAITING_MCU_RESULT', commit_mcu_command_uid=?
                   WHERE application_uid=? AND state='EDGE_SAVED'""",
                (commit_mcu_command_uid, application_uid),
            )
            return cur.rowcount == 1

    def mark_configuration_recovery_required(
        self,
        application_uid: str,
        error_code: str,
    ) -> bool:
        with self.transaction():
            row = self._conn.execute(
                "SELECT command_uid FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                """UPDATE configuration_state
                   SET state='RECOVERY_REQUIRED', error_code=?
                   WHERE application_uid=?""",
                (error_code, application_uid),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', processing_started_at=NULL,
                       last_error=?
                   WHERE command_uid=?""",
                (error_code, row["command_uid"]),
            )
            return True

    def fail_configuration_edge(
        self,
        application_uid: str,
        error_code: str,
        mcu_command_uid: Optional[str] = None,
    ) -> bool:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row or row["state"] == "FAILED":
                return False
            now = self._now()
            self._conn.execute(
                """UPDATE configuration_state
                   SET state='FAILED', error_code=?, commit_mcu_command_uid=?,
                       applied_at=?
                   WHERE application_uid=?""",
                (error_code, mcu_command_uid, now, application_uid),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?, processing_started_at=NULL,
                       mcu_command_uid=?, last_error=?
                   WHERE command_uid=?""",
                (
                    now,
                    mcu_command_uid,
                    error_code,
                    row["command_uid"],
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=row["device_name"],
                command_uid=row["command_uid"],
                application_uid=application_uid,
                stage="FAILED",
                version=row["config_version"],
                content_sha256=row["content_sha256"],
                mcu_payload_sha256=row["mcu_payload_sha256"],
                edge_event_sequence=seq,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            return True

    # ── MCU 关键事件可靠收件 ──

    def begin_mcu_receive_generation(self, mcu_boot_id: int) -> int:
        """Start a new Edge-local UART receive generation after HELLO."""
        if not isinstance(mcu_boot_id, int) or mcu_boot_id <= 0:
            raise ValueError("mcu_boot_id must be a positive integer")
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='mcu_receive_generation'"""
            ).fetchone()
            generation = int(row["state_value"] if row else 0) + 1
            now = self._now()
            self._upsert_state(
                self._conn,
                "mcu_receive_generation",
                str(generation),
                now,
            )
            self._upsert_state(
                self._conn,
                "active_mcu_boot_id",
                str(mcu_boot_id),
                now,
            )
            return generation

    def get_mcu_receive_generation(self) -> int:
        value = self.get_state("mcu_receive_generation", "0")
        return int(value or 0)

    def receive_mcu_frame(self, frame: dict) -> str:
        payload = frame.get("payload") or {}
        mcu_boot_id = payload.get("mcuBootId")
        event_sequence = payload.get("mcuEventSequence")
        if not isinstance(mcu_boot_id, int) or not isinstance(event_sequence, int):
            return "REJECTED"
        stable = {
            "messageName": frame.get("message_name"),
            "payload": payload,
        }
        content_sha256 = canonical_payload_sha256(stable)
        with self.transaction():
            generation_row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='mcu_receive_generation'"""
            ).fetchone()
            generation = int(
                generation_row["state_value"] if generation_row else 0
            )
            active_boot_row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='active_mcu_boot_id'"""
            ).fetchone()
            active_boot_id = (
                active_boot_row["state_value"] if active_boot_row else ""
            )
            if active_boot_id and int(active_boot_id) != mcu_boot_id:
                return "REJECTED"
            existing = self._conn.execute(
                """SELECT content_sha256 FROM mcu_event_inbox
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (generation, mcu_boot_id, event_sequence),
            ).fetchone()
            if existing:
                return (
                    "DUPLICATE"
                    if existing["content_sha256"] == content_sha256
                    else "CONFLICT"
                )
            historical_duplicate = self._conn.execute(
                """SELECT 1 FROM mcu_event_inbox
                   WHERE mcu_boot_id=? AND mcu_event_sequence=?
                     AND content_sha256=?
                   LIMIT 1""",
                (mcu_boot_id, event_sequence, content_sha256),
            ).fetchone()
            if historical_duplicate:
                return "DUPLICATE"
            self._conn.execute(
                """INSERT INTO mcu_event_inbox
                   (mcu_receive_generation, mcu_boot_id, mcu_event_sequence,
                    message_name, message_type, source_tx_sequence,
                    content_sha256, payload_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    generation,
                    mcu_boot_id,
                    event_sequence,
                    frame["message_name"],
                    frame["message_type"],
                    frame["tx_sequence"],
                    content_sha256,
                    _json.dumps(payload, ensure_ascii=False),
                ),
            )
            return "ACCEPTED"

    def list_pending_mcu_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM mcu_event_inbox
                   WHERE state='PENDING' ORDER BY rowid LIMIT ?""",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def mark_mcu_event_processed(
        self,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        mcu_receive_generation: Optional[int] = None,
    ) -> bool:
        with self.transaction():
            if mcu_receive_generation is None:
                mcu_receive_generation = self.get_mcu_receive_generation()
            cur = self._conn.execute(
                """UPDATE mcu_event_inbox
                   SET state='PROCESSED', processed_at=?, last_error=NULL
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (
                    self._now(),
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return cur.rowcount == 1

    def mark_mcu_event_failed(
        self,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        error: str,
        mcu_receive_generation: Optional[int] = None,
    ) -> bool:
        with self.transaction():
            if mcu_receive_generation is None:
                mcu_receive_generation = self.get_mcu_receive_generation()
            cur = self._conn.execute(
                """UPDATE mcu_event_inbox SET state='FAILED', last_error=?
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (
                    error,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return cur.rowcount == 1

    @staticmethod
    def _reject_archived_delivery_event(conn, event_type, *work_uids):
        if event_type != "DELIVERY_COMPLETE":
            return
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_delivery_issue'").fetchone():
            return
        for uid in work_uids:
            if uid is not None and conn.execute("SELECT 1 FROM native_delivery_issue WHERE work_uid=?", (uid,)).fetchone():
                raise ValueError("native delivery is archived; normal completion is forbidden")

    @staticmethod
    def _insert_event(conn, event: dict, event_type: str) -> None:
        EdgeStore._reject_archived_delivery_event(conn, event_type,
            event["target"]["uid"], event.get("payload", {}).get("sessionUid"))
        conn.execute(
            """INSERT INTO event_outbox
               (event_uid, edge_event_sequence, event_type, payload_json, work_uid)
               VALUES (?,?,?,?,?)""",
            (
                event["eventUid"],
                event["edgeEventSequence"],
                event_type,
                _json.dumps(event, ensure_ascii=False),
                event["target"]["uid"],
            ),
        )

    def _record_command_observation_in_tx(
        self,
        conn,
        command: dict,
        stage: str,
        *,
        mcu_command_uid: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> str:
        payload = {
            "observedCommandType": command["commandType"],
            "stage": stage,
            "mcuCommandUid": mcu_command_uid,
            "errorCode": error_code,
        }
        canonical_sha256 = canonical_payload_sha256(payload)
        error_identity = error_code if error_code is not None else ""
        existing = conn.execute(
            """SELECT event_uid, canonical_sha256
               FROM command_observation
               WHERE command_uid=? AND stage=? AND error_code=?""",
            (command["commandUid"], stage, error_identity),
        ).fetchone()
        if existing:
            return (
                "DUPLICATE"
                if existing["canonical_sha256"] == canonical_sha256
                else "CONFLICT"
            )
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        event = build_event_envelope(
            event_uid=event_uid,
            device_name=command["targetDeviceName"],
            edge_event_sequence=sequence,
            event_type="DEVICE_COMMAND_OBSERVED",
            target_type="DEVICE_COMMAND",
            target_uid=command["commandUid"],
            command_uid=command["commandUid"],
            payload=payload,
        )
        self._insert_event(
            conn,
            event,
            "DEVICE_COMMAND_OBSERVED",
        )
        conn.execute(
            """INSERT INTO command_observation
               (command_uid, stage, error_code, event_uid,
                canonical_sha256)
               VALUES (?, ?, ?, ?, ?)""",
            (
                command["commandUid"],
                stage,
                error_identity,
                event_uid,
                canonical_sha256,
            ),
        )
        return "ACCEPTED"

    def record_command_observation(
        self,
        command: dict,
        stage: str,
        *,
        mcu_command_uid: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> str:
        with self.transaction():
            return self._record_command_observation_in_tx(
                self._conn,
                command,
                stage,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
            )

    @staticmethod
    def _upsert_state(conn, key: str, value: str, now: str) -> None:
        conn.execute(
            """INSERT INTO device_state (state_key, state_value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(state_key) DO UPDATE
               SET state_value=excluded.state_value, updated_at=excluded.updated_at""",
            (key, value, now),
        )

    # ── 工单槽操作 ──

    def begin_mcu_firmware_update(
        self,
        *,
        update_uid: str,
        deployment_uid: str,
        source: str,
        package_path: str,
        package_sha256: str,
        manifest: dict,
        command_uid: Optional[str] = None,
        legacy_preflight: bool = False,
        allow_downgrade: bool = False,
        requested_reason: Optional[str] = None,
        package_ready: bool = True,
        device_name: Optional[str] = None,
    ) -> str:
        """Journal an update and atomically exclude physical business work."""
        _require_uuid4_local(update_uid, "update_uid")
        _require_uuid4_local(deployment_uid, "deployment_uid")
        if command_uid is not None:
            _require_uuid4_local(command_uid, "command_uid")
        if source not in {"CLOUD", "LOCAL"}:
            raise ValueError("MCU update source must be CLOUD or LOCAL")
        if not os.path.isabs(package_path):
            raise ValueError("MCU package path must be absolute")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        if not isinstance(legacy_preflight, bool):
            raise ValueError("legacy_preflight must be boolean")
        if not isinstance(allow_downgrade, bool):
            raise ValueError("allow_downgrade must be boolean")
        if not isinstance(package_ready, bool):
            raise ValueError("package_ready must be boolean")

        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = self._now()
        with self.transaction():
            existing = self._conn.execute(
                """SELECT update_uid, package_sha256, manifest_json,
                          legacy_preflight, allow_downgrade, package_ready
                   FROM mcu_firmware_update WHERE deployment_uid=?""",
                (deployment_uid,),
            ).fetchone()
            if existing:
                same = (
                    existing["package_sha256"] == package_sha256
                    and existing["manifest_json"] == manifest_json
                    and bool(existing["legacy_preflight"])
                    == legacy_preflight
                    and bool(existing["allow_downgrade"])
                    == allow_downgrade
                    and bool(existing["package_ready"]) == package_ready
                )
                return "DUPLICATE" if same else "CONFLICT"

            maintenance = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if maintenance:
                return "MAINTENANCE_BUSY"
            slot = self._conn.execute(
                "SELECT work_type FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if slot and slot["work_type"] != WORK_TYPE_NONE:
                return "WORK_BUSY"
            if command_uid is None:
                blocking_command = self._conn.execute(
                    """SELECT command_uid FROM command_inbox
                       WHERE state IN (
                         'PROCESSING', 'WAITING_MCU_RESULT',
                         'RECOVERY_REQUIRED'
                       ) LIMIT 1"""
                ).fetchone()
            else:
                blocking_command = self._conn.execute(
                    """SELECT command_uid FROM command_inbox
                       WHERE state IN (
                         'PROCESSING', 'WAITING_MCU_RESULT',
                         'RECOVERY_REQUIRED'
                       ) AND command_uid<>? LIMIT 1""",
                    (command_uid,),
                ).fetchone()
            if blocking_command:
                return "COMMAND_BUSY"

            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """INSERT INTO mcu_firmware_update (
                     update_uid, deployment_uid, command_uid, source,
                     package_path, package_sha256, manifest_json,
                     package_ready, state,
                     legacy_preflight, allow_downgrade, requested_reason,
                     previous_package_path, previous_package_sha256,
                     previous_manifest_json, requested_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update_uid,
                    deployment_uid,
                    command_uid,
                    source,
                    package_path,
                    package_sha256,
                    manifest_json,
                    int(package_ready),
                    int(legacy_preflight),
                    int(allow_downgrade),
                    requested_reason,
                    stable["current_package_path"] if stable else None,
                    stable["current_package_sha256"] if stable else None,
                    stable["current_manifest_json"] if stable else None,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """INSERT INTO maintenance_lock (
                     singleton_id, lock_type, owner_uid, detail_json,
                     acquired_at, updated_at
                   ) VALUES (1, 'MCU_FIRMWARE_UPDATE', ?, ?, ?, ?)""",
                (
                    update_uid,
                    _json.dumps(
                        {"deploymentUid": deployment_uid},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="QUEUED",
                )
            return "ACCEPTED"

    @staticmethod
    def _decode_mcu_update_row(row) -> Optional[dict]:
        if row is None:
            return None
        result = dict(row)
        result["manifest"] = _json.loads(result.pop("manifest_json"))
        previous = result.pop("previous_manifest_json")
        result["previous_manifest"] = (
            _json.loads(previous) if previous else None
        )
        prepare_identity = result.pop("prepare_identity_json")
        result["prepare_identity"] = (
            _json.loads(prepare_identity) if prepare_identity else None
        )
        result["prepare_recovery_required"] = bool(
            result["prepare_recovery_required"]
        )
        result["legacy_preflight"] = bool(result["legacy_preflight"])
        result["allow_downgrade"] = bool(result["allow_downgrade"])
        result["package_ready"] = bool(result["package_ready"])
        return result

    def get_mcu_firmware_update(self, update_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_mcu_firmware_update_by_deployment(
        self,
        deployment_uid: str,
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE deployment_uid=?",
                (deployment_uid,),
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_active_mcu_firmware_update(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT u.* FROM maintenance_lock l
                   JOIN mcu_firmware_update u ON u.update_uid=l.owner_uid
                   WHERE l.singleton_id=1
                     AND l.lock_type='MCU_FIRMWARE_UPDATE'"""
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_maintenance_lock(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        detail_json = result.pop("detail_json")
        result["detail"] = _json.loads(detail_json) if detail_json else None
        return result

    def get_mcu_firmware_state(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
        result = dict(row) if row else {"singleton_id": 1}
        for prefix in ("current", "previous"):
            raw = result.pop(f"{prefix}_manifest_json", None)
            result[f"{prefix}_manifest"] = _json.loads(raw) if raw else None
        return result

    def record_mcu_firmware_package_acquisition_attempt(
        self,
        update_uid: str,
        *,
        attempt_limit: int,
    ) -> Optional[int]:
        """Start one package attempt, or return ``None`` at the total limit."""
        if (
            not isinstance(attempt_limit, int)
            or isinstance(attempt_limit, bool)
            or attempt_limit <= 0
        ):
            raise ValueError("package acquisition attempt limit is invalid")
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                """SELECT package_ready, state,
                          package_acquisition_attempt_count
                   FROM mcu_firmware_update WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or bool(update["package_ready"])
                or update["state"] not in {
                    "QUEUED",
                    "PACKAGE_FETCH_FAILED",
                }
            ):
                raise ValueError("MCU package acquisition is not active")
            previous = int(
                update["package_acquisition_attempt_count"]
            )
            if previous >= attempt_limit:
                return None
            current = previous + 1
            changed = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET package_acquisition_attempt_count=?, state='QUEUED',
                       last_error_code=NULL, last_error_message=NULL,
                       updated_at=?
                   WHERE update_uid=?""",
                (current, now, update_uid),
            )
            if changed.rowcount != 1:
                raise RuntimeError("MCU package attempt journal update lost")
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return current

    def reject_mcu_firmware_update_before_start(
        self,
        *,
        update_uid: str,
        deployment_uid: str,
        command_uid: str,
        package_sha256: str,
        manifest: dict,
        error_code: str,
        error_message: str,
        device_name: str,
        requested_reason: Optional[str] = None,
    ) -> str:
        """Persist a reliable terminal refusal without taking maintenance."""
        _require_uuid4_local(update_uid, "update_uid")
        _require_uuid4_local(deployment_uid, "deployment_uid")
        _require_uuid4_local(command_uid, "command_uid")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        if not device_name:
            raise ValueError("device name is required for MCU rejection")
        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        package_path = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.abspath(self.db_path)),
                "mcu-firmware-rejected",
                f"{package_sha256}.efw",
            )
        )
        now = self._now()
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE deployment_uid=?",
                (deployment_uid,),
            ).fetchone()
            if existing:
                same = (
                    existing["command_uid"] == command_uid
                    and existing["package_sha256"] == package_sha256
                    and existing["manifest_json"] == manifest_json
                    and existing["state"] == "REJECTED"
                    and existing["last_error_code"] == error_code
                )
                return "DUPLICATE" if same else "CONFLICT"
            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """INSERT INTO mcu_firmware_update (
                     update_uid, deployment_uid, command_uid, source,
                     package_path, package_sha256, manifest_json,
                     package_ready, state, legacy_preflight,
                     allow_downgrade, requested_reason,
                     previous_package_path, previous_package_sha256,
                     previous_manifest_json, last_error_code,
                     last_error_message, requested_at, updated_at,
                     completed_at
                   ) VALUES (?, ?, ?, 'CLOUD', ?, ?, ?, 0, 'REJECTED',
                             0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update_uid,
                    deployment_uid,
                    command_uid,
                    package_path,
                    package_sha256,
                    manifest_json,
                    requested_reason,
                    stable["current_package_path"] if stable else None,
                    stable["current_package_sha256"] if stable else None,
                    stable["current_manifest_json"] if stable else None,
                    error_code,
                    error_message,
                    now,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=? AND state='PROCESSING'""",
                (now, error_code, command_uid),
            )
            self._create_mcu_firmware_progress_event_in_tx(
                self._conn,
                device_name=device_name,
                update_uid=update_uid,
                stage="REJECTED",
                error_code=error_code,
            )
            return "ACCEPTED"

    def attach_mcu_firmware_package(
        self,
        update_uid: str,
        *,
        package_path: str,
        package_sha256: str,
        manifest: dict,
        device_name: Optional[str] = None,
    ) -> str:
        """Atomically attach a verified package to its pre-I/O reservation."""
        if not os.path.isabs(package_path):
            raise ValueError("MCU package path must be absolute")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity_fields = (
            "releaseUid",
            "firmwareVersion",
            "firmwareVersionCode",
            "firmwareIdentityHex",
            "hardwareCompatibility",
            "fixedFrameRevision",
        )
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid or not update:
                return "NOT_ACTIVE"
            if update["source"] != "CLOUD":
                return "CONFLICT"
            reserved = _json.loads(update["manifest_json"])
            if (
                update["package_sha256"] != package_sha256
                or any(
                    reserved.get(field) != manifest.get(field)
                    for field in identity_fields
                )
            ):
                return "CONFLICT"
            if bool(update["package_ready"]):
                return "DUPLICATE" if update["manifest_json"] == manifest_json else "CONFLICT"
            if update["state"] not in {
                "QUEUED",
                "PACKAGE_FETCH_FAILED",
            }:
                return "CONFLICT"
            self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET package_path=?, package_sha256=?, manifest_json=?,
                       package_ready=1, state='QUEUED',
                       last_error_code=NULL, last_error_message=NULL,
                       updated_at=?
                   WHERE update_uid=?""",
                (
                    package_path,
                    package_sha256,
                    manifest_json,
                    now,
                    update_uid,
                ),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="QUEUED",
                )
            return "ACCEPTED"

    def fail_mcu_firmware_package_acquisition(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Keep the maintenance lock and expose a retryable package failure."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                """SELECT command_uid, package_ready
                   FROM mcu_firmware_update WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or bool(update["package_ready"])
            ):
                return False
            changed = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET state='PACKAGE_FETCH_FAILED', updated_at=?,
                       last_error_code=?, last_error_message=?
                   WHERE update_uid=?""",
                (now, error_code, error_message, update_uid),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if update["command_uid"]:
                # Publish the retry fact only after the owning inbox command
                # is durably retryable. This closes the race where a fresh
                # grant arrives before CommandProcessor handles the exception.
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (now, error_code, update["command_uid"]),
                )
            if device_name is not None:
                # Each fresh credential attempt must produce a fresh reliable
                # failure fact so the platform can wake the same command task
                # again. The immutable outbox event remains retained.
                self._conn.execute(
                    """DELETE FROM mcu_firmware_progress
                       WHERE update_uid=?
                         AND stage='PACKAGE_FETCH_FAILED'
                         AND target_attempt_count=0
                         AND rollback_attempt_count=0""",
                    (update_uid,),
                )
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="PACKAGE_FETCH_FAILED",
                    error_code=error_code,
                )
            return changed.rowcount == 1

    def transition_mcu_firmware_update(
        self,
        update_uid: str,
        state: str,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> bool:
        allowed = MCU_UPDATE_ACTIVE_STATES | MCU_UPDATE_TERMINAL_STATES
        if state not in allowed:
            raise ValueError("invalid MCU firmware update state")
        now = self._now()
        completed_at = now if state in MCU_UPDATE_TERMINAL_STATES else None
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET state=?, started_at=COALESCE(started_at, ?),
                       updated_at=?, completed_at=?, last_error_code=?,
                       last_error_message=?
                   WHERE update_uid=?""",
                (
                    state,
                    now,
                    now,
                    completed_at,
                    error_code,
                    error_message,
                    update_uid,
                ),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage=state,
                    error_code=error_code,
                )
            return updated.rowcount == 1

    def arm_mcu_firmware_prepare_recovery(
        self,
        update_uid: str,
        identity: dict,
    ) -> bool:
        """Persist the current application identity before any F2 execution."""

        identity_json = _canonical_mcu_prepare_identity(identity)
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET prepare_recovery_required=1, prepare_identity_json=?,
                        updated_at=?
                   WHERE update_uid=?
                     AND state IN (
                       'PREFLIGHT', 'PREPARED', 'FLASHING_TARGET',
                       'VERIFYING_TARGET', 'ROLLING_BACK',
                       'VERIFYING_ROLLBACK'
                     )""",
                (identity_json, now, update_uid),
            )
            if updated.rowcount != 1:
                return False
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return True

    def record_mcu_firmware_attempt(
        self,
        update_uid: str,
        *,
        rollback: bool,
        device_name: Optional[str] = None,
    ) -> int:
        state = "ROLLING_BACK" if rollback else "FLASHING_TARGET"
        column = (
            "rollback_attempt_count" if rollback else "target_attempt_count"
        )
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                raise ValueError("MCU update does not own the maintenance lock")
            self._conn.execute(
                f"""UPDATE mcu_firmware_update
                    SET {column}={column}+1, state=?,
                        started_at=COALESCE(started_at, ?), updated_at=?,
                        last_error_code=NULL, last_error_message=NULL
                    WHERE update_uid=?""",
                (state, now, now, update_uid),
            )
            row = self._conn.execute(
                f"SELECT {column} AS count FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage=state,
                )
            return int(row["count"])

    def complete_mcu_firmware_update(
        self,
        update_uid: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Promote the verified target, retain one prior stable package, unlock."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid or not update:
                return False
            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """UPDATE mcu_firmware_state SET
                     previous_package_path=?, previous_package_sha256=?,
                     previous_manifest_json=?, previous_installed_at=?,
                     current_package_path=?, current_package_sha256=?,
                     current_manifest_json=?, current_installed_at=?,
                     updated_at=? WHERE singleton_id=1""",
                (
                    stable["current_package_path"],
                    stable["current_package_sha256"],
                    stable["current_manifest_json"],
                    stable["current_installed_at"],
                    update["package_path"],
                    update["package_sha256"],
                    update["manifest_json"],
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """UPDATE mcu_firmware_update SET state='SUCCEEDED',
                     updated_at=?, completed_at=?, last_error_code=NULL,
                     last_error_message=NULL WHERE update_uid=?""",
                (now, now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="SUCCEEDED",
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return True

    def complete_mcu_firmware_rollback(
        self,
        update_uid: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Record successful restoration of the unchanged prior stable image."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update SET state='ROLLED_BACK',
                     updated_at=?, completed_at=? WHERE update_uid=?""",
                (now, now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="ROLLED_BACK",
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return updated.rowcount == 1

    def fail_mcu_firmware_update_locked(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Keep maintenance locked when neither target nor rollback is safe."""
        return self.transition_mcu_firmware_update(
            update_uid,
            "FAILED_LOCKED",
            error_code=error_code,
            error_message=error_message,
            device_name=device_name,
        )

    def reject_mcu_firmware_update(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Reject before touching flash and release the maintenance lock."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update SET state='REJECTED',
                     updated_at=?, completed_at=?, last_error_code=?,
                     last_error_message=? WHERE update_uid=?""",
                (now, now, error_code, error_message, update_uid),
            )
            command = self._conn.execute(
                """SELECT command_uid FROM mcu_firmware_update
                   WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if command and command["command_uid"]:
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (now, error_code, command["command_uid"]),
                )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="REJECTED",
                    error_code=error_code,
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return updated.rowcount == 1

    def retry_failed_mcu_firmware_update(
        self,
        update_uid: str,
        *,
        rollback_only: bool,
        reason: str,
    ) -> bool:
        """Re-arm a failed locked journal through an explicit local action."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("manual MCU update retry reason is required")
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or update["state"] != "FAILED_LOCKED"
            ):
                return False
            if rollback_only and (
                not update["previous_package_path"]
                or not update["previous_package_sha256"]
                or not update["previous_manifest_json"]
            ):
                raise ValueError("no stable rollback package is available")
            state = "ROLLING_BACK" if rollback_only else "QUEUED"
            self._conn.execute(
                """UPDATE mcu_firmware_update SET state=?,
                     target_attempt_count=0, rollback_attempt_count=0,
                     requested_reason=?, last_error_code=NULL,
                     last_error_message=NULL, completed_at=NULL,
                     updated_at=? WHERE update_uid=?""",
                (state, reason.strip()[:512], now, update_uid),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return True

    def create_mcu_firmware_progress_event(
        self,
        *,
        device_name: str,
        update_uid: str,
        stage: str,
        error_code: Optional[str] = None,
    ) -> str:
        """Persist one idempotent reliable progress fact for OneNet upload."""
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        with self.transaction():
            return self._create_mcu_firmware_progress_event_in_tx(
                self._conn,
                device_name=device_name,
                update_uid=update_uid,
                stage=stage,
                error_code=error_code,
            )

    def _create_mcu_firmware_progress_event_in_tx(
        self,
        conn,
        *,
        device_name: str,
        update_uid: str,
        stage: str,
        error_code: Optional[str] = None,
    ) -> str:
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        update = conn.execute(
            "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
            (update_uid,),
        ).fetchone()
        if update is None or update["state"] != stage:
            raise ValueError("MCU progress stage differs from journal")
        key = (
            update_uid,
            stage,
            update["target_attempt_count"],
            update["rollback_attempt_count"],
        )
        existing = conn.execute(
            """SELECT event_uid FROM mcu_firmware_progress
               WHERE update_uid=? AND stage=?
                 AND target_attempt_count=?
                 AND rollback_attempt_count=?""",
            key,
        ).fetchone()
        if existing:
            return "DUPLICATE"

        manifest = _json.loads(update["manifest_json"])
        installed_manifest = None
        if stage == "SUCCEEDED":
            installed_manifest = manifest
        elif stage == "ROLLED_BACK" and update["previous_manifest_json"]:
            installed_manifest = _json.loads(
                update["previous_manifest_json"]
            )
        elif stage in {
            "QUEUED",
            "PACKAGE_FETCH_FAILED",
            "PREFLIGHT",
            "PREPARED",
            "REJECTED",
        }:
            stable = conn.execute(
                """SELECT current_manifest_json
                   FROM mcu_firmware_state WHERE singleton_id=1"""
            ).fetchone()
            if stable and stable["current_manifest_json"]:
                installed_manifest = _json.loads(
                    stable["current_manifest_json"]
                )
        if stage in {
            "PACKAGE_FETCH_FAILED",
            "FAILED_LOCKED",
            "REJECTED",
        }:
            stable_error = error_code or update["last_error_code"]
            if not stable_error:
                raise ValueError("failed MCU progress requires an error code")
            progress_error = stable_error
        else:
            progress_error = None
        payload = {
            "deploymentUid": update["deployment_uid"],
            "updateUid": update_uid,
            "releaseUid": manifest["releaseUid"],
            "source": update["source"],
            "stage": stage,
            "firmwareVersion": manifest["firmwareVersion"],
            "firmwareVersionCode": manifest["firmwareVersionCode"],
            "firmwareIdentityHex": manifest["firmwareIdentityHex"],
            "fixedFrameRevision": manifest["fixedFrameRevision"],
            "targetAttemptCount": update["target_attempt_count"],
            "rollbackAttemptCount": update["rollback_attempt_count"],
            "legacyPreflight": bool(update["legacy_preflight"]),
            "downgradeAuthorized": bool(update["allow_downgrade"]),
            "installedFirmwareVersion": (
                installed_manifest["firmwareVersion"]
                if installed_manifest else None
            ),
            "installedFirmwareVersionCode": (
                installed_manifest["firmwareVersionCode"]
                if installed_manifest else None
            ),
            "installedFirmwareIdentityHex": (
                installed_manifest["firmwareIdentityHex"]
                if installed_manifest else None
            ),
            "errorCode": progress_error,
        }
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        event = build_event_envelope(
            event_uid=event_uid,
            device_name=device_name,
            edge_event_sequence=sequence,
            event_type="MCU_FIRMWARE_UPDATE_PROGRESS",
            target_type="MCU_FIRMWARE_DEPLOYMENT",
            target_uid=update["deployment_uid"],
            command_uid=update["command_uid"],
            delivery_class="RELIABLE_FACT",
            payload=payload,
        )
        self._insert_event(
            conn,
            event,
            "MCU_FIRMWARE_UPDATE_PROGRESS",
        )
        conn.execute(
            """INSERT INTO mcu_firmware_progress
               (update_uid, stage, target_attempt_count,
                rollback_attempt_count, event_uid, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (*key, event_uid, self._now()),
        )
        return "ACCEPTED"

    def reconcile_mcu_firmware_progress_events(
        self,
        device_name: str,
    ) -> int:
        """Backfill the current journal state after upgrading from schema v11."""
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        with self.transaction():
            rows = self._conn.execute(
                "SELECT update_uid, state FROM mcu_firmware_update"
            ).fetchall()
            created = 0
            for row in rows:
                disposition = self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=row["update_uid"],
                    stage=row["state"],
                )
                if disposition == "ACCEPTED":
                    created += 1
            return created

    def acquire_work_slot(
        self,
        work_type: str,
        work_uid: str,
        port_no: int,
        context: dict,
        *,
        observed_command: Optional[dict] = None,
        clean_restart_interlock_clearance: Optional[dict] = None,
    ) -> bool:
        with self.transaction():
            conn = self._conn
            maintenance = conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if maintenance:
                return False
            slot = conn.execute("SELECT work_type FROM work_slot WHERE slot_id=1").fetchone()
            if not slot or slot["work_type"] != WORK_TYPE_NONE:
                return False
            interlock_raw = self._clean_restart_interlock_raw_in_tx(
                conn,
                port_no,
            )
            interlock_active = self._clean_restart_interlock_value_active(
                interlock_raw,
            )
            if clean_restart_interlock_clearance is not None and (
                work_type not in {WORK_TYPE_DELIVERY, WORK_TYPE_CLEAN}
                or not interlock_active
            ):
                return False
            if (
                work_type in {WORK_TYPE_DELIVERY, WORK_TYPE_CLEAN}
                and interlock_active
            ):
                metadata = self._decode_clean_restart_interlock_metadata(
                    interlock_raw,
                )
                if (
                    metadata is None
                    or clean_restart_interlock_clearance != metadata
                    or context.get("start_bag_uid")
                    not in {
                        bag_uid
                        for bag_uid in (
                            metadata.get("oldBagUid"),
                            metadata.get("newBagUid"),
                        )
                        if isinstance(bag_uid, str) and bag_uid
                    }
                ):
                    return False
            conn.execute(
                "UPDATE work_slot SET work_type=?, work_uid=?, work_state='ACTIVE', port_no=?, context_json=?, updated_at=? WHERE slot_id=1",
                (work_type, work_uid, port_no, _json.dumps(context, ensure_ascii=False), self._now()),
            )
            if clean_restart_interlock_clearance is not None:
                cleared = conn.execute(
                    """UPDATE device_state
                       SET state_value='false', updated_at=?
                       WHERE state_key=? AND state_value=?""",
                    (
                        self._now(),
                        self._clean_restart_interlock_key(port_no),
                        interlock_raw,
                    ),
                )
                if cleared.rowcount != 1:
                    raise ValueError(
                        "clean restart interlock changed while acquiring work"
                    )
            if observed_command is not None:
                observation = self._record_command_observation_in_tx(
                    conn,
                    observed_command,
                    "ACCEPTED",
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            return True

    # ── 事件发件箱操作 ──

    def list_pending_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM event_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (
                       event_type='BUSINESS_CONFIRMATION_RECEIPT'
                       OR confirmed_at IS NULL
                     )
                     AND (
                       next_retry_at IS NULL
                       OR datetime(next_retry_at) <= datetime('now')
                     )
                   ORDER BY edge_event_sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recover_sending_events(self) -> int:
        """A process restart loses MQTT packet identities; resend by event UID."""
        with self.transaction():
            exists = self._conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='event_outbox'"""
            ).fetchone()
            if not exists:
                return 0
            repaired = self._conn.execute(
                """UPDATE event_outbox
                   SET state='CONFIRMED', mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE state<>'CONFIRMED'
                     AND confirmed_at IS NOT NULL
                     AND event_type<>'BUSINESS_CONFIRMATION_RECEIPT'"""
            ).rowcount
            if repaired:
                logger.warning(
                    "Repaired %d reliable event states from durable "
                    "business confirmations",
                    repaired,
                )
            return self._conn.execute(
                """UPDATE event_outbox
                   SET state='PENDING', mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE state='SENDING' AND tombstoned=0"""
            ).rowcount

    def count_pending_reliable_events(self) -> int:
        """Count unconfirmed RELIABLE_FACT rows, including in-flight rows."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT payload_json FROM event_outbox
                   WHERE state IN ('PENDING', 'SENDING')
                     AND tombstoned=0
                     AND confirmed_at IS NULL"""
            ).fetchall()
        count = 0
        for row in rows:
            try:
                envelope = _json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if envelope.get("deliveryClass") == "RELIABLE_FACT":
                count += 1
        return count

    def get_event(self, event_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
        return dict(row) if row else None

    def get_event_by_sequence(
        self,
        edge_event_sequence: int,
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_outbox "
                "WHERE edge_event_sequence=?",
                (edge_event_sequence,),
            ).fetchone()
        return dict(row) if row else None

    # ── 设备状态操作 ──

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_value FROM device_state WHERE state_key=?", (key,)
            ).fetchone()
        return row["state_value"] if row else default

    def get_state_record(self, key: str) -> Optional[dict]:
        """Return a state value together with its trusted SQLite write time."""
        with self._lock:
            row = self._conn.execute(
                """SELECT state_value, updated_at FROM device_state
                   WHERE state_key=?""",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction():
            self._conn.execute(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=?, updated_at=?""",
                (key, value, self._now(), value, self._now()),
            )

    def save_device_entry_url(
        self,
        url: str,
        sha256: str,
        issued_at: str,
    ) -> dict:
        """Atomically retain the newest platform-issued device entry URL."""
        _validate_device_entry_url(url, sha256)
        incoming_time = _parse_utc_instant(issued_at, "issued_at")
        incoming = {
            "deviceEntryUrl": url,
            "deviceEntryUrlSha256": sha256,
            "issuedAt": issued_at,
        }
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='device_entry_url'"""
            ).fetchone()
            if row:
                try:
                    current = _json.loads(row["state_value"])
                    _validate_device_entry_url(
                        current["deviceEntryUrl"],
                        current["deviceEntryUrlSha256"],
                    )
                    current_time = _parse_utc_instant(
                        current["issuedAt"],
                        "stored issuedAt",
                    )
                except (KeyError, TypeError, ValueError, _json.JSONDecodeError) as error:
                    raise RuntimeError(
                        "stored device entry URL is corrupt"
                    ) from error
                if incoming_time < current_time:
                    return {**current, "disposition": "STALE_IGNORED"}
                if incoming_time == current_time:
                    if current["deviceEntryUrlSha256"] != sha256:
                        raise ValueError(
                            "device entry URL conflicts at the same issuedAt"
                        )
                    return {**current, "disposition": "UNCHANGED"}

            serialized = _json.dumps(
                incoming,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            self._upsert_state(
                self._conn,
                "device_entry_url",
                serialized,
                self._now(),
            )
            return {**incoming, "disposition": "SAVED"}

    def get_device_entry_url(self) -> Optional[dict]:
        """Return the locally proven entry URL, or fail on corrupt state."""
        raw = self.get_state("device_entry_url")
        if not raw:
            return None
        try:
            record = _json.loads(raw)
            _validate_device_entry_url(
                record["deviceEntryUrl"],
                record["deviceEntryUrlSha256"],
            )
            _parse_utc_instant(record["issuedAt"], "stored issuedAt")
        except (KeyError, TypeError, ValueError, _json.JSONDecodeError) as error:
            raise RuntimeError("stored device entry URL is corrupt") from error
        return record

    def begin_native_device_entry_url_application(
        self,
        command: dict,
        journal: dict,
    ) -> dict:
        """Atomically park one cloud command behind its durable URL journal."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        validate_journal(journal)
        command_uid = command.get("commandUid")
        if (
            journal["sourceCommandUid"] != command_uid
            or journal["continuation"] != command.get("commandType")
        ):
            raise ValueError("device entry URL journal authority differs")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM command_inbox WHERE command_uid=?",
                (command_uid,),
            ).fetchone()
            if row is None or row["command_type"] not in {
                "SYNC_DEVICE_ENTRY_URL",
                "REQUEST_DEVICE_ACCEPTANCE",
            }:
                raise ValueError("device entry URL command is missing")
            stored_command = _json.loads(row["payload_json"])
            candidate_command = dict(command)
            if "cosGrant" in stored_command:
                candidate_command["cosGrant"] = None
            else:
                candidate_command.pop("cosGrant", None)
            if stored_command != candidate_command:
                raise ValueError("device entry URL command authority changed")
            if row["result_json"]:
                prior = _json.loads(row["result_json"])
                if not isinstance(prior, dict) or JOURNAL_KEY not in prior:
                    raise ValueError("device entry URL command result is corrupt")
                validate_journal(prior[JOURNAL_KEY])
                if (
                    prior[JOURNAL_KEY]["sourceCommandUid"] != command_uid
                    or prior[JOURNAL_KEY]["deviceEntryUrlSha256"]
                    != journal["deviceEntryUrlSha256"]
                ):
                    raise ValueError("device entry URL command journal conflicts")
                return prior[JOURNAL_KEY]
            if row["state"] != "PROCESSING":
                raise ValueError("device entry URL command is not processing")
            attempt = journal["attempt"]
            commit_uid = (
                attempt["commandUids"][-1]
                if attempt["commandUids"]
                else None
            )
            result = {
                "native_pending": True,
                "mcu_command_uid": commit_uid,
                JOURNAL_KEY: journal,
            }
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    commit_uid,
                    _json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    command_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("device entry URL command state changed")
            # A new required application supersedes any earlier proof, even
            # when the URL and MCU boot happen to be unchanged.  The new
            # command must earn its own terminal APPLIED result.
            self._conn.execute(
                "DELETE FROM device_state WHERE state_key=?",
                ("native_device_entry_url_applied_evidence",),
            )
            return journal

    def list_native_device_entry_url_applications(self) -> list[dict]:
        """Return validated nonterminal URL journals in command arrival order."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM command_inbox
                   WHERE command_type IN (
                       'SYNC_DEVICE_ENTRY_URL',
                       'REQUEST_DEVICE_ACCEPTANCE'
                   )
                     AND state='WAITING_MCU_RESULT'
                     AND COALESCE(last_error, '') !=
                         'WAITING_DEVICE_ENTRY_URL_RELOAD'
                   ORDER BY rowid"""
            ).fetchall()
        applications = []
        for row in rows:
            decoded = self._decode_command_row(row)
            result = decoded["result"]
            if not isinstance(result, dict) or JOURNAL_KEY not in result:
                raise ValueError("pending device entry URL journal is missing")
            journal = validate_journal(result[JOURNAL_KEY])
            if journal["state"] != "WAITING":
                raise ValueError("terminal device entry URL command remained waiting")
            applications.append({"command": decoded, "journal": journal})
        return applications

    def park_claimed_acceptance_for_device_entry_url_reload(
        self,
        command_uid: str,
        device_entry_url_sha256: str,
    ) -> bool:
        """Park an APPLIED acceptance until this UART link rewrites the HMI.

        The execution-only COS grant remains in the current CommandProcessor
        process.  A process restart deliberately fails this wait so the
        backend must provide fresh credentials; a current-link terminal URL
        result requeues the exact command below.
        """
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        if (
            not isinstance(device_entry_url_sha256, str)
            or len(device_entry_url_sha256) != 64
        ):
            raise ValueError("device entry URL reload digest is invalid")
        with self.transaction():
            row = self._conn.execute(
                """SELECT command_type, state, result_json
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if (
                row is None
                or row["command_type"] != "REQUEST_DEVICE_ACCEPTANCE"
                or row["state"] != "PROCESSING"
                or not row["result_json"]
            ):
                return False
            result = _json.loads(row["result_json"])
            if not isinstance(result, dict) or JOURNAL_KEY not in result:
                raise ValueError("acceptance URL application journal is missing")
            journal = validate_journal(result[JOURNAL_KEY])
            if (
                journal["state"] != "APPLIED"
                or journal["deviceEntryUrlSha256"]
                != device_entry_url_sha256
            ):
                raise ValueError("acceptance URL application is not applied")
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT',
                       processing_started_at=NULL,
                       last_error='WAITING_DEVICE_ENTRY_URL_RELOAD'
                   WHERE command_uid=? AND state='PROCESSING'""",
                (command_uid,),
            )
            return updated.rowcount == 1

    def requeue_native_acceptance_after_device_entry_url_reload(
        self,
        device_entry_url_sha256: str,
    ) -> int:
        """Resume only acceptance continuations parked for this URL digest."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        if (
            not isinstance(device_entry_url_sha256, str)
            or len(device_entry_url_sha256) != 64
        ):
            raise ValueError("device entry URL reload digest is invalid")
        resumed = 0
        with self.transaction():
            rows = self._conn.execute(
                """SELECT command_uid, result_json FROM command_inbox
                   WHERE command_type='REQUEST_DEVICE_ACCEPTANCE'
                     AND state='WAITING_MCU_RESULT'
                     AND last_error='WAITING_DEVICE_ENTRY_URL_RELOAD'
                   ORDER BY rowid"""
            ).fetchall()
            for row in rows:
                result = _json.loads(row["result_json"])
                if not isinstance(result, dict) or JOURNAL_KEY not in result:
                    raise ValueError(
                        "parked acceptance URL application journal is missing"
                    )
                journal = validate_journal(result[JOURNAL_KEY])
                if (
                    journal["state"] != "APPLIED"
                    or journal["deviceEntryUrlSha256"]
                    != device_entry_url_sha256
                ):
                    continue
                updated = self._conn.execute(
                    """UPDATE command_inbox
                       SET state='PENDING', processing_started_at=NULL,
                           last_error=NULL
                       WHERE command_uid=?
                         AND state='WAITING_MCU_RESULT'
                         AND last_error=
                             'WAITING_DEVICE_ENTRY_URL_RELOAD'""",
                    (row["command_uid"],),
                )
                resumed += updated.rowcount
        return resumed

    def find_native_device_entry_url_application(
        self,
        application_uid: str,
        commit_command_uid: str,
    ) -> Optional[dict]:
        """Find one current or terminal journal by both MCU identities."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM command_inbox
                   WHERE command_type IN (
                       'SYNC_DEVICE_ENTRY_URL',
                       'REQUEST_DEVICE_ACCEPTANCE'
                   ) AND result_json IS NOT NULL
                   ORDER BY rowid DESC"""
            ).fetchall()
        for row in rows:
            decoded = self._decode_command_row(row)
            result = decoded["result"]
            if not isinstance(result, dict) or JOURNAL_KEY not in result:
                continue
            journal = validate_journal(result[JOURNAL_KEY])
            attempt = journal["attempt"]
            if (
                attempt["applicationUid"] == application_uid
                and attempt["commandUids"]
                and attempt["commandUids"][-1] == commit_command_uid
            ):
                return {"command": decoded, "journal": journal}
        reload = self.get_native_device_entry_url_reload()
        if reload is not None:
            attempt = reload["attempt"]
            if (
                attempt["applicationUid"] == application_uid
                and attempt["commandUids"]
                and attempt["commandUids"][-1] == commit_command_uid
            ):
                return {"command": None, "journal": reload}
        return None

    def get_native_device_entry_url_reload(self) -> Optional[dict]:
        from native_device_entry_url import validate_journal

        raw = self.get_state("native_device_entry_url_reload")
        if not raw:
            return None
        try:
            return validate_journal(_json.loads(raw))
        except (TypeError, ValueError) as error:
            raise RuntimeError("stored device entry URL reload is corrupt") from error

    def get_native_device_entry_url_source_command_uid(
        self,
        device_entry_url: str,
        device_entry_url_sha256: str,
    ) -> Optional[str]:
        """Return the newest applied cloud authority for a local reload."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        _validate_device_entry_url(device_entry_url, device_entry_url_sha256)
        with self._lock:
            rows = self._conn.execute(
                """SELECT command_uid, result_json FROM command_inbox
                   WHERE command_type IN (
                       'SYNC_DEVICE_ENTRY_URL',
                       'REQUEST_DEVICE_ACCEPTANCE'
                   ) AND result_json IS NOT NULL
                   ORDER BY rowid DESC"""
            ).fetchall()
        for row in rows:
            result = _json.loads(row["result_json"])
            if not isinstance(result, dict) or JOURNAL_KEY not in result:
                continue
            journal = validate_journal(result[JOURNAL_KEY])
            if (
                journal["state"] == "APPLIED"
                and journal["deviceEntryUrl"] == device_entry_url
                and journal["deviceEntryUrlSha256"]
                == device_entry_url_sha256
            ):
                return row["command_uid"]
        return None

    def save_native_device_entry_url_reload(
        self,
        journal: dict,
        *,
        expected_application_uid: Optional[str] = None,
    ) -> bool:
        from native_device_entry_url import validate_journal

        validate_journal(journal)
        if journal["continuation"] != "LOCAL_RELOAD":
            raise ValueError("device entry URL reload continuation is invalid")
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='native_device_entry_url_reload'"""
            ).fetchone()
            if expected_application_uid is not None:
                if row is None:
                    return False
                current = validate_journal(_json.loads(row["state_value"]))
                if (
                    current["attempt"]["applicationUid"]
                    != expected_application_uid
                ):
                    return False
            self._upsert_state(
                self._conn,
                "native_device_entry_url_reload",
                _json.dumps(
                    journal,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                self._now(),
            )
            if journal["state"] == "WAITING":
                # Reconnecting the UART requires a fresh atomic HMI write.
                # Do not let the previous connection's APPLIED fact satisfy
                # acceptance while this replacement attempt is outstanding.
                self._conn.execute(
                    "DELETE FROM device_state WHERE state_key=?",
                    ("native_device_entry_url_applied_evidence",),
                )
            return True

    def replace_native_device_entry_url_journal(
        self,
        command_uid: str,
        expected_application_uid: str,
        journal: dict,
    ) -> bool:
        """Persist one boot rebase before any new UART-v2 command is created."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        validate_journal(journal)
        if journal["sourceCommandUid"] != command_uid:
            raise ValueError("rebased device entry URL authority differs")
        with self.transaction():
            row = self._conn.execute(
                """SELECT state, result_json FROM command_inbox
                   WHERE command_uid=? AND state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT'
                   )""",
                (command_uid,),
            ).fetchone()
            if row is None or not row["result_json"]:
                return False
            result = _json.loads(row["result_json"])
            current = validate_journal(result.get(JOURNAL_KEY))
            if current["attempt"]["applicationUid"] != expected_application_uid:
                return False
            result[JOURNAL_KEY] = journal
            result["mcu_command_uid"] = journal["attempt"]["commandUids"][-1]
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT', result_json=?,
                       mcu_command_uid=?, processing_started_at=NULL
                   WHERE command_uid=? AND state=?""",
                (
                    _json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    result["mcu_command_uid"],
                    command_uid,
                    row["state"],
                ),
            )
            return updated.rowcount == 1

    def save_native_device_entry_url_apply_result(
        self,
        payload: bytes,
        terminal: dict,
    ) -> str:
        """Persist raw MCU proof, URL evidence and command outcome atomically."""
        import uart2_protocol as uart2
        from native_device_entry_url import (
            JOURNAL_KEY,
            encode_command,
            validate_journal,
        )

        if not isinstance(payload, bytes):
            raise ValueError("device entry URL apply result must be immutable")
        values = uart2.decode_payload("DEVICE_ENTRY_URL_APPLY_RESULT", payload)
        validate_journal(terminal)
        raw_key = (
            "native_device_entry_url_result:"
            f"{values['mcuBootId']}:{values['mcuEventSequence']}"
        )
        raw_value = _json.dumps(
            {
                "messageName": "DEVICE_ENTRY_URL_APPLY_RESULT",
                "payloadHex": payload.hex(),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transaction():
            raw = self._conn.execute(
                "SELECT state_value FROM device_state WHERE state_key=?",
                (raw_key,),
            ).fetchone()
            if raw is not None and raw["state_value"] != raw_value:
                raise ValueError("device entry URL result identity conflict")
            if raw is None:
                self._upsert_state(self._conn, raw_key, raw_value, self._now())

            def require_complete_accepted_attempt(journal):
                attempt = journal["attempt"]
                for index, command_uid in enumerate(attempt["commandUids"]):
                    command_row = self._checked_native_command(
                        self._conn.execute(
                            """SELECT * FROM native_mcu_command
                               WHERE command_uid=?""",
                            (command_uid,),
                        ).fetchone()
                    )
                    if command_row is None:
                        raise ValueError(
                            "device entry URL result precedes its complete command set"
                        )
                    expected_name, expected_payload = encode_command(
                        journal,
                        index,
                        command_sequence=command_row["command_sequence"],
                    )
                    if (
                        command_row["message_name"] != expected_name
                        or command_row["payload"] != expected_payload
                        or command_row["mcu_boot_id"]
                        != attempt["targetMcuBootId"]
                        or command_row["decision_outcome"] != "ACCEPTED"
                        or command_row["decision_error"] != "NONE"
                        or command_row["conflict"]
                    ):
                        raise ValueError(
                            "device entry URL result lacks accepted original commands"
                        )

            rows = self._conn.execute(
                """SELECT * FROM command_inbox
                   WHERE command_type IN (
                       'SYNC_DEVICE_ENTRY_URL',
                       'REQUEST_DEVICE_ACCEPTANCE'
                   ) AND result_json IS NOT NULL
                   ORDER BY rowid DESC"""
            ).fetchall()
            matched = None
            existing_terminal = None
            for row in rows:
                result = _json.loads(row["result_json"])
                if not isinstance(result, dict) or JOURNAL_KEY not in result:
                    continue
                journal = validate_journal(result[JOURNAL_KEY])
                attempt = journal["attempt"]
                if (
                    attempt["applicationUid"] == values["applicationUid"]
                    and attempt["commandUids"]
                    and attempt["commandUids"][-1] == values["mcuCommandUid"]
                ):
                    matched = (row, result, journal)
                    if journal["state"] != "WAITING":
                        existing_terminal = journal
                    break
            if matched is None:
                reload_row = self._conn.execute(
                    """SELECT state_value FROM device_state
                       WHERE state_key='native_device_entry_url_reload'"""
                ).fetchone()
                if reload_row is None:
                    return "STALE"
                reload = validate_journal(_json.loads(reload_row["state_value"]))
                attempt = reload["attempt"]
                if not (
                    attempt["applicationUid"] == values["applicationUid"]
                    and attempt["commandUids"]
                    and attempt["commandUids"][-1] == values["mcuCommandUid"]
                ):
                    return "STALE"
                if reload["state"] != "WAITING":
                    return (
                        "DUPLICATE"
                        if reload["applyResultPayloadHex"] == payload.hex()
                        else "CONFLICT"
                    )
                if terminal["attempt"] != reload["attempt"]:
                    return "STALE"
                require_complete_accepted_attempt(reload)
                self._upsert_state(
                    self._conn,
                    "native_device_entry_url_reload",
                    _json.dumps(
                        terminal,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    self._now(),
                )
                evidence = terminal["appliedEvidence"]
                if terminal["state"] == "APPLIED":
                    active = self._conn.execute(
                        """SELECT state_value FROM device_state
                           WHERE state_key='device_entry_url'"""
                    ).fetchone()
                    if (
                        active is not None
                        and _json.loads(active["state_value"]).get(
                            "deviceEntryUrlSha256"
                        ) == terminal["deviceEntryUrlSha256"]
                    ):
                        self._upsert_state(
                            self._conn,
                            "native_device_entry_url_applied_evidence",
                            _json.dumps(
                                evidence,
                                ensure_ascii=True,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            self._now(),
                        )
                return terminal["state"]
            row, result, current = matched
            if existing_terminal is not None:
                if row["command_type"] == "SYNC_DEVICE_ENTRY_URL":
                    event = self._conn.execute(
                        """SELECT event_uid FROM event_outbox
                           WHERE event_type=
                               'DEVICE_ENTRY_URL_APPLICATION_RESULT'
                             AND json_extract(
                                 payload_json, '$.commandUid'
                             )=?""",
                        (row["command_uid"],),
                    ).fetchone()
                    if event is None:
                        raise ValueError(
                            "terminal URL sync lacks its reliable result event"
                        )
                return (
                    "DUPLICATE"
                    if existing_terminal["applyResultPayloadHex"] == payload.hex()
                    else "CONFLICT"
                )
            if terminal["attempt"] != current["attempt"]:
                return "STALE"
            require_complete_accepted_attempt(current)
            result[JOURNAL_KEY] = terminal
            evidence = terminal["appliedEvidence"]
            if terminal["state"] == "APPLIED":
                result.update({
                    "deviceEntryUrlSha256": terminal["deviceEntryUrlSha256"],
                    "disposition": "APPLIED",
                    "applicationUid": evidence["applicationUid"],
                    "mcuCommandUid": evidence["mcuCommandUid"],
                    "mcuBootId": evidence["mcuBootId"],
                    "basis": evidence["basis"],
                })
                state = (
                    "PENDING"
                    if row["command_type"] == "REQUEST_DEVICE_ACCEPTANCE"
                    else "COMPLETED"
                )
                processed_at = None if state == "PENDING" else self._now()
                last_error = None
                active = self._conn.execute(
                    """SELECT state_value FROM device_state
                       WHERE state_key='device_entry_url'"""
                ).fetchone()
                if active is not None:
                    active_url = _json.loads(active["state_value"])
                    if (
                        active_url.get("deviceEntryUrlSha256")
                        == terminal["deviceEntryUrlSha256"]
                    ):
                        self._upsert_state(
                            self._conn,
                            "native_device_entry_url_applied_evidence",
                            _json.dumps(
                                evidence,
                                ensure_ascii=True,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            self._now(),
                        )
            else:
                state = "FAILED"
                processed_at = self._now()
                last_error = "MCU_DEVICE_ENTRY_URL_" + evidence["faultCode"]
            if row["command_type"] == "SYNC_DEVICE_ENTRY_URL":
                cloud_command = _json.loads(row["payload_json"])
                device_name = cloud_command.get("targetDeviceName")
                if not isinstance(device_name, str) or not device_name:
                    raise ValueError("device entry URL sync lost its device")
                prior_event = self._conn.execute(
                    """SELECT event_uid, payload_json FROM event_outbox
                       WHERE event_type='DEVICE_ENTRY_URL_APPLICATION_RESULT'
                         AND json_extract(payload_json, '$.commandUid')=?""",
                    (row["command_uid"],),
                ).fetchone()
                event_payload = {
                    "applicationUid": evidence["applicationUid"],
                    "status": evidence["status"],
                    "deviceEntryUrlSha256": terminal["deviceEntryUrlSha256"],
                    "mcuCommandUid": evidence["mcuCommandUid"],
                    "mcuBootId": evidence["mcuBootId"],
                    "displayBasis": evidence["basis"],
                    "faultCode": evidence["faultCode"],
                }
                if prior_event is None:
                    event_uid = self._new_uid()
                    event = build_event_envelope(
                        event_uid=event_uid,
                        device_name=device_name,
                        edge_event_sequence=self._next_seq(self._conn),
                        event_type="DEVICE_ENTRY_URL_APPLICATION_RESULT",
                        target_type="DEVICE_ASSET",
                        target_uid=device_name,
                        command_uid=row["command_uid"],
                        payload=event_payload,
                    )
                    self._insert_event(
                        self._conn,
                        event,
                        "DEVICE_ENTRY_URL_APPLICATION_RESULT",
                    )
                else:
                    event_uid = prior_event["event_uid"]
                    prior = _json.loads(prior_event["payload_json"])
                    if (
                        prior.get("commandUid") != row["command_uid"]
                        or prior.get("payload") != event_payload
                    ):
                        raise ValueError(
                            "device entry URL result event conflicts"
                        )
                result["deviceEntryUrlApplicationEventUid"] = event_uid
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state=?, processed_at=?, processing_started_at=NULL,
                       result_json=?, last_error=?
                   WHERE command_uid=? AND state='WAITING_MCU_RESULT'""",
                (
                    state,
                    processed_at,
                    _json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    last_error,
                    row["command_uid"],
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("device entry URL command state changed")
            return terminal["state"]

    def save_unmatched_native_device_entry_url_apply_result(
        self,
        payload: bytes,
    ) -> str:
        """Retain a late valid result without attaching it to current work."""
        import uart2_protocol as uart2

        if not isinstance(payload, bytes):
            raise ValueError("device entry URL apply result must be immutable")
        values = uart2.decode_payload("DEVICE_ENTRY_URL_APPLY_RESULT", payload)
        key = (
            "native_device_entry_url_result:"
            f"{values['mcuBootId']}:{values['mcuEventSequence']}"
        )
        value = _json.dumps(
            {
                "messageName": "DEVICE_ENTRY_URL_APPLY_RESULT",
                "payloadHex": payload.hex(),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transaction():
            row = self._conn.execute(
                "SELECT state_value FROM device_state WHERE state_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                return "DUPLICATE" if row["state_value"] == value else "CONFLICT"
            self._upsert_state(self._conn, key, value, self._now())
            return "STALE"

    def get_native_device_entry_url_applied_evidence(self) -> Optional[dict]:
        """Return the latest proof that the MCU atomically queued the HMI write."""
        raw = self.get_state("native_device_entry_url_applied_evidence")
        if not raw:
            return None
        try:
            evidence = _json.loads(raw)
        except (TypeError, ValueError) as error:
            raise RuntimeError("stored device entry URL apply evidence is corrupt") from error
        required = {
            "deviceEntryUrlSha256",
            "applicationUid",
            "mcuCommandUid",
            "mcuBootId",
            "mcuEventSequence",
            "status",
            "faultCode",
            "basis",
        }
        if (
            not isinstance(evidence, dict)
            or set(evidence) != required
            or not isinstance(evidence["deviceEntryUrlSha256"], str)
            or len(evidence["deviceEntryUrlSha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in evidence["deviceEntryUrlSha256"]
            )
            or not isinstance(evidence["applicationUid"], str)
            or not isinstance(evidence["mcuCommandUid"], str)
            or type(evidence["mcuBootId"]) is not int
            or not 1 <= evidence["mcuBootId"] <= 9007199254740991
            or type(evidence["mcuEventSequence"]) is not int
            or not 1 <= evidence["mcuEventSequence"] <= 4294967295
            or evidence["status"] != "APPLIED"
            or evidence["faultCode"] is not None
            or evidence["basis"] != "UART3_COMMAND_ATOMICALLY_QUEUED"
        ):
            raise RuntimeError("stored device entry URL apply evidence is corrupt")
        try:
            if (
                str(_uuid.UUID(evidence["applicationUid"]))
                != evidence["applicationUid"]
                or not _uuid.UUID(evidence["applicationUid"]).int
                or str(_uuid.UUID(evidence["mcuCommandUid"]))
                != evidence["mcuCommandUid"]
                or not _uuid.UUID(evidence["mcuCommandUid"]).int
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "stored device entry URL apply evidence is corrupt"
            ) from error
        return evidence

    def get_native_device_entry_url_application_result(
        self,
        command_uid: str,
    ) -> Optional[dict]:
        """Expose one terminal raw MCU fact with both cloud/MCU identities."""
        from native_device_entry_url import JOURNAL_KEY, validate_journal

        command = self.get_command(command_uid)
        if command is None or command["command_type"] not in {
            "SYNC_DEVICE_ENTRY_URL",
            "REQUEST_DEVICE_ACCEPTANCE",
        }:
            return None
        result = command["result"]
        if not isinstance(result, dict) or JOURNAL_KEY not in result:
            return None
        journal = validate_journal(result[JOURNAL_KEY])
        if journal["state"] == "WAITING":
            return None
        return {
            "commandUid": command_uid,
            "mcuCommandUid": journal["attempt"]["commandUids"][-1],
            "applicationUid": journal["attempt"]["applicationUid"],
            "status": journal["appliedEvidence"]["status"],
            "displayBasis": journal["appliedEvidence"]["basis"],
            "faultCode": journal["appliedEvidence"]["faultCode"],
            "deviceEntryUrlSha256": journal["deviceEntryUrlSha256"],
            "mcuBootId": journal["appliedEvidence"]["mcuBootId"],
            "mcuEventSequence": journal["appliedEvidence"]["mcuEventSequence"],
            "rawPayloadHex": journal["applyResultPayloadHex"],
        }

    def recover_native_device_entry_url_commands(self) -> dict[str, int]:
        """Recover only nonphysical URL continuations after a Pi restart.

        SYNC can be reclaimed from its durable URL source.  Acceptance COS
        credentials are deliberately memory-only, so a processing acceptance
        must wait for the backend to supply a fresh grant.  A command already
        waiting on the MCU keeps waiting and is resumed by the foreground UART
        owner.
        """
        with self.transaction():
            sync = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE command_type='SYNC_DEVICE_ENTRY_URL'
                     AND state='PROCESSING'"""
            ).rowcount
            acceptance = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL,
                       last_error='ACCEPTANCE_GRANT_NOT_AVAILABLE'
                   WHERE command_type='REQUEST_DEVICE_ACCEPTANCE'
                     AND (
                         state='PROCESSING'
                         OR (
                             state='WAITING_MCU_RESULT'
                             AND last_error=
                                 'WAITING_DEVICE_ENTRY_URL_RELOAD'
                         )
                     )""",
                (self._now(),),
            ).rowcount
            return {
                "sync_requeued": sync,
                "acceptance_grant_lost": acceptance,
            }

    def save_fixed_frame_self_test(self, result: dict) -> bool:
        """Retain F0/F1 and report whether its smoke projection changed."""
        if not isinstance(result, dict):
            raise ValueError("fixed-frame self-test result must be an object")
        smoke_state = result.get("smokeState")
        smoke_health = result.get("smokeSensorHealth")
        fault_code = result.get("faultCode")
        legal = (
            smoke_state in {"NORMAL", "ALARM"}
            and smoke_health == "OK"
            and fault_code is None
        ) or (
            smoke_state == "UNKNOWN"
            and smoke_health in {
                "TIMEOUT",
                "SENSOR_FAULT",
                "PROTOCOL_ERROR",
            }
            and fault_code == "SMOKE_SENSOR"
        )
        if not legal:
            raise ValueError("invalid fixed-frame smoke projection")
        serialized = _json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transaction():
            now = self._now()
            smoke_values = {
                "smoke_state": smoke_state,
                "smoke_sensor_health": smoke_health,
                "smoke_fault_code": fault_code or "NONE",
                "port_1_smoke_state": smoke_state,
                "port_1_smoke_sensor_health": smoke_health,
                "port_1_smoke_fault_code": fault_code or "NONE",
            }
            placeholders = ",".join("?" for _ in smoke_values)
            rows = self._conn.execute(
                f"""SELECT state_key, state_value FROM device_state
                     WHERE state_key IN ({placeholders})""",
                tuple(smoke_values),
            ).fetchall()
            previous = {
                row["state_key"]: row["state_value"] for row in rows
            }
            projection_changed = any(
                previous.get(key) != value
                for key, value in smoke_values.items()
            )
            values = {
                "fixed_frame_latest_self_test_json": serialized,
                **smoke_values,
            }
            for key, value in values.items():
                self._upsert_state(self._conn, key, value, now)
            return projection_changed

    def get_or_create_edge_store_instance_uid(self) -> str:
        """Return the permanent identity of this freshly-created edge DB."""
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='edge_store_instance_uid'"""
            ).fetchone()
            if row:
                try:
                    parsed = _uuid.UUID(row["state_value"])
                except (ValueError, TypeError, AttributeError) as error:
                    raise RuntimeError(
                        "edge store instance identity is corrupt"
                    ) from error
                if parsed.version != 4 or str(parsed) != row["state_value"]:
                    raise RuntimeError(
                        "edge store instance identity is not UUIDv4"
                    )
                return str(parsed)
            instance_uid = str(_uuid.uuid4())
            self._upsert_state(
                self._conn,
                "edge_store_instance_uid",
                instance_uid,
                self._now(),
            )
            return instance_uid

    def get_edge_boot_id(self) -> str:
        return self.get_state("edge_boot_id")

    def set_edge_boot_id(self, boot_id: str) -> None:
        self.set_state("edge_boot_id", boot_id)

    def get_edge_event_sequence(self) -> int:
        return int(self.get_state("edge_event_sequence", "0"))

    def reserve_edge_event_sequence(self) -> int:
        """Atomically reserve one sequence for a direct telemetry event."""
        with self.transaction():
            return self._next_seq(self._conn)

    def save_mqtt_persistent_state(self, session_present: bool, reason_code: int) -> None:
        with self.transaction():
            now = self._now()
            self._conn.executemany(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=?, updated_at=?""",
                [
                    ("mqtt_session_present", "true" if session_present else "false", now,
                     "true" if session_present else "false", now),
                    ("mqtt_last_rc", str(reason_code), now, str(reason_code), now),
                ],
            )

    def record_safety_state_and_event(
        self,
        *,
        device_name: str,
        mcu_receive_generation: int,
        payload: dict,
    ) -> str:
        """Atomically project and publish one real UART safety change."""
        smoke_state = payload.get("smokeState")
        smoke_health = payload.get("smokeSensorHealth")
        fault_code = payload.get("faultCode")
        legal = (
            (
                smoke_state in {"NORMAL", "ALARM"}
                and smoke_health == "OK"
                and fault_code in (None, "NONE")
            )
            or (
                smoke_state == "UNKNOWN"
                and smoke_health != "OK"
                and fault_code == "SMOKE_SENSOR"
            )
        )
        if not legal:
            return "REJECTED"
        mcu_boot_id = payload.get("mcuBootId")
        mcu_event_sequence = payload.get("mcuEventSequence")
        if (
            not isinstance(mcu_boot_id, int)
            or mcu_boot_id <= 0
            or not isinstance(mcu_event_sequence, int)
            or mcu_event_sequence <= 0
        ):
            return "REJECTED"
        port_no = payload.get("portNo")
        if not isinstance(port_no, int) or port_no <= 0:
            port_no = None
        fixed_frame = payload.get("compatibilityMode") is True
        with self.transaction():
            existing = self._conn.execute(
                """SELECT event_uid FROM mcu_derived_event
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?
                     AND event_type='SAFETY_SENSOR_STATE_CHANGED'""",
                (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE mcu_event_inbox
                       SET state='PROCESSED', processed_at=?,
                           last_error=NULL
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?""",
                    (
                        self._now(),
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                )
                return "DUPLICATE"
            now = self._now()
            scope = (
                f"port_{port_no}" if port_no is not None else "device"
            )
            unchanged = False
            if fixed_frame:
                previous_row = self._conn.execute(
                    """SELECT state_value FROM device_state
                       WHERE state_key='fixed_frame_latest_smoke_json'"""
                ).fetchone()
                previous = None
                if previous_row:
                    try:
                        candidate = _json.loads(previous_row["state_value"])
                        previous = candidate if isinstance(candidate, dict) else None
                    except (TypeError, ValueError):
                        previous = None
                unchanged = bool(
                    previous
                    and previous.get("smokeState") == smoke_state
                    and previous.get("smokeSensorHealth") == smoke_health
                    and previous.get("faultCode")
                    == (None if fault_code in (None, "NONE") else fault_code)
                )
                self._upsert_state(
                    self._conn,
                    "fixed_frame_latest_smoke_json",
                    _json.dumps(
                        {
                            "smokeState": smoke_state,
                            "smokeSensorHealth": smoke_health,
                            "faultCode": (
                                None
                                if fault_code in (None, "NONE")
                                else fault_code
                            ),
                            "mcuBootId": mcu_boot_id,
                            "mcuEventSequence": mcu_event_sequence,
                            "rawFrameHex": payload.get("rawFrameHex"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_state",
                smoke_state,
                now,
            )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_sensor_health",
                smoke_health,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_state",
                smoke_state,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_sensor_health",
                smoke_health,
                now,
            )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_fault_code",
                "NONE" if fault_code in (None, "NONE") else fault_code,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_fault_code",
                "NONE" if fault_code in (None, "NONE") else fault_code,
                now,
            )
            if unchanged:
                self._conn.execute(
                    """UPDATE mcu_event_inbox
                       SET state='PROCESSED', processed_at=?,
                           last_error=NULL
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?""",
                    (
                        now,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                )
                return "UNCHANGED"
            event_uid = self._new_uid()
            sequence = self._next_seq(self._conn)
            work_type = payload.get("workType", "NONE")
            work_uid = (
                payload.get("workUid")
                if work_type != "NONE"
                else None
            )
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="SAFETY_SENSOR_STATE_CHANGED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "portNo": port_no,
                    "smokeState": smoke_state,
                    "smokeSensorHealth": smoke_health,
                    "faultCode": (
                        None
                        if fault_code in (None, "NONE")
                        else fault_code
                    ),
                    "workType": work_type,
                    "workUid": work_uid,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "SAFETY_SENSOR_STATE_CHANGED",
            )
            self._conn.execute(
                """INSERT INTO mcu_derived_event
                   (mcu_receive_generation, mcu_boot_id,
                    mcu_event_sequence, event_type, event_uid)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    "SAFETY_SENSOR_STATE_CHANGED",
                    event_uid,
                ),
            )
            self._conn.execute(
                """UPDATE mcu_event_inbox
                   SET state='PROCESSED', processed_at=?,
                       last_error=NULL
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=?
                     AND mcu_event_sequence=?""",
                (
                    now,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return "ACCEPTED"

    # ── 故障操作 ──

    def record_fault(self, component: str, fault_code: int, severity: str,
                     detail: Optional[dict] = None, *,
                     fault_uid: Optional[str] = None,
                     lifecycle: str = FAULT_OBSERVED) -> str:
        fault_uid = fault_uid or self._new_uid()
        recovered_at = self._now() if lifecycle == FAULT_RECOVERED else None
        with self.transaction():
            self._conn.execute(
                """INSERT INTO faults
                   (fault_uid, component, fault_code, severity, lifecycle,
                    detail_json, recovered_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(fault_uid) DO UPDATE SET
                     component=excluded.component,
                     fault_code=excluded.fault_code,
                     severity=excluded.severity,
                     lifecycle=excluded.lifecycle,
                     detail_json=excluded.detail_json,
                     recovered_at=excluded.recovered_at""",
                (
                    fault_uid,
                    component,
                    fault_code,
                    severity,
                    lifecycle,
                    _json.dumps(detail, ensure_ascii=False)
                    if detail
                    else None,
                    recovered_at,
                ),
            )
        return fault_uid

    def mark_fault_recovered(self, fault_uid: str) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                "UPDATE faults SET lifecycle='RECOVERED', recovered_at=? WHERE fault_uid=?",
                (self._now(), fault_uid),
            )
            return cur.rowcount > 0

    def list_active_faults(self) -> list[dict]:
        with self._lock:
            legacy_rows = self._conn.execute(
                "SELECT * FROM faults WHERE lifecycle='OBSERVED' ORDER BY observed_at"
            ).fetchall()
            edge_rows = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED'
                   ORDER BY first_detected_at"""
            ).fetchall()
        return [
            *(dict(row) for row in legacy_rows),
            *(dict(row) for row in edge_rows),
        ]

    def get_active_edge_fault(
        self,
        component: str,
        fault_code: str,
        port_no: Optional[int] = None,
    ) -> Optional[dict]:
        scope_key = (
            f"PORT:{port_no}" if port_no is not None else "DEVICE"
        )
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED' AND scope_key=?
                     AND component=? AND fault_code=?
                   LIMIT 1""",
                (scope_key, component, fault_code),
            ).fetchone()
        return dict(row) if row else None

    def observe_fault_and_create_event(
        self,
        *,
        device_name: str,
        component: str,
        fault_code: str,
        severity: str,
        port_no: Optional[int] = None,
        fault_uid: Optional[str] = None,
        mcu_boot_id: Optional[int] = None,
        mcu_event_sequence: Optional[int] = None,
        mcu_receive_generation: Optional[int] = None,
        detail: Optional[dict] = None,
    ) -> str:
        """Create or monotonically upgrade one active fault atomically."""
        if not device_name or device_name == "UNKNOWN_DEVICE":
            return "REJECTED"
        scope_key = (
            f"PORT:{port_no}" if port_no is not None else "DEVICE"
        )
        severity_rank = {
            "WARNING": 1,
            "BLOCK_PORT": 2,
            "BLOCK_DEVICE": 3,
        }
        if severity not in severity_rank:
            return "REJECTED"
        now = self._now()
        with self.transaction():
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                derived = self._conn.execute(
                    """SELECT event_uid FROM mcu_derived_event
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?
                         AND event_type='DEVICE_FAULT_OBSERVED'""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                ).fetchone()
                if derived:
                    self._mark_mcu_event_processed_in_tx(
                        self._conn,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        now,
                    )
                    return "DUPLICATE"
            active = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED' AND scope_key=?
                     AND component=? AND fault_code=?
                   LIMIT 1""",
                (scope_key, component, fault_code),
            ).fetchone()
            if active:
                current_rank = severity_rank.get(
                    active["severity"],
                    0,
                )
                self._conn.execute(
                    """UPDATE edge_fault_state
                       SET discovery_count=discovery_count+1,
                           last_detected_at=?
                       WHERE fault_uid=?""",
                    (now, active["fault_uid"]),
                )
                if severity_rank[severity] <= current_rank:
                    return "DUPLICATE"
                fault_uid = active["fault_uid"]
                self._conn.execute(
                    """UPDATE edge_fault_state SET severity=?
                       WHERE fault_uid=?""",
                    (severity, fault_uid),
                )
            else:
                fault_uid = fault_uid or self._new_uid()
                self._conn.execute(
                    """INSERT INTO edge_fault_state
                       (fault_uid, scope_key, port_no, component,
                        fault_code, severity, lifecycle,
                        first_detected_at, last_detected_at,
                        detail_json)
                       VALUES (?, ?, ?, ?, ?, ?, 'OBSERVED',
                               ?, ?, ?)""",
                    (
                        fault_uid,
                        scope_key,
                        port_no,
                        component,
                        fault_code,
                        severity,
                        now,
                        now,
                        (
                            _json.dumps(detail, ensure_ascii=False)
                            if detail
                            else None
                        ),
                    ),
                )
            sequence = self._next_seq(self._conn)
            event_uid = self._new_uid()
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DEVICE_FAULT_OBSERVED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "faultUid": fault_uid,
                    "portNo": port_no,
                    "component": component,
                    "severity": severity,
                    "faultCode": fault_code,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_FAULT_OBSERVED",
            )
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                self._conn.execute(
                    """INSERT INTO mcu_derived_event
                       (mcu_receive_generation, mcu_boot_id,
                        mcu_event_sequence, event_type, event_uid)
                       VALUES (?, ?, ?, 'DEVICE_FAULT_OBSERVED', ?)""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_uid,
                    ),
                )
                self._mark_mcu_event_processed_in_tx(
                    self._conn,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    now,
                )
            return "ACCEPTED"

    def recover_fault_and_create_event(
        self,
        *,
        device_name: str,
        fault_uid: str,
        component: str,
        fault_code: str,
        port_no: Optional[int],
        recovery_evidence: str,
        mcu_boot_id: Optional[int] = None,
        mcu_event_sequence: Optional[int] = None,
        mcu_receive_generation: Optional[int] = None,
    ) -> str:
        """Close exactly one active fault and create its recovery fact."""
        if not device_name or device_name == "UNKNOWN_DEVICE":
            return "REJECTED"
        with self.transaction():
            now = self._now()
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                derived = self._conn.execute(
                    """SELECT event_uid FROM mcu_derived_event
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?
                         AND event_type='DEVICE_FAULT_RECOVERED'""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                ).fetchone()
                if derived:
                    self._mark_mcu_event_processed_in_tx(
                        self._conn,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        now,
                    )
                    return "DUPLICATE"
            fault = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE fault_uid=?""",
                (fault_uid,),
            ).fetchone()
            if not fault:
                return "UNKNOWN"
            if fault["lifecycle"] == "RECOVERED":
                return "DUPLICATE"
            if (
                fault["component"] != component
                or fault["fault_code"] != fault_code
                or fault["port_no"] != port_no
            ):
                return "CONFLICT"
            self._conn.execute(
                """UPDATE edge_fault_state
                   SET lifecycle='RECOVERED', recovered_at=?,
                       recovery_evidence=?
                   WHERE fault_uid=?""",
                (now, recovery_evidence, fault_uid),
            )
            sequence = self._next_seq(self._conn)
            event_uid = self._new_uid()
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DEVICE_FAULT_RECOVERED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "faultUid": fault_uid,
                    "portNo": port_no,
                    "component": component,
                    "severity": fault["severity"],
                    "faultCode": fault_code,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_FAULT_RECOVERED",
            )
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                self._conn.execute(
                    """INSERT INTO mcu_derived_event
                       (mcu_receive_generation, mcu_boot_id,
                        mcu_event_sequence, event_type, event_uid)
                       VALUES (?, ?, ?, 'DEVICE_FAULT_RECOVERED', ?)""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_uid,
                    ),
                )
                self._mark_mcu_event_processed_in_tx(
                    self._conn,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    now,
                )
            return "ACCEPTED"

    def recover_native_control_communication_fault(
        self,
        *,
        device_name: str,
        fault_uid: str,
        mcu_boot_id: int,
        recovery_evidence: str,
    ) -> str:
        """Atomically clear one operator-selected native UART stop.

        This is deliberately narrower than the generic fault recovery path.
        It clears the admission latch only when the exact active fault is the
        manually recoverable native-control communication fault, no business
        still owns the local slot, and the caller supplies the current MCU
        boot observation. A stale operator retry therefore cannot clear a
        later occurrence of the same fault code.
        """
        if (
            not device_name
            or device_name == "UNKNOWN_DEVICE"
            or not isinstance(fault_uid, str)
            or not fault_uid
            or isinstance(mcu_boot_id, bool)
            or not isinstance(mcu_boot_id, int)
            or not 1 <= mcu_boot_id <= 9_007_199_254_740_991
            or not isinstance(recovery_evidence, str)
            or not recovery_evidence
            or len(recovery_evidence) > 1024
        ):
            return "REJECTED"
        with self.transaction(immediate=True):
            state = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='native_blocking_fault'"""
            ).fetchone()
            if (
                state is None
                or state["state_value"]
                != "MCU_COMMUNICATION_UNAVAILABLE"
            ):
                return "CONFLICT"
            slot = self._conn.execute(
                """SELECT work_type FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            if slot is None or slot["work_type"] != WORK_TYPE_NONE:
                return "BUSY"
            fault = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE fault_uid=?""",
                (fault_uid,),
            ).fetchone()
            if fault is None:
                return "UNKNOWN"
            if (
                fault["lifecycle"] != "OBSERVED"
                or fault["scope_key"] != "DEVICE"
                or fault["port_no"] is not None
                or fault["component"] != "UART"
                or fault["fault_code"] != "UART_PROTOCOL"
                or fault["severity"] != "BLOCK_DEVICE"
            ):
                return "CONFLICT"
            try:
                detail = _json.loads(fault["detail_json"] or "{}")
            except (TypeError, ValueError, _json.JSONDecodeError):
                return "CONFLICT"
            if (
                detail.get("profile")
                != "native-control-communication-v1"
                or detail.get("reasonCode")
                != "MCU_COMMUNICATION_UNAVAILABLE"
                or detail.get("automaticRecovery") is not False
            ):
                return "CONFLICT"

            now = self._now()
            updated = self._conn.execute(
                """UPDATE edge_fault_state
                   SET lifecycle='RECOVERED', recovered_at=?,
                       recovery_evidence=?
                   WHERE fault_uid=? AND lifecycle='OBSERVED'""",
                (now, recovery_evidence, fault_uid),
            )
            if updated.rowcount != 1:
                return "CONFLICT"
            sequence = self._next_seq(self._conn)
            event_uid = self._new_uid()
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DEVICE_FAULT_RECOVERED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "faultUid": fault_uid,
                    "portNo": None,
                    "component": "UART",
                    "severity": "BLOCK_DEVICE",
                    "faultCode": "UART_PROTOCOL",
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": None,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_FAULT_RECOVERED",
            )
            cleared = self._conn.execute(
                """UPDATE device_state SET state_value='', updated_at=?
                   WHERE state_key='native_blocking_fault'
                     AND state_value='MCU_COMMUNICATION_UNAVAILABLE'""",
                (now,),
            )
            if cleared.rowcount != 1:
                raise RuntimeError(
                    "native communication admission latch changed"
                )
            return "ACCEPTED"

    @staticmethod
    def _mark_mcu_event_processed_in_tx(
        conn,
        mcu_receive_generation: int,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        processed_at: str,
    ) -> None:
        conn.execute(
            """UPDATE mcu_event_inbox
               SET state='PROCESSED', processed_at=?,
                   last_error=NULL
               WHERE mcu_receive_generation=?
                 AND mcu_boot_id=? AND mcu_event_sequence=?""",
            (
                processed_at,
                mcu_receive_generation,
                mcu_boot_id,
                mcu_event_sequence,
            ),
        )

    # ── 完整性校验与维护 ──

    def integrity_check(self) -> bool:
        try:
            with self._lock:
                row = self._conn.execute("PRAGMA integrity_check").fetchone()
            ok = row[0] == "ok"
            if not ok:
                logger.error("SQLite 完整性检查失败: %s", row[0])
            return ok
        except Exception as e:
            logger.error("SQLite 完整性检查异常: %s", e)
            return False

    def tombstone_confirmed_events(self, older_than_hours: int = 72) -> int:
        with self.transaction():
            conn = self._conn
            rows = conn.execute(
                """SELECT event_uid, event_type FROM event_outbox
                   WHERE state = 'CONFIRMED' AND tombstoned = 0
                     AND datetime(confirmed_at) <= datetime('now', ?)""",
                (f"-{older_than_hours} hours",),
            ).fetchall()
            count = 0
            for r in rows:
                conn.execute(
                    "INSERT INTO tombstones (tombstone_uid, original_type, original_uid) VALUES (?, ?, ?)",
                    (self._new_uid(), r["event_type"], r["event_uid"]),
                )
                conn.execute("UPDATE event_outbox SET tombstoned=1 WHERE event_uid=?", (r["event_uid"],))
                count += 1
            if count:
                logger.info("墓碑化 %d 个已确认事件", count)
            return count

    def cleanup_dead_photos(self, older_than_hours: int = 72) -> int:
        with self.transaction():
            conn = self._conn
            rows = conn.execute(
                """SELECT photo_uid, local_path FROM photo_outbox
                   WHERE state = 'DEAD' AND tombstoned = 0
                     AND created_at <= datetime('now', ?)""",
                (f"-{older_than_hours} hours",),
            ).fetchall()
            count = 0
            for r in rows:
                try:
                    if os.path.exists(r["local_path"]):
                        os.remove(r["local_path"])
                except OSError:
                    pass
                conn.execute("UPDATE photo_outbox SET tombstoned=1 WHERE photo_uid=?", (r["photo_uid"],))
                count += 1
            return count

    # ── 独立远程维护隧道槽 ──

    def import_remote_support_status_event(
        self,
        fact: dict[str, Any],
    ) -> str:
        """Reliably adopt one fact from the independent tunnel agent.

        The agent keeps the fact until this EdgeStore transaction commits and
        the local RPC acknowledgement succeeds.  Replaying the same event UID
        is therefore normal after either process crashes.
        """

        required = {
            "eventUid",
            "sessionUid",
            "commandUid",
            "deviceName",
            "remotePort",
            "state",
            "failureCode",
            "occurredAt",
        }
        if (
            not isinstance(fact, dict)
            or frozenset(fact) not in {
                frozenset(required),
                frozenset(required | {"clockQuality"}),
            }
        ):
            raise ValueError("remote support status fact fields are invalid")
        event_uid = _require_uuid4_local(fact["eventUid"], "eventUid")
        session_uid = _require_uuid4_local(
            fact["sessionUid"],
            "sessionUid",
        )
        command_uid = _require_uuid4_local(
            fact["commandUid"],
            "commandUid",
        )
        device_name = fact["deviceName"]
        if (
            not isinstance(device_name, str)
            or not 1 <= len(device_name) <= 64
            or any(character in device_name for character in "\x00\r\n")
        ):
            raise ValueError("remote support status deviceName is invalid")
        remote_port = fact["remotePort"]
        if (
            isinstance(remote_port, bool)
            or not isinstance(remote_port, int)
            or remote_port not in range(22011, 22015)
        ):
            raise ValueError("remote support status remotePort is invalid")
        state = fact["state"]
        if state not in {
            "CONNECTING",
            "OPEN",
            "CLOSED",
            "FAILED",
            "EXPIRED",
        }:
            raise ValueError("remote support status state is invalid")
        failure_code = fact["failureCode"]
        if (
            failure_code is not None
            and failure_code not in REMOTE_SUPPORT_FAILURE_CODES
        ):
            raise ValueError("remote support status failureCode is invalid")
        if state == "FAILED" and failure_code is None:
            raise ValueError("FAILED remote support status requires failureCode")
        occurred_at = fact["occurredAt"]
        clock_quality = fact.get("clockQuality", "SYNCED")
        if clock_quality not in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}:
            raise ValueError("remote support clockQuality is invalid")
        if clock_quality == "SYNCED":
            _parse_utc_instant(occurred_at, "occurredAt")
        elif occurred_at is not None:
            raise ValueError("unsynced remote support fact carries occurredAt")
        payload = {
            "sessionUid": session_uid,
            "state": state,
            "remotePort": remote_port,
            "failureCode": failure_code,
        }

        with self.transaction():
            existing = self._conn.execute(
                "SELECT payload_json FROM event_outbox WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if existing is not None:
                envelope = _json.loads(existing["payload_json"])
                same = (
                    envelope.get("eventType")
                    == "REMOTE_SUPPORT_TUNNEL_STATUS"
                    and envelope.get("commandUid") == command_uid
                    and envelope.get("target")
                    == {"type": "DEVICE_ASSET", "uid": device_name}
                    and envelope.get("occurredAt") == occurred_at
                    and envelope.get("clockQuality") == clock_quality
                    and envelope.get("payload") == payload
                )
                return "DUPLICATE" if same else "CONFLICT"
            sequence = self._next_seq(self._conn)
            envelope = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="REMOTE_SUPPORT_TUNNEL_STATUS",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                command_uid=command_uid,
                delivery_class="RELIABLE_FACT",
                payload=payload,
            )
            envelope["occurredAt"] = occurred_at
            envelope["clockQuality"] = clock_quality
            self._insert_event(
                self._conn,
                envelope,
                "REMOTE_SUPPORT_TUNNEL_STATUS",
            )
            return "ACCEPTED"

    def request_remote_support_open(
        self,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        expires_at: str,
    ) -> str:
        """Persist desired OPEN and CONNECTING evidence in one transaction."""

        _require_uuid4_local(session_uid, "session_uid")
        _require_uuid4_local(command_uid, "command_uid")
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("device_name is required")
        if (
            isinstance(remote_port, bool)
            or not isinstance(remote_port, int)
            or remote_port not in range(22011, 22015)
        ):
            raise ValueError("remote_port must be one of 22011..22014")
        deadline_reference = local_deadline_reference()
        if (
            deadline_reference is not None
            and _parse_utc_instant(expires_at, "expires_at")
            <= deadline_reference
        ):
            raise ValueError("remote support session is already expired")
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if existing:
                same_session = existing["session_uid"] == session_uid
                same_request = (
                    same_session
                    and existing["remote_port"] == remote_port
                    and existing["expires_at"] == expires_at
                    and existing["device_name"] == device_name
                )
                if same_session:
                    # A session UID is a permanent idempotency identity.  A
                    # delayed/replayed open must never resurrect a CLOSED,
                    # FAILED or EXPIRED lease; a genuinely new lease uses a
                    # new session UID.
                    return "DUPLICATE" if same_request else "CONFLICT"
                if existing["state"] not in REMOTE_SUPPORT_TERMINAL_STATES:
                    return "CONFLICT"
            now = self._now()
            self._conn.execute(
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
            self._create_remote_support_status_event_in_tx(
                self._conn,
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
        _require_uuid4_local(session_uid, "session_uid")
        _require_uuid4_local(command_uid, "command_uid")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "NOT_FOUND"
            if row["state"] in REMOTE_SUPPORT_TERMINAL_STATES:
                return "ALREADY_TERMINAL"
            if row["state"] == "CLOSING":
                return "DUPLICATE"
            self._conn.execute(
                """UPDATE remote_support_session
                   SET command_uid=?, state='CLOSING', failure_code=NULL,
                       next_attempt_at=NULL, updated_at=?
                   WHERE singleton_id=1""",
                (command_uid, self._now()),
            )
            return "ACCEPTED"

    def get_remote_support_session(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
        return dict(row) if row else None

    def transition_remote_support_session(
        self,
        session_uid: str,
        state: str,
        *,
        failure_code: Optional[str] = None,
    ) -> str:
        if state not in {
            "CONNECTING",
            "OPEN",
            "CLOSED",
            "FAILED",
            "EXPIRED",
        }:
            raise ValueError("invalid remote support status state")
        if failure_code is not None and failure_code not in REMOTE_SUPPORT_FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        if state == "FAILED" and failure_code is None:
            raise ValueError("FAILED remote support state requires failure_code")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "STALE"
            if row["state"] in REMOTE_SUPPORT_TERMINAL_STATES:
                return "TERMINAL"
            if row["state"] == state and row["failure_code"] == failure_code:
                return "DUPLICATE"
            now = self._now()
            self._conn.execute(
                """UPDATE remote_support_session
                   SET state=?, failure_code=?, next_attempt_at=NULL,
                       attempt_count=CASE WHEN ?='OPEN' THEN 0 ELSE attempt_count END,
                       updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (state, failure_code, state, now, session_uid),
            )
            self._create_remote_support_status_event_in_tx(
                self._conn,
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
        failure_code: str,
        max_attempts: int,
    ) -> str:
        if failure_code not in REMOTE_SUPPORT_FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if (
                row is None
                or row["session_uid"] != session_uid
                or row["state"] in REMOTE_SUPPORT_TERMINAL_STATES
                or row["state"] == "CLOSING"
            ):
                return "STALE"
            attempts = int(row["attempt_count"]) + 1
            next_state = (
                "FAILED" if attempts >= max_attempts else "CONNECTING"
            )
            self._conn.execute(
                """UPDATE remote_support_session
                   SET state=?, failure_code=?, attempt_count=?,
                        next_attempt_at=NULL, updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (
                    next_state,
                    failure_code,
                    attempts,
                    self._now(),
                    session_uid,
                ),
            )
            if row["state"] != next_state or row["failure_code"] != failure_code:
                self._create_remote_support_status_event_in_tx(
                    self._conn,
                    session_uid=session_uid,
                    command_uid=row["command_uid"],
                    device_name=row["device_name"],
                    remote_port=row["remote_port"],
                    state=next_state,
                    failure_code=failure_code,
                )
            return next_state

    def _create_remote_support_status_event_in_tx(
        self,
        conn,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        state: str,
        failure_code: Optional[str],
    ) -> None:
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        payload = {
            "sessionUid": session_uid,
            "state": state,
            "remotePort": remote_port,
            "failureCode": failure_code,
        }
        envelope = build_event_envelope(
            event_uid=event_uid,
            device_name=device_name,
            edge_event_sequence=sequence,
            event_type="REMOTE_SUPPORT_TUNNEL_STATUS",
            target_type="DEVICE_ASSET",
            target_uid=device_name,
            command_uid=command_uid,
            delivery_class="RELIABLE_FACT",
            payload=payload,
        )
        self._insert_event(conn, envelope, "REMOTE_SUPPORT_TUNNEL_STATUS")

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("EdgeStore 已关闭")

    # ── 照片发件箱操作 ──

    def reserve_photo_captures(
        self,
        captures: list[dict],
    ) -> list[dict]:
        """Persist stable photo identities before touching a camera."""
        reserved = []
        with self.transaction():
            for capture in captures:
                existing = self._conn.execute(
                    """SELECT * FROM photo_outbox
                       WHERE work_type=? AND work_uid=? AND slot_name=?
                       ORDER BY tombstoned, created_at
                       LIMIT 1""",
                    (
                        capture["work_type"],
                        capture["work_uid"],
                        capture["slot_name"],
                    ),
                ).fetchone()
                if existing:
                    reserved.append(dict(existing))
                    continue
                self._conn.execute(
                    """INSERT INTO photo_outbox
                       (photo_uid, slot_name, local_path, cos_key,
                        state, work_uid, work_type, device_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        capture["photo_uid"],
                        capture["slot_name"],
                        capture["local_path"],
                        capture.get("cos_key"),
                        PHOTO_CAPTURE_PENDING,
                        capture["work_uid"],
                        capture["work_type"],
                        capture["device_name"],
                    ),
                )
                row = self._conn.execute(
                    """SELECT * FROM photo_outbox
                       WHERE photo_uid=?""",
                    (capture["photo_uid"],),
                ).fetchone()
                reserved.append(dict(row))
        return reserved

    def list_capture_pending_photos(
        self,
        limit: int = 100,
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE state=? AND tombstoned=0
                   ORDER BY created_at, slot_name
                   LIMIT ?""",
                (PHOTO_CAPTURE_PENDING, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_photo(self, photo_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE photo_uid=?""",
                (photo_uid,),
            ).fetchone()
        return dict(row) if row else None

    def mark_photo_captured(
        self,
        photo_uid: str,
        *,
        content_sha256: str,
        size_bytes: int,
        captured_at: str,
        captured_clock_quality: str,
    ) -> bool:
        if captured_clock_quality not in {
            "SYNCED", "ESTIMATED", "UNAVAILABLE"
        }:
            raise ValueError("captured clock quality is invalid")
        with self.transaction():
            updated = self._conn.execute(
                """UPDATE photo_outbox
                   SET state=?, content_sha256=?, size_bytes=?,
                       captured_at=?, captured_clock_quality=?,
                       last_error=NULL
                   WHERE photo_uid=? AND state=?
                     AND tombstoned=0""",
                (
                    PHOTO_PENDING,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    captured_clock_quality,
                    photo_uid,
                    PHOTO_CAPTURE_PENDING,
                ),
            )
            return updated.rowcount == 1

    def list_pending_photos(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (
                       next_retry_at IS NULL
                       OR datetime(next_retry_at) <= datetime('now')
                     )
                   ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recover_photo_upload_queue(self) -> int:
        """Drop in-memory grant assumptions after an Edge process restart."""
        with self.transaction():
            rows = self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', grant_request_event_uid=NULL,
                        grant_generation=grant_generation+1,
                        next_retry_at=NULL, url=NULL,
                        last_error='EDGE_RESTARTED'
                   WHERE state IN ('PENDING', 'UPLOADING')
                     AND tombstoned=0 AND work_type IS NOT NULL"""
            ).rowcount
            return rows

    def assign_photo_grant_request(
        self,
        photo_uids: list[str],
        event_uid: str,
    ) -> None:
        if not photo_uids:
            return
        placeholders = ",".join("?" for _ in photo_uids)
        with self.transaction():
            self._conn.execute(
                f"""UPDATE photo_outbox
                    SET grant_request_event_uid=?
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING'""",
                (event_uid, *photo_uids),
            )

    def ensure_photo_grant_request(
        self,
        photo_uids: list[str],
        event_uid: str,
        payload: dict,
        *,
        device_name: str,
        work_type: str,
        work_uid: str,
    ) -> str:
        """Atomically create/reuse one request event and assign its photos."""
        if not photo_uids:
            raise ValueError("photo_uids must not be empty")
        placeholders = ",".join("?" for _ in photo_uids)
        with self.transaction():
            rows = self._conn.execute(
                f"""SELECT photo_uid, grant_request_event_uid
                    FROM photo_outbox
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING' AND tombstoned=0""",
                tuple(photo_uids),
            ).fetchall()
            if len(rows) != len(set(photo_uids)):
                raise ValueError("photo grant request contains unavailable photo")
            existing = next(
                (
                    row["grant_request_event_uid"]
                    for row in rows
                    if row["grant_request_event_uid"]
                ),
                None,
            )
            selected_event_uid = existing or event_uid
            if existing is None:
                seq = self._next_seq(self._conn)
                event = build_event_envelope(
                    event_uid=event_uid,
                    device_name=device_name,
                    edge_event_sequence=seq,
                    event_type="PHOTO_UPLOAD_GRANT_REQUESTED",
                    target_type=work_type,
                    target_uid=work_uid,
                    payload=payload,
                )
                self._insert_event(
                    self._conn,
                    event,
                    "PHOTO_UPLOAD_GRANT_REQUESTED",
                )
            self._conn.execute(
                f"""UPDATE photo_outbox
                    SET grant_request_event_uid=?
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING'""",
                (selected_event_uid, *photo_uids),
            )
            return selected_event_uid

    def invalidate_photo_grant(
        self,
        work_type: str,
        work_uid: str,
        error_code: str,
    ) -> int:
        with self.transaction():
            return self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', grant_request_event_uid=NULL,
                        grant_generation=grant_generation+1,
                        next_retry_at=NULL, url=NULL, last_error=?
                   WHERE work_type=? AND work_uid=?
                     AND state IN ('PENDING', 'UPLOADING')
                     AND tombstoned=0""",
                (error_code, work_type, work_uid),
            ).rowcount

    def mark_photo_uploading(self, photo_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='UPLOADING', url=NULL
                   WHERE photo_uid=?""",
                (photo_uid,),
            )

    def mark_photo_uploaded(
        self,
        photo_uid: str,
        cos_key: str,
        url: Optional[str] = None,
        status_event_uid: Optional[str] = None,
    ) -> None:
        if not url:
            raise ValueError("uploaded photo requires url")
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='UPLOADED', cos_key=?, url=?, uploaded_at=?,
                       status_event_uid=?, last_error=NULL,
                       next_retry_at=NULL
                   WHERE photo_uid=?""",
                (
                    cos_key,
                    url,
                    self._now(),
                    status_event_uid,
                    photo_uid,
                ),
            )

    def mark_photo_pending_retry(
        self,
        photo_uid: str,
        error_code: str = "PHOTO_UPLOAD_FAILED",
    ) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(120, 5 * (1 + retries))
        next_retry = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_s + backoff))
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', retry_count=retry_count+1,
                        next_retry_at=?, last_error=?, url=NULL
                   WHERE photo_uid=?""",
                (next_retry, error_code, photo_uid),
            )

    def mark_photo_dead(
        self,
        photo_uid: str,
        error_code: str = "PHOTO_UPLOAD_EXPIRED",
        status_event_uid: Optional[str] = None,
    ) -> None:
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='DEAD', url=NULL, last_error=?,
                       status_event_uid=?
                   WHERE photo_uid=?""",
                (error_code, status_event_uid, photo_uid),
            )

    def record_photo_status(
        self,
        photo_uid: str,
        event_uid: str,
        payload: dict,
        *,
        state: str,
        cos_key: Optional[str] = None,
        url: Optional[str] = None,
        error_code: Optional[str] = None,
        expected_state: Optional[str] = None,
    ) -> str:
        """Atomically persist a terminal photo state and its reliable fact."""
        if state not in (PHOTO_UPLOADED, PHOTO_DEAD):
            raise ValueError("photo status state is invalid")
        if state == PHOTO_UPLOADED and not url:
            raise ValueError("uploaded photo status requires url")
        if state == PHOTO_DEAD and url is not None:
            raise ValueError("missing photo status must not contain url")
        with self.transaction():
            photo = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE photo_uid=? AND tombstoned=0""",
                (photo_uid,),
            ).fetchone()
            if not photo:
                return "UNKNOWN"
            if expected_state is not None and photo["state"] != expected_state:
                return "STATE_CHANGED"
            if photo["status_event_uid"]:
                return "DUPLICATE"
            seq = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=photo["device_name"],
                edge_event_sequence=seq,
                event_type="PHOTO_STATUS_REPORTED",
                target_type=photo["work_type"],
                target_uid=photo["work_uid"],
                payload=payload,
            )
            self._insert_event(
                self._conn,
                event,
                "PHOTO_STATUS_REPORTED",
            )
            uploaded_at = self._now() if state == PHOTO_UPLOADED else None
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state=?, cos_key=COALESCE(?, cos_key),
                       url=?, uploaded_at=?,
                       status_event_uid=?, last_error=?,
                       next_retry_at=NULL
                   WHERE photo_uid=?""",
                (
                    state,
                    cos_key,
                    url,
                    uploaded_at,
                    event_uid,
                    error_code,
                    photo_uid,
                ),
            )
            return "ACCEPTED"

    def get_photos_by_work(self, work_uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM photo_outbox WHERE work_uid=? AND tombstoned=0 ORDER BY slot_name",
                (work_uid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_confirmed_uploaded_photos(
        self,
        limit: int = 20,
    ) -> list[dict]:
        """List uploaded files whose status fact was confirmed by backend."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT p.*
                   FROM photo_outbox p
                   JOIN event_outbox e
                     ON e.event_uid = p.status_event_uid
                   WHERE p.state='UPLOADED'
                     AND p.tombstoned=0
                     AND e.state='CONFIRMED'
                     AND EXISTS (
                       SELECT 1
                       FROM confirmation_inbox c
                       WHERE c.event_uid=e.event_uid
                         AND c.outcome='BUSINESS_APPLIED'
                     )
                     AND EXISTS (
                       SELECT 1
                       FROM event_outbox completed
                       WHERE completed.work_uid=p.work_uid
                         AND completed.event_type=CASE p.work_type
                           WHEN 'DELIVERY_SESSION'
                             THEN 'DELIVERY_COMPLETE'
                           WHEN 'CLEAN_OPERATION'
                             THEN 'CLEAN_COMPLETE'
                         END
                         AND completed.state='CONFIRMED'
                     )
                   ORDER BY p.uploaded_at
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def tombstone_photo(self, photo_uid: str) -> bool:
        with self.transaction():
            row = self._conn.execute(
                """SELECT photo_uid FROM photo_outbox
                   WHERE photo_uid=? AND tombstoned=0""",
                (photo_uid,),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                """INSERT INTO tombstones
                   (tombstone_uid, original_type, original_uid)
                   VALUES (?, 'PHOTO', ?)""",
                (self._new_uid(), photo_uid),
            )
            self._conn.execute(
                """UPDATE photo_outbox SET tombstoned=1
                   WHERE photo_uid=?""",
                (photo_uid,),
            )
            return True

    def mark_event_sending(
        self,
        event_uid: str,
        mqtt_msg_id: Optional[int],
    ) -> bool:
        with self.transaction():
            cursor = self._conn.execute(
                """UPDATE event_outbox
                   SET state='SENDING', mqtt_msg_id=?
                   WHERE event_uid=? AND state='PENDING'
                     AND (
                       event_type='BUSINESS_CONFIRMATION_RECEIPT'
                       OR confirmed_at IS NULL
                     )""",
                (mqtt_msg_id, event_uid),
            )
            return cursor.rowcount > 0

    def mark_event_pending_retry(self, event_uid: str) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM event_outbox WHERE event_uid=?", (event_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(60, 2 ** min(6, retries))
        next_retry = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(now_s + backoff),
        )
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='PENDING', "
                "retry_count=retry_count+1, next_retry_at=? "
                "WHERE event_uid=? AND state IN ('PENDING', 'SENDING') "
                "AND (event_type='BUSINESS_CONFIRMATION_RECEIPT' "
                "OR confirmed_at IS NULL)",
                (next_retry, event_uid),
            )

    def record_event_platform_reply(
        self,
        edge_event_sequence: int,
        code: int,
    ) -> Optional[str]:
        event = self.get_event_by_sequence(edge_event_sequence)
        if not event:
            return None
        event_uid = event["event_uid"]
        now = self._now()
        with self.transaction():
            if code in (0, 200):
                self._conn.execute(
                    """UPDATE event_outbox
                       SET last_platform_code=?,
                           platform_accepted_at=COALESCE(
                               platform_accepted_at, ?
                           ),
                           last_platform_reply_at=?
                       WHERE event_uid=?""",
                    (code, now, now, event_uid),
                )
            elif 2400 <= code <= 2499:
                self._conn.execute(
                    """UPDATE event_outbox
                       SET state='DEAD',
                           last_platform_code=?,
                           last_platform_reply_at=?,
                           next_retry_at=NULL
                       WHERE event_uid=?
                         AND state<>'CONFIRMED'
                         AND (
                           event_type='BUSINESS_CONFIRMATION_RECEIPT'
                           OR confirmed_at IS NULL
                         )""",
                    (code, now, event_uid),
                )
            else:
                self._conn.execute(
                    """UPDATE event_outbox
                       SET last_platform_code=?,
                           last_platform_reply_at=?
                       WHERE event_uid=?""",
                    (code, now, event_uid),
                )
        if code not in (0, 200) and not 2400 <= code <= 2499:
            self.mark_event_pending_retry(event_uid)
        return event_uid

    def mark_event_dead(self, event_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='DEAD' WHERE event_uid=?", (event_uid,)
            )

    def mark_control_receipt_published(self, event_uid: str) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=? AND event_type=
                     'BUSINESS_CONFIRMATION_RECEIPT'""",
                (EVENT_CONFIRMED, self._now(), event_uid),
            )
            return cur.rowcount > 0

    def list_unconfirmed_events(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event_outbox WHERE state='SENDING' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def release_work_slot(self, work_uid: str) -> bool:
        with self.transaction():
            conn = self._conn
            slot = conn.execute("SELECT work_uid FROM work_slot WHERE slot_id=1").fetchone()
            if not slot or slot["work_uid"] != work_uid:
                return False
            conn.execute(
                "UPDATE work_slot SET work_type='NONE', work_uid=NULL, work_state=NULL, port_no=NULL, context_json=NULL, updated_at=? WHERE slot_id=1",
                (self._now(),),
            )
            return True

    def get_work_slot(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
        if row and row["work_type"] != WORK_TYPE_NONE:
            result = dict(row)
            result["context"] = _json.loads(result["context_json"]) if result["context_json"] else {}
            return result
        return None

    def update_work_context(self, work_uid: str, context: dict) -> bool:
        with self.transaction():
            conn = self._conn
            row = conn.execute(
                "SELECT work_uid FROM work_slot WHERE slot_id=1 AND work_uid=?", (work_uid,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE work_slot SET context_json=?, updated_at=? WHERE slot_id=1",
                (_json.dumps(context, ensure_ascii=False), self._now()),
            )
            return True

    def claim_clean_unlock_dispatch(
        self,
        *,
        work_uid: str,
        start_mcu_command_uid: str,
        preunlock_measurement_uid: str,
        recovery_generation: int,
        action_sequence: int,
        unlock_mcu_command_uid: str,
        unlock_action_key: str,
    ) -> Optional[dict] | str:
        """Atomically let either cancellation or the next unlock win.

        The caller may have spent time taking photos after it loaded the work
        context.  This transaction reloads the authoritative slot and changes
        it to UNLOCKING only if no END_CLEAN_BEFORE_UNLOCK intent was committed
        in the meantime.  Once this claim wins, that END command no longer
        accepts the phase; if END won first, this method returns ``None`` and
        no unlock command may be prepared or written.
        """

        deadline_reference = local_deadline_reference()
        with self.transaction(immediate=True):
            row = self._conn.execute(
                """SELECT work_type, work_uid, context_json
                   FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            if (
                row is None
                or row["work_type"] != WORK_TYPE_CLEAN
                or row["work_uid"] != work_uid
                or not row["context_json"]
            ):
                return None
            context = _json.loads(row["context_json"])
            if not isinstance(context, dict):
                raise ValueError("active clean context is invalid")
            # OneNet ingress persists commands on its own thread, while the
            # command consumer handles MCU events serially.  An END request
            # can therefore be durable during slow photo I/O even though its
            # WorkManager method has not run yet.  Treat that accepted inbox
            # fact as winning this same transaction; otherwise the consumer
            # would unlock first and only process the queued cancellation
            # afterwards.
            pending_end_rows = self._conn.execute(
                """SELECT command_uid, command_type, payload_json,
                          canonical_sha256, received_at,
                          received_clock_quality
                   FROM command_inbox
                   WHERE command_type='END_CLEAN_BEFORE_UNLOCK'
                     AND state='PENDING'"""
            ).fetchall()
            pending_valid_end = False
            for pending_end_row in pending_end_rows:
                pending_command = _json.loads(
                    pending_end_row["payload_json"]
                )
                if not isinstance(pending_command, dict):
                    raise ValueError(
                        "pending clean cancellation command is invalid"
                    )
                pending_payload = pending_command.get("payload")
                if not isinstance(pending_payload, dict):
                    raise ValueError(
                        "pending clean cancellation payload is invalid"
                    )
                if (
                    pending_command.get("commandUid")
                    != pending_end_row["command_uid"]
                    or pending_command.get("commandType")
                    != pending_end_row["command_type"]
                    or pending_end_row["command_type"]
                    != "END_CLEAN_BEFORE_UNLOCK"
                ):
                    raise ValueError(
                        "pending clean cancellation identity is invalid"
                    )
                stable_command = dict(pending_command)
                stable_command.pop("cosGrant", None)
                if canonical_payload_sha256(stable_command) != (
                    pending_end_row["canonical_sha256"]
                ):
                    raise ValueError(
                        "pending clean cancellation digest is invalid"
                    )
                if pending_end_row["received_clock_quality"] not in {
                    "SYNCED",
                    "ESTIMATED",
                    "UNAVAILABLE",
                }:
                    raise ValueError(
                        "pending clean cancellation clock fact is invalid"
                    )
                if not (
                    pending_payload.get("operationUid") == work_uid
                    and pending_payload.get("portNo")
                    == context.get("port_no")
                ):
                    continue
                expires_at = _parse_utc_instant(
                    pending_command.get("expiresAt"),
                    "expiresAt",
                )
                if (
                    deadline_reference is not None
                    and expires_at <= deadline_reference
                ):
                    updated = self._conn.execute(
                        """UPDATE command_inbox
                           SET state='FAILED', processed_at=?,
                               processing_started_at=NULL,
                               last_error='COMMAND_EXPIRED'
                           WHERE command_uid=? AND state='PENDING'""",
                        (
                            self._now(),
                            pending_end_row["command_uid"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ValueError(
                            "expired clean cancellation state changed"
                        )
                    observation = self._record_command_observation_in_tx(
                        self._conn,
                        pending_command,
                        "FAILED",
                        error_code="COMMAND_EXPIRED",
                    )
                    if observation == "CONFLICT":
                        raise ValueError(
                            "expired clean cancellation observation conflicts"
                        )
                    continue
                pending_valid_end = True
            if pending_valid_end:
                return "DEFERRED_BY_PENDING_END"
            exact_preunlock_state = (
                context.get("phase") == "PREUNLOCK_MEASURED"
                and not isinstance(context.get("end_before_unlock"), dict)
                and context.get("start_mcu_command_uid")
                == start_mcu_command_uid
                and context.get("preunlock_measurement_uid")
                == preunlock_measurement_uid
                and context.get("recovery_generation")
                == recovery_generation
                and context.get("action_sequence") == action_sequence
            )
            if not exact_preunlock_state:
                return None
            context["unlock_mcu_command_uid"] = unlock_mcu_command_uid
            context["unlock_action_key"] = unlock_action_key
            context["phase"] = "UNLOCKING"
            updated = self._conn.execute(
                """UPDATE work_slot SET context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(context, ensure_ascii=False),
                    self._now(),
                    WORK_TYPE_CLEAN,
                    work_uid,
                ),
            )
            if updated.rowcount != 1:
                return None
            return context

    def claim_clean_end_before_unlock(
        self,
        *,
        work_uid: str,
        port_no: int,
        command_uid: str,
        mcu_command_uid: str,
        reason: str,
    ) -> Optional[dict]:
        """Atomically persist cancellation before an unlock can be claimed."""

        with self.transaction(immediate=True):
            row = self._conn.execute(
                """SELECT work_type, work_uid, port_no, context_json
                   FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            if (
                row is None
                or row["work_type"] != WORK_TYPE_CLEAN
                or row["work_uid"] != work_uid
                or row["port_no"] != port_no
                or not row["context_json"]
            ):
                return None
            context = _json.loads(row["context_json"])
            if not isinstance(context, dict):
                raise ValueError("active clean context is invalid")
            existing = context.get("end_before_unlock")
            if isinstance(existing, dict):
                exact_retry = (
                    existing.get("command_uid") == command_uid
                    and existing.get("reason") == reason
                    and isinstance(existing.get("mcu_command_uid"), str)
                    and existing.get("mcu_command_uid") == mcu_command_uid
                    and context.get("phase")
                    in {
                        "ENDING_BEFORE_UNLOCK",
                        "END_BEFORE_UNLOCK_RESULT_UNKNOWN",
                        "END_BEFORE_UNLOCK_RETRYABLE",
                    }
                )
                safe_takeover = (
                    existing.get("command_uid") != command_uid
                    and existing.get("reason") == reason
                    and existing.get("state")
                    == "RETRYABLE_NOT_ACCEPTED"
                    and existing.get("mcu_command_uid") == mcu_command_uid
                    and context.get("phase")
                    == "END_BEFORE_UNLOCK_RETRYABLE"
                )
                if not safe_takeover:
                    return context if exact_retry else None
                inbox = self._conn.execute(
                    """SELECT command_type, state, payload_json
                       FROM command_inbox WHERE command_uid=?""",
                    (command_uid,),
                ).fetchone()
                incoming_command = (
                    _json.loads(inbox["payload_json"])
                    if inbox is not None and inbox["payload_json"]
                    else None
                )
                incoming_payload = (
                    incoming_command.get("payload")
                    if isinstance(incoming_command, dict)
                    else None
                )
                if not (
                    inbox is not None
                    and inbox["command_type"]
                    == "END_CLEAN_BEFORE_UNLOCK"
                    and inbox["state"] == "PROCESSING"
                    and isinstance(incoming_command, dict)
                    and incoming_command.get("commandUid") == command_uid
                    and incoming_command.get("commandType")
                    == "END_CLEAN_BEFORE_UNLOCK"
                    and isinstance(incoming_payload, dict)
                    and incoming_payload.get("operationUid") == work_uid
                    and incoming_payload.get("portNo") == port_no
                    and incoming_payload.get("reason") == reason
                ):
                    return None
                # Reuse the original MCU command identity. The earlier UART
                # outcome proved that command was not accepted, so this is a
                # continuation of one stop intent, never a second operation.
                existing["command_uid"] = command_uid
                existing["state"] = "REQUESTED"
                context["phase"] = "ENDING_BEFORE_UNLOCK"
                updated = self._conn.execute(
                    """UPDATE work_slot SET context_json=?, updated_at=?
                       WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                    (
                        _json.dumps(context, ensure_ascii=False),
                        self._now(),
                        WORK_TYPE_CLEAN,
                        work_uid,
                    ),
                )
                return context if updated.rowcount == 1 else None
            if context.get("phase") not in {
                "STARTING",
                "WAITING_PREUNLOCK_WEIGHT",
                "PREUNLOCK_MEASURED",
                "PREUNLOCK_PHOTO_BLOCKED",
            }:
                return None
            context["end_before_unlock"] = {
                "command_uid": command_uid,
                "mcu_command_uid": mcu_command_uid,
                "reason": reason,
                "state": "REQUESTED",
            }
            context["phase"] = "ENDING_BEFORE_UNLOCK"
            updated = self._conn.execute(
                """UPDATE work_slot SET context_json=?, updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(context, ensure_ascii=False),
                    self._now(),
                    WORK_TYPE_CLEAN,
                    work_uid,
                ),
            )
            if updated.rowcount != 1:
                return None
            return context

    def complete_fixed_frame_work(
        self,
        *,
        work_type: str,
        work_uid: str,
        command_uid: str,
        command_result: dict,
        context: dict,
        observation: dict,
        event_uid: str,
        event_type: str,
        event_payload: dict,
        device_name: str,
        target_type: str,
        bag_baseline: Optional[dict] = None,
        fullness_transition: Optional[dict] = None,
        release_work_slot: bool = True,
    ) -> str:
        """Atomically finish a DD/EF work item and optionally release its slot."""
        if not isinstance(release_work_slot, bool):
            raise TypeError("release_work_slot must be boolean")
        with self.transaction():
            self._reject_archived_delivery_event(self._conn, event_type, work_uid, event_payload.get("sessionUid"))
            slot = self._conn.execute(
                """SELECT work_type, work_uid FROM work_slot
                   WHERE slot_id=1"""
            ).fetchone()
            if (
                not slot
                or slot["work_type"] != work_type
                or slot["work_uid"] != work_uid
            ):
                return "UNKNOWN"
            existing = self._conn.execute(
                "SELECT event_uid FROM event_outbox WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if existing:
                return "DUPLICATE"
            sequence = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type=event_type,
                target_type=target_type,
                target_uid=work_uid,
                command_uid=command_uid,
                payload=event_payload,
            )
            self._insert_event(self._conn, event, event_type)
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(command_result, ensure_ascii=False),
                    command_uid,
                ),
            )
            now = self._now()
            self._upsert_state(
                self._conn,
                "fixed_frame_latest_observation_json",
                _json.dumps(observation, ensure_ascii=False),
                now,
            )
            self._upsert_state(
                self._conn,
                (
                    f"port_{context['port_no']}_"
                    f"{work_type.lower()}_runtime_context_json"
                ),
                _json.dumps(context, ensure_ascii=False),
                now,
            )
            if bag_baseline is not None:
                self._upsert_bag_baseline_in_tx(
                    self._conn,
                    bag_baseline,
                )
            if fullness_transition is not None:
                self._apply_fullness_transition_in_tx(
                    self._conn,
                    fullness_transition,
                )
            if work_type == WORK_TYPE_CLEAN:
                self._set_clean_restart_interlock_in_tx(
                    self._conn,
                    int(context["port_no"]),
                    False,
                )
            if release_work_slot:
                self._conn.execute(
                    """UPDATE work_slot
                       SET work_type='NONE', work_uid=NULL,
                           work_state=NULL, port_no=NULL,
                           context_json=NULL, updated_at=?
                       WHERE slot_id=1""",
                    (now,),
                )
            else:
                self._conn.execute(
                    """UPDATE work_slot SET context_json=?, updated_at=?
                       WHERE slot_id=1 AND work_uid=?""",
                    (
                        _json.dumps(context, ensure_ascii=False),
                        now,
                        work_uid,
                    ),
                )
            return "ACCEPTED"

    def quarantine_delivery_recovery(
        self,
        *,
        work_uid: str,
        original_command_uid: str,
        recovery_command_uid: str,
        recovery_uid: str,
        context: dict,
        event_payload: dict,
        device_name: str,
        release_work_slot: bool = True,
    ) -> str:
        """Atomically publish an issue-only terminal and release exact work.

        This intentionally does not call the normal fixed-frame completion
        path: no observation, fullness, baseline, delivery order or financial
        fact is derived from an unknown physical outcome.
        """

        if not isinstance(release_work_slot, bool):
            raise TypeError("release_work_slot must be boolean")
        with self.transaction():
            existing = self._conn.execute(
                """SELECT event_type, payload_json
                   FROM event_outbox WHERE event_uid=?""",
                (recovery_uid,),
            ).fetchone()
            if existing is not None:
                try:
                    event = _json.loads(existing["payload_json"])
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "delivery recovery evidence is corrupt"
                    ) from error
                if not (
                    existing["event_type"]
                    == "DELIVERY_RECOVERY_QUARANTINED"
                    and event.get("eventUid") == recovery_uid
                    and event.get("commandUid") == recovery_command_uid
                    and event.get("target")
                    == {"type": "DELIVERY_SESSION", "uid": work_uid}
                    and event.get("payload") == event_payload
                ):
                    raise ValueError("delivery recovery evidence conflicts")
                return "DUPLICATE"

            slot = self._conn.execute(
                """SELECT work_type, work_uid, work_state
                   FROM work_slot WHERE slot_id=1"""
            ).fetchone()
            if not (
                slot is not None
                and slot["work_type"] == WORK_TYPE_DELIVERY
                and slot["work_uid"] == work_uid
                and slot["work_state"] == "RECOVERY_REQUIRED"
            ):
                return "UNKNOWN"
            original = self._conn.execute(
                """SELECT command_type, state
                   FROM command_inbox WHERE command_uid=?""",
                (original_command_uid,),
            ).fetchone()
            recovery = self._conn.execute(
                """SELECT command_type, state
                   FROM command_inbox WHERE command_uid=?""",
                (recovery_command_uid,),
            ).fetchone()
            if not (
                original is not None
                and original["command_type"] == "START_DELIVERY_SESSION"
                and original["state"] == "RECOVERY_REQUIRED"
                and recovery is not None
                and recovery["command_type"]
                == "QUARANTINE_DELIVERY_RECOVERY"
                and recovery["state"] == "PROCESSING"
            ):
                raise ValueError("delivery recovery command state changed")

            sequence = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=recovery_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DELIVERY_RECOVERY_QUARANTINED",
                target_type="DELIVERY_SESSION",
                target_uid=work_uid,
                command_uid=recovery_command_uid,
                payload=event_payload,
            )
            self._insert_event(
                self._conn,
                event,
                "DELIVERY_RECOVERY_QUARANTINED",
            )
            now = self._now()
            original_result = {
                "disposition": "RECOVERY_QUARANTINED",
                "recoveryUid": recovery_uid,
                "physicalOutcome": "UNKNOWN",
                "businessValue": "NONE",
            }
            recovery_result = {
                "disposition": "QUARANTINED",
                "recoveryUid": recovery_uid,
                "sessionUid": work_uid,
                "originalCommandUid": original_command_uid,
                "businessValue": "NONE",
            }
            updated_original = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND command_type=
                       'START_DELIVERY_SESSION'
                     AND state='RECOVERY_REQUIRED'""",
                (
                    now,
                    _json.dumps(original_result, ensure_ascii=False),
                    original_command_uid,
                ),
            )
            updated_recovery = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND command_type=
                       'QUARANTINE_DELIVERY_RECOVERY'
                     AND state='PROCESSING'""",
                (
                    now,
                    _json.dumps(recovery_result, ensure_ascii=False),
                    recovery_command_uid,
                ),
            )
            if (
                updated_original.rowcount != 1
                or updated_recovery.rowcount != 1
            ):
                raise ValueError("delivery recovery command state changed")
            self._upsert_state(
                self._conn,
                "fixed_frame_latest_delivery_recovery_json",
                _json.dumps(event_payload, ensure_ascii=False),
                now,
            )
            if release_work_slot:
                self._clear_work_slot_in_tx(self._conn)
            else:
                updated_slot = self._conn.execute(
                    """UPDATE work_slot
                       SET context_json=?, updated_at=?
                       WHERE slot_id=1 AND work_type=? AND work_uid=?
                         AND work_state='RECOVERY_REQUIRED'""",
                    (
                        _json.dumps(context, ensure_ascii=False),
                        now,
                        WORK_TYPE_DELIVERY,
                        work_uid,
                    ),
                )
                if updated_slot.rowcount != 1:
                    raise ValueError("delivery recovery work changed")
            return "ACCEPTED"

    def fail_fixed_frame_work(
        self,
        *,
        work_uid: str,
        command: dict,
        error_code: str,
        mcu_command_uid: Optional[str],
        stage: str = "FAILED",
        release_work_slot: bool = True,
        work_context: Optional[dict] = None,
    ) -> bool:
        """Atomically fail a fixed-frame work item without replaying it."""
        if not isinstance(release_work_slot, bool):
            raise TypeError("release_work_slot must be boolean")
        with self.transaction():
            slot = self._conn.execute(
                "SELECT work_uid FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if not slot or slot["work_uid"] != work_uid:
                return False
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    stage,
                    mcu_command_uid=mcu_command_uid,
                    error_code=error_code,
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            if release_work_slot:
                self._conn.execute(
                    """UPDATE work_slot
                       SET work_type='NONE', work_uid=NULL,
                           work_state=NULL, port_no=NULL,
                           context_json=NULL, updated_at=?
                       WHERE slot_id=1""",
                    (self._now(),),
                )
            elif work_context is not None:
                self._conn.execute(
                    """UPDATE work_slot SET context_json=?, updated_at=?
                       WHERE slot_id=1 AND work_uid=?""",
                    (
                        _json.dumps(work_context, ensure_ascii=False),
                        self._now(),
                        work_uid,
                    ),
                )
            return True

    def mark_fixed_frame_result_overdue_for_recovery(
        self,
        *,
        work_type: str,
        work_uid: str,
        command_type: str,
        command_uid: str,
        expected_command_state: str,
        work_context: dict,
    ) -> bool:
        """Retain already-dispatched fixed-frame work while its result is late.

        Move the exact command and work slot into a non-replayable recovery
        state in one transaction. A later uniquely-bound DD or EF can still
        report the original physical result while a new operation stays
        blocked.
        """

        expected_command_types = {
            WORK_TYPE_DELIVERY: "START_DELIVERY_SESSION",
            WORK_TYPE_CLEAN: "START_CLEAN_OPERATION",
        }
        if expected_command_types.get(work_type) != command_type:
            raise ValueError("fixed-frame overdue work binding is invalid")
        if expected_command_state not in {
            "WAITING_MCU_RESULT",
            "FAILED",
            "RECOVERY_REQUIRED",
        }:
            raise ValueError("fixed-frame overdue command state is invalid")
        with self.transaction(immediate=True):
            slot = self._conn.execute(
                """SELECT work_type, work_uid FROM work_slot
                   WHERE slot_id=1"""
            ).fetchone()
            command = self._conn.execute(
                """SELECT command_type, state FROM command_inbox
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if (
                slot is None
                or slot["work_type"] != work_type
                or slot["work_uid"] != work_uid
                or command is None
                or command["command_type"] != command_type
                or command["state"] != expected_command_state
            ):
                return False
            command_updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', processed_at=NULL,
                       processing_started_at=NULL,
                       last_error='MCU_RESULT_OVERDUE'
                   WHERE command_uid=? AND command_type=?
                     AND state=?""",
                (command_uid, command_type, expected_command_state),
            )
            slot_updated = self._conn.execute(
                """UPDATE work_slot
                   SET work_state='RECOVERY_REQUIRED', context_json=?,
                       updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(work_context, ensure_ascii=False),
                    self._now(),
                    work_type,
                    work_uid,
                ),
            )
            if command_updated.rowcount != 1 or slot_updated.rowcount != 1:
                raise ValueError(
                    "fixed-frame overdue state changed"
                )
            return True

    def mark_fixed_frame_state_corrupt_for_recovery(
        self,
        *,
        work_type: str,
        work_uid: str,
        command_type: str,
        command_uid: Optional[str],
        work_context: dict,
    ) -> bool:
        """Retain fixed-frame work whose command/action binding is damaged.

        Once AA may have reached the fixed-frame MCU, a missing inbox row is
        evidence of damaged local state, not evidence that no physical action
        occurred. Keep the only work slot occupied so no later operation can
        claim an otherwise anonymous DD or EF result.
        """

        expected_command_types = {
            WORK_TYPE_DELIVERY: "START_DELIVERY_SESSION",
            WORK_TYPE_CLEAN: "START_CLEAN_OPERATION",
        }
        if expected_command_types.get(work_type) != command_type:
            raise ValueError("fixed-frame corrupt work binding is invalid")
        with self.transaction(immediate=True):
            slot = self._conn.execute(
                """SELECT work_type, work_uid FROM work_slot
                   WHERE slot_id=1"""
            ).fetchone()
            if (
                slot is None
                or slot["work_type"] != work_type
                or slot["work_uid"] != work_uid
            ):
                return False
            if isinstance(command_uid, str) and command_uid:
                # If the row still exists, make the command non-replayable as
                # well.  A missing row is the corruption being contained and
                # must not prevent the slot itself from becoming a lock.
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='RECOVERY_REQUIRED', processed_at=NULL,
                           processing_started_at=NULL,
                           last_error='FIXED_FRAME_STATE_CORRUPT'
                       WHERE command_uid=?
                         AND command_type=?
                         AND state IN (
                           'WAITING_MCU_RESULT',
                           'RECOVERY_REQUIRED',
                           'FAILED'
                         )""",
                    (command_uid, command_type),
                )
            updated = self._conn.execute(
                """UPDATE work_slot
                   SET work_state='RECOVERY_REQUIRED', context_json=?,
                       updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(work_context, ensure_ascii=False),
                    self._now(),
                    work_type,
                    work_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "fixed-frame corrupt state changed"
                )
            return True

    def mark_clean_window_expired_for_recovery(
        self,
        work_uid: str,
    ) -> str:
        """Atomically retain an expired clean for the original recovery flow."""

        error_code = "COMMAND_EXPIRED"
        with self.transaction():
            slot = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if (
                slot is None
                or slot["work_type"] != WORK_TYPE_CLEAN
                or slot["work_uid"] != work_uid
            ):
                return "STALE"
            context = (
                _json.loads(slot["context_json"])
                if slot["context_json"]
                else {}
            )
            command_uid = context.get("start_command_uid")
            if not command_uid:
                raise ValueError(
                    "active clean has no persisted start command"
                )
            row = self._conn.execute(
                """SELECT payload_json FROM command_inbox
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "active clean start command is not persisted"
                )
            command = _json.loads(row["payload_json"])
            if (
                command.get("commandUid") != command_uid
                or command.get("commandType") != "START_CLEAN_OPERATION"
            ):
                raise ValueError(
                    "active clean start command identity is invalid"
                )

            context["phase"] = "CLEAN_RECOVERY_REQUIRED"
            context["recovery_error_code"] = error_code
            now = self._now()
            self._conn.execute(
                """UPDATE work_slot
                   SET work_state='RECOVERY_REQUIRED', context_json=?,
                       updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(context, ensure_ascii=False),
                    now,
                    WORK_TYPE_CLEAN,
                    work_uid,
                ),
            )
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (now, error_code, command_uid),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "active clean start command state could not be updated"
                )
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "FAILED",
                mcu_command_uid=context.get("start_mcu_command_uid"),
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise ValueError("command observation conflict")
            return "RECOVERY_REQUIRED"

    def abort_interrupted_work(self) -> dict[str, Any]:
        """Cancel one non-durable physical work slot after edge restart.

        A completion already present in the reliable outbox is authoritative:
        its event and photos remain replayable and no FAILED observation is
        generated. Otherwise the start command is failed with EDGE_RESTARTED,
        incomplete photo uploads are stopped, and an interrupted clean sets a
        persistent port interlock that only a later completed clean can clear.
        """
        with self.transaction():
            slot = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if not slot or slot["work_type"] == WORK_TYPE_NONE:
                return {"outcome": "NO_ACTIVE_WORK"}
            work_type = slot["work_type"]
            work_uid = slot["work_uid"]
            port_no = slot["port_no"]
            context = (
                _json.loads(slot["context_json"])
                if slot["context_json"]
                else {}
            )
            if isinstance(context.get("job_safety"), dict):
                # The legacy restart path predates the permanent action
                # ledger. Clearing this slot would discard the only local
                # completion/reconciliation facts while updater.db correctly
                # remains locked. Stage-four work is resolved by WorkManager
                # against that permanent ledger, never by this generic abort.
                return {
                    "outcome": "JOB_SAFETY_RECONCILIATION_REQUIRED",
                    "work_type": work_type,
                    "work_uid": work_uid,
                }
            completion_type = {
                WORK_TYPE_DELIVERY: "DELIVERY_COMPLETE",
                WORK_TYPE_CLEAN: "CLEAN_COMPLETE",
                WORK_TYPE_FULLNESS: "FULLNESS_SAMPLE_COMPLETE",
                WORK_TYPE_BASELINE: "BASELINE_MEASUREMENT_COMPLETE",
            }.get(work_type)
            durable_completion = None
            if completion_type:
                durable_completion = self._conn.execute(
                    """SELECT event_uid FROM event_outbox
                       WHERE work_uid=? AND event_type=?
                         AND tombstoned=0
                       LIMIT 1""",
                    (work_uid, completion_type),
                ).fetchone()
            if durable_completion:
                if work_type == WORK_TYPE_CLEAN and port_no is not None:
                    self._set_clean_restart_interlock_in_tx(
                        self._conn, int(port_no), False
                    )
                self._clear_work_slot_in_tx(self._conn)
                return {
                    "outcome": "DURABLE_COMPLETION_PRESERVED",
                    "work_type": work_type,
                    "work_uid": work_uid,
                }

            command_uid = (
                context.get("start_command_uid")
                or context.get("command_uid")
            )
            command = None
            if command_uid:
                row = self._conn.execute(
                    "SELECT payload_json FROM command_inbox WHERE command_uid=?",
                    (command_uid,),
                ).fetchone()
                command = _json.loads(row["payload_json"]) if row else None
            command_observed = False
            if command and command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    "FAILED",
                    mcu_command_uid=context.get("start_mcu_command_uid"),
                    error_code="EDGE_RESTARTED",
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
                command_observed = True
            if command:
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL,
                           last_error='EDGE_RESTARTED'
                       WHERE command_uid=?""",
                    (self._now(), command_uid),
                )
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='DEAD', next_retry_at=NULL,
                       last_error='EDGE_RESTARTED'
                   WHERE work_uid=?
                     AND tombstoned=0
                     AND state NOT IN ('UPLOADED', 'DEAD')""",
                (work_uid,),
            )
            if work_type == WORK_TYPE_CLEAN and port_no is not None:
                self._set_clean_restart_interlock_in_tx(
                    self._conn, int(port_no), True
                )
            self._clear_work_slot_in_tx(self._conn)
            return {
                "outcome": "ABORTED",
                "work_type": work_type,
                "work_uid": work_uid,
                "command_observed": command_observed,
            }

    @staticmethod
    def _clear_work_slot_in_tx(conn) -> None:
        conn.execute(
            """UPDATE work_slot
               SET work_type='NONE', work_uid=NULL,
                   work_state=NULL, port_no=NULL,
                   context_json=NULL, updated_at=datetime('now')
               WHERE slot_id=1"""
        )

    @staticmethod
    def _clean_restart_interlock_key(port_no: int) -> str:
        return f"port_{port_no}_clean_restart_interlock"

    @staticmethod
    def _clean_restart_interlock_value_active(value: Optional[str]) -> bool:
        # Unknown historical/corrupt values fail closed.  Only the explicit
        # inactive representation admits work without a matching handoff.
        return value not in {None, "", "false"}

    @staticmethod
    def _decode_clean_restart_interlock_metadata(
        value: Optional[str],
    ) -> Optional[dict]:
        if not value or value in {"true", "false"}:
            return None
        try:
            metadata = _json.loads(value)
        except (TypeError, ValueError):
            return None
        if (
            not isinstance(metadata, dict)
            or metadata.get("profile")
            != "native-clean-bag-interlock-v1"
        ):
            return None
        return metadata

    def _clean_restart_interlock_raw_in_tx(
        self,
        conn,
        port_no: int,
    ) -> Optional[str]:
        row = conn.execute(
            """SELECT state_value FROM device_state
               WHERE state_key=?""",
            (self._clean_restart_interlock_key(port_no),),
        ).fetchone()
        return row["state_value"] if row else None

    def _set_clean_restart_interlock_in_tx(
        self,
        conn,
        port_no: int,
        active: bool,
        *,
        metadata: Optional[dict] = None,
    ) -> None:
        current = self._clean_restart_interlock_raw_in_tx(conn, port_no)
        current_metadata = self._decode_clean_restart_interlock_metadata(
            current,
        )
        if active and metadata is not None:
            if (
                not isinstance(metadata, dict)
                or metadata.get("profile")
                != "native-clean-bag-interlock-v1"
                or metadata.get("portNo") != port_no
            ):
                raise ValueError("invalid native clean bag interlock metadata")
            if current_metadata is not None and current_metadata != metadata:
                raise ValueError("another native clean bag interlock is active")
            value = _json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        elif active:
            # A legacy writer may not erase the evidence carried by a newer
            # structured lock.
            value = current if current_metadata is not None else "true"
        else:
            # Old completion/restart paths are allowed to clear only the old
            # boolean latch. Structured recovery is cleared exclusively by a
            # matching backend-authorized START inside acquire_work_slot().
            value = current if current_metadata is not None else "false"
        self._upsert_state(
            conn,
            self._clean_restart_interlock_key(port_no),
            value,
            self._now(),
        )

    def clean_restart_interlock_active(self, port_no: int) -> bool:
        return self._clean_restart_interlock_value_active(
            self.get_state(
                self._clean_restart_interlock_key(port_no),
                "false",
            )
        )

    def get_clean_restart_interlock_metadata(
        self,
        port_no: int,
    ) -> Optional[dict]:
        return self._decode_clean_restart_interlock_metadata(
            self.get_state(
                self._clean_restart_interlock_key(port_no),
                "false",
            )
        )

    def clear_clean_restart_interlock(self, port_no: int) -> None:
        with self.transaction():
            self._set_clean_restart_interlock_in_tx(
                self._conn, port_no, False
            )

    def get_bag_baseline(self, bag_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bag_baseline WHERE bag_uid=?",
                (bag_uid,),
            ).fetchone()
        return dict(row) if row else None

    def get_port_fullness_state(
        self,
        port_no: int,
        bag_uid: str,
    ) -> str:
        """Absence of a FULL fact is the deliberate NOT_FULL default."""
        with self._lock:
            row = self._conn.execute(
                """SELECT bag_uid, state
                   FROM port_fullness_state
                   WHERE port_no=?""",
                (port_no,),
            ).fetchone()
        if row is None or row["bag_uid"] != bag_uid:
            return "NOT_FULL"
        return row["state"]

    def _apply_fullness_transition_in_tx(
        self,
        conn,
        transition: dict,
    ) -> bool:
        port_no = transition["port_no"]
        bag_uid = transition["bag_uid"]
        desired_state = transition["state"]
        current = conn.execute(
            """SELECT bag_uid, state
               FROM port_fullness_state
               WHERE port_no=?""",
            (port_no,),
        ).fetchone()
        current_state = (
            current["state"]
            if current is not None and current["bag_uid"] == bag_uid
            else "NOT_FULL"
        )
        now = self._now()
        if current_state == desired_state:
            conn.execute(
                """INSERT INTO port_fullness_state
                   (port_no, bag_uid, state, last_state_change_uid,
                    last_event_uid, updated_at)
                   VALUES (?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(port_no) DO UPDATE SET
                     bag_uid=excluded.bag_uid,
                     state=excluded.state,
                     last_state_change_uid=CASE
                       WHEN port_fullness_state.bag_uid=excluded.bag_uid
                       THEN port_fullness_state.last_state_change_uid
                       ELSE NULL END,
                     last_event_uid=CASE
                       WHEN port_fullness_state.bag_uid=excluded.bag_uid
                       THEN port_fullness_state.last_event_uid
                       ELSE NULL END,
                     updated_at=excluded.updated_at""",
                (port_no, bag_uid, desired_state, now),
            )
            return False

        sequence = self._next_seq(conn)
        envelope = build_event_envelope(
            event_uid=transition["event_uid"],
            device_name=transition["device_name"],
            edge_event_sequence=sequence,
            event_type="FULLNESS_STATE_CHANGED",
            target_type="PORT_FULLNESS_STATE",
            target_uid=transition["state_change_uid"],
            command_uid=None,
            payload=transition["payload"],
        )
        self._insert_event(
            conn,
            envelope,
            "FULLNESS_STATE_CHANGED",
        )
        conn.execute(
            """INSERT INTO port_fullness_state
               (port_no, bag_uid, state, last_state_change_uid,
                last_event_uid, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(port_no) DO UPDATE SET
                 bag_uid=excluded.bag_uid,
                 state=excluded.state,
                 last_state_change_uid=excluded.last_state_change_uid,
                 last_event_uid=excluded.last_event_uid,
                 updated_at=excluded.updated_at""",
            (
                port_no,
                bag_uid,
                desired_state,
                transition["state_change_uid"],
                transition["event_uid"],
                now,
            ),
        )
        return True

    @staticmethod
    def _upsert_bag_baseline_in_tx(conn, baseline: dict) -> None:
        conn.execute(
            """INSERT INTO bag_baseline
               (bag_uid, weight_grams, source_kind,
                source_work_type, source_work_uid,
                source_mcu_boot_id, source_mcu_event_sequence,
                source_observed_at, measurement_uid, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(bag_uid) DO UPDATE SET
                 weight_grams=excluded.weight_grams,
                 source_kind=excluded.source_kind,
                 source_work_type=excluded.source_work_type,
                 source_work_uid=excluded.source_work_uid,
                 source_mcu_boot_id=excluded.source_mcu_boot_id,
                 source_mcu_event_sequence=
                   excluded.source_mcu_event_sequence,
                 source_observed_at=excluded.source_observed_at,
                 measurement_uid=excluded.measurement_uid,
                 updated_at=excluded.updated_at""",
            (
                baseline["bag_uid"],
                baseline["weight_grams"],
                baseline["source_kind"],
                baseline.get("source_work_type"),
                baseline.get("source_work_uid"),
                baseline.get("source_mcu_boot_id"),
                baseline.get("source_mcu_event_sequence"),
                baseline.get("source_observed_at"),
                baseline["measurement_uid"],
                baseline["updated_at"],
            ),
        )

    def complete_fixed_frame_local_result(
        self,
        *,
        result_type: str,
        result_key: str,
        command: dict,
        event_uid: str,
        event_type: str,
        target_type: str,
        target_uid: str,
        event_payload: dict,
        result: dict,
        bag_baseline: Optional[dict] = None,
        work_uid: Optional[str] = None,
        work_context: Optional[dict] = None,
    ) -> str:
        """Freeze a local fixed-frame result and its reliable event."""
        with self.transaction():
            existing = self._conn.execute(
                """SELECT event_uid, result_json
                   FROM fixed_frame_local_result
                   WHERE result_type=? AND result_key=?""",
                (result_type, result_key),
            ).fetchone()
            if existing:
                frozen_result = (
                    _json.loads(existing["result_json"])
                    if existing["result_json"]
                    else {}
                )
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='COMPLETED', processed_at=?,
                           processing_started_at=NULL,
                           result_json=?, last_error=NULL
                       WHERE command_uid=?""",
                    (
                        self._now(),
                        _json.dumps(
                            frozen_result,
                            ensure_ascii=False,
                        ),
                        command["commandUid"],
                    ),
                )
                self._freeze_optional_work_context_in_tx(
                    work_uid,
                    work_context,
                )
                return "DUPLICATE"
            sequence = self._next_seq(self._conn)
            envelope = build_event_envelope(
                event_uid=event_uid,
                device_name=command["targetDeviceName"],
                edge_event_sequence=sequence,
                event_type=event_type,
                target_type=target_type,
                target_uid=target_uid,
                command_uid=command["commandUid"],
                payload=event_payload,
            )
            self._insert_event(self._conn, envelope, event_type)
            if bag_baseline is not None:
                self._upsert_bag_baseline_in_tx(
                    self._conn,
                    bag_baseline,
                )
            self._conn.execute(
                """INSERT INTO fixed_frame_local_result
                   (result_type, result_key, command_uid, event_uid,
                    payload_json, result_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    result_type,
                    result_key,
                    command["commandUid"],
                    event_uid,
                    _json.dumps(event_payload, ensure_ascii=False),
                    _json.dumps(result, ensure_ascii=False),
                ),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL,
                       result_json=?, last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(result, ensure_ascii=False),
                    command["commandUid"],
                ),
            )
            self._freeze_optional_work_context_in_tx(
                work_uid,
                work_context,
            )
            return "ACCEPTED"

    def _freeze_optional_work_context_in_tx(
        self,
        work_uid: Optional[str],
        work_context: Optional[dict],
    ) -> None:
        if work_context is None:
            return
        if not work_uid:
            raise ValueError("work_uid is required with work_context")
        updated = self._conn.execute(
            """UPDATE work_slot SET context_json=?, updated_at=?
               WHERE slot_id=1 AND work_uid=?""",
            (
                _json.dumps(work_context, ensure_ascii=False),
                self._now(),
                work_uid,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("active work context changed")

    def reserve_compat_mcu_event_sequence(self) -> int:
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='fixed_frame_compat_event_sequence'"""
            ).fetchone()
            sequence = int(row["state_value"] if row else 0) + 1
            self._upsert_state(
                self._conn,
                "fixed_frame_compat_event_sequence",
                str(sequence),
                self._now(),
            )
            return sequence

    # ── 原子事务 4: 接收业务确认 ──

    def receive_business_confirmation(self, confirmation_uid: str, event_uid: str,
                                      outcome: str, payload: Optional[dict] = None) -> str:
        with self.transaction():
            conn = self._conn
            if (conn.execute("SELECT 1 FROM native_result_report_outbox WHERE event_uid=?", (event_uid,)).fetchone()
                    or conn.execute("SELECT 1 FROM native_delivery_issue_report WHERE event_uid=?", (event_uid,)).fetchone()):
                raise ValueError("native report requires the complete backend confirmation command")
            existing = conn.execute(
                "SELECT confirmation_uid FROM confirmation_inbox WHERE confirmation_uid=?",
                (confirmation_uid,),
            ).fetchone()
            if existing:
                return "DUPLICATE"
            conn.execute(
                "INSERT INTO confirmation_inbox (confirmation_uid, event_uid, outcome, payload_json) VALUES (?,?,?,?)",
                (confirmation_uid, event_uid, outcome,
                 _json.dumps(payload, ensure_ascii=False) if payload else None),
            )
            conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=?""",
                (EVENT_CONFIRMED, self._now(), event_uid),
            )
            return "ACCEPTED"

    def receive_business_confirmation_and_create_receipt(
        self,
        command_uid: Optional[str] = None,
        confirmation_payload: Optional[dict] = None,
        device_name: str = "",
        *,
        command: Optional[dict] = None,
    ) -> str:
        """Persist I-045 confirmation and create the required receipt event.

        Returns ACCEPTED, DUPLICATE or REJECTED. A rejected confirmation is not
        persisted because it does not match a local reliable event.
        """

        if command is not None:
            command_uid = command["commandUid"]
            confirmation_payload = command["payload"]
            stable_command = dict(command)
            stable_command.pop("cosGrant", None)
        else:
            stable_command = {
                "commandUid": command_uid,
                "payload": confirmation_payload,
            }
        if not isinstance(confirmation_payload, dict) or not command_uid:
            return "REJECTED"
        confirmation_uid = confirmation_payload.get("confirmationUid")
        original_event_uid = confirmation_payload.get("originalEventUid")
        original_payload_sha256 = confirmation_payload.get("originalPayloadSha256")
        outcome = confirmation_payload.get("outcome")
        if not confirmation_uid or not original_event_uid or not original_payload_sha256 or not outcome:
            return "REJECTED"
        confirmation_sha256 = canonical_payload_sha256(stable_command)

        with self.transaction():
            conn = self._conn
            native_task = conn.execute("SELECT * FROM native_result_report_outbox WHERE event_uid=?", (original_event_uid,)).fetchone()
            if native_task is not None:
                from native_result_report import confirmation_for_report
                stable_command, _ = confirmation_for_report(self, conn, native_task, command, device_name)
            issue_task = conn.execute("SELECT * FROM native_delivery_issue_report WHERE event_uid=?", (original_event_uid,)).fetchone()
            if issue_task is not None:
                from native_delivery_issue_report import confirmation_for_issue_report
                stable_command, _ = confirmation_for_issue_report(self, conn, issue_task, command, device_name)
            existing = conn.execute(
                """SELECT command_uid, canonical_sha256,
                          payload_json, receipt_event_uid
                   FROM confirmation_inbox
                   WHERE confirmation_uid=?""",
                (confirmation_uid,),
            ).fetchone()
            if existing:
                stored_payload = (
                    _json.loads(existing["payload_json"])
                    if existing["payload_json"]
                    else None
                )
                same = (
                    existing["command_uid"] in (None, command_uid)
                    and stored_payload == confirmation_payload
                    and existing["canonical_sha256"] in (
                        None,
                        confirmation_sha256,
                    )
                )
                if not same:
                    return "CONFLICT"
                self._save_native_result_confirmation(conn, native_task, stable_command)
                self._save_native_delivery_issue_confirmation(conn, issue_task, stable_command)
                if existing["receipt_event_uid"]:
                    conn.execute(
                        """UPDATE event_outbox
                           SET state='PENDING', mqtt_msg_id=NULL,
                               next_retry_at=NULL, confirmed_at=NULL,
                               tombstoned=0
                           WHERE event_uid=? AND event_type=
                             'BUSINESS_CONFIRMATION_RECEIPT'""",
                        (existing["receipt_event_uid"],),
                    )
                return "DUPLICATE"

            event_confirmation = conn.execute(
                """SELECT confirmation_uid FROM confirmation_inbox
                   WHERE event_uid=?""",
                (original_event_uid,),
            ).fetchone()
            if event_confirmation:
                return "CONFLICT"
            event = conn.execute(
                "SELECT payload_json FROM event_outbox WHERE event_uid=?",
                (original_event_uid,),
            ).fetchone()
            if not event:
                return "REJECTED"
            event_json = _json.loads(event["payload_json"])
            if (
                event_json.get("deliveryClass") is not None
                and event_json.get("deliveryClass")
                != "RELIABLE_FACT"
            ):
                return "REJECTED"
            local_sha = event_json.get("payloadSha256")
            if local_sha != original_payload_sha256:
                return "REJECTED"

            seq = self._next_seq(conn)
            receipt = build_business_confirmation_receipt(
                device_name=device_name,
                command_uid=command_uid,
                confirmation_uid=confirmation_uid,
                original_event_uid=original_event_uid,
                original_payload_sha256=original_payload_sha256,
                outcome=outcome,
                edge_event_sequence=seq,
            )
            conn.execute(
                """INSERT INTO event_outbox
                   (event_uid, edge_event_sequence, event_type,
                    payload_json, work_uid)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt["eventUid"],
                    seq,
                    "BUSINESS_CONFIRMATION_RECEIPT",
                    _json.dumps(receipt, ensure_ascii=False),
                    original_event_uid,
                ),
            )
            conn.execute(
                """INSERT INTO confirmation_inbox
                   (confirmation_uid, event_uid, outcome, payload_json,
                    command_uid, canonical_sha256, receipt_event_uid)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    confirmation_uid,
                    original_event_uid,
                    outcome,
                    _json.dumps(
                        confirmation_payload,
                        ensure_ascii=False,
                    ),
                    command_uid,
                    confirmation_sha256,
                    receipt["eventUid"],
                ),
            )
            conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=?""",
                (
                    EVENT_CONFIRMED,
                    self._now(),
                    original_event_uid,
                ),
            )
            if outcome == "EVENT_QUARANTINED":
                self._upsert_state(
                    conn,
                    f"quarantined_event:{original_event_uid}",
                    _json.dumps(
                        {
                            "confirmationUid": confirmation_uid,
                            "errorCode": confirmation_payload.get(
                                "errorCode"
                            ),
                            "quarantineUid": confirmation_payload.get(
                                "quarantineUid"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    self._now(),
                )
            self._save_native_result_confirmation(conn, native_task, stable_command)
            self._save_native_delivery_issue_confirmation(conn, issue_task, stable_command)
            return "ACCEPTED"

    # ── 原子事务 5: 登记照片 ──

    def register_photo(
        self,
        photo_uid: str,
        slot_name: str,
        local_path: str,
        cos_key: Optional[str] = None,
        work_uid: Optional[str] = None,
        *,
        work_type: Optional[str] = None,
        device_name: Optional[str] = None,
        content_sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        captured_at: Optional[str] = None,
        captured_clock_quality: Optional[str] = None,
    ) -> str:
        if captured_at is None:
            if captured_clock_quality is not None:
                raise ValueError(
                    "captured clock quality requires captured_at"
                )
        else:
            # Calls predating EdgeStore v17 supplied a captured timestamp but
            # had no separate quality argument. Preserve their historical
            # semantics: an explicitly supplied capture time was trusted.
            captured_clock_quality = captured_clock_quality or "SYNCED"
            if captured_clock_quality not in {
                "SYNCED", "ESTIMATED", "UNAVAILABLE"
            }:
                raise ValueError("captured clock quality is invalid")
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            conn.execute(
                """INSERT INTO photo_outbox
                   (photo_uid, slot_name, local_path, cos_key, work_uid,
                    work_type, device_name, content_sha256, size_bytes,
                    captured_at, captured_clock_quality)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    photo_uid,
                    slot_name,
                    local_path,
                    cos_key,
                    work_uid,
                    work_type,
                    device_name,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    captured_clock_quality,
                ),
            )
            return "ACCEPTED"

    # ── 原子事务 2: 接收 MCU 事件 ──

    def receive_mcu_event(self, event_uid: str, event_type: str, payload: dict,
                          work_uid: Optional[str] = None) -> str:
        with self.transaction():
            conn = self._conn
            self._reject_archived_delivery_event(conn, event_type, work_uid, payload.get("sessionUid"))
            existing = conn.execute(
                "SELECT state FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            seq = self._next_seq(conn)
            conn.execute(
                "INSERT INTO event_outbox (event_uid, edge_event_sequence, event_type, payload_json, work_uid) VALUES (?,?,?,?,?)",
                (event_uid, seq, event_type, _json.dumps(payload, ensure_ascii=False), work_uid),
            )
            return "ACCEPTED"

    # ── 原子事务 3: 创建边缘事件 ──

    def create_edge_event(self, event_uid: str, event_type: str, payload: dict,
                          work_uid: Optional[str] = None,
                          work_state_update: Optional[dict] = None,
                          *,
                          device_name: Optional[str] = None,
                          target_type: Optional[str] = None,
                          target_uid: Optional[str] = None,
                          command_uid: Optional[str] = None,
                          delivery_class: str = "RELIABLE_FACT",
                          fullness_transition: Optional[dict] = None,
                          mcu_receive_generation: Optional[int] = None,
                          mcu_boot_id: Optional[int] = None,
                          mcu_event_sequence: Optional[int] = None) -> str:
        with self.transaction():
            conn = self._conn
            self._reject_archived_delivery_event(conn, event_type, work_uid, target_uid, payload.get("sessionUid"))
            has_mcu_identity = (
                isinstance(mcu_receive_generation, int)
                and not isinstance(mcu_receive_generation, bool)
                and mcu_receive_generation >= 0
                and isinstance(mcu_boot_id, int)
                and not isinstance(mcu_boot_id, bool)
                and mcu_boot_id > 0
                and isinstance(mcu_event_sequence, int)
                and not isinstance(mcu_event_sequence, bool)
                and mcu_event_sequence > 0
            )
            if has_mcu_identity:
                derived = conn.execute(
                    """SELECT event_uid FROM mcu_derived_event
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=? AND mcu_event_sequence=?
                         AND event_type=?""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_type,
                    ),
                ).fetchone()
                if derived:
                    if work_state_update and work_uid:
                        duplicate_context = work_state_update.get(
                            "context", {}
                        )
                        conn.execute(
                            """UPDATE work_slot
                               SET work_state=?, context_json=?, updated_at=?
                               WHERE work_uid=?""",
                            (
                                work_state_update.get("state"),
                                _json.dumps(
                                    duplicate_context,
                                    ensure_ascii=False,
                                ),
                                self._now(),
                                work_uid,
                            ),
                        )
                    conn.execute(
                        """UPDATE mcu_event_inbox
                           SET state='PROCESSED', processed_at=?,
                               last_error=NULL
                           WHERE mcu_receive_generation=?
                             AND mcu_boot_id=?
                             AND mcu_event_sequence=?""",
                        (
                            self._now(),
                            mcu_receive_generation,
                            mcu_boot_id,
                            mcu_event_sequence,
                        ),
                    )
                    return "DUPLICATE"
            existing = conn.execute(
                "SELECT state FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            seq = self._next_seq(conn)
            stored_payload = payload
            if device_name and target_type:
                stored_payload = build_event_envelope(
                    event_uid=event_uid,
                    device_name=device_name,
                    edge_event_sequence=seq,
                    event_type=event_type,
                    target_type=target_type,
                    target_uid=target_uid or work_uid or event_uid,
                    command_uid=command_uid,
                    delivery_class=delivery_class,
                    payload=payload,
                )
            conn.execute(
                "INSERT INTO event_outbox (event_uid, edge_event_sequence, event_type, payload_json, work_uid) VALUES (?,?,?,?,?)",
                (
                    event_uid,
                    seq,
                    event_type,
                    _json.dumps(stored_payload, ensure_ascii=False),
                    work_uid,
                ),
            )
            if has_mcu_identity:
                conn.execute(
                    """INSERT INTO mcu_derived_event
                       (mcu_receive_generation, mcu_boot_id,
                        mcu_event_sequence, event_type, event_uid)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_type,
                        event_uid,
                    ),
                )
            if event_type == "CLEAN_COMPLETE" and payload.get("portNo"):
                self._set_clean_restart_interlock_in_tx(
                    conn,
                    int(payload["portNo"]),
                    False,
                )
            if fullness_transition is not None:
                self._apply_fullness_transition_in_tx(
                    conn,
                    fullness_transition,
                )
            if work_state_update and work_uid:
                ctx = work_state_update.get("context", {})
                conn.execute(
                    "UPDATE work_slot SET work_state=?, context_json=?, updated_at=? WHERE work_uid=?",
                    (work_state_update.get("state"), _json.dumps(ctx, ensure_ascii=False),
                     self._now(), work_uid),
                )
            if has_mcu_identity:
                conn.execute(
                    """UPDATE mcu_event_inbox
                       SET state='PROCESSED', processed_at=?,
                           last_error=NULL
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?""",
                    (
                        self._now(),
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                )
            return "ACCEPTED"

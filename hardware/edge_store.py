"""edge_store.py —— 香橙派边缘 SQLite 存储。

SQLite 是香橙派的唯一持久化真相源。
"""

from __future__ import annotations

import json as _json
import logging
import os
import sqlite3
import threading
import time
import uuid as _uuid
from contextlib import contextmanager
from typing import Any, Optional

from onenet_wire import (
    build_business_confirmation_receipt,
    build_configuration_progress_event,
    build_event_envelope,
    canonical_payload_sha256,
)

logger = logging.getLogger("edge-store")

CURRENT_SCHEMA_VERSION = 3
WORK_TYPE_NONE = "NONE"
WORK_TYPE_DELIVERY = "DELIVERY"
WORK_TYPE_CLEAN = "CLEAN"
WORK_TYPE_FULLNESS = "FULLNESS"
WORK_TYPE_BASELINE = "BASELINE"
EVENT_PENDING = "PENDING"
EVENT_SENDING = "SENDING"
EVENT_CONFIRMED = "CONFIRMED"
EVENT_DEAD = "DEAD"
PHOTO_PENDING = "PENDING"
PHOTO_UPLOADING = "UPLOADING"
PHOTO_UPLOADED = "UPLOADED"
PHOTO_DEAD = "DEAD"
FAULT_OBSERVED = "OBSERVED"
FAULT_RECOVERED = "RECOVERED"


class EdgeStore:
    """香橙派边缘 SQLite 存储。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
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
        self._migrate()
        logger.info("EdgeStore 初始化: %s (v%d)", self.db_path, CURRENT_SCHEMA_VERSION)

    def _migrate(self) -> None:
        conn = self._conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        if current >= CURRENT_SCHEMA_VERSION:
            return
        logger.info("EdgeStore 迁移: v%d -> v%d", current, CURRENT_SCHEMA_VERSION)
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
        conn.commit()

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
            state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            uploaded_at TEXT,
            work_uid TEXT,
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
            deployment_code TEXT NOT NULL,
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

    # ── 事务辅助 ──

    @contextmanager
    def transaction(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

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
                   (command_uid, command_type, payload_json, canonical_sha256)
                   VALUES (?,?,?,?)""",
                (
                    command_uid,
                    command_type,
                    _json.dumps(payload, ensure_ascii=False),
                    canonical_sha256,
                ),
            )
            return "ACCEPTED"

    def get_command(self, command_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM command_inbox WHERE command_uid=?", (command_uid,)
            ).fetchone()
        return self._decode_command_row(row)

    def claim_next_command(self) -> Optional[dict]:
        with self.transaction():
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

    def recover_interrupted_commands(self) -> dict[str, int]:
        """Only configuration transmission is safe for automatic replay."""
        with self.transaction():
            config = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state='PROCESSING'
                     AND command_type='APPLY_CONFIGURATION'"""
            ).rowcount
            physical = self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED_PHYSICAL_COMMAND'
                   WHERE state='PROCESSING'
                     AND command_type<>'APPLY_CONFIGURATION'"""
            ).rowcount
            return {"configuration_requeued": config, "physical_locked": physical}

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
                   (application_uid, command_uid, deployment_code, config_version,
                    content_sha256, mcu_payload_sha256, payload_json,
                    part_command_uids_json, state, edge_saved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    application_uid,
                    command["commandUid"],
                    command["deploymentCode"],
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
                deployment_code=command["deploymentCode"],
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
                deployment_code=row["deployment_code"],
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
                deployment_code=row["deployment_code"],
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
    def _insert_event(conn, event: dict, event_type: str) -> None:
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

    def acquire_work_slot(self, work_type: str, work_uid: str, port_no: int,
                          context: dict) -> bool:
        with self.transaction():
            conn = self._conn
            slot = conn.execute("SELECT work_type FROM work_slot WHERE slot_id=1").fetchone()
            if not slot or slot["work_type"] != WORK_TYPE_NONE:
                return False
            conn.execute(
                "UPDATE work_slot SET work_type=?, work_uid=?, work_state='ACTIVE', port_no=?, context_json=?, updated_at=? WHERE slot_id=1",
                (work_type, work_uid, port_no, _json.dumps(context, ensure_ascii=False), self._now()),
            )
            return True

    # ── 事件发件箱操作 ──

    def list_pending_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM event_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
                   ORDER BY edge_event_sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_event(self, event_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
        return dict(row) if row else None

    # ── 设备状态操作 ──

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_value FROM device_state WHERE state_key=?", (key,)
            ).fetchone()
        return row["state_value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.transaction():
            self._conn.execute(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=?, updated_at=?""",
                (key, value, self._now(), value, self._now()),
            )

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
            rows = self._conn.execute(
                "SELECT * FROM faults WHERE lifecycle='OBSERVED' ORDER BY observed_at"
            ).fetchall()
        return [dict(r) for r in rows]

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

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("EdgeStore 已关闭")

    # ── 照片发件箱操作 ──

    def list_pending_photos(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
                   ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_photo_uploading(self, photo_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE photo_outbox SET state='UPLOADING' WHERE photo_uid=?", (photo_uid,)
            )

    def mark_photo_uploaded(self, photo_uid: str, cos_key: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE photo_outbox SET state='UPLOADED', cos_key=?, uploaded_at=? WHERE photo_uid=?",
                (cos_key, self._now(), photo_uid),
            )

    def mark_photo_pending_retry(self, photo_uid: str) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(120, 5 * (1 + retries))
        next_retry = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_s + backoff))
        with self.transaction():
            self._conn.execute(
                "UPDATE photo_outbox SET state='PENDING', retry_count=retry_count+1, next_retry_at=? WHERE photo_uid=?",
                (next_retry, photo_uid),
            )

    def mark_photo_dead(self, photo_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE photo_outbox SET state='DEAD' WHERE photo_uid=?", (photo_uid,)
            )

    def get_photos_by_work(self, work_uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM photo_outbox WHERE work_uid=? AND tombstoned=0 ORDER BY slot_name",
                (work_uid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_event_sending(self, event_uid: str, mqtt_msg_id: int) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='SENDING', mqtt_msg_id=? WHERE event_uid=?",
                (mqtt_msg_id, event_uid),
            )

    def mark_event_pending_retry(self, event_uid: str) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM event_outbox WHERE event_uid=?", (event_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(60, 2 ** min(6, retries))
        next_retry = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_s + backoff))
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='PENDING', retry_count=retry_count+1, next_retry_at=? WHERE event_uid=?",
                (next_retry, event_uid),
            )

    def mark_event_dead(self, event_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='DEAD' WHERE event_uid=?", (event_uid,)
            )

    def mark_control_receipt_published(self, event_uid: str) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                "UPDATE event_outbox SET state=?, confirmed_at=? WHERE event_uid=? AND event_type='BUSINESS_CONFIRMATION_RECEIPT'",
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

    # ── 原子事务 4: 接收业务确认 ──

    def receive_business_confirmation(self, confirmation_uid: str, event_uid: str,
                                      outcome: str, payload: Optional[dict] = None) -> str:
        with self.transaction():
            conn = self._conn
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
                "UPDATE event_outbox SET state=?, confirmed_at=? WHERE event_uid=?",
                (EVENT_CONFIRMED, self._now(), event_uid),
            )
            return "ACCEPTED"

    def receive_business_confirmation_and_create_receipt(
        self,
        command_uid: str,
        confirmation_payload: dict,
        deployment_code: str,
    ) -> str:
        """Persist I-045 confirmation and create the required receipt event.

        Returns ACCEPTED, DUPLICATE or REJECTED. A rejected confirmation is not
        persisted because it does not match a local reliable event.
        """

        confirmation_uid = confirmation_payload.get("confirmationUid")
        original_event_uid = confirmation_payload.get("originalEventUid")
        original_payload_sha256 = confirmation_payload.get("originalPayloadSha256")
        outcome = confirmation_payload.get("outcome")
        if not confirmation_uid or not original_event_uid or not original_payload_sha256 or not outcome:
            return "REJECTED"

        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT confirmation_uid FROM confirmation_inbox WHERE confirmation_uid=?",
                (confirmation_uid,),
            ).fetchone()
            if existing:
                return "DUPLICATE"

            event = conn.execute(
                "SELECT payload_json FROM event_outbox WHERE event_uid=?",
                (original_event_uid,),
            ).fetchone()
            if not event:
                return "REJECTED"
            event_json = _json.loads(event["payload_json"])
            local_sha = event_json.get("payloadSha256") or event_json.get("payload_sha256")
            if local_sha and local_sha != original_payload_sha256:
                return "REJECTED"

            conn.execute(
                "INSERT INTO confirmation_inbox (confirmation_uid, event_uid, outcome, payload_json) VALUES (?,?,?,?)",
                (
                    confirmation_uid,
                    original_event_uid,
                    outcome,
                    _json.dumps(confirmation_payload, ensure_ascii=False),
                ),
            )
            conn.execute(
                "UPDATE event_outbox SET state=?, confirmed_at=? WHERE event_uid=?",
                (EVENT_CONFIRMED, self._now(), original_event_uid),
            )
            seq = self._next_seq(conn)
            receipt = build_business_confirmation_receipt(
                deployment_code=deployment_code,
                command_uid=command_uid,
                confirmation_uid=confirmation_uid,
                original_event_uid=original_event_uid,
                original_payload_sha256=original_payload_sha256,
                outcome=outcome,
                edge_event_sequence=seq,
            )
            conn.execute(
                "INSERT INTO event_outbox (event_uid, edge_event_sequence, event_type, payload_json, work_uid) VALUES (?,?,?,?,?)",
                (
                    receipt["eventUid"],
                    seq,
                    "BUSINESS_CONFIRMATION_RECEIPT",
                    _json.dumps(receipt, ensure_ascii=False),
                    original_event_uid,
                ),
            )
            return "ACCEPTED"

    # ── 原子事务 5: 登记照片 ──

    def register_photo(self, photo_uid: str, slot_name: str, local_path: str,
                       cos_key: Optional[str] = None, work_uid: Optional[str] = None) -> str:
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            conn.execute(
                "INSERT INTO photo_outbox (photo_uid, slot_name, local_path, cos_key, work_uid) VALUES (?,?,?,?,?)",
                (photo_uid, slot_name, local_path, cos_key, work_uid),
            )
            return "ACCEPTED"

    # ── 原子事务 2: 接收 MCU 事件 ──

    def receive_mcu_event(self, event_uid: str, event_type: str, payload: dict,
                          work_uid: Optional[str] = None) -> str:
        with self.transaction():
            conn = self._conn
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
                          deployment_code: Optional[str] = None,
                          target_type: Optional[str] = None,
                          target_uid: Optional[str] = None,
                          command_uid: Optional[str] = None,
                          delivery_class: str = "RELIABLE_FACT") -> str:
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            seq = self._next_seq(conn)
            stored_payload = payload
            if deployment_code and target_type:
                stored_payload = build_event_envelope(
                    event_uid=event_uid,
                    deployment_code=deployment_code,
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
            if work_state_update and work_uid:
                ctx = work_state_update.get("context", {})
                conn.execute(
                    "UPDATE work_slot SET work_state=?, context_json=?, updated_at=? WHERE work_uid=?",
                    (work_state_update.get("state"), _json.dumps(ctx, ensure_ascii=False),
                     self._now(), work_uid),
                )
            return "ACCEPTED"

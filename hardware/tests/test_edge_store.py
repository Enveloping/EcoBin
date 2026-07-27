"""edge_store.py 单元测试。

测试 SQLite schema 创建、五个原子事务、工单槽操作、事件/照片发件箱、故障和完整性校验。
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from edge_store import (
    CURRENT_SCHEMA_VERSION,
    EdgeStore,
    WORK_TYPE_DELIVERY,
    WORK_TYPE_NONE,
    WORK_TYPE_CLEAN,
    EVENT_PENDING,
    EVENT_SENDING,
    EVENT_CONFIRMED,
    PHOTO_PENDING,
    PHOTO_UPLOADED,
    FAULT_OBSERVED,
    FAULT_RECOVERED,
)


def make_store() -> EdgeStore:
    """在临时文件中创建已初始化的 EdgeStore。"""
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    store = EdgeStore(path)
    store.initialize()
    return store


class TestEdgeStoreInit:
    """初始化与 Schema 创建。"""

    def test_initialize_creates_tables(self):
        store = make_store()
        tables = [
            "schema_version", "command_inbox", "work_slot",
            "event_outbox", "photo_outbox", "confirmation_inbox",
            "device_state", "faults", "tombstones",
        ]
        for name in tables:
            row = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            assert row is not None, f"table {name} missing"
        store.close()

    def test_initialize_sets_pragmas(self):
        store = make_store()
        wal = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = store._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert wal.lower() == "wal"
        assert fk == 1
        store.close()

    def test_initialize_idempotent(self):
        store = make_store()
        store.initialize()
        store.close()

    def test_schema_version_recorded(self):
        store = make_store()
        row = store._conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION
        store.close()

    def test_work_slot_prefilled(self):
        store = make_store()
        row = store._conn.execute(
            "SELECT work_type FROM work_slot WHERE slot_id=1"
        ).fetchone()
        assert row["work_type"] == WORK_TYPE_NONE
        store.close()

    def test_device_state_prefilled(self):
        store = make_store()
        assert store.get_edge_boot_id() == ""
        assert store.get_edge_event_sequence() == 0
        assert store.get_mcu_receive_generation() == 0
        store.close()

    def test_v2_mcu_event_inbox_migrates_without_losing_history(self):
        path = os.path.join(tempfile.mkdtemp(), "v2.db")
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.execute(
            """CREATE TABLE device_state (
                state_key TEXT NOT NULL PRIMARY KEY,
                state_value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE TABLE mcu_event_inbox (
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
            )"""
        )
        conn.execute(
            """CREATE INDEX idx_mcu_event_state
               ON mcu_event_inbox(state, rowid)"""
        )
        conn.execute(
            """INSERT INTO mcu_event_inbox
               (mcu_boot_id, mcu_event_sequence, message_name,
                message_type, source_tx_sequence, content_sha256,
                payload_json, state)
               VALUES (42, 7, 'SAFETY_SENSOR_EVENT', 54, 9, ?, ?, 'PROCESSED')""",
            ("a" * 64, '{"mcuBootId":42,"mcuEventSequence":7}'),
        )
        conn.commit()
        conn.close()

        store = EdgeStore(path)
        store.initialize()

        row = store._conn.execute(
            """SELECT mcu_receive_generation, mcu_boot_id,
                      mcu_event_sequence, state
               FROM mcu_event_inbox"""
        ).fetchone()
        assert dict(row) == {
            "mcu_receive_generation": 0,
            "mcu_boot_id": 42,
            "mcu_event_sequence": 7,
            "state": "PROCESSED",
        }
        assert store.get_mcu_receive_generation() == 0
        assert store.begin_mcu_receive_generation(42) == 1
        store.close()


class TestAtomicReceiveCommand:
    """原子事务 1: 接收命令。"""

    def test_accept_new(self):
        store = make_store()
        result = store.receive_command("cmd-1", "OPEN_DOOR", {"door": 1})
        assert result == "ACCEPTED"
        store.close()

    def test_duplicate_rejected(self):
        store = make_store()
        store.receive_command("cmd-1", "OPEN_DOOR", {"door": 1})
        result = store.receive_command("cmd-1", "OPEN_DOOR", {"door": 1})
        assert result == "DUPLICATE"
        store.close()

    def test_same_uid_with_different_content_is_conflict(self):
        store = make_store()
        store.receive_command("cmd-1", "OPEN_DOOR", {"door": 1})
        result = store.receive_command("cmd-1", "OPEN_DOOR", {"door": 2})
        assert result == "CONFLICT"
        store.close()

    def test_claim_and_complete_command(self):
        store = make_store()
        store.receive_command("cmd-1", "OPEN_DOOR", {"door": 1})
        command = store.claim_next_command()
        assert command["command_uid"] == "cmd-1"
        assert command["state"] == "PROCESSING"
        assert store.complete_command("cmd-1", {"done": True})
        assert store.get_command("cmd-1")["state"] == "COMPLETED"
        store.close()

    def test_fixed_frame_restart_fails_indeterminate_physical_command(self):
        store = make_store()
        store.receive_command(
            "cmd-1",
            "START_DELIVERY_SESSION",
            {"commandUid": "cmd-1"},
        )
        assert store.claim_next_command()["state"] == "PROCESSING"
        assert store.mark_command_waiting_mcu(
            "cmd-1",
            "mcu-cmd-1",
        )

        recovered = store.recover_interrupted_commands(
            physical_recovery_required=False,
        )

        assert recovered["physical_locked"] == 0
        assert recovered["physical_failed"] == 1
        command = store.get_command("cmd-1")
        assert command["state"] == "FAILED"
        assert command["last_error"] == (
            "PROCESS_RESTARTED_MCU_STATE_UNKNOWN"
        )
        store.close()


class TestMcuEventInbox:
    def test_persist_duplicate_and_conflict(self):
        store = make_store()
        frame = {
            "message_name": "CONFIG_APPLY_RESULT",
            "message_type": 20,
            "tx_sequence": 3,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "status": "APPLIED",
            },
        }
        assert store.receive_mcu_frame(frame) == "ACCEPTED"
        assert store.receive_mcu_frame(frame) == "DUPLICATE"
        changed = {
            **frame,
            "payload": {**frame["payload"], "status": "FAILED"},
        }
        assert store.receive_mcu_frame(changed) == "CONFLICT"
        pending = store.list_pending_mcu_events()
        assert len(pending) == 1
        assert store.mark_mcu_event_processed(42, 1)
        assert store.list_pending_mcu_events() == []
        store.close()

    def test_fixed_boot_id_reuses_sequence_in_new_receive_generation(self):
        store = make_store()
        first_generation = store.begin_mcu_receive_generation(42)
        first = {
            "message_name": "DELIVERY_DOOR_COMMAND_RESULT",
            "message_type": 51,
            "tx_sequence": 3,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 8_630_700,
                "result": "COMMAND_DISPATCHED",
            },
        }
        assert store.receive_mcu_frame(first) == "ACCEPTED"
        assert store.mark_mcu_event_processed(
            42,
            1,
            first_generation,
        )

        second_generation = store.begin_mcu_receive_generation(42)
        restarted = {
            **first,
            "tx_sequence": 4,
            "payload": {
                **first["payload"],
                "uptimeMs": 361_900,
                "result": "COALESCED_WITH_EXISTING_CLOSE",
            },
        }
        assert second_generation == first_generation + 1
        assert store.receive_mcu_frame(first) == "DUPLICATE"
        assert store.receive_mcu_frame(restarted) == "ACCEPTED"
        assert store.receive_mcu_frame(restarted) == "DUPLICATE"
        pending = store.list_pending_mcu_events()
        assert len(pending) == 1
        assert pending[0]["mcu_receive_generation"] == second_generation
        store.close()


class TestAtomicReceiveMcuEvent:
    """原子事务 2: 接收 MCU 事件。"""

    def test_accept_new_event(self):
        store = make_store()
        result = store.receive_mcu_event("evt-1", "DOOR_OPENED", {"door": 1})
        assert result == "ACCEPTED"
        row = store._conn.execute(
            "SELECT edge_event_sequence FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["edge_event_sequence"] == 1
        store.close()

    def test_sequence_monotonic(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.receive_mcu_event("evt-2", "E2", {})
        row = store._conn.execute(
            "SELECT edge_event_sequence FROM event_outbox WHERE event_uid='evt-2'"
        ).fetchone()
        assert row["edge_event_sequence"] == 2
        store.close()

    def test_duplicate_rejected(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        result = store.receive_mcu_event("evt-1", "E1", {})
        assert result == "DUPLICATE"
        store.close()


class TestAtomicCreateEdgeEvent:
    """原子事务 3: 创建边缘事件。"""

    def test_create_without_work_update(self):
        store = make_store()
        result = store.create_edge_event("evt-1", "DELIVERY_COMPLETE", {"weight": 100})
        assert result == "ACCEPTED"
        store.close()

    def test_create_with_work_update(self):
        store = make_store()
        store.acquire_work_slot(WORK_TYPE_DELIVERY, "work-1", 1, {"session_id": "s1"})
        result = store.create_edge_event(
            "evt-1", "DELIVERY_COMPLETE", {"weight": 100},
            work_uid="work-1",
            work_state_update={"state": "COMPLETED", "context": {"done": True}},
        )
        assert result == "ACCEPTED"
        slot = store.get_work_slot()
        assert slot["work_state"] == "COMPLETED"
        assert slot["context"]["done"] is True
        store.close()


class TestAtomicReceiveConfirmation:
    """原子事务 4: 接收业务确认。"""

    def test_confirm_event(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        result = store.receive_business_confirmation("conf-1", "evt-1", "BUSINESS_APPLIED")
        assert result == "ACCEPTED"
        row = store._conn.execute(
            "SELECT state FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["state"] == EVENT_CONFIRMED
        store.close()

    def test_duplicate_confirmation(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.receive_business_confirmation("conf-1", "evt-1", "BUSINESS_APPLIED")
        result = store.receive_business_confirmation("conf-1", "evt-1", "BUSINESS_APPLIED")
        assert result == "DUPLICATE"
        store.close()


class TestAtomicRegisterPhoto:
    """原子事务 5: 登记照片。"""

    def test_register_photo(self):
        store = make_store()
        result = store.register_photo("photo-1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        assert result == "ACCEPTED"
        row = store._conn.execute(
            "SELECT state FROM photo_outbox WHERE photo_uid='photo-1'"
        ).fetchone()
        assert row["state"] == PHOTO_PENDING
        store.close()

    def test_duplicate_photo(self):
        store = make_store()
        store.register_photo("photo-1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        result = store.register_photo("photo-1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        assert result == "DUPLICATE"
        store.close()


class TestWorkSlotOperations:
    """工单槽操作。"""

    def test_acquire_and_release(self):
        store = make_store()
        ok = store.acquire_work_slot(WORK_TYPE_DELIVERY, "work-1", 2, {"key": "val"})
        assert ok
        slot = store.get_work_slot()
        assert slot is not None
        assert slot["work_type"] == WORK_TYPE_DELIVERY
        assert slot["work_uid"] == "work-1"
        assert slot["port_no"] == 2
        assert slot["context"]["key"] == "val"
        ok = store.release_work_slot("work-1")
        assert ok
        assert store.get_work_slot() is None
        store.close()

    def test_acquire_rejects_when_busy(self):
        store = make_store()
        store.acquire_work_slot(WORK_TYPE_DELIVERY, "work-1", 1, {})
        ok = store.acquire_work_slot(WORK_TYPE_CLEAN, "work-2", 1, {})
        assert not ok
        store.close()

    def test_release_wrong_uid_fails(self):
        store = make_store()
        store.acquire_work_slot(WORK_TYPE_DELIVERY, "work-1", 1, {})
        ok = store.release_work_slot("work-2")
        assert not ok
        store.close()

    def test_update_work_context(self):
        store = make_store()
        store.acquire_work_slot(WORK_TYPE_DELIVERY, "work-1", 1, {"phase": "start"})
        ok = store.update_work_context("work-1", {"phase": "opening"})
        assert ok
        slot = store.get_work_slot()
        assert slot["context"]["phase"] == "opening"
        store.close()


class TestEventOutboxOperations:
    """事件发件箱操作。"""

    def test_list_pending_events(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.receive_mcu_event("evt-2", "E2", {})
        events = store.list_pending_events()
        assert len(events) == 2
        assert events[0]["event_uid"] == "evt-1"
        assert events[1]["event_uid"] == "evt-2"
        store.close()

    def test_mark_event_sending_and_retry(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.mark_event_sending("evt-1", 42)
        row = store._conn.execute(
            "SELECT state, mqtt_msg_id FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["state"] == EVENT_SENDING
        assert row["mqtt_msg_id"] == 42
        store.mark_event_pending_retry("evt-1")
        row = store._conn.execute(
            "SELECT state, retry_count FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["state"] == EVENT_PENDING
        assert row["retry_count"] == 1
        store.close()

    def test_iso_retry_timestamp_becomes_due(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store._conn.execute(
            """UPDATE event_outbox
               SET next_retry_at=strftime(
                   '%Y-%m-%dT%H:%M:%S', 'now', '-1 second'
               )
               WHERE event_uid='evt-1'"""
        )
        store._conn.commit()

        assert [
            row["event_uid"] for row in store.list_pending_events()
        ] == ["evt-1"]
        store.close()

    def test_retry_timestamp_is_utc(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        before = datetime.now(timezone.utc)

        store.mark_event_pending_retry("evt-1")

        row = store.get_event("evt-1")
        retry_at = datetime.fromisoformat(
            row["next_retry_at"]
        ).replace(tzinfo=timezone.utc)
        assert before <= retry_at
        assert (retry_at - before).total_seconds() <= 61
        store.close()

    def test_mark_event_dead(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.mark_event_dead("evt-1")
        row = store._conn.execute(
            "SELECT state FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["state"] == "DEAD"
        store.close()


class TestPhotoOutboxOperations:
    """照片发件箱操作。"""

    def test_list_pending_photos(self):
        store = make_store()
        store.register_photo("p1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        store.register_photo("p2", "CLOSE_INSIDE", "/tmp/p2.jpg")
        photos = store.list_pending_photos()
        assert len(photos) == 2
        store.close()

    def test_mark_photo_uploaded(self):
        store = make_store()
        store.register_photo("p1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        store.mark_photo_uploaded("p1", "cos/key/p1.jpg")
        row = store._conn.execute(
            "SELECT state, cos_key FROM photo_outbox WHERE photo_uid='p1'"
        ).fetchone()
        assert row["state"] == PHOTO_UPLOADED
        assert row["cos_key"] == "cos/key/p1.jpg"
        store.close()

    def test_iso_retry_timestamp_becomes_due(self):
        store = make_store()
        store.register_photo("p1", "OPEN_OUTSIDE", "/tmp/p1.jpg")
        store._conn.execute(
            """UPDATE photo_outbox
               SET next_retry_at=strftime(
                   '%Y-%m-%dT%H:%M:%S', 'now', '-1 second'
               )
               WHERE photo_uid='p1'"""
        )
        store._conn.commit()

        assert [
            row["photo_uid"] for row in store.list_pending_photos()
        ] == ["p1"]
        store.close()


class TestFaultOperations:
    """故障操作。"""

    def test_record_and_recover(self):
        store = make_store()
        uid = store.record_fault("UART", 256, "WARNING", {"detail": "test"})
        assert uid
        active = store.list_active_faults()
        assert len(active) == 1
        assert active[0]["fault_code"] == 256
        ok = store.mark_fault_recovered(uid)
        assert ok
        assert len(store.list_active_faults()) == 0
        store.close()


class TestIntegrity:
    """完整性校验与维护。"""

    def test_integrity_check(self):
        store = make_store()
        assert store.integrity_check()
        store.close()

    def test_tombstone_confirmed_events(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.receive_business_confirmation("conf-1", "evt-1", "BUSINESS_APPLIED")
        row = store._conn.execute(
            "SELECT confirmed_at FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["confirmed_at"] is not None
        count = store.tombstone_confirmed_events(0)
        assert count == 1
        row = store._conn.execute(
            "SELECT tombstoned FROM event_outbox WHERE event_uid='evt-1'"
        ).fetchone()
        assert row["tombstoned"] == 1
        store.close()

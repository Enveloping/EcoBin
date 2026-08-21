"""edge_store.py 单元测试。

测试 SQLite schema 创建、五个原子事务、工单槽操作、事件/照片发件箱、故障和完整性校验。
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone

import pytest

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


def firmware_manifest(version_code: int = 10000) -> dict:
    return {
        "schemaVersion": 1,
        "releaseUid": str(uuid.uuid4()),
        "mcuPartNumber": "STM32F103C8T6",
        "hardwareCompatibility": "ECOBIN_MAINBOARD_V1.1",
        "firmwareVersion": "1.0.0",
        "firmwareVersionCode": version_code,
        "fixedFrameRevision": 2,
        "flashBase": "0x08000000",
        "imageSize": 1024,
        "imageSha256": "a" * 64,
        "firmwareIdentityHex": "0102030405060708",
        "buildCommit": "b" * 40,
        "builtAt": "2026-08-19T00:00:00Z",
        "signingKeyId": "release-2026",
    }


class TestEdgeStoreInit:
    """初始化与 Schema 创建。"""

    def test_initialize_creates_tables(self):
        store = make_store()
        tables = [
            "schema_version", "command_inbox", "work_slot",
            "event_outbox", "photo_outbox", "confirmation_inbox",
            "device_state", "faults", "tombstones",
            "port_fullness_state",
            "maintenance_lock", "mcu_firmware_update",
            "mcu_firmware_state", "mcu_firmware_progress",
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

    def test_v11_to_v12_migration_recreates_firmware_state_index(
        self,
        tmp_path,
    ):
        path = str(tmp_path / "v11-firmware.db")
        store = EdgeStore(path)
        store.initialize()
        store._conn.execute(
            "DELETE FROM schema_version WHERE version >= 12"
        )
        store._conn.commit()
        store.close()

        migrated = EdgeStore(path)
        migrated.initialize()

        indexes = {
            row["name"]
            for row in migrated._conn.execute(
                "PRAGMA index_list('mcu_firmware_update')"
            ).fetchall()
        }
        assert "idx_mcu_update_state" in indexes
        migrated.close()

    def test_v13_to_v14_migration_adds_prepare_recovery_journal(
        self,
        tmp_path,
    ):
        path = str(tmp_path / "v13-firmware.db")
        store = EdgeStore(path)
        store.initialize()
        stable_uid = str(uuid.uuid4())
        stable_manifest = firmware_manifest(10000)
        assert store.begin_mcu_firmware_update(
            update_uid=stable_uid,
            deployment_uid=str(uuid.uuid4()),
            source="LOCAL",
            package_path=str((tmp_path / "stable.efw").resolve()),
            package_sha256="1" * 64,
            manifest=stable_manifest,
            legacy_preflight=True,
        ) == "ACCEPTED"
        assert store.complete_mcu_firmware_update(stable_uid)
        active_uid = str(uuid.uuid4())
        assert store.begin_mcu_firmware_update(
            update_uid=active_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=str((tmp_path / "target.efw").resolve()),
            package_sha256="2" * 64,
            manifest=firmware_manifest(20000),
        ) == "ACCEPTED"
        assert store.transition_mcu_firmware_update(active_uid, "PREFLIGHT")
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update DROP COLUMN prepare_identity_json"
        )
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update DROP COLUMN prepare_recovery_required"
        )
        store._conn.execute("DELETE FROM schema_version WHERE version >= 14")
        store._conn.commit()
        store.close()

        migrated = EdgeStore(path)
        migrated.initialize()

        columns = {
            row["name"]
            for row in migrated._conn.execute(
                "PRAGMA table_info('mcu_firmware_update')"
            ).fetchall()
        }
        assert "prepare_recovery_required" in columns
        assert "prepare_identity_json" in columns
        assert migrated._conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
        active = migrated.get_mcu_firmware_update(active_uid)
        assert active["prepare_recovery_required"] is True
        assert active["prepare_identity"] == {
            "protocolRevision": 2,
            "firmwareVersionCode": stable_manifest["firmwareVersionCode"],
            "firmwareVersion": stable_manifest["firmwareVersion"],
            "firmwareIdentityHex": stable_manifest["firmwareIdentityHex"],
        }
        migrated.close()

    @pytest.mark.parametrize("persisted_column_count", (1, 2))
    def test_v14_migration_resumes_after_ddl_was_persisted_before_version(
        self,
        tmp_path,
        persisted_column_count,
    ):
        path = str(
            tmp_path / f"v14-interrupted-{persisted_column_count}.db"
        )
        store = EdgeStore(path)
        store.initialize()
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update DROP COLUMN prepare_identity_json"
        )
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update "
            "DROP COLUMN prepare_recovery_required"
        )
        store._conn.execute("DELETE FROM schema_version WHERE version >= 14")
        store._conn.commit()
        store.close()

        interrupted = sqlite3.connect(path)
        interrupted.execute(
            """ALTER TABLE mcu_firmware_update
               ADD COLUMN prepare_recovery_required INTEGER
                   NOT NULL DEFAULT 0
                   CHECK (prepare_recovery_required IN (0, 1))"""
        )
        if persisted_column_count == 2:
            interrupted.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_identity_json TEXT"""
            )
        interrupted.commit()
        interrupted.close()

        resumed = EdgeStore(path)
        resumed.initialize()

        columns = {
            row["name"]
            for row in resumed._conn.execute(
                "PRAGMA table_info('mcu_firmware_update')"
            ).fetchall()
        }
        assert {
            "prepare_recovery_required",
            "prepare_identity_json",
        }.issubset(columns)
        assert resumed._conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
        resumed.close()

    def test_migration_rolls_back_schema_changes_when_a_step_fails(
        self,
        tmp_path,
        monkeypatch,
    ):
        path = str(tmp_path / "v14-transaction-rollback.db")
        store = EdgeStore(path)
        store.initialize()
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update DROP COLUMN prepare_identity_json"
        )
        store._conn.execute(
            "ALTER TABLE mcu_firmware_update "
            "DROP COLUMN prepare_recovery_required"
        )
        store._conn.execute("DELETE FROM schema_version WHERE version >= 14")
        store._conn.commit()
        store.close()

        def interrupt_after_first_ddl(interrupted_store):
            interrupted_store._conn.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_recovery_required INTEGER
                       NOT NULL DEFAULT 0
                       CHECK (prepare_recovery_required IN (0, 1))"""
            )
            raise RuntimeError("injected migration interruption")

        with monkeypatch.context() as migration_patch:
            migration_patch.setattr(
                EdgeStore,
                "_migrate_v14",
                interrupt_after_first_ddl,
            )
            interrupted = EdgeStore(path)
            with pytest.raises(
                RuntimeError,
                match="injected migration interruption",
            ):
                interrupted.initialize()
            interrupted.close()

        inspection = sqlite3.connect(path)
        columns = {
            row[1]
            for row in inspection.execute(
                "PRAGMA table_info('mcu_firmware_update')"
            ).fetchall()
        }
        version = inspection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        inspection.close()

        assert "prepare_recovery_required" not in columns
        assert version == 13

        recovered = EdgeStore(path)
        recovered.initialize()
        assert recovered._conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
        recovered.close()

    def test_v9_rejects_a_v5_database_instead_of_migrating_it(self):
        path = os.path.join(tempfile.mkdtemp(), "v5-photo-url.db")
        store = EdgeStore(path)
        store.initialize()
        store._conn.execute("DELETE FROM schema_version")
        store._conn.execute(
            "INSERT INTO schema_version (version) VALUES (5)"
        )
        store._conn.executemany(
            """INSERT INTO photo_outbox
               (photo_uid, slot_name, local_path, url, state)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    "capture-pending",
                    "BEFORE_INNER",
                    "/tmp/capture-pending.jpg",
                    "https://example.invalid/capture-pending.jpg",
                    "CAPTURE_PENDING",
                ),
                (
                    "upload-pending",
                    "BEFORE_OUTER",
                    "/tmp/upload-pending.jpg",
                    "https://example.invalid/upload-pending.jpg",
                    "PENDING",
                ),
                (
                    "missing",
                    "AFTER_INNER",
                    "/tmp/missing.jpg",
                    "https://example.invalid/missing.jpg",
                    "DEAD",
                ),
                (
                    "available",
                    "AFTER_OUTER",
                    "/tmp/available.jpg",
                    "https://example.invalid/available.jpg",
                    "UPLOADED",
                ),
            ],
        )
        store._conn.commit()
        store.close()

        incompatible = EdgeStore(path)
        with pytest.raises(
            RuntimeError,
            match="永久资产 v9 不读取旧设备数据库",
        ):
            incompatible.initialize()
        incompatible.close()

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

    def test_device_entry_url_is_atomic_and_older_commands_cannot_roll_it_back(
        self,
    ):
        store = make_store()
        old_url = "https://www.jinshoubao.com/device-entry/old"
        new_url = "https://www.jinshoubao.com/device-entry/new"
        old_hash = hashlib.sha256(old_url.encode("ascii")).hexdigest()
        new_hash = hashlib.sha256(new_url.encode("ascii")).hexdigest()

        assert store.save_device_entry_url(
            new_url,
            new_hash,
            "2026-08-08T02:00:00.000Z",
        )["disposition"] == "SAVED"
        stale = store.save_device_entry_url(
            old_url,
            old_hash,
            "2026-08-08T01:00:00.000Z",
        )

        assert stale["disposition"] == "STALE_IGNORED"
        assert store.get_device_entry_url() == {
            "deviceEntryUrl": new_url,
            "deviceEntryUrlSha256": new_hash,
            "issuedAt": "2026-08-08T02:00:00.000Z",
        }
        store.close()

    def test_v9_rejects_a_v2_database_instead_of_migrating_it(self):
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

        incompatible = EdgeStore(path)
        with pytest.raises(
            RuntimeError,
            match="永久资产 v9 不读取旧设备数据库",
        ):
            incompatible.initialize()
        incompatible.close()


class TestMcuFirmwareUpdateJournal:
    def test_prepare_recovery_identity_is_durable_before_f2(self, tmp_path):
        path = str(tmp_path / "edge.db")
        store = EdgeStore(path)
        store.initialize()
        update_uid = str(uuid.uuid4())
        identity = {
            "protocolRevision": 2,
            "firmwareVersionCode": 10000,
            "firmwareVersion": "1.0.0",
            "firmwareIdentityHex": "0102030405060708",
        }
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="1" * 64,
            manifest=firmware_manifest(20000),
        ) == "ACCEPTED"
        assert store.transition_mcu_firmware_update(update_uid, "PREFLIGHT")

        assert store.arm_mcu_firmware_prepare_recovery(update_uid, identity)
        store.close()

        reopened = EdgeStore(path)
        reopened.initialize()
        update = reopened.get_mcu_firmware_update(update_uid)
        assert update["prepare_recovery_required"] is True
        assert update["prepare_identity"] == identity
        assert reopened.get_maintenance_lock()["owner_uid"] == update_uid
        reopened.close()

    def test_update_lock_blocks_new_work_and_command_claims(self, tmp_path):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        store.receive_command("cmd-pending", "TEST", {})
        update_uid = str(uuid.uuid4())
        deployment_uid = str(uuid.uuid4())

        result = store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=deployment_uid,
            source="LOCAL",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="1" * 64,
            manifest=firmware_manifest(),
            legacy_preflight=True,
            requested_reason="bench rollout",
        )

        assert result == "ACCEPTED"
        assert store.get_maintenance_lock()["owner_uid"] == update_uid
        assert store.acquire_work_slot(
            WORK_TYPE_DELIVERY,
            "blocked-work",
            1,
            {},
        ) is False
        assert store.claim_next_command() is None
        store.close()

    def test_update_lock_allows_only_its_failed_package_command_retry(
        self,
        tmp_path,
    ):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        command_uid = str(uuid.uuid4())
        update_uid = str(uuid.uuid4())
        deployment_uid = str(uuid.uuid4())
        store.receive_command(
            command_uid,
            "START_MCU_FIRMWARE_UPDATE",
            {"commandUid": command_uid},
        )
        assert store.claim_next_command()["command_uid"] == command_uid
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=deployment_uid,
            command_uid=command_uid,
            source="CLOUD",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="1" * 64,
            manifest=firmware_manifest(),
            package_ready=False,
        ) == "ACCEPTED"
        assert store.fail_mcu_firmware_package_acquisition(
            update_uid,
            "COS_DOWNLOAD_FAILED",
            "temporary download failure",
        )
        failed = store.get_command(command_uid)
        assert failed["state"] == "FAILED"
        assert failed["last_error"] == "COS_DOWNLOAD_FAILED"
        assert store.requeue_failed_mcu_firmware_command(command_uid)

        retried = store.claim_next_command()

        assert retried["command_uid"] == command_uid
        assert retried["attempt_count"] == 2
        store.close()

    def test_current_and_previous_stable_packages_are_shifted_atomically(
        self,
        tmp_path,
    ):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        first_uid = str(uuid.uuid4())
        first_manifest = firmware_manifest(10000)
        first_path = str((tmp_path / "first.efw").resolve())
        assert store.begin_mcu_firmware_update(
            update_uid=first_uid,
            deployment_uid=str(uuid.uuid4()),
            source="LOCAL",
            package_path=first_path,
            package_sha256="1" * 64,
            manifest=first_manifest,
            legacy_preflight=True,
        ) == "ACCEPTED"
        assert store.record_mcu_firmware_attempt(
            first_uid,
            rollback=False,
        ) == 1
        assert store.complete_mcu_firmware_update(first_uid)

        second_uid = str(uuid.uuid4())
        second_manifest = firmware_manifest(20000)
        second_path = str((tmp_path / "second.efw").resolve())
        assert store.begin_mcu_firmware_update(
            update_uid=second_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=second_path,
            package_sha256="2" * 64,
            manifest=second_manifest,
        ) == "ACCEPTED"
        pending = store.get_mcu_firmware_update(second_uid)
        assert pending["previous_package_path"] == first_path
        assert pending["previous_manifest"] == first_manifest
        assert store.complete_mcu_firmware_update(second_uid)

        stable = store.get_mcu_firmware_state()
        assert stable["current_package_path"] == second_path
        assert stable["current_manifest"] == second_manifest
        assert stable["previous_package_path"] == first_path
        assert stable["previous_manifest"] == first_manifest
        assert store.get_maintenance_lock() is None
        store.close()

    def test_successful_rollback_keeps_stable_identity_and_unlocks(self, tmp_path):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        stable_uid = str(uuid.uuid4())
        stable_manifest = firmware_manifest(10000)
        assert store.begin_mcu_firmware_update(
            update_uid=stable_uid,
            deployment_uid=str(uuid.uuid4()),
            source="LOCAL",
            package_path=str((tmp_path / "stable.efw").resolve()),
            package_sha256="1" * 64,
            manifest=stable_manifest,
            legacy_preflight=True,
        ) == "ACCEPTED"
        assert store.complete_mcu_firmware_update(stable_uid)

        failed_uid = str(uuid.uuid4())
        assert store.begin_mcu_firmware_update(
            update_uid=failed_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=str((tmp_path / "bad.efw").resolve()),
            package_sha256="2" * 64,
            manifest=firmware_manifest(20000),
        ) == "ACCEPTED"
        assert store.record_mcu_firmware_attempt(
            failed_uid,
            rollback=True,
        ) == 1
        assert store.complete_mcu_firmware_rollback(failed_uid)

        assert store.get_mcu_firmware_update(failed_uid)["state"] == "ROLLED_BACK"
        assert store.get_mcu_firmware_state()["current_manifest"] == stable_manifest
        assert store.get_maintenance_lock() is None
        assert store.create_mcu_firmware_progress_event(
            device_name="SN-TEST-1",
            update_uid=failed_uid,
            stage="ROLLED_BACK",
        ) == "ACCEPTED"
        envelope = json.loads(store.list_pending_events()[0]["payload_json"])
        assert envelope["payload"]["firmwareVersionCode"] == 20000
        assert (
            envelope["payload"]["installedFirmwareVersion"]
            == stable_manifest["firmwareVersion"]
        )
        assert (
            envelope["payload"]["installedFirmwareVersionCode"]
            == stable_manifest["firmwareVersionCode"]
        )
        assert (
            envelope["payload"]["installedFirmwareIdentityHex"]
            == stable_manifest["firmwareIdentityHex"]
        )
        store.close()

    def test_failed_update_lock_survives_process_reopen(self, tmp_path):
        path = tmp_path / "edge.db"
        store = EdgeStore(str(path))
        store.initialize()
        update_uid = str(uuid.uuid4())
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=str(uuid.uuid4()),
            source="LOCAL",
            package_path=str((tmp_path / "bad.efw").resolve()),
            package_sha256="3" * 64,
            manifest=firmware_manifest(),
            legacy_preflight=True,
        ) == "ACCEPTED"
        assert store.fail_mcu_firmware_update_locked(
            update_uid,
            "ROLLBACK_FAILED",
            "target and rollback verification failed",
        )
        store.close()

        reopened = EdgeStore(str(path))
        reopened.initialize()
        update = reopened.get_active_mcu_firmware_update()
        assert update["update_uid"] == update_uid
        assert update["state"] == "FAILED_LOCKED"
        assert update["last_error_code"] == "ROLLBACK_FAILED"
        reopened.close()

    def test_cloud_update_may_exclude_its_own_processing_command(self, tmp_path):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        command_uid = str(uuid.uuid4())
        store.receive_command(command_uid, "START_MCU_FIRMWARE_UPDATE", {})
        assert store.claim_next_command()["command_uid"] == command_uid

        assert store.begin_mcu_firmware_update(
            update_uid=str(uuid.uuid4()),
            deployment_uid=str(uuid.uuid4()),
            command_uid=command_uid,
            source="CLOUD",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="4" * 64,
            manifest=firmware_manifest(),
        ) == "ACCEPTED"
        store.close()

    def test_update_progress_event_is_reliable_and_attempt_idempotent(self, tmp_path):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        update_uid = str(uuid.uuid4())
        deployment_uid = str(uuid.uuid4())
        manifest = firmware_manifest()
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=deployment_uid,
            source="LOCAL",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="5" * 64,
            manifest=manifest,
            legacy_preflight=True,
        ) == "ACCEPTED"

        assert store.create_mcu_firmware_progress_event(
            device_name="SN-TEST-1",
            update_uid=update_uid,
            stage="QUEUED",
        ) == "ACCEPTED"
        assert store.create_mcu_firmware_progress_event(
            device_name="SN-TEST-1",
            update_uid=update_uid,
            stage="QUEUED",
        ) == "DUPLICATE"
        events = store.list_pending_events()
        envelope = json.loads(events[0]["payload_json"])
        assert envelope["eventType"] == "MCU_FIRMWARE_UPDATE_PROGRESS"
        assert envelope["deliveryClass"] == "RELIABLE_FACT"
        assert envelope["target"] == {
            "type": "MCU_FIRMWARE_DEPLOYMENT",
            "uid": deployment_uid,
        }
        assert envelope["payload"]["stage"] == "QUEUED"
        assert envelope["payload"]["releaseUid"] == manifest["releaseUid"]
        assert envelope["payload"]["installedFirmwareVersion"] is None
        store.close()

    def test_success_state_and_progress_event_commit_atomically(
        self,
        tmp_path,
        monkeypatch,
    ):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        update_uid = str(uuid.uuid4())
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="6" * 64,
            manifest=firmware_manifest(),
        ) == "ACCEPTED"
        store.record_mcu_firmware_attempt(update_uid, rollback=False)
        store.transition_mcu_firmware_update(
            update_uid,
            "VERIFYING_TARGET",
        )

        def fail_outbox_insert(*_args, **_kwargs):
            raise RuntimeError("injected outbox failure")

        monkeypatch.setattr(store, "_insert_event", fail_outbox_insert)
        with pytest.raises(RuntimeError, match="outbox failure"):
            store.complete_mcu_firmware_update(
                update_uid,
                device_name="SN-TEST-1",
            )

        update = store.get_mcu_firmware_update(update_uid)
        assert update["state"] == "VERIFYING_TARGET"
        assert store.get_maintenance_lock()["owner_uid"] == update_uid
        assert store.list_pending_events() == []
        store.close()

    def test_startup_reconciliation_backfills_a_missing_terminal_event(
        self,
        tmp_path,
    ):
        store = EdgeStore(str(tmp_path / "edge.db"))
        store.initialize()
        update_uid = str(uuid.uuid4())
        assert store.begin_mcu_firmware_update(
            update_uid=update_uid,
            deployment_uid=str(uuid.uuid4()),
            source="CLOUD",
            package_path=str((tmp_path / "release.efw").resolve()),
            package_sha256="7" * 64,
            manifest=firmware_manifest(),
        ) == "ACCEPTED"
        assert store.record_mcu_firmware_attempt(
            update_uid,
            rollback=False,
        ) == 1
        assert store.complete_mcu_firmware_update(update_uid)
        assert store.list_pending_events() == []

        assert store.reconcile_mcu_firmware_progress_events(
            "SN-TEST-1"
        ) == 1
        assert store.reconcile_mcu_firmware_progress_events(
            "SN-TEST-1"
        ) == 0
        events = store.list_pending_events()
        assert len(events) == 1
        envelope = json.loads(events[0]["payload_json"])
        assert envelope["payload"]["stage"] == "SUCCEEDED"
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
        assert command["last_error"] == "EDGE_RESTARTED"
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


class TestPortFullnessStateTransitions:
    """只有当前袋状态真正变化时才创建可靠满溢事件。"""

    @staticmethod
    def transition(
        *,
        bag_uid: str,
        state: str,
        event_uid: str,
        state_change_uid: str,
    ) -> dict:
        return {
            "port_no": 1,
            "bag_uid": bag_uid,
            "state": state,
            "state_change_uid": state_change_uid,
            "event_uid": event_uid,
            "device_name": "SN-DEMO-0001",
            "payload": {
                "stateChangeUid": state_change_uid,
                "portNo": 1,
                "bagUid": bag_uid,
                "state": state,
            },
        }

    @staticmethod
    def complete_with_transition(
        store: EdgeStore,
        completion_uid: str,
        transition: dict,
    ) -> None:
        assert store.create_edge_event(
            completion_uid,
            "DELIVERY_COMPLETE",
            {},
            device_name="SN-DEMO-0001",
            target_type="DELIVERY_SESSION",
            target_uid=completion_uid,
            fullness_transition=transition,
        ) == "ACCEPTED"

    def test_full_not_full_and_bag_replacement_are_state_changes_only(self):
        store = make_store()
        bag_a = "10000000-0000-4000-8000-000000000001"
        bag_b = "10000000-0000-4000-8000-000000000002"

        assert store.get_port_fullness_state(1, bag_a) == "NOT_FULL"

        self.complete_with_transition(
            store,
            "20000000-0000-4000-8000-000000000001",
            self.transition(
                bag_uid=bag_a,
                state="FULL",
                event_uid="30000000-0000-4000-8000-000000000001",
                state_change_uid=(
                    "40000000-0000-4000-8000-000000000001"
                ),
            ),
        )
        assert store.get_port_fullness_state(1, bag_a) == "FULL"

        self.complete_with_transition(
            store,
            "20000000-0000-4000-8000-000000000002",
            self.transition(
                bag_uid=bag_a,
                state="FULL",
                event_uid="30000000-0000-4000-8000-000000000002",
                state_change_uid=(
                    "40000000-0000-4000-8000-000000000002"
                ),
            ),
        )
        assert store._conn.execute(
            """SELECT COUNT(*) FROM event_outbox
               WHERE event_type='FULLNESS_STATE_CHANGED'"""
        ).fetchone()[0] == 1

        self.complete_with_transition(
            store,
            "20000000-0000-4000-8000-000000000003",
            self.transition(
                bag_uid=bag_a,
                state="NOT_FULL",
                event_uid="30000000-0000-4000-8000-000000000003",
                state_change_uid=(
                    "40000000-0000-4000-8000-000000000003"
                ),
            ),
        )
        assert store.get_port_fullness_state(1, bag_a) == "NOT_FULL"
        assert store._conn.execute(
            """SELECT COUNT(*) FROM event_outbox
               WHERE event_type='FULLNESS_STATE_CHANGED'"""
        ).fetchone()[0] == 2

        self.complete_with_transition(
            store,
            "20000000-0000-4000-8000-000000000004",
            self.transition(
                bag_uid=bag_b,
                state="NOT_FULL",
                event_uid="30000000-0000-4000-8000-000000000004",
                state_change_uid=(
                    "40000000-0000-4000-8000-000000000004"
                ),
            ),
        )
        assert store.get_port_fullness_state(1, bag_b) == "NOT_FULL"
        assert store._conn.execute(
            """SELECT COUNT(*) FROM event_outbox
               WHERE event_type='FULLNESS_STATE_CHANGED'"""
        ).fetchone()[0] == 2
        row = store._conn.execute(
            """SELECT bag_uid, state, last_event_uid
               FROM port_fullness_state WHERE port_no=1"""
        ).fetchone()
        assert dict(row) == {
            "bag_uid": bag_b,
            "state": "NOT_FULL",
            "last_event_uid": None,
        }
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

    def test_restart_preserves_durable_completion_without_failed_event(self):
        store = make_store()
        command_uid = "10000000-0000-4000-8000-000000000001"
        work_uid = "20000000-0000-4000-8000-000000000001"
        command = {
            "commandUid": command_uid,
            "commandType": "START_DELIVERY_SESSION",
            "targetDeviceName": "SN-DEMO-0001",
        }
        store.receive_command(
            command_uid,
            command["commandType"],
            command,
        )
        store.claim_next_command()
        store.acquire_work_slot(
            WORK_TYPE_DELIVERY,
            work_uid,
            1,
            {
                "start_command_uid": command_uid,
                "device_name": "SN-DEMO-0001",
            },
        )
        store.receive_mcu_event(
            "30000000-0000-4000-8000-000000000001",
            "DELIVERY_COMPLETE",
            {"sessionUid": work_uid},
            work_uid,
        )

        result = store.abort_interrupted_work()

        assert result["outcome"] == "DURABLE_COMPLETION_PRESERVED"
        assert store.get_work_slot() is None
        assert [
            row for row in store.list_pending_events()
            if row["event_type"] == "DELIVERY_COMPLETE"
        ]
        assert not [
            row for row in store.list_pending_events()
            if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
        ]
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

    def test_stale_sender_cannot_reopen_a_business_confirmed_event(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        stale_event = store.list_pending_events()[0]

        assert store.receive_business_confirmation(
            "conf-1",
            "evt-1",
            "BUSINESS_APPLIED",
        ) == "ACCEPTED"

        store.mark_event_sending(stale_event["event_uid"], 42)
        store.mark_event_pending_retry(stale_event["event_uid"])

        row = store.get_event("evt-1")
        assert row["state"] == EVENT_CONFIRMED
        assert row["confirmed_at"] is not None
        assert row["mqtt_msg_id"] is None
        assert row["retry_count"] == 0
        assert store.list_pending_events() == []
        assert store.count_pending_reliable_events() == 0
        store.close()

    def test_initialize_repairs_a_confirmed_event_left_pending(self):
        store = make_store()
        path = store.db_path
        store.receive_mcu_event("evt-1", "E1", {})
        assert store.receive_business_confirmation(
            "conf-1",
            "evt-1",
            "BUSINESS_APPLIED",
        ) == "ACCEPTED"
        store._conn.execute(
            """UPDATE event_outbox
               SET state='PENDING', mqtt_msg_id=42,
                   next_retry_at='2000-01-01T00:00:00'
               WHERE event_uid='evt-1'"""
        )
        store._conn.commit()
        store.close()

        recovered = EdgeStore(path)
        recovered.initialize()

        row = recovered.get_event("evt-1")
        assert row["state"] == EVENT_CONFIRMED
        assert row["confirmed_at"] is not None
        assert row["mqtt_msg_id"] is None
        assert row["next_retry_at"] is None
        assert recovered.list_pending_events() == []
        recovered.close()

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

    def test_platform_acceptance_is_recorded_without_confirming_business(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        event = store.get_event("evt-1")

        event_uid = store.record_event_platform_reply(
            event["edge_event_sequence"],
            200,
        )

        row = store.get_event("evt-1")
        assert event_uid == "evt-1"
        assert row["state"] == EVENT_PENDING
        assert row["last_platform_code"] == 200
        assert row["platform_accepted_at"] is not None
        assert row["last_platform_reply_at"] is not None
        store.close()

    def test_permanent_platform_rejection_cannot_be_resurrected_by_puback(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        event = store.get_event("evt-1")

        store.record_event_platform_reply(
            event["edge_event_sequence"],
            2402,
        )
        store.mark_event_pending_retry("evt-1")

        row = store.get_event("evt-1")
        assert row["state"] == "DEAD"
        assert row["last_platform_code"] == 2402
        assert row["next_retry_at"] is None
        store.close()

    def test_retryable_platform_rejection_requeues_event(self):
        store = make_store()
        store.receive_mcu_event("evt-1", "E1", {})
        store.mark_event_sending("evt-1", 42)
        event = store.get_event("evt-1")

        store.record_event_platform_reply(
            event["edge_event_sequence"],
            500,
        )

        row = store.get_event("evt-1")
        assert row["state"] == EVENT_PENDING
        assert row["retry_count"] == 1
        assert row["last_platform_code"] == 500
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
        store.mark_photo_uploaded(
            "p1",
            "cos/key/p1.jpg",
            "https://example.invalid/cos/key/p1.jpg",
        )
        row = store._conn.execute(
            """SELECT state, cos_key, url FROM photo_outbox
               WHERE photo_uid='p1'"""
        ).fetchone()
        assert row["state"] == PHOTO_UPLOADED
        assert row["cos_key"] == "cos/key/p1.jpg"
        assert row["url"] == "https://example.invalid/cos/key/p1.jpg"
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

    def test_reliable_fault_lifecycle_deduplicates_and_upgrades(self):
        store = make_store()
        fault_uid = str(uuid.uuid4())

        assert store.observe_fault_and_create_event(
            device_name="SN-DEMO-0001",
            component="CAMERA",
            fault_code="CAMERA_CAPTURE",
            severity="WARNING",
            fault_uid=fault_uid,
        ) == "ACCEPTED"
        assert store.observe_fault_and_create_event(
            device_name="SN-DEMO-0001",
            component="CAMERA",
            fault_code="CAMERA_CAPTURE",
            severity="WARNING",
            fault_uid=str(uuid.uuid4()),
        ) == "DUPLICATE"
        assert store.observe_fault_and_create_event(
            device_name="SN-DEMO-0001",
            component="CAMERA",
            fault_code="CAMERA_CAPTURE",
            severity="BLOCK_DEVICE",
        ) == "ACCEPTED"

        observed = store._conn.execute(
            """SELECT payload_json FROM event_outbox
               WHERE event_type='DEVICE_FAULT_OBSERVED'
               ORDER BY edge_event_sequence"""
        ).fetchall()
        assert len(observed) == 2
        assert {
            json.loads(row["payload_json"])["payload"]["faultUid"]
            for row in observed
        } == {fault_uid}
        active = store.list_active_faults()
        assert len(active) == 1
        assert active[0]["severity"] == "BLOCK_DEVICE"
        assert active[0]["discovery_count"] == 3

        assert store.recover_fault_and_create_event(
            device_name="SN-DEMO-0001",
            fault_uid=fault_uid,
            component="CAMERA",
            fault_code="CAMERA_CAPTURE",
            port_no=None,
            recovery_evidence="CAPTURE_SUCCEEDED",
        ) == "ACCEPTED"
        assert store.recover_fault_and_create_event(
            device_name="SN-DEMO-0001",
            fault_uid=fault_uid,
            component="CAMERA",
            fault_code="CAMERA_CAPTURE",
            port_no=None,
            recovery_evidence="CAPTURE_SUCCEEDED",
        ) == "DUPLICATE"
        recovered = store._conn.execute(
            """SELECT COUNT(*) AS count FROM event_outbox
               WHERE event_type='DEVICE_FAULT_RECOVERED'"""
        ).fetchone()
        assert recovered["count"] == 1
        assert store.list_active_faults() == []
        store.close()

    def test_real_safety_event_atomically_finishes_mcu_inbox(self):
        store = make_store()
        generation = store.begin_mcu_receive_generation(101)
        frame = {
            "message_name": "SAFETY_SENSOR_EVENT",
            "message_type": 54,
            "tx_sequence": 9,
            "payload": {
                "mcuBootId": 101,
                "mcuEventSequence": 7,
                "portNo": 1,
                "smokeState": "ALARM",
                "smokeSensorHealth": "OK",
                "faultCode": "NONE",
                "workType": "NONE",
                "workUid": None,
            },
        }
        assert store.receive_mcu_frame(frame) == "ACCEPTED"

        assert store.record_safety_state_and_event(
            device_name="SN-DEMO-0001",
            mcu_receive_generation=generation,
            payload=frame["payload"],
        ) == "ACCEPTED"
        inbox = store._conn.execute(
            """SELECT state FROM mcu_event_inbox
               WHERE mcu_receive_generation=?
                 AND mcu_boot_id=101 AND mcu_event_sequence=7""",
            (generation,),
        ).fetchone()
        assert inbox["state"] == "PROCESSED"
        assert store.get_state("port_1_smoke_state") == "ALARM"
        assert store.record_safety_state_and_event(
            device_name="SN-DEMO-0001",
            mcu_receive_generation=generation,
            payload=frame["payload"],
        ) == "DUPLICATE"
        count = store._conn.execute(
            """SELECT COUNT(*) AS count FROM event_outbox
               WHERE event_type='SAFETY_SENSOR_STATE_CHANGED'"""
        ).fetchone()["count"]
        assert count == 1
        store.close()

    def test_fixed_frame_safety_only_emits_when_state_changes(self):
        store = make_store()
        generation = store.begin_mcu_receive_generation(101)

        def record(sequence, smoke_state):
            frame = {
                "message_name": "SAFETY_SENSOR_EVENT",
                "message_type": 54,
                "tx_sequence": sequence,
                "payload": {
                    "mcuBootId": 101,
                    "mcuEventSequence": sequence,
                    "portNo": 1,
                    "smokeState": smoke_state,
                    "smokeSensorHealth": "OK",
                    "faultCode": "NONE",
                    "workType": "NONE",
                    "workUid": None,
                    "compatibilityMode": True,
                    "rawFrameHex": (
                        "cc00cc" if smoke_state == "NORMAL" else "cc01cc"
                    ),
                },
            }
            assert store.receive_mcu_frame(frame) == "ACCEPTED"
            return store.record_safety_state_and_event(
                device_name="SN-DEMO-0001",
                mcu_receive_generation=generation,
                payload=frame["payload"],
            )

        assert record(1, "NORMAL") == "ACCEPTED"
        assert record(2, "NORMAL") == "UNCHANGED"
        assert record(3, "ALARM") == "ACCEPTED"

        rows = store._conn.execute(
            """SELECT payload_json FROM event_outbox
               WHERE event_type='SAFETY_SENSOR_STATE_CHANGED'
               ORDER BY edge_event_sequence"""
        ).fetchall()
        assert [
            json.loads(row["payload_json"])["payload"]["smokeState"]
            for row in rows
        ] == ["NORMAL", "ALARM"]
        inbox = store._conn.execute(
            """SELECT state FROM mcu_event_inbox
               WHERE mcu_receive_generation=?
                 AND mcu_boot_id=101 AND mcu_event_sequence=2""",
            (generation,),
        ).fetchone()
        assert inbox["state"] == "PROCESSED"
        assert store.get_state("port_1_smoke_state") == "ALARM"
        store.close()

    def test_fixed_frame_self_test_reports_smoke_projection_changes(self):
        store = make_store()
        timeout = {
            "queryStatus": "TIMEOUT",
            "communicationHealthy": False,
            "portNo": 1,
            "validFlags": 0,
            "weightValid": False,
            "weightGrams": None,
            "weightMeasurementUid": None,
            "infraredValid": False,
            "infraredBlocked": None,
            "smokeCode": None,
            "smokeState": "UNKNOWN",
            "smokeSensorHealth": "TIMEOUT",
            "faultCode": "SMOKE_SENSOR",
            "rawFrameHex": None,
        }
        normal = {
            **timeout,
            "queryStatus": "OK",
            "communicationHealthy": True,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 12_000,
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
            "faultCode": None,
        }

        assert store.save_fixed_frame_self_test(timeout) is True
        assert store.save_fixed_frame_self_test(timeout) is False
        assert store.save_fixed_frame_self_test(normal) is True
        assert store.save_fixed_frame_self_test(normal) is False
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

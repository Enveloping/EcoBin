from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from communication_store import CommunicationStore


NOW = datetime(2030, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)


def test_store_records_immutable_process_starts_across_restarts(tmp_path: Path):
    path = tmp_path / "communication.db"
    first = CommunicationStore(path, utc_now=lambda: NOW, process_id=lambda: 123)
    first.initialize()
    first_start = first.record_process_start("communication-1.0.0")
    first.close()

    second = CommunicationStore(path, utc_now=lambda: NOW, process_id=lambda: 456)
    second.initialize()
    second_start = second.record_process_start("communication-1.0.1")
    status = second.get_status()
    second.close()

    assert first_start["startUid"] != second_start["startUid"]
    assert first_start["startedAt"] == "2030-01-02T03:04:05.678Z"
    assert status == {
        "schemaVersion": 2,
        "processStartCount": 2,
        "latestProcessStart": second_start,
        "inboundCommandCount": 0,
        "inboundBusinessAcceptedCount": 0,
        "outboundBusinessEventCount": 0,
        "outboundSendAttemptCount": 0,
        "outboundPlatformConfirmedCount": 0,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT release_version, process_id FROM process_start_fact ORDER BY sequence"
        ).fetchall() == [
            ("communication-1.0.0", 123),
            ("communication-1.0.1", 456),
        ]


def test_initialize_is_idempotent_on_one_store(tmp_path: Path):
    store = CommunicationStore(tmp_path / "communication.db")
    try:
        store.initialize()
        store.initialize()
        assert store.get_status()["schemaVersion"] == 2
    finally:
        store.close()


def test_store_refuses_business_database_name(tmp_path: Path):
    with pytest.raises(ValueError, match="must not use.*edge.db"):
        CommunicationStore(tmp_path / "edge.db")


def test_store_refuses_symbolic_link(tmp_path: Path):
    target = tmp_path / "actual.db"
    target.touch()
    link = tmp_path / "communication.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(PermissionError, match="symlink"):
        CommunicationStore(link).initialize()


def test_incompatible_schema_is_rejected_without_replacing_fact(tmp_path: Path):
    path = tmp_path / "communication.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version(singleton_id INTEGER PRIMARY KEY, version INTEGER)"
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 99)")
    if os.name == "posix":
        path.chmod(0o600)

    store = CommunicationStore(path)
    with pytest.raises(RuntimeError, match="schema is incompatible"):
        store.initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id=1"
        ).fetchone()[0] == 99


def test_invalid_clock_and_release_do_not_create_process_fact(tmp_path: Path):
    store = CommunicationStore(
        tmp_path / "communication.db",
        utc_now=lambda: datetime(2030, 1, 1),
    )
    store.initialize()
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.record_process_start("communication-1")
        with pytest.raises(ValueError, match="release version"):
            store.record_process_start("")
        with pytest.raises(ValueError, match="release version"):
            store.record_process_start("v" * 33)
        assert store.get_status()["processStartCount"] == 0
    finally:
        store.close()


def _create_v1_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                version INTEGER NOT NULL CHECK (version > 0)
            );
            INSERT INTO schema_version(singleton_id, version) VALUES (1, 1);
            CREATE TABLE process_start_fact (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                start_uid TEXT NOT NULL UNIQUE,
                release_version TEXT NOT NULL,
                process_id INTEGER NOT NULL CHECK (process_id > 0),
                started_at TEXT NOT NULL
            );
            INSERT INTO process_start_fact (
                start_uid, release_version, process_id, started_at
            ) VALUES (
                '10000000-0000-4000-8000-000000000001',
                'communication-1.0.0', 123,
                '2030-01-02T03:04:05.678Z'
            );
            """
        )
    if os.name == "posix":
        path.chmod(0o600)


def test_v1_database_migrates_to_v2_without_losing_process_facts(
    tmp_path: Path,
):
    path = tmp_path / "communication.db"
    _create_v1_database(path)

    store = CommunicationStore(path)
    try:
        store.initialize()
        status = store.get_status()
        assert status["schemaVersion"] == 2
        assert status["processStartCount"] == 1
        assert status["latestProcessStart"]["releaseVersion"] == (
            "communication-1.0.0"
        )
    finally:
        store.close()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id=1"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM inbound_command_ledger"
        ).fetchone() == (0,)


def test_v1_to_v2_migration_rolls_back_schema_and_version_together(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "communication.db"
    _create_v1_database(path)

    def fail_after_ddl(self, connection):
        connection.execute("CREATE TABLE interrupted_migration(value INTEGER)")
        raise RuntimeError("simulated power loss")

    monkeypatch.setattr(
        CommunicationStore,
        "_migrate_v1_to_v2",
        fail_after_ddl,
    )
    with pytest.raises(RuntimeError, match="simulated power loss"):
        CommunicationStore(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version WHERE singleton_id=1"
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table' AND name='interrupted_migration'"""
        ).fetchone() == (0,)
        assert connection.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table' AND name='inbound_command_ledger'"""
        ).fetchone() == (0,)


def test_quick_check_rejects_a_corrupt_database(tmp_path: Path):
    path = tmp_path / "communication.db"
    path.write_bytes(b"not-a-sqlite-database")
    if os.name == "posix":
        path.chmod(0o600)

    with pytest.raises(RuntimeError, match="quick_check failed"):
        CommunicationStore(path).initialize()


def test_inbound_command_is_two_phase_idempotent_and_conflict_safe(
    tmp_path: Path,
):
    store = CommunicationStore(tmp_path / "communication.db", utc_now=lambda: NOW)
    store.initialize()
    command_uid = "10000000-0000-4000-8000-000000000001"
    digest = "a" * 64
    try:
        assert store.receive_inbound_command(command_uid, digest) == "ACCEPTED"
        assert store.receive_inbound_command(command_uid, digest) == (
            "DUPLICATE_RECEIVED"
        )
        assert store.receive_inbound_command(command_uid, "b" * 64) == (
            "CONFLICT"
        )
        assert store.mark_inbound_business_accepted(
            command_uid,
            digest,
        ) == "ACCEPTED"
        assert store.mark_inbound_business_accepted(
            command_uid,
            digest,
        ) == "DUPLICATE"
        assert store.receive_inbound_command(command_uid, digest) == (
            "DUPLICATE_BUSINESS_ACCEPTED"
        )
        assert store.get_inbound_command(command_uid) == {
            "command_uid": command_uid,
            "content_sha256": digest,
            "state": "BUSINESS_ACCEPTED",
            "received_at": "2030-01-02T03:04:05.678Z",
            "business_accepted_at": "2030-01-02T03:04:05.678Z",
        }
    finally:
        store.close()


def test_permanent_inbound_ledger_survives_business_database_rollback(
    tmp_path: Path,
):
    ledger_path = tmp_path / "communication.db"
    business_path = tmp_path / "edge.db"
    business_snapshot_path = tmp_path / "edge-before-command.db"
    command_uid = "10000000-0000-4000-8000-000000000001"
    digest = "a" * 64

    with sqlite3.connect(business_path) as business:
        business.execute(
            "CREATE TABLE accepted_command(command_uid TEXT PRIMARY KEY)"
        )
        with sqlite3.connect(business_snapshot_path) as snapshot:
            business.backup(snapshot)

    first = CommunicationStore(ledger_path)
    first.initialize()
    assert first.receive_inbound_command(command_uid, digest) == "ACCEPTED"
    with sqlite3.connect(business_path) as business:
        business.execute(
            "INSERT INTO accepted_command(command_uid) VALUES (?)",
            (command_uid,),
        )
    assert first.mark_inbound_business_accepted(command_uid, digest) == (
        "ACCEPTED"
    )
    first.close()

    # The updater restores edge.db to its snapshot from before the command,
    # while the permanent communication.db remains outside that rollback.
    with sqlite3.connect(business_snapshot_path) as snapshot:
        with sqlite3.connect(business_path) as business:
            snapshot.backup(business)
    with sqlite3.connect(business_path) as business:
        assert business.execute(
            "SELECT COUNT(*) FROM accepted_command WHERE command_uid=?",
            (command_uid,),
        ).fetchone() == (0,)

    # Redelivery still cannot present accepted physical work as a new command.
    reopened = CommunicationStore(ledger_path)
    try:
        reopened.initialize()
        assert reopened.receive_inbound_command(command_uid, digest) == (
            "DUPLICATE_BUSINESS_ACCEPTED"
        )
    finally:
        reopened.close()


def test_outbound_event_attempts_and_platform_confirmation_are_monotonic(
    tmp_path: Path,
):
    store = CommunicationStore(tmp_path / "communication.db", utc_now=lambda: NOW)
    store.initialize()
    event_uid = "20000000-0000-4000-8000-000000000001"
    attempt_uid = "30000000-0000-4000-8000-000000000001"
    confirmation_uid = "40000000-0000-4000-8000-000000000001"
    digest = "c" * 64
    try:
        assert store.receive_outbound_business_event(event_uid, digest) == (
            "ACCEPTED"
        )
        assert store.receive_outbound_business_event(event_uid, digest) == (
            "DUPLICATE"
        )
        assert store.receive_outbound_business_event(
            event_uid,
            "d" * 64,
        ) == "CONFLICT"

        started = store.begin_outbound_send_attempt(
            event_uid,
            digest,
            attempt_uid,
        )
        assert started == {
            "disposition": "ACCEPTED",
            "attemptSequence": 1,
            "outcome": "STARTED",
        }
        assert store.begin_outbound_send_attempt(
            event_uid,
            digest,
            attempt_uid,
        ) == {
            "disposition": "DUPLICATE",
            "attemptSequence": 1,
            "outcome": "STARTED",
        }
        assert store.complete_outbound_send_attempt(
            event_uid,
            digest,
            attempt_uid,
            "RESULT_UNKNOWN",
        ) == "ACCEPTED"
        assert store.complete_outbound_send_attempt(
            event_uid,
            digest,
            attempt_uid,
            "RESULT_UNKNOWN",
        ) == "DUPLICATE"
        assert store.complete_outbound_send_attempt(
            event_uid,
            digest,
            attempt_uid,
            "TRANSPORT_ACCEPTED",
        ) == "CONFLICT"

        assert store.confirm_outbound_business_event(
            event_uid,
            digest,
            confirmation_uid,
            "e" * 64,
        ) == "ACCEPTED"
        assert store.confirm_outbound_business_event(
            event_uid,
            digest,
            confirmation_uid,
            "e" * 64,
        ) == "DUPLICATE"
        assert store.receive_outbound_business_event(event_uid, digest) == (
            "DUPLICATE_PLATFORM_CONFIRMED"
        )
        assert store.begin_outbound_send_attempt(
            event_uid,
            digest,
            "30000000-0000-4000-8000-000000000002",
        ) == {"disposition": "ALREADY_CONFIRMED"}

        event = store.get_outbound_business_event(event_uid)
        assert event["state"] == "PLATFORM_CONFIRMED"
        assert event["attempts"][0]["outcome"] == "RESULT_UNKNOWN"
        assert event["platform_confirmation"] == {
            "confirmation_uid": confirmation_uid,
            "confirmation_sha256": "e" * 64,
            "received_at": "2030-01-02T03:04:05.678Z",
        }
    finally:
        store.close()


def test_unknown_identities_and_invalid_values_fail_closed(tmp_path: Path):
    store = CommunicationStore(tmp_path / "communication.db")
    store.initialize()
    try:
        unknown = str(uuid.uuid4())
        assert store.mark_inbound_business_accepted(unknown, "a" * 64) == (
            "UNKNOWN"
        )
        assert store.begin_outbound_send_attempt(
            unknown,
            "a" * 64,
            str(uuid.uuid4()),
        ) == {"disposition": "UNKNOWN"}
        assert store.confirm_outbound_business_event(
            unknown,
            "a" * 64,
            str(uuid.uuid4()),
            "b" * 64,
        ) == "UNKNOWN"
        with pytest.raises(ValueError, match="lowercase UUIDv4"):
            store.receive_inbound_command("not-a-uuid", "a" * 64)
        with pytest.raises(ValueError, match="SHA-256 is invalid"):
            store.receive_outbound_business_event(unknown, "A" * 64)
        with pytest.raises(ValueError, match="outcome is invalid"):
            store.complete_outbound_send_attempt(
                unknown,
                "a" * 64,
                str(uuid.uuid4()),
                "MAYBE",
            )
    finally:
        store.close()


def test_ledger_rows_cannot_be_deleted_or_regressed(tmp_path: Path):
    store = CommunicationStore(tmp_path / "communication.db")
    store.initialize()
    command_uid = "10000000-0000-4000-8000-000000000001"
    event_uid = "20000000-0000-4000-8000-000000000001"
    attempt_uid = "30000000-0000-4000-8000-000000000001"
    confirmation_uid = "40000000-0000-4000-8000-000000000001"
    try:
        assert store.receive_inbound_command(command_uid, "a" * 64) == "ACCEPTED"
        assert store.mark_inbound_business_accepted(
            command_uid,
            "a" * 64,
        ) == "ACCEPTED"
        assert store.receive_outbound_business_event(event_uid, "b" * 64) == (
            "ACCEPTED"
        )
        assert store.begin_outbound_send_attempt(
            event_uid,
            "b" * 64,
            attempt_uid,
        )["disposition"] == "ACCEPTED"
        assert store.complete_outbound_send_attempt(
            event_uid,
            "b" * 64,
            attempt_uid,
            "RESULT_UNKNOWN",
        ) == "ACCEPTED"
        assert store.confirm_outbound_business_event(
            event_uid,
            "b" * 64,
            confirmation_uid,
            "c" * 64,
        ) == "ACCEPTED"

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with store.transaction() as connection:
                connection.execute(
                    "DELETE FROM inbound_command_ledger WHERE command_uid=?",
                    (command_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="not monotonic"):
            with store.transaction() as connection:
                connection.execute(
                    """UPDATE inbound_command_ledger
                       SET state='RECEIVED', business_accepted_at=NULL
                       WHERE command_uid=?""",
                    (command_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with store.transaction() as connection:
                connection.execute(
                    """DELETE FROM outbound_business_event_ledger
                       WHERE event_uid=?""",
                    (event_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with store.transaction() as connection:
                connection.execute(
                    "DELETE FROM outbound_send_attempt WHERE attempt_uid=?",
                    (attempt_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="not monotonic"):
            with store.transaction() as connection:
                connection.execute(
                    """UPDATE outbound_send_attempt
                       SET outcome='TRANSPORT_ACCEPTED'
                       WHERE attempt_uid=?""",
                    (attempt_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with store.transaction() as connection:
                connection.execute(
                    """DELETE FROM outbound_platform_confirmation
                       WHERE confirmation_uid=?""",
                    (confirmation_uid,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            with store.transaction() as connection:
                connection.execute(
                    """INSERT INTO inbound_command_ledger
                       (command_uid, content_sha256, state, received_at)
                       VALUES ('not-a-uuid', ?, 'RECEIVED', ?)""",
                    ("A" * 64, "2030-01-02T03:04:05.678Z"),
                )
    finally:
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership boundary")
def test_store_refuses_broad_parent_permissions(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o770)
    state_dir.chmod(0o770)
    try:
        with pytest.raises(PermissionError, match="private mode 0700"):
            CommunicationStore(state_dir / "communication.db").initialize()
    finally:
        state_dir.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard-link boundary")
def test_store_refuses_existing_hard_link(tmp_path: Path):
    original = tmp_path / "original.db"
    original.touch(mode=0o600)
    linked = tmp_path / "communication.db"
    os.link(original, linked)

    with pytest.raises(PermissionError, match="hard links"):
        CommunicationStore(linked).initialize()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode boundary")
def test_store_refuses_existing_database_with_broad_permissions(tmp_path: Path):
    path = tmp_path / "communication.db"
    path.touch(mode=0o644)

    with pytest.raises(PermissionError, match="permissions are too broad"):
        CommunicationStore(path).initialize()

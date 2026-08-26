from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from edge_store import EdgeStore
from remote_support_store import RemoteSupportStore
from trusted_clock import ClockSample


def _uid() -> str:
    return str(uuid.uuid4())


def _store(tmp_path: Path) -> RemoteSupportStore:
    result = RemoteSupportStore(tmp_path / "remote-support.db")
    result.initialize()
    return result


def test_agent_store_owns_session_and_durable_status_outbox(
    tmp_path: Path,
):
    store = _store(tmp_path)
    session_uid = _uid()
    command_uid = _uid()

    assert store.request_remote_support_open(
        session_uid=session_uid,
        command_uid=command_uid,
        device_name="ECM0-TEST",
        remote_port=22011,
        expires_at="2099-01-01T00:00:00.000Z",
    ) == "ACCEPTED"
    assert store.get_remote_support_session()["state"] == "CONNECTING"

    events = store.list_status_events(limit=10)
    assert len(events) == 1
    assert events[0] == {
        "eventUid": events[0]["eventUid"],
        "sessionUid": session_uid,
        "commandUid": command_uid,
        "deviceName": "ECM0-TEST",
        "remotePort": 22011,
        "state": "CONNECTING",
            "failureCode": None,
            "occurredAt": events[0]["occurredAt"],
            "clockQuality": "SYNCED",
        }

    event_uid = events[0]["eventUid"]
    store.close()

    reopened = _store(tmp_path)
    assert reopened.get_remote_support_session()["session_uid"] == session_uid
    assert reopened.list_status_events()[0]["eventUid"] == event_uid
    assert reopened.ack_status_event(event_uid) == "ACCEPTED"
    assert reopened.ack_status_event(event_uid) == "DUPLICATE"
    assert reopened.list_status_events() == []
    reopened.close()


def test_agent_store_terminal_session_uid_cannot_be_resurrected(
    tmp_path: Path,
):
    store = _store(tmp_path)
    session_uid = _uid()
    request = {
        "session_uid": session_uid,
        "command_uid": _uid(),
        "device_name": "ECM0-TEST",
        "remote_port": 22012,
        "expires_at": "2099-01-01T00:00:00.000Z",
    }
    assert store.request_remote_support_open(**request) == "ACCEPTED"
    assert store.transition_remote_support_session(
        session_uid,
        "CLOSED",
    ) == "ACCEPTED"
    event_count = len(store.list_status_events())

    assert store.request_remote_support_open(
        **{**request, "command_uid": _uid()},
    ) == "DUPLICATE"
    assert store.get_remote_support_session()["state"] == "CLOSED"
    assert len(store.list_status_events()) == event_count
    assert store.request_remote_support_open(
        **{**request, "command_uid": _uid(), "remote_port": 22013},
    ) == "CONFLICT"
    store.close()


def test_untrusted_clock_does_not_reject_deadline_or_emit_false_instant(
    tmp_path: Path,
):
    raw = "2023-11-14T22:13:20.000Z"
    store = RemoteSupportStore(
        tmp_path / "remote-support.db",
        clock_sampler=lambda: ClockSample(
            "UNAVAILABLE", None, None, raw
        ),
    )
    store.initialize()

    assert store.request_remote_support_open(
        session_uid=_uid(),
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22011,
        expires_at="2020-01-01T00:00:00.000Z",
    ) == "ACCEPTED"
    event = store.list_status_events()[0]
    assert event["occurredAt"] is None
    assert event["clockQuality"] == "UNAVAILABLE"
    store.close()


def _create_interrupted_v1_migration(
    path: Path,
    *,
    current_shape: str,
    schema_version: int = 1,
) -> str:
    event_uid = _uid()
    legacy_table = """
        CREATE TABLE {table_name} (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT NOT NULL UNIQUE,
            session_uid TEXT NOT NULL,
            command_uid TEXT NOT NULL,
            device_name TEXT NOT NULL,
            remote_port INTEGER NOT NULL,
            state TEXT NOT NULL,
            failure_code TEXT,
            occurred_at TEXT NOT NULL
        )
    """
    required_table = """
        CREATE TABLE status_outbox (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT NOT NULL UNIQUE,
            session_uid TEXT NOT NULL,
            command_uid TEXT NOT NULL,
            device_name TEXT NOT NULL,
            remote_port INTEGER NOT NULL,
            state TEXT NOT NULL,
            failure_code TEXT,
            occurred_at TEXT,
            raw_occurred_at TEXT NOT NULL,
            clock_quality TEXT NOT NULL
        )
    """
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO schema_version VALUES (?)",
            (schema_version,),
        )
        connection.execute(
            legacy_table.format(table_name="status_outbox_v1")
        )
        if current_shape == "required":
            connection.execute(required_table)
        connection.execute(
            """INSERT INTO status_outbox_v1 (
                   event_uid, session_uid, command_uid, device_name,
                   remote_port, state, failure_code, occurred_at
               ) VALUES (?, ?, ?, 'ECM0-TEST', 22011,
                         'CONNECTING', NULL, ?)""",
            (
                event_uid,
                _uid(),
                _uid(),
                "2026-08-24T10:00:00.000Z",
            ),
        )
    return event_uid


def test_v2_migration_recovers_backup_when_new_table_already_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-support.db"
    event_uid = _create_interrupted_v1_migration(
        path,
        current_shape="required",
        schema_version=2,
    )
    post_upgrade_event_uid = _uid()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO status_outbox (
                   event_uid, session_uid, command_uid, device_name,
                   remote_port, state, failure_code, occurred_at,
                   raw_occurred_at, clock_quality
               ) VALUES (?, ?, ?, 'ECM0-TEST', 22011,
                         'OPEN', NULL, ?, ?, 'SYNCED')""",
            (
                post_upgrade_event_uid,
                _uid(),
                _uid(),
                "2026-08-24T10:01:00.000Z",
                "2026-08-24T10:01:00.000Z",
            ),
        )

    store = RemoteSupportStore(path)
    store.initialize()

    assert [event["eventUid"] for event in store.list_status_events()] == [
        event_uid,
        post_upgrade_event_uid,
    ]
    assert store._require_connection().execute(
        "SELECT name FROM sqlite_master WHERE name='status_outbox_v1'"
    ).fetchone() is None
    assert store._require_connection().execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()[0] == 2
    store.close()


def test_v2_migration_recovers_when_only_legacy_rename_was_durable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-support.db"
    event_uid = _create_interrupted_v1_migration(
        path,
        current_shape="missing",
    )

    store = RemoteSupportStore(path)
    store.initialize()

    assert [event["eventUid"] for event in store.list_status_events()] == [
        event_uid
    ]
    assert store._require_connection().execute(
        "SELECT name FROM sqlite_master WHERE name='status_outbox_v1'"
    ).fetchone() is None
    store.close()


def test_v2_migration_rejects_conflicting_durable_event_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "remote-support.db"
    event_uid = _create_interrupted_v1_migration(
        path,
        current_shape="required",
        schema_version=2,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO status_outbox (
                   event_uid, session_uid, command_uid, device_name,
                   remote_port, state, failure_code, occurred_at,
                   raw_occurred_at, clock_quality
               ) VALUES (?, ?, ?, 'ECM0-TEST', 22011,
                         'OPEN', NULL, ?, ?, 'SYNCED')""",
            (
                event_uid,
                _uid(),
                _uid(),
                "2026-08-24T10:01:00.000Z",
                "2026-08-24T10:01:00.000Z",
            ),
        )

    store = RemoteSupportStore(path)
    with pytest.raises(
        RuntimeError,
        match="status outbox facts conflict",
    ):
        store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM status_outbox_v1"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM status_outbox"
        ).fetchone()[0] == 1


def test_nonexpired_legacy_session_is_imported_once_for_cutover(
    tmp_path: Path,
):
    legacy_path = tmp_path / "edge.db"
    legacy = EdgeStore(str(legacy_path))
    legacy.initialize()
    session_uid = _uid()
    command_uid = _uid()
    legacy.request_remote_support_open(
        session_uid=session_uid,
        command_uid=command_uid,
        device_name="ECM0-TEST",
        remote_port=22014,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    legacy.transition_remote_support_session(session_uid, "OPEN")
    legacy.close()

    store = _store(tmp_path)
    assert store.import_legacy_edge_store(legacy_path) == "IMPORTED"
    row = store.get_remote_support_session()
    assert row["session_uid"] == session_uid
    assert row["state"] == "CONNECTING"
    assert store.import_legacy_edge_store(legacy_path) == "ALREADY_INITIALIZED"
    store.close()

from __future__ import annotations

import uuid
from pathlib import Path

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

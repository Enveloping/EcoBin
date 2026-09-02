from __future__ import annotations

import os
import sqlite3
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
        "schemaVersion": 1,
        "processStartCount": 2,
        "latestProcessStart": second_start,
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
        assert store.get_status()["schemaVersion"] == 1
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

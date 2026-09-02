from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from updater_store import UpdaterStore


def _now() -> datetime:
    return datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)


def _store(path: Path, release_version: str) -> UpdaterStore:
    result = UpdaterStore(
        path,
        release_version=release_version,
        utc_now=_now,
    )
    result.initialize()
    return result


def test_store_persists_stage3_posture_and_each_real_runtime_instance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "updater-v1")
    first_status = first.get_status()
    first.close()

    second = _store(path, "updater-v2")
    second_status = second.get_status()

    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert first_status == {
        "component": "DEVICE_UPDATER",
        "schemaVersion": 1,
        "runtimeInstanceUid": first_status["runtimeInstanceUid"],
        "releaseVersion": "updater-v1",
        "startedAt": "2026-09-02T08:30:00.000Z",
        "managementStateSequence": 1,
        "updatesEnabled": False,
        "jobGateMode": "NOT_ENFORCED_STAGE3",
        "maintenanceState": "IDLE",
        "businessUpdateEnabled": False,
        "mcuUpdateEnabled": False,
    }
    assert uuid.UUID(first_status["runtimeInstanceUid"]).version == 4
    assert second_status["runtimeInstanceUid"] != first_status[
        "runtimeInstanceUid"
    ]
    assert second_status["releaseVersion"] == "updater-v2"

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall() == [(1,)]
        assert connection.execute(
            """SELECT component, release_version, started_at
               FROM updater_runtime_instance
               ORDER BY rowid"""
        ).fetchall() == [
            (
                "DEVICE_UPDATER",
                "updater-v1",
                "2026-09-02T08:30:00.000Z",
            ),
            (
                "DEVICE_UPDATER",
                "updater-v2",
                "2026-09-02T08:30:00.000Z",
            ),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM updater_management_state"
        ).fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not any("update_job" in table for table in tables)
    second.close()


def test_store_rejects_incompatible_schema_without_registering_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE schema_version (
                   singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                   version INTEGER NOT NULL CHECK (version > 0)
               )"""
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 2)")
    if os.name == "posix":
        path.chmod(0o600)

    store = UpdaterStore(path, release_version="updater-v1")
    with pytest.raises(
        RuntimeError,
        match="updater database schema is incompatible",
    ):
        store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='updater_runtime_instance'"
        ).fetchone() is None


@pytest.mark.parametrize(
    "release_version",
    ["", "x" * 33, " updater-v1", "updater-v1\n"],
)
def test_store_rejects_invalid_release_version(
    tmp_path: Path,
    release_version: str,
) -> None:
    with pytest.raises(ValueError, match="release version"):
        UpdaterStore(
            tmp_path / "updater.db",
            release_version=release_version,
        )


def test_store_never_accepts_the_business_database_name(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must not use the business edge.db"):
        UpdaterStore(tmp_path / "edge.db", release_version="updater-v1")


def test_store_rejects_database_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.db"
    target.touch()
    link = tmp_path / "updater.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("database symlinks are unavailable")

    store = UpdaterStore(link, release_version="updater-v1")
    with pytest.raises(PermissionError, match="single-linked regular file"):
        store.initialize()


def test_store_rejects_hard_linked_database(tmp_path: Path) -> None:
    target = tmp_path / "real.db"
    target.touch()
    link = tmp_path / "updater.db"
    try:
        os.link(target, link)
    except OSError:
        pytest.skip("database hard links are unavailable")

    store = UpdaterStore(link, release_version="updater-v1")
    with pytest.raises(PermissionError, match="single-linked regular file"):
        store.initialize()


def test_store_requires_precreated_private_state_directory(
    tmp_path: Path,
) -> None:
    store = UpdaterStore(
        tmp_path / "missing" / "updater.db",
        release_version="updater-v1",
    )

    with pytest.raises(PermissionError, match="parent does not exist"):
        store.initialize()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are required")
def test_store_rejects_shared_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "updater.db"
    path.touch(mode=0o600)
    path.chmod(0o640)

    store = UpdaterStore(path, release_version="updater-v1")
    with pytest.raises(PermissionError, match="permissions are too broad"):
        store.initialize()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are required")
def test_store_rejects_shared_database_parent(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o770)

    store = UpdaterStore(
        parent / "updater.db",
        release_version="updater-v1",
    )
    with pytest.raises(PermissionError, match="group/world access"):
        store.initialize()

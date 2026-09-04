from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest
from business_runtime_cutover import (
    STOP_UNITS,
    BusinessRuntimeCutover,
    BusinessRuntimeCutoverError,
    CutoverPaths,
    _exclusive_creatable_lock,
    _sqlite_read_only_uri,
)
from business_runtime_cutover_state import (
    BusinessRuntimeCutoverMode,
    inspect_business_runtime_cutover,
)

OPERATION_UID = "22b00000-0000-4000-8000-000000000010"
EVIDENCE_SHA256 = "a" * 64
NOW = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)


class RecordingServices:
    def __init__(self) -> None:
        self.stops: list[tuple[str, ...]] = []
        self.inactive_checks: list[tuple[str, ...]] = []

    def stop(self, units: tuple[str, ...]) -> None:
        self.stops.append(units)

    def require_inactive(self, units: tuple[str, ...]) -> None:
        self.inactive_checks.append(units)


class FailsFirstStopServices(RecordingServices):
    def stop(self, units: tuple[str, ...]) -> None:
        super().stop(units)
        if len(self.stops) == 1:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_STOP_FAILED",
                "injected stop interruption",
            )


def _identity() -> tuple[int, int]:
    if os.name == "posix":
        return os.getuid(), os.getgid()
    return 0, 0


def _prepare_directory(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True)
    path.chmod(mode)


def _paths(tmp_path: Path) -> CutoverPaths:
    state = tmp_path / "privileged/business-runtime-cutover"
    runtime = tmp_path / "run/privileged"
    legacy = tmp_path / "hardware"
    business = tmp_path / "business"
    for directory in (state, runtime, legacy, business, business / "photos"):
        _prepare_directory(directory)
    lock = runtime / "business-runtime-cutover.lock"
    lock.touch(mode=0o600)
    return CutoverPaths(
        state_root=state,
        pending_marker=state / "pending.json",
        active_marker=state / "active.json",
        lock_path=lock,
        legacy_data_root=legacy,
        business_data_root=business,
        current_business_release=tmp_path / "opt/business/current",
    )


def _create_legacy_database(
    paths: CutoverPaths,
    *,
    active_work: bool = False,
    escaping_photo: bool = False,
) -> tuple[Path, Path]:
    photo = paths.legacy_photos / "delivery/work-1/open/photo-1.jpg"
    _prepare_directory(photo.parent)
    photo.write_bytes(b"photo-evidence")
    photo.chmod(0o600)
    active_path = (
        paths.legacy_data_root.parent / "escaped.jpg"
        if escaping_photo
        else photo
    )
    connection = sqlite3.connect(paths.legacy_database)
    connection.executescript(
        """
        CREATE TABLE schema_version(version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (18);
        CREATE TABLE work_slot(
            slot_id INTEGER PRIMARY KEY,
            work_type TEXT NOT NULL,
            work_uid TEXT,
            work_state TEXT
        );
        CREATE TABLE photo_outbox(
            photo_uid TEXT PRIMARY KEY,
            local_path TEXT NOT NULL,
            tombstoned INTEGER NOT NULL
        );
        CREATE TABLE event_outbox(
            event_uid TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE maintenance_lock(
            singleton_id INTEGER PRIMARY KEY,
            lock_type TEXT NOT NULL,
            owner_uid TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO work_slot VALUES (1, ?, ?, ?)",
        (
            "DELIVERY" if active_work else "NONE",
            "work-1" if active_work else None,
            "ACTIVE" if active_work else None,
        ),
    )
    connection.execute(
        "INSERT INTO photo_outbox VALUES ('photo-1', ?, 0)",
        (str(active_path),),
    )
    connection.execute(
        "INSERT INTO photo_outbox VALUES ('old-photo', ?, 1)",
        (str(paths.legacy_data_root.parent / "old-removed.jpg"),),
    )
    connection.execute(
        "INSERT INTO event_outbox VALUES ('event-1', '{\"state\":\"pending\"}')"
    )
    connection.commit()
    connection.close()
    paths.legacy_database.chmod(0o600)
    paths.legacy_boot_id.write_text("9001\n", encoding="ascii")
    paths.legacy_boot_id.chmod(0o600)
    paths.legacy_configuration.write_text(
        json.dumps({"version": 7, "state": "APPLIED"}),
        encoding="utf-8",
    )
    paths.legacy_configuration.chmod(0o600)
    return photo, active_path


def _cutover(
    paths: CutoverPaths,
    services: RecordingServices | None = None,
) -> tuple[BusinessRuntimeCutover, RecordingServices]:
    uid, gid = _identity()
    selected_services = services or RecordingServices()
    return (
        BusinessRuntimeCutover(
            paths=paths,
            business_uid=uid,
            business_gid=gid,
            privileged_uid=uid,
            privileged_gid=gid,
            services=selected_services,
            utc_now=lambda: NOW,
            mutation_guard=lambda: nullcontext(),
        ),
        selected_services,
    )


def test_cutover_migrates_idle_database_identity_configuration_and_photos(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    source_photo, _ = _create_legacy_database(paths)
    original_database = paths.legacy_database.read_bytes()
    cutover, services = _cutover(paths)

    result = cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert result["disposition"] == "ACTIVATED"
    assert result["cutoverMode"] == "ACTIVE"
    assert result["runtimeDataVerified"] is True
    assert not paths.pending_marker.exists()
    assert paths.active_marker.is_file()
    assert paths.legacy_database.read_bytes() == original_database
    assert paths.business_boot_id.read_text(encoding="ascii") == "9001\n"
    assert json.loads(paths.business_configuration.read_text(encoding="utf-8")) == {
        "version": 7,
        "state": "APPLIED",
    }
    target_photo = paths.business_photos / source_photo.relative_to(
        paths.legacy_photos
    )
    assert target_photo.read_bytes() == b"photo-evidence"
    with sqlite3.connect(paths.business_database) as connection:
        assert connection.execute(
            "SELECT payload_json FROM event_outbox WHERE event_uid='event-1'"
        ).fetchone() == ('{"state":"pending"}',)
        assert connection.execute(
            "SELECT local_path FROM photo_outbox WHERE photo_uid='photo-1'"
        ).fetchone() == (str(target_photo),)
        assert connection.execute(
            "SELECT local_path FROM photo_outbox WHERE photo_uid='old-photo'"
        ).fetchone() == (str(paths.legacy_data_root.parent / "old-removed.jpg"),)
    assert services.stops == [STOP_UNITS]
    assert services.inactive_checks == [STOP_UNITS]
    if os.name == "posix":
        for path in (
            paths.business_database,
            paths.business_boot_id,
            paths.business_configuration,
            target_photo,
            paths.active_marker,
        ):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_repeating_completed_cutover_is_read_only_and_idempotent(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    cutover, services = _cutover(paths)
    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)
    target_before = paths.business_database.read_bytes()

    result = cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert result["disposition"] == "ALREADY_ACTIVE"
    assert paths.business_database.read_bytes() == target_before
    assert services.stops == [STOP_UNITS]


def test_busy_work_is_rejected_before_persisting_or_stopping(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths, active_work=True)
    cutover, services = _cutover(paths)

    with pytest.raises(
        BusinessRuntimeCutoverError,
        match="physical business operation is still active",
    ):
        cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    inspection = inspect_business_runtime_cutover(
        pending_path=paths.pending_marker,
        active_path=paths.active_marker,
        expected_uid=_identity()[0],
        expected_gid=_identity()[1],
    )
    assert inspection.mode is BusinessRuntimeCutoverMode.LEGACY
    assert not paths.active_marker.exists()
    assert services.stops == []
    with sqlite3.connect(paths.legacy_database) as connection:
        connection.execute(
            """UPDATE work_slot
               SET work_type='NONE', work_uid=NULL, work_state=NULL
               WHERE slot_id=1"""
        )
        connection.commit()

    result = cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert result["disposition"] == "ACTIVATED"
    assert len(services.stops) == 1


def test_pending_cutover_resumes_after_service_stop_was_interrupted(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    services = FailsFirstStopServices()
    cutover, _ = _cutover(paths, services)

    with pytest.raises(BusinessRuntimeCutoverError, match="stop interruption"):
        cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    inspection = inspect_business_runtime_cutover(
        pending_path=paths.pending_marker,
        active_path=paths.active_marker,
        expected_uid=_identity()[0],
        expected_gid=_identity()[1],
    )
    assert inspection.mode is BusinessRuntimeCutoverMode.PREPARING

    result = cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert result["disposition"] == "ACTIVATED"
    assert len(services.stops) == 2


def test_mcu_maintenance_is_rejected_before_persisting_or_stopping(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    with sqlite3.connect(paths.legacy_database) as connection:
        connection.execute(
            "INSERT INTO maintenance_lock VALUES (1, 'MCU_FIRMWARE_UPDATE', 'mcu-1')"
        )
        connection.commit()
    cutover, services = _cutover(paths)

    with pytest.raises(
        BusinessRuntimeCutoverError,
        match="MCU maintenance operation is still active",
    ):
        cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert not paths.pending_marker.exists()
    assert services.stops == []


def test_active_plus_matching_pending_finalizes_interrupted_marker_commit(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    cutover, services = _cutover(paths)
    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)
    pending = {
        "schemaVersion": 1,
        "phase": "PREPARING",
        "operationUid": OPERATION_UID,
        "evidenceSha256": EVIDENCE_SHA256,
        "startedAt": "2026-09-04T12:00:00Z",
    }
    paths.pending_marker.write_text(
        json.dumps(pending, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    paths.pending_marker.chmod(0o600)

    result = cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert result["disposition"] == "FINALIZED_AFTER_INTERRUPTION"
    assert not paths.pending_marker.exists()
    assert len(services.stops) == 1


def test_cutover_rejects_a_preexisting_unowned_target_without_stopping(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    paths.business_database.write_bytes(b"unexpected")
    cutover, services = _cutover(paths)

    with pytest.raises(BusinessRuntimeCutoverError, match="already exists"):
        cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert not paths.pending_marker.exists()
    assert services.stops == []


def test_cutover_fails_closed_when_active_photo_escapes_legacy_root(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths, escaping_photo=True)
    cutover, _ = _cutover(paths)

    with pytest.raises(BusinessRuntimeCutoverError, match="escapes"):
        cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    assert paths.pending_marker.is_file()
    assert not paths.active_marker.exists()


def test_active_verification_allows_normal_database_changes_after_cutover(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    cutover, _ = _cutover(paths)
    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)
    with sqlite3.connect(paths.business_database) as connection:
        connection.execute(
            "INSERT INTO event_outbox VALUES ('event-2', '{\"state\":\"new\"}')"
        )
        connection.commit()

    assert cutover.verify_active()["runtimeDataVerified"] is True


def test_active_verification_accepts_a_later_business_schema(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    cutover, _ = _cutover(paths)
    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)
    with sqlite3.connect(paths.business_database) as connection:
        connection.execute("INSERT INTO schema_version VALUES (19)")
        connection.commit()

    assert cutover.verify_active()["runtimeDataVerified"] is True


def test_clean_wal_database_uses_immutable_read_only_verification(
    tmp_path: Path,
) -> None:
    database = tmp_path / "edge.db"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == (
            "wal",
        )
        connection.execute("CREATE TABLE facts(value TEXT NOT NULL)")
        connection.execute("INSERT INTO facts VALUES ('checkpointed')")
        connection.commit()
    finally:
        connection.close()

    # Windows may retain empty WAL bookkeeping files after a clean close;
    # model the Linux boot state observed on the device explicitly.
    Path(f"{database}-wal").unlink(missing_ok=True)
    Path(f"{database}-shm").unlink(missing_ok=True)
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    assert _sqlite_read_only_uri(
        database,
        immutable_when_clean=True,
    ).endswith("?mode=ro&immutable=1")


def test_recovery_sidecars_disable_immutable_verification(
    tmp_path: Path,
) -> None:
    database = tmp_path / "edge.db"
    writer = sqlite3.connect(database)
    reader: sqlite3.Connection | None = None
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE facts(value TEXT NOT NULL)")
        writer.execute("INSERT INTO facts VALUES ('committed-in-wal')")
        writer.commit()
        assert Path(f"{database}-wal").exists()
        assert Path(f"{database}-shm").exists()
        uri = _sqlite_read_only_uri(database, immutable_when_clean=True)
        assert uri.endswith("?mode=ro")
        assert "immutable" not in uri
        reader = sqlite3.connect(uri, uri=True)
        assert reader.execute("SELECT value FROM facts").fetchone() == (
            "committed-in-wal",
        )
    finally:
        if reader is not None:
            reader.close()
        writer.close()

    journal = Path(f"{database}-journal")
    journal.touch()
    try:
        uri = _sqlite_read_only_uri(database, immutable_when_clean=True)
        assert uri.endswith("?mode=ro")
        assert "immutable" not in uri
    finally:
        journal.unlink()


@pytest.mark.skipif(
    os.name != "posix" or os.getuid() != 0,
    reason="requires root to verify the distinct business service identity",
)
def test_cutover_assigns_every_managed_file_to_the_business_identity(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    business_uid = 1201
    business_gid = 1202
    for directory in (paths.business_data_root, paths.business_photos):
        os.chown(directory, business_uid, business_gid)
    services = RecordingServices()
    cutover = BusinessRuntimeCutover(
        paths=paths,
        business_uid=business_uid,
        business_gid=business_gid,
        privileged_uid=0,
        privileged_gid=0,
        services=services,
        utc_now=lambda: NOW,
        mutation_guard=lambda: nullcontext(),
    )

    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)

    target_photo = next(paths.business_photos.rglob("*.jpg"))
    for path in (
        paths.business_database,
        paths.business_boot_id,
        paths.business_configuration,
        target_photo,
    ):
        details = path.stat()
        assert (details.st_uid, details.st_gid) == (business_uid, business_gid)
        assert stat.S_IMODE(details.st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock")
def test_shared_maintenance_lock_excludes_a_parallel_cutover(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "maintenance/installer.lock"
    uid, gid = _identity()

    with _exclusive_creatable_lock(lock, uid=uid, gid=gid), pytest.raises(
        BusinessRuntimeCutoverError,
        match="maintenance is already running",
    ), _exclusive_creatable_lock(lock, uid=uid, gid=gid):
        pass

    details = lock.stat()
    assert stat.S_IMODE(details.st_mode) == 0o600
    assert (details.st_uid, details.st_gid) == (uid, gid)


def test_ambiguous_markers_are_invalid_to_the_runtime_selector(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _create_legacy_database(paths)
    cutover, _ = _cutover(paths)
    cutover.prepare(OPERATION_UID, EVIDENCE_SHA256)
    paths.pending_marker.write_text("{}\n", encoding="utf-8")
    paths.pending_marker.chmod(0o600)

    inspection = inspect_business_runtime_cutover(
        pending_path=paths.pending_marker,
        active_path=paths.active_marker,
        expected_uid=_identity()[0],
        expected_gid=_identity()[1],
    )

    assert inspection.mode is BusinessRuntimeCutoverMode.INVALID
    assert inspection.error_code == "BUSINESS_RUNTIME_CUTOVER_MARKERS_AMBIGUOUS"

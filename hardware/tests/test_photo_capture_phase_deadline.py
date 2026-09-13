"""A bounded optional-camera wait cannot relabel a later business phase."""
import hashlib
from pathlib import Path
import threading

import pytest

from edge_store import EdgeStore
from photo_manager import PhotoManager, CLEAN_OPEN_SLOTS, CLEAN_CLOSE_SLOTS, DELIVERY_OPEN_SLOTS


@pytest.fixture
def photos(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    manager = PhotoManager(store, str(tmp_path / "photos"), outside_camera_source=0,
        inside_camera_source=1, device_name="device-1", start_upload_worker=False)
    try:
        yield manager, store
    finally:
        manager.close()
        store.close()


@pytest.mark.parametrize("method,slots", [
    ("capture_open_photos_async", DELIVERY_OPEN_SLOTS),
    ("capture_clean_open_photos_async", CLEAN_OPEN_SLOTS),
    ("capture_clean_close_photos_async", CLEAN_CLOSE_SLOTS),
])
def test_expired_phase_is_durable_and_late_capture_cannot_revive_it(photos, monkeypatch, method, slots):
    manager, store = photos
    started, release = threading.Event(), threading.Event()

    def blocked_capture(path, source):
        started.set()
        assert release.wait(3)
        Path(path).write_bytes(b"\xff\xd8late-camera-frame")

    monkeypatch.setattr(manager, "_capture_camera_to_path", blocked_capture)
    assert getattr(manager, method)("work-1")
    assert started.wait(1)
    try:
        assert {row["slot_name"] for row in store.get_photos_by_work("work-1")} == set(slots)
        manager.expire_pending_captures("work-1", slots)
        before = store.get_photos_by_work("work-1")
        assert {row["state"] for row in before} == {"DEAD"}
        assert all(row["status_event_uid"] and not row["content_sha256"] for row in before)
        assert len(store.list_pending_events()) == 2
        manager.expire_pending_captures("work-1", slots)
        assert store.get_photos_by_work("work-1") == before
    finally:
        release.set()
        manager._capture_queue.join()
    assert store.get_photos_by_work("work-1") == before
    assert len(store.list_pending_events()) == 2
    assert not any(Path(row["local_path"]).exists() for row in before)


def test_capture_committed_during_expiry_check_wins_without_deleting_its_file(photos, monkeypatch):
    manager, store = photos
    # Stop only the test worker so we can place a real SQLite capture commit
    # between the manager's pending read and the store's atomic state check.
    manager.close()
    reserved = manager._reserve_capture_slots("work-1", "DELIVERY_SESSION", DELIVERY_OPEN_SLOTS)
    original = store.record_photo_status

    def capture_then_expire(photo_uid, **kwargs):
        row = store.get_photo(photo_uid)
        path = Path(row["local_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = b"\xff\xd8already-captured"
        path.write_bytes(frame)
        assert store.mark_photo_captured(photo_uid, content_sha256=hashlib.sha256(frame).hexdigest(),
            size_bytes=len(frame), captured_at="2026-09-13T00:00:00.000Z", captured_clock_quality="SYNCED")
        return original(photo_uid, **kwargs)

    monkeypatch.setattr(store, "record_photo_status", capture_then_expire)
    manager.expire_pending_captures("work-1", DELIVERY_OPEN_SLOTS)
    saved = store.get_photos_by_work("work-1")
    assert {row["photo_uid"] for row in saved} == {row["photo_uid"] for row in reserved}
    assert {row["state"] for row in saved} == {"PENDING"}
    assert all(Path(row["local_path"]).exists() and not row["status_event_uid"] for row in saved)
    assert store.list_pending_events() == []


def test_failed_missing_fact_commit_propagates_as_storage_failure(photos, monkeypatch):
    manager, store = photos
    manager.close()
    manager._reserve_capture_slots("work-1", "DELIVERY_SESSION", DELIVERY_OPEN_SLOTS)
    def failed_commit(*args, **kwargs):
        raise OSError("injected database storage failure")
    monkeypatch.setattr(store, "record_photo_status", failed_commit)
    with pytest.raises(OSError, match="database storage failure"):
        manager.expire_pending_captures("work-1", DELIVERY_OPEN_SLOTS)
    assert {row["state"] for row in store.get_photos_by_work("work-1")} == {"CAPTURE_PENDING"}

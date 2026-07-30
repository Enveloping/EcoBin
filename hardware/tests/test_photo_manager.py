import builtins
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from edge_store import EdgeStore
from photo_manager import PhotoManager


def test_async_capture_does_not_block_workflow_thread(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        outside_camera_source=(
            "/dev/v4l/by-id/"
            "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
        ),
        inside_camera_source=(
            "/dev/v4l/by-id/"
            "usb-icSpring_icspring_camera-video-index0"
        ),
        deployment_code="Dp_demo_01",
        trusted_cos_environment={
            "baseUrl": (
                "https://ecobin-contract-1250000000.cos."
                "ap-guangzhou.myqcloud.com"
            ),
        },
    )
    capture_started = threading.Event()
    release_capture = threading.Event()
    camera_indices = []

    def slow_capture(path, camera_index):
        camera_indices.append(camera_index)
        capture_started.set()
        assert release_capture.wait(2)
        with open(path, "wb") as output:
            output.write(b"\xff\xd8\xfftest-photo")

    photos._capture_camera_to_path = slow_capture
    started_at = time.monotonic()

    assert photos.capture_open_photos_async("session-1")

    assert time.monotonic() - started_at < 0.2
    assert capture_started.wait(1)
    try:
        reserved = store.get_photos_by_work("session-1")
        assert {row["slot_name"] for row in reserved} == {
            "BEFORE_OUTER",
            "BEFORE_INNER",
        }
        assert {row["state"] for row in reserved} == {
            "CAPTURE_PENDING"
        }
        completion = photos.get_completion_photo_facts(
            "session-1",
            "DELIVERY_SESSION",
        )
        assert len(completion) == 4
        assert all(photo["status"] == "UPLOAD_PENDING" for photo in completion)
        assert all(photo["url"] is None for photo in completion)
        assert all(photo["photoUid"] is None for photo in completion)
    finally:
        release_capture.set()
    deadline = time.monotonic() + 2
    while (
        any(
            row["state"] == "CAPTURE_PENDING"
            for row in store.get_photos_by_work("session-1")
        )
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    registered = store.get_photos_by_work("session-1")
    assert {row["slot_name"] for row in registered} == {
        "BEFORE_OUTER",
        "BEFORE_INNER",
    }
    assert {row["state"] for row in registered} == {"PENDING"}
    assert camera_indices == [
        (
            "/dev/v4l/by-id/"
            "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
        ),
        "/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0",
    ]
    photos.close()
    store.close()


def test_capture_uses_explicit_v4l2_source_and_last_warmup_frame(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    source = (
        "/dev/v4l/by-id/"
        "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
    )
    opened_with = []
    written_frames = []

    class FakeCapture:
        def __init__(self, *args):
            opened_with.append(args)
            self.read_count = 0
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            self.read_count += 1
            return True, f"frame-{self.read_count}"

        def release(self):
            self.released = True

    captures = []

    def video_capture(*args):
        capture = FakeCapture(*args)
        captures.append(capture)
        return capture

    fake_cv2 = SimpleNamespace(
        CAP_V4L2=200,
        VideoCapture=video_capture,
        imwrite=lambda path, frame: written_frames.append(frame) or True,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        outside_camera_source=source,
        inside_camera_source=(
            "/dev/v4l/by-id/"
            "usb-icSpring_icspring_camera-video-index0"
        ),
        camera_warmup_frames=3,
        start_upload_worker=False,
    )
    try:
        photos._capture_camera_to_path(
            str(tmp_path / "outside.jpg"),
            source,
        )
    finally:
        photos.close()
        store.close()

    assert opened_with == [(source, fake_cv2.CAP_V4L2)]
    assert captures[0].read_count == 3
    assert captures[0].released
    assert written_frames == ["frame-3"]


def test_clean_capture_phases_use_first_open_then_final_close_slots(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        deployment_code="Dp_demo_01",
        start_upload_worker=False,
        simulate_camera=True,
    )

    photos.capture_clean_open_photos("operation-1")
    first_rows = store.get_photos_by_work("operation-1")
    assert {row["slot_name"] for row in first_rows} == {
        "FIRST_OPEN_OUTER",
        "FIRST_OPEN_INNER",
    }

    photos.capture_clean_close_photos("operation-1")
    all_rows = store.get_photos_by_work("operation-1")
    assert {row["slot_name"] for row in all_rows} == {
        "FIRST_OPEN_OUTER",
        "FIRST_OPEN_INNER",
        "FINAL_CLOSE_OUTER",
        "FINAL_CLOSE_INNER",
    }
    assert {row["state"] for row in all_rows} == {"PENDING"}

    photos.close()
    store.close()


def test_missing_opencv_is_not_replaced_with_a_fake_photo(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        start_upload_worker=False,
    )
    real_import = builtins.__import__

    def missing_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("cv2 unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_cv2)
    target = tmp_path / "not-a-photo.jpg"
    try:
        with pytest.raises(RuntimeError, match="OPENCV_NOT_INSTALLED"):
            photos._capture_camera_to_path(str(target), 1)
        assert not target.exists()
    finally:
        photos.close()
        store.close()


def test_capture_failure_is_persisted_as_permanently_missing(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        deployment_code="Dp_demo_01",
        start_upload_worker=False,
    )

    def failed_capture(path, camera_index):
        raise RuntimeError("CAMERA_UNAVAILABLE")

    photos._capture_camera_to_path = failed_capture
    assert photos.capture_open_photos_async("session-1")

    deadline = time.monotonic() + 2
    rows = []
    while time.monotonic() < deadline:
        rows = store.get_photos_by_work("session-1")
        if len(rows) == 2 and all(
            row["state"] == "DEAD" for row in rows
        ):
            break
        time.sleep(0.01)

    assert len(rows) == 2
    assert {row["state"] for row in rows} == {"DEAD"}
    events = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='PHOTO_STATUS_REPORTED'"""
    ).fetchall()
    assert len(events) == 2
    for event in events:
        photo = json.loads(event["payload_json"])["payload"]["photo"]
        assert photo["status"] == "PERMANENTLY_MISSING"
        assert photo["photoUid"] is None
        assert photo["url"] is None
        assert photo["sha256"] is None
        assert photo["sizeBytes"] is None
        assert photo["capturedAt"] is None
        assert photo["missingReason"] == "CAMERA_UNAVAILABLE"

    photos.close()
    store.close()


def test_restart_recovers_durable_capture_reservations(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    ready_path = tmp_path / "ready.jpg"
    ready_path.write_bytes(b"\xff\xd8\xffready")
    captures = [
        {
            "photo_uid": "photo-ready",
            "slot_name": "BEFORE_OUTER",
            "local_path": str(ready_path),
            "work_uid": "session-ready",
            "work_type": "DELIVERY_SESSION",
            "deployment_code": "Dp_demo_01",
        },
        {
            "photo_uid": "photo-missing",
            "slot_name": "BEFORE_INNER",
            "local_path": str(tmp_path / "missing.jpg"),
            "work_uid": "session-missing",
            "work_type": "DELIVERY_SESSION",
            "deployment_code": "Dp_demo_01",
        },
    ]
    store.reserve_photo_captures(captures)

    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        deployment_code="Dp_demo_01",
        start_upload_worker=False,
    )

    ready = store.get_photos_by_work("session-ready")[0]
    missing = store.get_photos_by_work("session-missing")[0]
    assert ready["state"] == "PENDING"
    assert ready["content_sha256"]
    assert ready["size_bytes"] == ready_path.stat().st_size
    assert missing["state"] == "DEAD"
    assert missing["last_error"] == "EDGE_RESTARTED_BEFORE_CAPTURE"
    photos.close()
    store.close()


def test_duplicate_capture_trigger_does_not_recapture_same_slots(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(
        store,
        str(tmp_path / "photos"),
        deployment_code="Dp_demo_01",
        start_upload_worker=False,
    )
    first_capture_started = threading.Event()
    release_first_capture = threading.Event()
    capture_count = 0

    def controlled_capture(path, camera_index):
        nonlocal capture_count
        capture_count += 1
        if capture_count == 1:
            first_capture_started.set()
            assert release_first_capture.wait(2)
        with open(path, "wb") as output:
            output.write(b"\xff\xd8\xffphoto")

    photos._capture_camera_to_path = controlled_capture
    try:
        assert photos.capture_open_photos_async("session-1")
        assert first_capture_started.wait(1)
        assert photos.capture_open_photos_async("session-1")
        release_first_capture.set()
        photos._capture_queue.join()

        rows = store.get_photos_by_work("session-1")
        assert capture_count == 2
        assert {row["state"] for row in rows} == {"PENDING"}
    finally:
        release_first_capture.set()
        photos.close()
        store.close()

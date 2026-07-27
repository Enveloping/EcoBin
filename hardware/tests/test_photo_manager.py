import threading
import time

from edge_store import EdgeStore
from photo_manager import PhotoManager


def test_async_capture_does_not_block_workflow_thread(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    photos = PhotoManager(store, str(tmp_path / "photos"))
    capture_started = threading.Event()
    release_capture = threading.Event()

    def slow_capture(_path):
        capture_started.set()
        assert release_capture.wait(2)

    photos._capture_camera_to_path = slow_capture
    started_at = time.monotonic()

    assert photos.capture_open_photos_async("session-1")

    assert time.monotonic() - started_at < 0.2
    assert capture_started.wait(1)
    release_capture.set()
    deadline = time.monotonic() + 2
    while (
        len(store.get_photos_by_work("session-1")) < 2
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    registered = store.get_photos_by_work("session-1")
    assert {row["slot_name"] for row in registered} == {
        "OPEN_OUTSIDE",
        "OPEN_INSIDE",
    }
    store.close()

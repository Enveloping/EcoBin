"""photo_manager.py -- photo queue and COS upload manager."""
from __future__ import annotations
import logging
import os
import queue
import threading
import uuid as _uuid
from edge_store import EdgeStore
logger = logging.getLogger("photo-manager")
SLOTS = ["OPEN_OUTSIDE", "OPEN_INSIDE", "CLOSE_OUTSIDE", "CLOSE_INSIDE"]
PHOTO_QUEUE_CAPACITY = 32


class PhotoManager:

    def __init__(self, store: EdgeStore, photo_dir=None):
        self._store = store
        if photo_dir is None:
            import os as _os
            photo_dir = _os.path.join(_os.path.dirname(__file__), 'data', 'photos')
        self._photo_dir = photo_dir
        self._urls = {}
        self._urls_lock = threading.Lock()
        self._capture_queue = queue.Queue(maxsize=PHOTO_QUEUE_CAPACITY)
        threading.Thread(
            target=self._capture_worker,
            daemon=True,
            name="photo-capture",
        ).start()

    def capture_open_photos(self, work_uid):
        return self._capture_slots(work_uid, ["OPEN_OUTSIDE", "OPEN_INSIDE"])

    def capture_close_photos(self, work_uid):
        return self._capture_slots(work_uid, ["CLOSE_OUTSIDE", "CLOSE_INSIDE"])

    def capture_clean_photos(self, work_uid):
        return self._capture_slots(work_uid, SLOTS)

    def capture_open_photos_async(self, work_uid):
        return self._enqueue_capture(
            work_uid,
            ("OPEN_OUTSIDE", "OPEN_INSIDE"),
        )

    def capture_close_photos_async(self, work_uid):
        return self._enqueue_capture(
            work_uid,
            ("CLOSE_OUTSIDE", "CLOSE_INSIDE"),
        )

    def capture_clean_photos_async(self, work_uid):
        return self._enqueue_capture(work_uid, tuple(SLOTS))

    def _enqueue_capture(self, work_uid, slot_names):
        try:
            self._capture_queue.put_nowait((work_uid, slot_names))
            return True
        except queue.Full:
            logger.warning(
                "photo capture queue full; dropping request: work=%s slots=%s",
                work_uid,
                ",".join(slot_names),
            )
            return False

    def _capture_worker(self):
        while True:
            work_uid, slot_names = self._capture_queue.get()
            try:
                self._capture_slots(work_uid, slot_names)
            except Exception:
                logger.exception(
                    "unexpected asynchronous photo failure: work=%s",
                    work_uid,
                )
            finally:
                self._capture_queue.task_done()

    def _capture_slots(self, work_uid, slot_names):
        results = {}
        os.makedirs(self._photo_dir, exist_ok=True)
        for slot in slot_names:
            photo_uid = str(_uuid.uuid4())
            filename = f"{work_uid}_{slot}_{photo_uid[:8]}.jpg"
            local_path = os.path.join(self._photo_dir, filename)
            try:
                self._capture_camera_to_path(local_path)
                self._store.register_photo(photo_uid, slot, local_path, None, work_uid)
                results[slot] = {"photo_uid": photo_uid, "local_path": local_path, "status": "OK"}
            except Exception as e:
                logger.warning("photo capture failed: %s %s", slot, e)
                results[slot] = {"photo_uid": photo_uid, "local_path": None, "status": "FAILED"}
        with self._urls_lock:
            self._urls.pop(work_uid, None)
        return results

    def _capture_camera_to_path(self, path):
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(path, frame)
                return
            raise RuntimeError("cv2 capture returned null frame")
        except ImportError:
            dummy = bytearray(1024)
            dummy[0:3] = b'\xff\xd8\xff'
            with open(path, 'wb') as f:
                f.write(dummy)

    def get_slot_urls(self, work_uid):
        with self._urls_lock:
            if work_uid in self._urls:
                return dict(self._urls[work_uid])
        photos = self._store.get_photos_by_work(work_uid)
        urls = {}
        for p in photos:
            urls[p["slot_name"]] = p.get("cos_key") or p.get("local_path", "")
        with self._urls_lock:
            self._urls[work_uid] = dict(urls)
        return urls

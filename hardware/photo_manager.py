"""Persistent photo capture, temporary-grant upload and status reporting."""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
import uuid as _uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from camera_selection import CURRENT_INSIDE_CAMERA, CURRENT_OUTSIDE_CAMERA
from camera_capture import (
    CAMERA_CAPTURE_FAILED,
    CAMERA_OPEN_FAILED,
    CAMERA_WRITE_FAILED,
    OPENCV_NOT_INSTALLED,
    CameraCaptureError,
    capture_v4l2_jpeg,
    run_camera_captures,
)
from edge_store import EdgeStore
from onenet_wire import (
    WORK_PHOTO_SLOTS,
    WORK_TYPE_PATH,
    validate_command_envelope,
    validate_cos_grant,
)
from trusted_clock import local_deadline_reference, sample_clock
from simulated_camera import (
    capture_simulated_camera,
    is_simulated_camera_source,
)

logger = logging.getLogger("photo-manager")

PHOTO_QUEUE_CAPACITY = 32
MAXIMUM_PHOTO_BYTES = 20 * 1024 * 1024
DELIVERY_OPEN_SLOTS = ("BEFORE_OUTER", "BEFORE_INNER")
DELIVERY_CLOSE_SLOTS = ("AFTER_OUTER", "AFTER_INNER")
CLEAN_OPEN_SLOTS = (
    "FIRST_OPEN_OUTER",
    "FIRST_OPEN_INNER",
)
CLEAN_CLOSE_SLOTS = (
    "FINAL_CLOSE_OUTER",
    "FINAL_CLOSE_INNER",
)
CLEAN_SLOTS = CLEAN_OPEN_SLOTS + CLEAN_CLOSE_SLOTS
PHOTO_MISSING_REASONS = frozenset({
    "CAMERA_UNAVAILABLE",
    "PHOTO_CAPTURE_FAILED",
    "EDGE_RESTARTED_BEFORE_CAPTURE",
    "CAPTURE_FILE_INVALID_AFTER_RESTART",
    "CV2_IMAGE_WRITE_FAILED",
    "CV2_CAPTURE_NULL_FRAME",
    "PHOTO_FILE_MISSING",
    "PHOTO_UPLOAD_EXPIRED",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PhotoManager:

    def __init__(
        self,
        store: EdgeStore,
        photo_dir=None,
        outside_camera_source=CURRENT_OUTSIDE_CAMERA,
        inside_camera_source=CURRENT_INSIDE_CAMERA,
        *,
        device_name="",
        uploader=None,
        upload_poll_seconds=1.0,
        grant_expiry_skew_seconds=30,
        retention_hours=72,
        start_upload_worker=True,
        trusted_cos_environment=None,
    ):
        self._store = store
        if photo_dir is None:
            photo_dir = os.path.join(
                os.path.dirname(__file__),
                "data",
                "photos",
            )
        self._photo_dir = photo_dir
        if self._camera_identity(
            outside_camera_source
        ) == self._camera_identity(inside_camera_source):
            raise ValueError(
                "outside and inside cameras must be different"
            )
        self._outside_camera_source = outside_camera_source
        self._inside_camera_source = inside_camera_source
        self._device_name = device_name
        self._uploader = uploader
        self._upload_poll_seconds = upload_poll_seconds
        self._grant_expiry_skew_seconds = grant_expiry_skew_seconds
        self._retention = timedelta(hours=retention_hours)
        self._trusted_cos_environment = trusted_cos_environment
        self._capture_lock = threading.Lock()
        self._grant_lock = threading.Lock()
        self._grants: dict[tuple[str, str], dict[str, Any]] = {}
        self._stop = threading.Event()
        self._upload_wake = threading.Event()
        self._capture_queue = queue.Queue(maxsize=PHOTO_QUEUE_CAPACITY)
        self._capture_thread = threading.Thread(
            target=self._capture_worker,
            daemon=True,
            name="photo-capture",
        )
        self._upload_thread = None
        self._recover_interrupted_captures()
        self._capture_thread.start()
        if (
            start_upload_worker
            and uploader is not None
            and device_name
        ):
            recovered = self._store.recover_photo_upload_queue()
            if recovered:
                logger.info(
                    "recovered %d pending photos without retaining STS grants",
                    recovered,
                )
            self._upload_thread = threading.Thread(
                target=self._upload_worker,
                daemon=True,
                name="photo-upload",
            )
            self._upload_thread.start()

    def close(self) -> None:
        self._stop.set()
        self._upload_wake.set()
        if self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2)
        if (
            self._upload_thread is not None
            and self._upload_thread.is_alive()
        ):
            self._upload_thread.join(timeout=2)

    def capture_open_photos(self, work_uid):
        return self._capture_slots(
            work_uid,
            "DELIVERY_SESSION",
            DELIVERY_OPEN_SLOTS,
        )

    def capture_close_photos(self, work_uid):
        return self._capture_slots(
            work_uid,
            "DELIVERY_SESSION",
            DELIVERY_CLOSE_SLOTS,
        )

    def capture_clean_photos(self, work_uid):
        return self._capture_slots(
            work_uid,
            "CLEAN_OPERATION",
            CLEAN_SLOTS,
        )

    def capture_clean_open_photos(self, work_uid):
        return self._capture_slots(
            work_uid,
            "CLEAN_OPERATION",
            CLEAN_OPEN_SLOTS,
        )

    def capture_clean_close_photos(self, work_uid):
        return self._capture_slots(
            work_uid,
            "CLEAN_OPERATION",
            CLEAN_CLOSE_SLOTS,
        )

    def capture_open_photos_async(self, work_uid):
        return self._enqueue_capture(
            work_uid,
            "DELIVERY_SESSION",
            DELIVERY_OPEN_SLOTS,
        )

    def capture_close_photos_async(self, work_uid):
        return self._enqueue_capture(
            work_uid,
            "DELIVERY_SESSION",
            DELIVERY_CLOSE_SLOTS,
        )

    def capture_clean_photos_async(self, work_uid):
        return self._enqueue_capture(
            work_uid,
            "CLEAN_OPERATION",
            CLEAN_SLOTS,
        )

    def capture_acceptance_probe(self, challenge_uid: str) -> dict[str, Any]:
        """Capture one fresh image from each configured physical camera.

        Acceptance probes deliberately bypass ``photo_outbox``: they are not
        business photos and must not be retried later with an expired grant.
        The caller owns the returned files and must remove them after COS
        readback.  Both cameras are attempted so a single failure still gives
        the platform precise evidence instead of hiding the second result.
        """
        challenge_uid = str(_uuid.UUID(challenge_uid))
        sources = (
            ("OUTSIDE", self._outside_camera_source),
            ("INSIDE", self._inside_camera_source),
        )
        probe_dir = os.path.join(
            self._photo_dir,
            "device-acceptance",
            challenge_uid,
        )
        os.makedirs(probe_dir, exist_ok=True)
        with self._capture_lock:
            entries = {}
            tasks = {}
            for camera_name, source in sources:
                photo_uid = str(_uuid.uuid4())
                path = os.path.join(
                    probe_dir,
                    camera_name.lower(),
                    f"{photo_uid}.jpg",
                )
                os.makedirs(os.path.dirname(path), exist_ok=True)
                temporary_path = self._temporary_path(path)
                entries[camera_name] = {
                    "camera": camera_name,
                    "simulated": is_simulated_camera_source(source),
                    "path": None,
                    "contentSha256": None,
                    "sizeBytes": 0,
                    "error": None,
                    "finalPath": path,
                    "temporaryPath": temporary_path,
                }
                tasks[camera_name] = (
                    lambda capture_path=temporary_path, camera_source=source:
                    self._capture_camera_to_path(
                        capture_path,
                        camera_source,
                    )
                )

            outcomes = run_camera_captures(tasks)
            results: list[dict[str, Any]] = []
            for camera_name, _source in sources:
                entry = entries[camera_name]
                path = entry.pop("finalPath")
                temporary_path = entry.pop("temporaryPath")
                capture_error = outcomes[camera_name].error
                try:
                    if capture_error is not None:
                        raise capture_error
                    with open(temporary_path, "rb+") as captured:
                        os.fsync(captured.fileno())
                    os.replace(temporary_path, path)
                    self._sync_directory(os.path.dirname(path))
                    size_bytes = os.path.getsize(path)
                    if not 1 <= size_bytes <= MAXIMUM_PHOTO_BYTES:
                        raise RuntimeError("captured photo size is invalid")
                    entry.update({
                        "path": path,
                        "contentSha256": _file_sha256(path),
                        "sizeBytes": size_bytes,
                    })
                except Exception as error:
                    entry["error"] = self._capture_error_code(error)
                    for candidate in (temporary_path, path):
                        try:
                            if os.path.exists(candidate):
                                os.remove(candidate)
                        except OSError:
                            pass
                results.append(entry)
        return {
            "challengeUid": challenge_uid,
            "camerasSimulated": any(
                item["simulated"] for item in results
            ),
            "captures": results,
        }

    def _enqueue_capture(self, work_uid, work_type, slot_names):
        try:
            captures = self._reserve_capture_slots(
                work_uid,
                work_type,
                slot_names,
            )
        except Exception:
            logger.exception(
                "photo capture reservation failed: work=%s",
                work_uid,
            )
            return False
        pending = [
            capture
            for capture in captures
            if (
                capture["state"] == "CAPTURE_PENDING"
                and not capture["tombstoned"]
            )
        ]
        if not pending:
            return True
        try:
            self._capture_queue.put_nowait(pending)
            return True
        except queue.Full:
            logger.warning(
                "photo capture queue full; request remains durable: "
                "work=%s slots=%s",
                work_uid,
                ",".join(slot_names),
            )
            return True

    def _capture_worker(self):
        while not self._stop.is_set():
            try:
                captures = self._capture_queue.get(timeout=0.5)
            except queue.Empty:
                captures = self._store.list_capture_pending_photos(
                    limit=PHOTO_QUEUE_CAPACITY,
                )
                if captures:
                    self._capture_reserved_photos(captures)
                continue
            try:
                self._capture_reserved_photos(captures)
            except Exception:
                logger.exception(
                    "unexpected asynchronous photo failure"
                )
            finally:
                self._capture_queue.task_done()

    def _capture_slots(self, work_uid, work_type, slot_names):
        captures = self._reserve_capture_slots(
            work_uid,
            work_type,
            slot_names,
        )
        return self._capture_reserved_photos(captures)

    def _reserve_capture_slots(
        self,
        work_uid,
        work_type,
        slot_names,
    ):
        work_path = WORK_TYPE_PATH[work_type]
        captures = []
        for slot in slot_names:
            photo_uid = str(_uuid.uuid4())
            object_key = (
                f"ecobin/{work_path}/"
                f"{work_uid}/{slot}/{photo_uid}.jpg"
            )
            local_path = os.path.join(
                self._photo_dir,
                work_path,
                work_uid,
                slot,
                f"{photo_uid}.jpg",
            )
            captures.append(
                {
                    "photo_uid": photo_uid,
                    "slot_name": slot,
                    "local_path": local_path,
                    "work_uid": work_uid,
                    "work_type": work_type,
                    "device_name": self._device_name,
                    "cos_key": object_key,
                }
            )
        return self._store.reserve_photo_captures(captures)

    def _capture_reserved_photos(self, captures):
        with self._capture_lock:
            return self._capture_reserved_photos_serial(captures)

    def _capture_reserved_photos_serial(self, captures):
        results = {}
        groups = defaultdict(list)
        for reserved in captures:
            photo = self._store.get_photo(reserved["photo_uid"])
            if photo is None:
                continue
            slot = photo["slot_name"]
            photo_uid = photo["photo_uid"]
            if (
                photo["state"] != "CAPTURE_PENDING"
                or photo["tombstoned"]
            ):
                results[slot] = {
                    "photo_uid": photo_uid,
                    "local_path": photo["local_path"],
                    "status": photo["state"],
                }
                continue
            groups[
                (
                    photo["work_type"],
                    photo["work_uid"],
                    self._capture_phase(slot),
                )
            ].append(photo)

        for group in groups.values():
            results.update(self._capture_reserved_group(group))
        return results

    def _capture_reserved_group(self, photos):
        tasks = {}
        paths = {}
        capture_errors = {}
        for photo in photos:
            slot = photo["slot_name"]
            photo_uid = photo["photo_uid"]
            local_path = photo["local_path"]
            directory = os.path.dirname(local_path)
            temporary_path = self._temporary_path(local_path)
            try:
                os.makedirs(directory, exist_ok=True)
                source = self._camera_source_for_slot(slot)
                tasks[photo_uid] = (
                    lambda path=temporary_path, camera_source=source:
                    self._capture_camera_to_path(path, camera_source)
                )
            except Exception as error:
                capture_errors[photo_uid] = error
            paths[photo_uid] = (local_path, temporary_path, directory)

        outcomes = run_camera_captures(tasks)
        for photo_uid, outcome in outcomes.items():
            if outcome.error is not None:
                capture_errors[photo_uid] = outcome.error

        results = {}
        for photo in photos:
            slot = photo["slot_name"]
            photo_uid = photo["photo_uid"]
            local_path, temporary_path, directory = paths[photo_uid]
            error = capture_errors.get(photo_uid)
            if error is None:
                try:
                    with open(temporary_path, "rb+") as captured:
                        os.fsync(captured.fileno())
                    os.replace(temporary_path, local_path)
                    self._sync_directory(directory)
                    size_bytes = os.path.getsize(local_path)
                    if not 1 <= size_bytes <= MAXIMUM_PHOTO_BYTES:
                        raise RuntimeError("captured photo size is invalid")
                    content_sha256 = _file_sha256(local_path)
                    clock_sample = sample_clock()
                    captured_at = clock_sample.raw_observed_at or _utc_now()
                    captured = self._store.mark_photo_captured(
                        photo_uid,
                        content_sha256=content_sha256,
                        size_bytes=size_bytes,
                        captured_at=captured_at,
                        captured_clock_quality=clock_sample.quality,
                    )
                    if not captured:
                        raise RuntimeError("PHOTO_CAPTURE_STATE_CONFLICT")
                    results[slot] = {
                        "photo_uid": photo_uid,
                        "local_path": local_path,
                        "status": "OK",
                    }
                    self._upload_wake.set()
                    continue
                except Exception as persistence_error:
                    error = persistence_error

            for path in (temporary_path, local_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
            reason = self._capture_error_code(error)
            try:
                self._report_permanently_missing(
                    photo,
                    reason,
                )
            except Exception:
                logger.exception(
                    "photo capture failure persistence failed: "
                    "photo=%s",
                    photo_uid,
                )
            logger.warning(
                "photo capture failed: %s %s",
                slot,
                reason,
            )
            results[slot] = {
                "photo_uid": photo_uid,
                "local_path": None,
                "status": "FAILED",
            }
        return results

    def _recover_interrupted_captures(self) -> None:
        for photo in self._store.list_capture_pending_photos(limit=1000):
            local_path = photo["local_path"]
            if os.path.isfile(local_path):
                try:
                    size_bytes = os.path.getsize(local_path)
                    if not 1 <= size_bytes <= MAXIMUM_PHOTO_BYTES:
                        raise RuntimeError(
                            "CAPTURE_FILE_INVALID_AFTER_RESTART"
                        )
                    captured_at = datetime.fromtimestamp(
                        os.path.getmtime(local_path),
                        timezone.utc,
                    ).isoformat(timespec="milliseconds").replace(
                        "+00:00",
                        "Z",
                    )
                    if not self._store.mark_photo_captured(
                        photo["photo_uid"],
                        content_sha256=_file_sha256(local_path),
                        size_bytes=size_bytes,
                        captured_at=captured_at,
                        captured_clock_quality="ESTIMATED",
                    ):
                        raise RuntimeError(
                            "PHOTO_CAPTURE_STATE_CONFLICT"
                        )
                    self._upload_wake.set()
                    continue
                except Exception as error:
                    reason = self._capture_error_code(error)
            else:
                reason = "EDGE_RESTARTED_BEFORE_CAPTURE"
            try:
                os.remove(self._temporary_path(local_path))
            except FileNotFoundError:
                pass
            self._report_permanently_missing(photo, reason)

    @staticmethod
    def _temporary_path(local_path: str) -> str:
        directory, filename = os.path.split(local_path)
        stem, extension = os.path.splitext(filename)
        return os.path.join(
            directory,
            f".{stem}.tmp{extension}",
        )

    @staticmethod
    def _capture_error_code(error: Exception) -> str:
        message = str(error).strip()
        if message in PHOTO_MISSING_REASONS:
            return message
        return "PHOTO_CAPTURE_FAILED"

    @staticmethod
    def _sync_directory(directory: str) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _camera_identity(camera_source):
        if isinstance(camera_source, str):
            if is_simulated_camera_source(camera_source):
                return camera_source
            return os.path.realpath(camera_source)
        return camera_source

    def _camera_source_for_slot(self, slot):
        if slot.endswith("_OUTER"):
            return self._outside_camera_source
        if slot.endswith("_INNER"):
            return self._inside_camera_source
        raise ValueError(f"unknown photo slot: {slot}")

    @staticmethod
    def _capture_phase(slot):
        for suffix in ("_OUTER", "_INNER"):
            if slot.endswith(suffix):
                return slot[:-len(suffix)]
        raise ValueError(f"unknown photo slot: {slot}")

    def _capture_camera_to_path(self, path, camera_source):
        if is_simulated_camera_source(camera_source):
            capture_simulated_camera(path, camera_source)
            logger.info(
                "simulated camera captured: source=%s path=%s",
                camera_source,
                path,
            )
            return
        try:
            capture_v4l2_jpeg(camera_source, path)
        except CameraCaptureError as error:
            code = {
                OPENCV_NOT_INSTALLED: "OPENCV_NOT_INSTALLED",
                CAMERA_OPEN_FAILED: "CV2_CAMERA_OPEN_FAILED",
                CAMERA_CAPTURE_FAILED: "CV2_CAPTURE_NULL_FRAME",
                CAMERA_WRITE_FAILED: "CV2_IMAGE_WRITE_FAILED",
            }.get(error.code, "PHOTO_CAPTURE_FAILED")
            raise RuntimeError(code) from error

    def offer_initial_grant(
        self,
        work_type: str,
        work_uid: str,
        grant: dict[str, Any] | None,
    ) -> None:
        if grant is None:
            return
        validate_cos_grant(
            grant,
            device_name=self._device_name,
            work_type=work_type,
            work_uid=work_uid,
            trusted_environment=self._trusted_cos_environment,
        )
        self._remember_grant(
            work_type,
            work_uid,
            grant,
            WORK_PHOTO_SLOTS[work_type],
        )

    def offer_upload_grant(self, command: dict[str, Any]) -> dict[str, Any]:
        validate_command_envelope(
            command,
            trusted_environment=self._trusted_cos_environment,
        )
        payload = command["payload"]
        request_uid = payload["grantRequestEventUid"]
        photos = self._store.get_photos_by_work(payload["workUid"])
        if not any(
            photo.get("work_type") == payload["workType"]
            and photo.get("grant_request_event_uid") == request_uid
            and photo["state"] in ("PENDING", "UPLOADING")
            for photo in photos
        ):
            raise ValueError("photo grant request is not pending")
        self._remember_grant(
            payload["workType"],
            payload["workUid"],
            command["cosGrant"],
            tuple(payload["authorizedSlots"]),
        )
        return {
            "grantUid": command["cosGrant"]["grantUid"],
            "grantRequestEventUid": request_uid,
            "workType": payload["workType"],
            "workUid": payload["workUid"],
            "authorizedSlotCount": len(payload["authorizedSlots"]),
        }

    def _remember_grant(
        self,
        work_type: str,
        work_uid: str,
        grant: dict[str, Any],
        authorized_slots: tuple[str, ...],
    ) -> None:
        with self._grant_lock:
            self._grants[(work_type, work_uid)] = {
                "grant": {
                    **grant,
                    "sessionTokenParts": list(
                        grant["sessionTokenParts"]
                    ),
                },
                "authorized_slots": frozenset(authorized_slots),
            }
        self._upload_wake.set()

    def _upload_worker(self) -> None:
        while not self._stop.is_set():
            self._upload_wake.wait(self._upload_poll_seconds)
            self._upload_wake.clear()
            if self._stop.is_set():
                break
            try:
                self.process_uploads_once()
            except Exception:
                logger.exception("unexpected photo upload worker failure")

    def process_uploads_once(self) -> bool:
        if self._uploader is None or not self._device_name:
            return False
        pending = self._store.list_pending_photos(limit=100)
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for photo in pending:
            if photo.get("work_type") and photo.get("work_uid"):
                grouped[
                    (photo["work_type"], photo["work_uid"])
                ].append(photo)
        progressed = False
        for identity, photos in grouped.items():
            if self._process_work_photos(identity, photos):
                progressed = True
        self._cleanup_confirmed_photos()
        return progressed

    def _process_work_photos(
        self,
        identity: tuple[str, str],
        photos: list[dict],
    ) -> bool:
        work_type, work_uid = identity
        progressed = False
        active_photos = []
        for photo in photos:
            if self._photo_expired(photo):
                self._report_permanently_missing(
                    photo,
                    "PHOTO_UPLOAD_EXPIRED",
                )
                progressed = True
            else:
                active_photos.append(photo)
        if not active_photos:
            return progressed
        photos = active_photos
        with self._grant_lock:
            grant_state = self._grants.get(identity)
        if grant_state is None:
            reason = self._grant_request_reason(photos)
            self._ensure_grant_request(photos, reason)
            return True
        grant = grant_state["grant"]
        expires_at = _parse_utc(grant["expiresAt"])
        deadline_reference = local_deadline_reference()
        if (
            deadline_reference is not None
            and expires_at
            <= deadline_reference + timedelta(
                seconds=self._grant_expiry_skew_seconds
            )
        ):
            with self._grant_lock:
                self._grants.pop(identity, None)
            self._store.invalidate_photo_grant(
                work_type,
                work_uid,
                "GRANT_EXPIRED",
            )
            refreshed = self._store.list_pending_photos(limit=100)
            self._ensure_grant_request(
                [
                    row
                    for row in refreshed
                    if (
                        row.get("work_type"),
                        row.get("work_uid"),
                    ) == identity
                ],
                "GRANT_EXPIRED",
            )
            return True

        for photo in photos:
            if photo["slot_name"] not in grant_state["authorized_slots"]:
                continue
            local_path = photo["local_path"]
            if not os.path.isfile(local_path):
                self._store.mark_photo_pending_retry(
                    photo["photo_uid"],
                    "PHOTO_FILE_MISSING",
                )
                progressed = True
                continue
            object_key = photo.get("cos_key") or (
                f"{grant['keyPrefix']}{photo['slot_name']}/"
                f"{photo['photo_uid']}.jpg"
            )
            expected_key = (
                f"{grant['keyPrefix']}{photo['slot_name']}/"
                f"{photo['photo_uid']}.jpg"
            )
            if object_key != expected_key:
                raise RuntimeError("COS_OBJECT_KEY_MISMATCH")
            expected_url = (
                f"{grant['baseUrl'].rstrip('/')}/{object_key}"
            )
            self._store.mark_photo_uploading(photo["photo_uid"])
            try:
                url = self._uploader.upload(
                    grant,
                    local_path,
                    object_key,
                )
                if (
                    expected_url
                    != f"{grant['baseUrl'].rstrip('/')}/{object_key}"
                    or url != expected_url
                ):
                    raise RuntimeError("COS_URL_MISMATCH")
                self._report_available(
                    photo,
                    url,
                    object_key,
                )
                progressed = True
            except Exception as error:
                if self._invalidates_grant(error):
                    with self._grant_lock:
                        self._grants.pop(identity, None)
                    self._store.invalidate_photo_grant(
                        work_type,
                        work_uid,
                        "UPLOAD_RETRY",
                    )
                    refreshed = self._store.list_pending_photos(
                        limit=100
                    )
                    self._ensure_grant_request(
                        [
                            row
                            for row in refreshed
                            if (
                                row.get("work_type"),
                                row.get("work_uid"),
                            ) == identity
                        ],
                        "UPLOAD_RETRY",
                    )
                    progressed = True
                    break
                self._store.mark_photo_pending_retry(
                    photo["photo_uid"],
                    "PHOTO_UPLOAD_FAILED",
                )
                logger.warning(
                    "photo upload failed: photo=%s error_type=%s",
                    photo["photo_uid"],
                    type(error).__name__,
                )
                progressed = True
        return progressed

    @staticmethod
    def _invalidates_grant(error: Exception) -> bool:
        error_name = type(error).__name__.upper()
        error_text = str(error).upper()
        markers = (
            "AUTH",
            "CREDENTIAL",
            "FORBIDDEN",
            "PERMISSION",
            "SIGNATURE",
            "TOKEN",
            "POLICY",
            "EXPIRED",
        )
        return any(
            marker in error_name or marker in error_text
            for marker in markers
        )

    @staticmethod
    def _grant_request_reason(photos: list[dict]) -> str:
        if any(photo.get("last_error") == "EDGE_RESTARTED" for photo in photos):
            return "EDGE_RESTARTED"
        if any(photo.get("retry_count", 0) > 0 for photo in photos):
            return "UPLOAD_RETRY"
        return "INITIAL_GRANT_MISSING"

    def _ensure_grant_request(
        self,
        photos: list[dict],
        reason: str,
    ) -> str | None:
        if not photos:
            return None
        existing = next(
            (
                photo["grant_request_event_uid"]
                for photo in photos
                if photo.get("grant_request_event_uid")
            ),
            None,
        )
        if existing:
            self._store.assign_photo_grant_request(
                [photo["photo_uid"] for photo in photos],
                existing,
            )
            return existing
        work_type = photos[0]["work_type"]
        work_uid = photos[0]["work_uid"]
        event_uid = str(_uuid.uuid4())
        present_slots = {photo["slot_name"] for photo in photos}
        requested_slots = [
            slot
            for slot in WORK_PHOTO_SLOTS[work_type]
            if slot in present_slots
        ]
        return self._store.ensure_photo_grant_request(
            [photo["photo_uid"] for photo in photos],
            event_uid,
            {
                "workType": work_type,
                "workUid": work_uid,
                "requestedSlots": requested_slots,
                "reason": reason,
            },
            device_name=photos[0]["device_name"],
            work_type=work_type,
            work_uid=work_uid,
        )

    def _report_available(
        self,
        photo: dict,
        url: str,
        object_key: str,
    ) -> None:
        event_uid = str(_uuid.uuid4())
        created = self._store.record_photo_status(
            photo["photo_uid"],
            event_uid=event_uid,
            payload={
                "workType": photo["work_type"],
                "workUid": photo["work_uid"],
                "photo": {
                    "slot": photo["slot_name"],
                    "status": "AVAILABLE",
                    "photoUid": photo["photo_uid"],
                    "url": url,
                    "sha256": photo["content_sha256"],
                    "sizeBytes": photo["size_bytes"],
                    "capturedAt": self._trusted_captured_at(photo),
                    "missingReason": None,
                },
            },
            state="UPLOADED",
            cos_key=object_key,
            url=url,
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise RuntimeError(
                f"photo status persistence {created.lower()}"
            )

    def _report_permanently_missing(
        self,
        photo: dict,
        reason: str,
    ) -> None:
        was_captured = bool(
            photo.get("content_sha256")
            and photo.get("size_bytes")
        )
        event_uid = str(_uuid.uuid4())
        created = self._store.record_photo_status(
            photo["photo_uid"],
            event_uid=event_uid,
            payload={
                "workType": photo["work_type"],
                "workUid": photo["work_uid"],
                "photo": {
                    "slot": photo["slot_name"],
                    "status": "PERMANENTLY_MISSING",
                    "photoUid": (
                        photo["photo_uid"] if was_captured else None
                    ),
                    "url": None,
                    "sha256": (
                        photo["content_sha256"]
                        if was_captured
                        else None
                    ),
                    "sizeBytes": (
                        photo["size_bytes"] if was_captured else None
                    ),
                    "capturedAt": (
                        self._trusted_captured_at(photo)
                        if was_captured
                        else None
                    ),
                    "missingReason": reason,
                },
            },
            state="DEAD",
            error_code=reason,
        )
        if created not in ("ACCEPTED", "DUPLICATE"):
            raise RuntimeError(
                f"missing photo status persistence {created.lower()}"
            )
        try:
            os.remove(photo["local_path"])
        except FileNotFoundError:
            pass

    def _photo_expired(self, photo: dict) -> bool:
        captured_at = photo.get("captured_at")
        deadline_reference = local_deadline_reference()
        if (
            not captured_at
            or photo.get("captured_clock_quality") != "SYNCED"
            or deadline_reference is None
        ):
            return False
        return _parse_utc(captured_at) <= (
            deadline_reference - self._retention
        )

    @staticmethod
    def _trusted_captured_at(photo: dict) -> str | None:
        if photo.get("captured_clock_quality") != "SYNCED":
            return None
        return photo.get("captured_at")

    def _cleanup_confirmed_photos(self) -> None:
        for photo in self._store.list_confirmed_uploaded_photos(limit=20):
            try:
                os.remove(photo["local_path"])
            except FileNotFoundError:
                pass
            self._store.tombstone_photo(photo["photo_uid"])

    def get_slot_urls(self, work_uid):
        photos = self._store.get_photos_by_work(work_uid)
        return {
            photo["slot_name"]: (
                photo.get("url")
                if photo["state"] == "UPLOADED"
                else None
            )
            for photo in photos
        }

    def get_completion_photo_facts(
        self,
        work_uid: str,
        work_type: str,
    ) -> list[dict[str, Any]]:
        """Return the four-slot snapshot without predicting future URLs."""
        photos = {
            photo["slot_name"]: photo
            for photo in self._store.get_photos_by_work(work_uid)
            if photo.get("work_type") == work_type
        }
        facts = []
        for slot in WORK_PHOTO_SLOTS[work_type]:
            photo = photos.get(slot)
            if photo is None or photo["state"] == "CAPTURE_PENDING":
                facts.append({
                    "slot": slot,
                    "status": "UPLOAD_PENDING",
                    "photoUid": None,
                    "url": None,
                    "sha256": None,
                    "sizeBytes": None,
                    "capturedAt": None,
                    "missingReason": "CAMERA_NOT_READY",
                })
                continue
            state = photo["state"]
            if state == "UPLOADED":
                if not photo.get("url"):
                    raise RuntimeError("UPLOADED_PHOTO_URL_MISSING")
                facts.append({
                    "slot": slot,
                    "status": "AVAILABLE",
                    "photoUid": photo["photo_uid"],
                    "url": photo["url"],
                    "sha256": photo["content_sha256"],
                    "sizeBytes": photo["size_bytes"],
                    "capturedAt": self._trusted_captured_at(photo),
                    "missingReason": None,
                })
                continue
            was_captured = bool(
                photo.get("content_sha256")
                and photo.get("size_bytes")
            )
            if state == "DEAD":
                facts.append({
                    "slot": slot,
                    "status": "PERMANENTLY_MISSING",
                    "photoUid": (
                        photo["photo_uid"] if was_captured else None
                    ),
                    "url": None,
                    "sha256": (
                        photo["content_sha256"] if was_captured else None
                    ),
                    "sizeBytes": (
                        photo["size_bytes"] if was_captured else None
                    ),
                    "capturedAt": (
                        self._trusted_captured_at(photo)
                        if was_captured
                        else None
                    ),
                    "missingReason": (
                        photo.get("last_error")
                        or "PHOTO_CAPTURE_FAILED"
                    ),
                })
                continue
            if was_captured:
                facts.append({
                    "slot": slot,
                    "status": "UPLOAD_PENDING",
                    "photoUid": photo["photo_uid"],
                    "url": None,
                    "sha256": photo["content_sha256"],
                    "sizeBytes": photo["size_bytes"],
                    "capturedAt": self._trusted_captured_at(photo),
                    "missingReason": "PHOTO_UPLOAD_PENDING",
                })
            else:
                facts.append({
                    "slot": slot,
                    "status": "UPLOAD_PENDING",
                    "photoUid": None,
                    "url": None,
                    "sha256": None,
                    "sizeBytes": None,
                    "capturedAt": None,
                    "missingReason": "CAMERA_NOT_READY",
                })
        return facts

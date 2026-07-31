import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from command_processor import CommandProcessor
from edge_store import EdgeStore
from onenet_wire import (
    WORK_PHOTO_SLOTS,
    canonical_payload_sha256,
    encode_event_post,
)
from photo_manager import PhotoManager
from work_manager import WorkManager


DEPLOYMENT_CODE = "Dp_demo_01"


def _instant(delta: timedelta) -> str:
    return (
        datetime.now(timezone.utc) + delta
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeUploader:
    def __init__(self):
        self.calls = []

    def upload(self, grant, local_path, object_key):
        self.calls.append(
            {
                "local_path": local_path,
                "object_key": object_key,
                "grant_uid": grant["grantUid"],
            }
        )
        return f"{grant['baseUrl']}/{object_key}"


class AuthFailThenSucceedUploader:
    def __init__(self):
        self.calls = []
        self.failed = False

    def upload(self, grant, local_path, object_key):
        self.calls.append(object_key)
        if not self.failed:
            self.failed = True
            raise PermissionError("signature expired")
        return f"{grant['baseUrl']}/{object_key}"


def _grant(work_uid):
    return {
        "grantUid": str(uuid.uuid4()),
        "tmpSecretId": "TMP_SECRET_ID_MUST_NOT_REACH_SQLITE",
        "tmpSecretKey": "TMP_SECRET_KEY_MUST_NOT_REACH_SQLITE",
        "sessionTokenParts": [
            "SESSION_TOKEN_PART_1_MUST_NOT_REACH_SQLITE",
            "SESSION_TOKEN_PART_2_MUST_NOT_REACH_SQLITE",
        ],
        "bucket": "ecobin-contract-1250000000",
        "region": "ap-guangzhou",
        "baseUrl": (
            "https://ecobin-contract-1250000000.cos."
            "ap-guangzhou.myqcloud.com"
        ),
        "keyPrefix": (
            f"ecobin/{DEPLOYMENT_CODE}/delivery-session/"
            f"{work_uid}/"
        ),
        "expiresAt": _instant(timedelta(minutes=10)),
    }


def _grant_command(work_uid, request_event_uid, grant):
    payload = {
        "grantRequestEventUid": request_event_uid,
        "workType": "DELIVERY_SESSION",
        "workUid": work_uid,
        "authorizedSlots": list(
            WORK_PHOTO_SLOTS["DELIVERY_SESSION"]
        ),
    }
    return {
        "schemaVersion": 1,
        "commandUid": str(uuid.uuid4()),
        "commandType": "PROVIDE_PHOTO_UPLOAD_GRANT",
        "deploymentCode": DEPLOYMENT_CODE,
        "target": {
            "type": "PHOTO_GRANT_REQUEST",
            "uid": request_event_uid,
        },
        "issuedAt": _instant(timedelta()),
        "expiresAt": _instant(timedelta(minutes=5)),
        "payloadSchemaVersion": 1,
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
        "cosGrant": grant,
    }


def test_photo_grant_upload_and_status_report_without_secret_persistence(
    tmp_path,
):
    db_path = tmp_path / "edge.db"
    local_path = tmp_path / "photo.jpg"
    content = b"\xff\xd8\xffphoto-content"
    local_path.write_bytes(content)
    work_uid = str(uuid.uuid4())
    photo_uid = str(uuid.uuid4())
    store = EdgeStore(str(db_path))
    store.initialize()
    assert store.register_photo(
        photo_uid,
        "BEFORE_OUTER",
        str(local_path),
        work_uid=work_uid,
        work_type="DELIVERY_SESSION",
        deployment_code=DEPLOYMENT_CODE,
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        captured_at=_instant(timedelta()),
    ) == "ACCEPTED"
    uploader = FakeUploader()
    photos = PhotoManager(
        store,
        photo_dir=str(tmp_path / "photos"),
        deployment_code=DEPLOYMENT_CODE,
        uploader=uploader,
        start_upload_worker=False,
    )

    assert photos.process_uploads_once()
    request = store._conn.execute(
        """SELECT event_uid, payload_json FROM event_outbox
           WHERE event_type='PHOTO_UPLOAD_GRANT_REQUESTED'"""
    ).fetchone()
    assert request is not None
    assert encode_event_post(
        "PHOTO_UPLOAD_GRANT_REQUESTED",
        json.loads(request["payload_json"]),
    )

    grant = _grant(work_uid)
    command = _grant_command(work_uid, request["event_uid"], grant)
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    stored_command = store.get_command(command["commandUid"])
    assert stored_command["payload"]["cosGrant"] is None

    work = WorkManager(store, object(), None, photos)
    processor = CommandProcessor(store, object(), work)
    assert processor.offer_cos_grant(
        command["commandUid"],
        command["cosGrant"],
    )
    assert processor.process_next()
    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"

    assert photos.process_uploads_once()
    uploaded = store.get_photos_by_work(work_uid)[0]
    expected_key = (
        f"{grant['keyPrefix']}BEFORE_OUTER/{photo_uid}.jpg"
    )
    assert uploaded["state"] == "UPLOADED"
    assert uploaded["cos_key"] == expected_key
    assert uploaded["url"] == f"{grant['baseUrl']}/{expected_key}"
    assert uploader.calls == [
        {
            "local_path": str(local_path),
            "object_key": expected_key,
            "grant_uid": grant["grantUid"],
        }
    ]

    status = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='PHOTO_STATUS_REPORTED'"""
    ).fetchone()
    assert status is not None
    assert encode_event_post(
        "PHOTO_STATUS_REPORTED",
        json.loads(status["payload_json"]),
    )

    store._conn.execute("PRAGMA wal_checkpoint(FULL)")
    secret_markers = (
        grant["tmpSecretId"],
        grant["tmpSecretKey"],
        *grant["sessionTokenParts"],
    )
    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.iterdir()
        if path.name.startswith("edge.db")
    )
    for marker in secret_markers:
        assert marker.encode() not in persisted

    photos.close()
    store.close()


def test_expired_photo_becomes_permanently_missing_without_grant(
    tmp_path,
):
    local_path = tmp_path / "expired.jpg"
    local_path.write_bytes(b"\xff\xd8\xffexpired")
    work_uid = str(uuid.uuid4())
    photo_uid = str(uuid.uuid4())
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.register_photo(
        photo_uid,
        "AFTER_INNER",
        str(local_path),
        work_uid=work_uid,
        work_type="DELIVERY_SESSION",
        deployment_code=DEPLOYMENT_CODE,
        content_sha256="a" * 64,
        size_bytes=10,
        captured_at=_instant(timedelta(hours=-73)),
    )
    photos = PhotoManager(
        store,
        deployment_code=DEPLOYMENT_CODE,
        uploader=FakeUploader(),
        start_upload_worker=False,
    )

    assert photos.process_uploads_once()

    photo = store.get_photos_by_work(work_uid)[0]
    assert photo["state"] == "DEAD"
    assert photo["last_error"] == "PHOTO_UPLOAD_EXPIRED"
    assert not local_path.exists()
    events = store._conn.execute(
        """SELECT event_type, payload_json FROM event_outbox
           ORDER BY edge_event_sequence"""
    ).fetchall()
    assert [row["event_type"] for row in events] == [
        "PHOTO_STATUS_REPORTED"
    ]
    payload = json.loads(events[0]["payload_json"])["payload"]
    assert payload["photo"]["status"] == "PERMANENTLY_MISSING"

    photos.close()
    store.close()


def test_quarantined_photo_status_does_not_delete_local_photo(tmp_path):
    local_path = tmp_path / "quarantined.jpg"
    content = b"\xff\xd8\xffquarantined"
    local_path.write_bytes(content)
    work_uid = str(uuid.uuid4())
    photo_uid = str(uuid.uuid4())
    status_event_uid = str(uuid.uuid4())
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.register_photo(
        photo_uid,
        "BEFORE_OUTER",
        str(local_path),
        work_uid=work_uid,
        work_type="DELIVERY_SESSION",
        deployment_code=DEPLOYMENT_CODE,
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        captured_at=_instant(timedelta()),
    )
    assert store.record_photo_status(
        photo_uid,
        event_uid=status_event_uid,
        payload={
            "workType": "DELIVERY_SESSION",
            "workUid": work_uid,
            "photo": {
                "slot": "BEFORE_OUTER",
                "status": "AVAILABLE",
                "photoUid": photo_uid,
                "url": "https://example.invalid/photo.jpg",
                "sha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
                "capturedAt": _instant(timedelta()),
                "missingReason": None,
            },
        },
        state="UPLOADED",
        cos_key=f"photos/{photo_uid}.jpg",
        url="https://example.invalid/photo.jpg",
    ) == "ACCEPTED"
    assert store.receive_business_confirmation(
        str(uuid.uuid4()),
        status_event_uid,
        "EVENT_QUARANTINED",
    ) == "ACCEPTED"
    photos = PhotoManager(
        store,
        deployment_code=DEPLOYMENT_CODE,
        uploader=FakeUploader(),
        start_upload_worker=False,
    )

    photos.process_uploads_once()

    assert local_path.exists()
    row = store.get_photos_by_work(work_uid)[0]
    assert row["tombstoned"] == 0
    photos.close()
    store.close()


def test_business_applied_photo_status_deletes_local_photo(tmp_path):
    local_path = tmp_path / "associated.jpg"
    content = b"\xff\xd8\xffassociated"
    local_path.write_bytes(content)
    work_uid = str(uuid.uuid4())
    photo_uid = str(uuid.uuid4())
    status_event_uid = str(uuid.uuid4())
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.register_photo(
        photo_uid,
        "BEFORE_OUTER",
        str(local_path),
        work_uid=work_uid,
        work_type="DELIVERY_SESSION",
        deployment_code=DEPLOYMENT_CODE,
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        captured_at=_instant(timedelta()),
    )
    store.record_photo_status(
        photo_uid,
        event_uid=status_event_uid,
        payload={
            "workType": "DELIVERY_SESSION",
            "workUid": work_uid,
            "photo": {
                "slot": "BEFORE_OUTER",
                "status": "AVAILABLE",
                "photoUid": photo_uid,
                "url": "https://example.invalid/photo.jpg",
                "sha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
                "capturedAt": _instant(timedelta()),
                "missingReason": None,
            },
        },
        state="UPLOADED",
        cos_key=f"photos/{photo_uid}.jpg",
        url="https://example.invalid/photo.jpg",
    )
    store.receive_business_confirmation(
        str(uuid.uuid4()),
        status_event_uid,
        "BUSINESS_APPLIED",
    )
    completion_event_uid = str(uuid.uuid4())
    store.create_edge_event(
        completion_event_uid,
        "DELIVERY_COMPLETE",
        {"sessionUid": work_uid},
        work_uid=work_uid,
        deployment_code=DEPLOYMENT_CODE,
        target_type="DELIVERY_SESSION",
    )
    store.receive_business_confirmation(
        str(uuid.uuid4()),
        completion_event_uid,
        "BUSINESS_APPLIED",
    )
    photos = PhotoManager(
        store,
        deployment_code=DEPLOYMENT_CODE,
        uploader=FakeUploader(),
        start_upload_worker=False,
    )

    photos.process_uploads_once()

    assert not local_path.exists()
    assert store.get_photos_by_work(work_uid) == []
    photos.close()
    store.close()


def test_grant_request_event_and_photo_assignment_are_atomic(tmp_path):
    work_uid = str(uuid.uuid4())
    photo_uid = str(uuid.uuid4())
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.register_photo(
        photo_uid,
        "BEFORE_OUTER",
        str(tmp_path / "photo.jpg"),
        work_uid=work_uid,
        work_type="DELIVERY_SESSION",
        deployment_code=DEPLOYMENT_CODE,
    )
    photos = PhotoManager(
        store,
        deployment_code=DEPLOYMENT_CODE,
        start_upload_worker=False,
    )

    def non_atomic_assignment_must_not_be_used(photo_uids, event_uid):
        raise RuntimeError("simulated crash between event and assignment")

    store.assign_photo_grant_request = non_atomic_assignment_must_not_be_used
    event_uid = photos._ensure_grant_request(
        store.get_photos_by_work(work_uid),
        "INITIAL_GRANT_MISSING",
    )

    assigned = store.get_photos_by_work(work_uid)[0]
    assert assigned["grant_request_event_uid"] == event_uid
    events = store._conn.execute(
        """SELECT event_uid FROM event_outbox
           WHERE event_type='PHOTO_UPLOAD_GRANT_REQUESTED'"""
    ).fetchall()
    assert [row["event_uid"] for row in events] == [event_uid]
    photos.close()
    store.close()


def test_completion_urls_remain_empty_until_upload_succeeds(
    tmp_path,
):
    work_uid = str(uuid.uuid4())
    base_url = (
        "https://ecobin-contract-1250000000.cos."
        "ap-guangzhou.myqcloud.com"
    )
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    uploader = AuthFailThenSucceedUploader()
    photos = PhotoManager(
        store,
        photo_dir=str(tmp_path / "photos"),
        outside_camera_source="simulated://outside",
        inside_camera_source="simulated://inside",
        deployment_code=DEPLOYMENT_CODE,
        uploader=uploader,
        start_upload_worker=False,
        trusted_cos_environment={
            "bucket": "ecobin-contract-1250000000",
            "region": "ap-guangzhou",
            "baseUrl": base_url,
        },
    )
    assert photos.capture_open_photos(work_uid)
    assert photos.capture_close_photos(work_uid)

    completion = photos.get_completion_photo_facts(
        work_uid,
        "DELIVERY_SESSION",
    )
    original_photo_uids = {
        fact["slot"]: fact["photoUid"]
        for fact in completion
    }
    assert all(fact["status"] == "UPLOAD_PENDING" for fact in completion)
    assert all(fact["url"] is None for fact in completion)
    original_keys = {
        photo["slot_name"]: photo["cos_key"]
        for photo in store.get_photos_by_work(work_uid)
    }
    assert len(original_keys) == 4

    assert photos.process_uploads_once()
    first_request = store._conn.execute(
        """SELECT event_uid FROM event_outbox
           WHERE event_type='PHOTO_UPLOAD_GRANT_REQUESTED'
           ORDER BY edge_event_sequence DESC LIMIT 1"""
    ).fetchone()["event_uid"]
    photos.offer_upload_grant(
        _grant_command(work_uid, first_request, _grant(work_uid))
    )

    assert photos.process_uploads_once()
    second_request = store._conn.execute(
        """SELECT event_uid FROM event_outbox
           WHERE event_type='PHOTO_UPLOAD_GRANT_REQUESTED'
             AND event_uid<>?
           ORDER BY edge_event_sequence DESC LIMIT 1""",
        (first_request,),
    ).fetchone()["event_uid"]
    after_failure = photos.get_completion_photo_facts(
        work_uid,
        "DELIVERY_SESSION",
    )
    assert all(fact["url"] is None for fact in after_failure)
    assert {
        fact["slot"]: fact["photoUid"]
        for fact in after_failure
    } == original_photo_uids
    assert {
        photo["slot_name"]: photo["cos_key"]
        for photo in store.get_photos_by_work(work_uid)
    } == original_keys

    photos.offer_upload_grant(
        _grant_command(work_uid, second_request, _grant(work_uid))
    )
    assert photos.process_uploads_once()
    uploaded = photos.get_completion_photo_facts(
        work_uid,
        "DELIVERY_SESSION",
    )
    assert all(fact["status"] == "AVAILABLE" for fact in uploaded)
    uploaded_urls = {
        fact["slot"]: fact["url"]
        for fact in uploaded
    }
    assert all(
        url and url.startswith(f"{base_url}/")
        for url in uploaded_urls.values()
    )
    assert {
        fact["slot"]: fact["photoUid"]
        for fact in uploaded
    } == original_photo_uids

    expected_keys = set(original_keys.values())
    assert {
        url.removeprefix(f"{base_url}/")
        for url in uploaded_urls.values()
    } == expected_keys
    assert set(uploader.calls) == expected_keys
    assert len(uploader.calls) == 5
    photos.close()
    store.close()


def test_completed_photo_grant_duplicate_remains_successful(
    tmp_path,
):
    work_uid = str(uuid.uuid4())
    request_uid = str(uuid.uuid4())
    command = _grant_command(
        work_uid,
        request_uid,
        _grant(work_uid),
    )
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    store.complete_command(
        command["commandUid"],
        {"grantRequestEventUid": request_uid},
    )

    class NoLongerPendingWork:
        def accept_photo_upload_grant(self, received):
            raise ValueError("photo grant request is not pending")

    processor = CommandProcessor(
        store,
        object(),
        NoLongerPendingWork(),
    )
    assert processor.accept_photo_upload_grant_now(command)
    assert store.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    store.close()

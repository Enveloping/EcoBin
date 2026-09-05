from __future__ import annotations

import base64
import hashlib
import io
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from business_update_downloader import BusinessUpdateDownloader
from business_update_store import BusinessUpdateStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT_UID = "22222222-2222-4222-8222-222222222222"
COMMAND_UID = "33333333-3333-4333-8333-333333333333"
RELEASE_UID = "44444444-4444-4444-8444-444444444444"
CANCEL_COMMAND_UID = "55555555-5555-4555-8555-555555555555"
BASE_URL = "https://private.example.invalid"
OBJECT_KEY = f"edge-runtime/releases/{RELEASE_UID}/package.tar.gz"
NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class Response(io.BytesIO):
    status = 200

    def __init__(self, content: bytes, url: str) -> None:
        super().__init__(content)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def _request(content: bytes, signature: bytes) -> dict:
    return {
        "updateUid": UPDATE_UID,
        "deploymentUid": DEPLOYMENT_UID,
        "commandUid": COMMAND_UID,
        "releaseId": RELEASE_UID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": hashlib.sha256(content).hexdigest(),
        "packageSize": len(content),
        "signingKeyId": "business_2030",
        "stablePayloadSha256": "b" * 64,
        "controlSequence": 1,
        "objectKey": OBJECT_KEY,
        "signatureSha256": hashlib.sha256(signature).hexdigest(),
        "packageSignatureBase64": base64.b64encode(signature).decode("ascii"),
        "observationWindowSeconds": 1800,
        "downloadTimeoutSeconds": 1800,
        "drainTimeoutSeconds": 1800,
        "maximumRetryCount": 3,
    }


def _journal(path, content: bytes, signature: bytes) -> BusinessUpdateStore:
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    journal.create_or_refresh_remote_update(
        _request(content, signature), authorization_sequence=1
    )
    return journal


def test_download_uses_volatile_grant_and_writes_exact_private_package(
    tmp_path,
) -> None:
    content = b"signed business runtime package"
    signature = bytes(range(64))
    state = tmp_path / "updater.db"
    incoming = tmp_path / "incoming"
    incoming.mkdir(mode=0o700)
    incoming.chmod(0o700)
    journal = _journal(state, content, signature)
    url = f"{BASE_URL}/{OBJECT_KEY}?temporary-secret=one"
    ready = []
    downloader = BusinessUpdateDownloader(
        journal=journal,
        incoming_root=incoming,
        trusted_base_url=BASE_URL,
        open_url=lambda actual, _timeout: Response(content, actual),
        utc_now=lambda: NOW,
        on_package_ready=lambda: ready.append(True),
    )

    downloader.accept_authorization(
        update_uid=UPDATE_UID,
        authorization_sequence=1,
        url=url,
        expires_at=NOW + timedelta(minutes=30),
    )
    assert downloader.process_once() is True

    update_dir = incoming / UPDATE_UID
    archive = update_dir / "package.tar.gz"
    signature_file = update_dir / "package.sig"
    assert archive.read_bytes() == content
    assert signature_file.read_bytes() == signature
    if os.name == "posix":
        assert archive.stat().st_mode & 0o777 == 0o600
        assert signature_file.stat().st_mode & 0o777 == 0o600
    assert journal.get_remote_update(UPDATE_UID)["downloadState"] == "DOWNLOADED"
    assert ready == [True]
    with sqlite3.connect(state) as connection:
        durable_text = " ".join(
            str(value)
            for table in ("business_runtime_update", "business_remote_update")
            for row in connection.execute(f"SELECT * FROM {table}")
            for value in row
        )
    assert "temporary-secret" not in durable_text
    assert url not in durable_text


def test_restart_without_complete_package_requires_fresh_authorization(
    tmp_path,
) -> None:
    content = b"interrupted package"
    signature = b"S" * 64
    incoming = tmp_path / "incoming"
    incoming.mkdir(mode=0o700)
    incoming.chmod(0o700)
    journal = _journal(tmp_path / "updater.db", content, signature)
    journal.begin_remote_download(UPDATE_UID, 1)
    downloader = BusinessUpdateDownloader(
        journal=journal,
        incoming_root=incoming,
        trusted_base_url=BASE_URL,
        utc_now=lambda: NOW,
    )

    downloader._recover_interrupted_downloads()

    remote = journal.get_remote_update(UPDATE_UID)
    assert remote["downloadState"] == "WAITING_AUTHORIZATION"
    assert remote["downloadErrorCode"] == "DOWNLOAD_AUTHORIZATION_LOST"


def test_digest_mismatch_is_a_terminal_package_rejection(tmp_path) -> None:
    expected = b"expected package"
    actual = b"tampered package"
    signature = b"T" * 64
    incoming = tmp_path / "incoming"
    incoming.mkdir(mode=0o700)
    incoming.chmod(0o700)
    journal = _journal(tmp_path / "updater.db", expected, signature)
    url = f"{BASE_URL}/{OBJECT_KEY}?temporary-secret=two"
    downloader = BusinessUpdateDownloader(
        journal=journal,
        incoming_root=incoming,
        trusted_base_url=BASE_URL,
        open_url=lambda supplied, _timeout: Response(actual, supplied),
        utc_now=lambda: NOW,
    )
    downloader.accept_authorization(
        update_uid=UPDATE_UID,
        authorization_sequence=1,
        url=url,
        expires_at=NOW + timedelta(minutes=30),
    )

    assert downloader.process_once() is True

    assert journal.get_update(UPDATE_UID)["state"] == "REJECTED"
    assert journal.get_remote_update(UPDATE_UID)["downloadErrorCode"] in {
        "BUSINESS_PACKAGE_SIZE_MISMATCH",
        "BUSINESS_PACKAGE_SHA256_MISMATCH",
    }
    assert not (incoming / UPDATE_UID / "package.tar.gz").exists()


def test_cancel_interrupts_download_and_removes_only_its_private_tree(
    tmp_path,
) -> None:
    content = b"x" * (2 * 1024 * 1024)
    signature = b"C" * 64
    incoming = tmp_path / "incoming"
    incoming.mkdir(mode=0o700)
    incoming.chmod(0o700)
    unrelated = incoming / "keep.txt"
    unrelated.write_text("keep", encoding="ascii")
    journal = _journal(tmp_path / "updater.db", content, signature)
    journal.request_remote_cancellation(
        {
            "cancelCommandUid": CANCEL_COMMAND_UID,
            "updateUid": UPDATE_UID,
            "deploymentUid": DEPLOYMENT_UID,
            "controlSequence": 2,
            "reason": "cancel before mutation",
        }
    )
    url = f"{BASE_URL}/{OBJECT_KEY}?temporary-secret=cancel"
    downloader: BusinessUpdateDownloader

    class CancellingResponse(Response):
        def read(self, size: int = -1) -> bytes:
            chunk = super().read(size)
            downloader.cancel(UPDATE_UID)
            return chunk

    downloader = BusinessUpdateDownloader(
        journal=journal,
        incoming_root=incoming,
        trusted_base_url=BASE_URL,
        open_url=lambda supplied, _timeout: CancellingResponse(
            content, supplied
        ),
        utc_now=lambda: NOW,
    )
    downloader.accept_authorization(
        update_uid=UPDATE_UID,
        authorization_sequence=1,
        url=url,
        expires_at=NOW + timedelta(minutes=30),
    )

    assert downloader.process_once() is True
    assert downloader.cleanup_cancelled(UPDATE_UID) is True
    assert not (incoming / UPDATE_UID).exists()
    assert unrelated.read_text(encoding="ascii") == "keep"
    assert journal.get_remote_update(UPDATE_UID)["downloadState"] == (
        "DOWNLOADING"
    )

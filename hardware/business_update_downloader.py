"""Volatile-authority downloader for remote business-runtime packages.

Only immutable package facts and the latest authorization sequence enter the
updater database.  The presigned URL exists solely in this process memory and
is never included in logs or durable error messages.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from business_update_store import BusinessUpdateStore, BusinessUpdateStoreError


DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class DownloadAuthorization:
    update_uid: str
    authorization_sequence: int
    url: str
    expires_at: datetime


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open_without_redirect(url: str, timeout_seconds: float) -> BinaryIO:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "EcoBin-Device-Updater/1"},
        method="GET",
    )
    return opener.open(request, timeout=timeout_seconds)


class BusinessUpdateDownloader:
    """Download one immutable object while the old business runtime stays up."""

    def __init__(
        self,
        *,
        journal: BusinessUpdateStore,
        incoming_root: str | os.PathLike[str],
        trusted_base_url: str,
        open_url: Callable[[str, float], BinaryIO] = _open_without_redirect,
        utc_now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        retry_delay_seconds: float = RETRY_DELAY_SECONDS,
        on_package_ready: Callable[[], None] | None = None,
    ) -> None:
        if retry_delay_seconds <= 0:
            raise ValueError("business download retry delay must be positive")
        self.journal = journal
        self.incoming_root = Path(incoming_root)
        self.trusted_base_url = _canonical_base_url(trusted_base_url)
        self._open_url = open_url
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._retry_delay_seconds = float(retry_delay_seconds)
        self._on_package_ready = on_package_ready
        self._pending: dict[str, DownloadAuthorization] = {}
        self._pending_lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._active_update_uid: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: BaseException | None = None

    def set_package_ready_callback(self, callback: Callable[[], None]) -> None:
        self._on_package_ready = callback

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.failure = None
        self._recover_interrupted_downloads()
        self._thread = threading.Thread(
            target=self._run,
            name="business-update-downloader",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_seconds)
        self._thread = None

    def accept_authorization(
        self,
        *,
        update_uid: str,
        authorization_sequence: int,
        url: str,
        expires_at: datetime,
    ) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("download authorization expiry must be timezone-aware")
        material = self.journal.get_remote_download_material(update_uid)
        if material is None:
            raise BusinessUpdateStoreError(
                "BUSINESS_REMOTE_UPDATE_NOT_FOUND",
                "remote business update was not found",
            )
        if material["authorizationSequence"] != authorization_sequence:
            raise BusinessUpdateStoreError(
                "DOWNLOAD_AUTHORIZATION_SUPERSEDED",
                "download authorization is no longer current",
            )
        _require_download_url(
            url,
            trusted_base_url=self.trusted_base_url,
            object_key=material["objectKey"],
        )
        authorization = DownloadAuthorization(
            update_uid=update_uid,
            authorization_sequence=authorization_sequence,
            url=url,
            expires_at=expires_at.astimezone(timezone.utc),
        )
        with self._pending_lock:
            current = self._pending.get(update_uid)
            if (
                current is None
                or authorization_sequence >= current.authorization_sequence
            ):
                self._pending[update_uid] = authorization
        self._wake.set()

    def cancel(self, update_uid: str) -> None:
        """Forget volatile authority and interrupt an in-flight safe download."""

        if self.journal.get_remote_update(update_uid) is None:
            raise BusinessUpdateStoreError(
                "BUSINESS_REMOTE_UPDATE_NOT_FOUND",
                "remote business update was not found",
            )
        with self._pending_lock:
            self._cancelled.add(update_uid)
            self._pending.pop(update_uid, None)
        self._wake.set()

    def cleanup_cancelled(self, update_uid: str) -> bool:
        """Remove only this updater-owned download tree once I/O has stopped."""

        with self._pending_lock:
            self._cancelled.add(update_uid)
            self._pending.pop(update_uid, None)
            if self._active_update_uid == update_uid:
                return False
        directory = self.incoming_root / update_uid
        if directory.exists() or directory.is_symlink():
            _remove_private_download_tree(directory, self.incoming_root)
        with self._pending_lock:
            self._cancelled.discard(update_uid)
        return True

    def process_once(self) -> bool:
        authorization = self._take_next()
        if authorization is None:
            return False
        with self._pending_lock:
            if authorization.update_uid in self._cancelled:
                return True
            self._active_update_uid = authorization.update_uid
        try:
            return self._process_authorization(authorization)
        finally:
            with self._pending_lock:
                if self._active_update_uid == authorization.update_uid:
                    self._active_update_uid = None

    def _process_authorization(
        self,
        authorization: DownloadAuthorization,
    ) -> bool:
        material = self.journal.get_remote_download_material(
            authorization.update_uid
        )
        if material is None or material["state"] != "RECEIVED":
            return True
        if material["authorizationSequence"] != authorization.authorization_sequence:
            return True
        if self._complete_package_exists(material):
            self.journal.mark_remote_downloaded(
                authorization.update_uid,
                authorization.authorization_sequence,
            )
            self._notify_ready()
            return True
        if self._aware_utc_now() >= authorization.expires_at:
            self.journal.mark_remote_download_authorization_required(
                authorization.update_uid,
                authorization.authorization_sequence,
                "DOWNLOAD_AUTHORIZATION_EXPIRED",
            )
            return True
        try:
            self.journal.begin_remote_download(
                authorization.update_uid,
                authorization.authorization_sequence,
            )
            self._download(authorization, material)
            self.journal.mark_remote_downloaded(
                authorization.update_uid,
                authorization.authorization_sequence,
            )
            self._notify_ready()
        except BusinessUpdateStoreError as error:
            if error.code not in {
                "DOWNLOAD_AUTHORIZATION_SUPERSEDED",
                "BUSINESS_DOWNLOAD_RETRY_EXHAUSTED",
            }:
                raise
        except _DownloadCancelled:
            return True
        except _PermanentDownloadError as error:
            self.journal.reject_remote_download(
                authorization.update_uid,
                authorization.authorization_sequence,
                error.code,
            )
        except _AuthorizationDownloadError as error:
            self.journal.mark_remote_download_authorization_required(
                authorization.update_uid,
                authorization.authorization_sequence,
                error.code,
            )
        except Exception:
            # Exception text from an HTTP library may contain the signed URL.
            # Never persist or log it.  Retry only while this in-memory grant
            # remains current and unexpired.
            latest = self.journal.get_remote_download_material(
                authorization.update_uid
            )
            if (
                latest is not None
                and latest["state"] == "RECEIVED"
                and latest["authorizationSequence"]
                == authorization.authorization_sequence
                and self._aware_utc_now() < authorization.expires_at
                and latest["downloadAttemptCount"]
                < max(1, latest["maximumRetryCount"])
            ):
                if not self._stop.wait(self._retry_delay_seconds):
                    with self._pending_lock:
                        self._pending[authorization.update_uid] = authorization
                    self._wake.set()
            else:
                self.journal.mark_remote_download_authorization_required(
                    authorization.update_uid,
                    authorization.authorization_sequence,
                    "BUSINESS_DOWNLOAD_TEMPORARY_FAILURE",
                )
        return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self.process_once():
                    continue
                self._wake.wait(0.5)
                self._wake.clear()
        except BaseException as error:  # noqa: BLE001 - worker death is fatal
            self.failure = error
            self._stop.set()

    def _download(
        self,
        authorization: DownloadAuthorization,
        material: dict[str, Any],
    ) -> None:
        update_directory = self._prepare_update_directory(
            authorization.update_uid
        )
        signature_path = update_directory / "package.sig"
        _write_exact_immutable(
            signature_path,
            material["signatureBytes"],
            material["signatureSha256"],
        )
        final_path = update_directory / "package.tar.gz"
        part_path = update_directory / (
            f"package.tar.gz.{authorization.authorization_sequence}.part"
        )
        if part_path.exists():
            _require_regular_owned_path(part_path)
            part_path.unlink()
        deadline = self._monotonic() + material["downloadTimeoutSeconds"]
        digest = hashlib.sha256()
        total = 0
        timeout = min(30.0, float(material["downloadTimeoutSeconds"]))
        try:
            response = self._open_url(authorization.url, timeout)
            with response:
                final_url = getattr(response, "geturl", lambda: authorization.url)()
                _require_download_url(
                    final_url,
                    trusted_base_url=self.trusted_base_url,
                    object_key=material["objectKey"],
                )
                status = getattr(response, "status", 200)
                if status != 200:
                    raise _AuthorizationDownloadError(
                        "DOWNLOAD_AUTHORIZATION_REJECTED"
                    )
                with part_path.open("xb") as target:
                    os.chmod(part_path, 0o600)
                    while True:
                        if self._is_cancelled(authorization.update_uid):
                            raise _DownloadCancelled()
                        if self._monotonic() >= deadline:
                            raise _AuthorizationDownloadError(
                                "BUSINESS_DOWNLOAD_TIMEOUT"
                            )
                        chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > material["packageSize"]:
                            raise _PermanentDownloadError(
                                "BUSINESS_PACKAGE_SIZE_MISMATCH"
                            )
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise _PermanentDownloadError(
                    "BUSINESS_PACKAGE_OBJECT_NOT_FOUND"
                ) from None
            if error.code in {401, 403} or 300 <= error.code < 400:
                raise _AuthorizationDownloadError(
                    "DOWNLOAD_AUTHORIZATION_REJECTED"
                ) from None
            raise
        finally:
            if part_path.exists() and (
                total != material["packageSize"]
                or digest.hexdigest() != material["packageSha256"]
            ):
                part_path.unlink()
        if total != material["packageSize"]:
            raise _PermanentDownloadError("BUSINESS_PACKAGE_SIZE_MISMATCH")
        if digest.hexdigest() != material["packageSha256"]:
            raise _PermanentDownloadError("BUSINESS_PACKAGE_SHA256_MISMATCH")
        if self._is_cancelled(authorization.update_uid):
            raise _DownloadCancelled()
        os.replace(part_path, final_path)
        os.chmod(final_path, 0o600)
        _fsync_directory(update_directory)

    def _prepare_update_directory(self, update_uid: str) -> Path:
        _require_private_directory(self.incoming_root)
        directory = self.incoming_root / update_uid
        if directory.exists():
            _require_private_directory(directory)
        else:
            directory.mkdir(mode=0o700)
        return directory

    def _complete_package_exists(self, material: dict[str, Any]) -> bool:
        directory = self.incoming_root / material["updateUid"]
        archive = directory / "package.tar.gz"
        signature = directory / "package.sig"
        return (
            _is_exact_file(
                archive,
                material["packageSha256"],
                material["packageSize"],
            )
            and _is_exact_file(signature, material["signatureSha256"], 64)
        )

    def _recover_interrupted_downloads(self) -> None:
        for material in self.journal.list_remote_updates_requiring_recovery():
            if material is None:
                continue
            if self._complete_package_exists(material):
                self.journal.mark_remote_downloaded(
                    material["updateUid"], material["authorizationSequence"]
                )
                self._notify_ready()
            else:
                self.journal.mark_remote_download_authorization_required(
                    material["updateUid"],
                    material["authorizationSequence"],
                    "DOWNLOAD_AUTHORIZATION_LOST",
                )

    def _take_next(self) -> DownloadAuthorization | None:
        with self._pending_lock:
            if not self._pending:
                return None
            update_uid = next(iter(self._pending))
            return self._pending.pop(update_uid)

    def _is_cancelled(self, update_uid: str) -> bool:
        with self._pending_lock:
            return update_uid in self._cancelled

    def _notify_ready(self) -> None:
        callback = self._on_package_ready
        if callback is not None:
            callback()

    def _aware_utc_now(self) -> datetime:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("business downloader clock must be timezone-aware")
        return value.astimezone(timezone.utc)


class _PermanentDownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _AuthorizationDownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DownloadCancelled(RuntimeError):
    pass


def _canonical_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("trusted business download base URL is invalid")
    return f"https://{parsed.netloc}"


def _require_download_url(
    value: str,
    *,
    trusted_base_url: str,
    object_key: str,
) -> None:
    parsed = urlsplit(value)
    trusted = urlsplit(trusted_base_url)
    if (
        parsed.scheme != trusted.scheme
        or parsed.netloc != trusted.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != "/" + object_key
        or not parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "business download URL differs from the trusted object location"
        )


def _require_private_directory(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise ValueError("business download directory is not a real directory")
    if os.name == "posix" and details.st_mode & 0o077:
        raise ValueError("business download directory is not private")


def _require_regular_owned_path(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise ValueError("business download path is not a regular file")


def _remove_private_download_tree(path: Path, parent: Path) -> None:
    try:
        if path.parent.resolve(strict=True) != parent.resolve(strict=True):
            raise ValueError("business download cleanup escaped its fixed parent")
        details = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise ValueError("business download cleanup target is unsafe")
    for current_text, directories, files in os.walk(path, followlinks=False):
        current = Path(current_text)
        for name in (*directories, *files):
            entry = current / name
            entry_details = entry.lstat()
            if stat.S_ISLNK(entry_details.st_mode) or not (
                stat.S_ISDIR(entry_details.st_mode)
                or stat.S_ISREG(entry_details.st_mode)
            ):
                raise ValueError("business download cleanup tree is unsafe")
            if stat.S_ISREG(entry_details.st_mode) and entry_details.st_nlink != 1:
                raise ValueError("business download cleanup file is not single-link")
    shutil.rmtree(path)
    _fsync_directory(parent)


def _write_exact_immutable(path: Path, content: bytes, digest: str) -> None:
    if path.exists():
        if not _is_exact_file(path, digest, len(content)):
            raise _PermanentDownloadError("BUSINESS_SIGNATURE_FILE_CONFLICT")
        return
    with path.open("xb") as target:
        os.chmod(path, 0o600)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    _fsync_directory(path.parent)


def _is_exact_file(path: Path, digest: str, size: int) -> bool:
    try:
        _require_regular_owned_path(path)
        if path.stat().st_size != size:
            return False
        actual = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
                actual.update(chunk)
        return actual.hexdigest() == digest
    except (OSError, ValueError):
        return False


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["BusinessUpdateDownloader", "DownloadAuthorization"]

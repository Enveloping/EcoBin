"""Fixed, local-only primitives for activating the business runtime.

All paths and the systemd unit are constants owned by the image.  Socket
callers provide only UUID identities; they can never select a path, service,
command, executable, or command-line argument.  Release trees are copied
without following links, accepting hard links, or preserving special files.
Package code is never executed by this root process.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

from local_control import LocalControlActionError

SYSTEMCTL = "/usr/bin/systemctl"
BUSINESS_SERVICE = "ecobin-hardware.service"
BUSINESS_ROOT = Path("/opt/ecobin/business")
RELEASE_ROOT = BUSINESS_ROOT / "releases"
CURRENT_LINK = BUSINESS_ROOT / "current"
PREVIOUS_LINK = BUSINESS_ROOT / "previous"
BUSINESS_DATABASE = Path("/var/lib/ecobin/business/edge.db")
UPDATER_ROOT = Path("/var/lib/ecobin/updater")
STAGING_ROOT = UPDATER_ROOT / "staging"
SNAPSHOT_ROOT = Path("/var/lib/ecobin/privileged/business-snapshots")
RELEASE_MARKER = ".ecobin-release.json"
MAX_RELEASE_FILES = 20_000
MAX_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
SYSTEMCTL_TIMEOUT_SECONDS = 30


class BusinessActivationPrimitives:
    """Perform only the fixed operations needed by the permanent updater."""

    def __init__(
        self,
        *,
        business_root: Path = BUSINESS_ROOT,
        database_path: Path = BUSINESS_DATABASE,
        updater_root: Path = UPDATER_ROOT,
        snapshot_root: Path = SNAPSHOT_ROOT,
        business_uid: int,
        business_gid: int,
        updater_uid: int,
        updater_gid: int,
        privileged_uid: int = 0,
        privileged_gid: int = 0,
        command_runner: Any = subprocess.run,
    ) -> None:
        self.business_root = business_root
        self.release_root = business_root / "releases"
        self.current_link = business_root / "current"
        self.previous_link = business_root / "previous"
        self.database_path = database_path
        self.updater_root = updater_root
        self.staging_root = updater_root / "staging"
        self.snapshot_root = snapshot_root
        for value, name in (
            (business_uid, "business UID"),
            (business_gid, "business GID"),
            (updater_uid, "updater UID"),
            (updater_gid, "updater GID"),
            (privileged_uid, "privileged helper UID"),
            (privileged_gid, "privileged helper GID"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        self.business_uid = business_uid
        self.business_gid = business_gid
        self.updater_uid = updater_uid
        self.updater_gid = updater_gid
        self.privileged_uid = privileged_uid
        self.privileged_gid = privileged_gid
        self._run_command = command_runner

    def status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"businessRuntimeState": self._service_state()}

    def stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        self._systemctl("stop")
        state = self._service_state()
        if state != "INACTIVE":
            raise LocalControlActionError(
                "SERVICE_CONTROL_FAILED",
                "fixed business service did not stop",
            )
        return {
            "updateUid": update_uid,
            "businessRuntimeState": state,
        }

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        self._systemctl("start")
        state = self._service_state()
        if state != "ACTIVE":
            raise LocalControlActionError(
                "SERVICE_CONTROL_FAILED",
                "fixed business service did not become active",
            )
        return {
            "updateUid": update_uid,
            "businessRuntimeState": state,
        }

    def snapshot_database(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        self._require_service_inactive()
        _require_regular_file(self.database_path, "business database")
        self._ensure_snapshot_root()
        update_directory = self.snapshot_root / update_uid
        _create_or_require_directory(
            update_directory,
            mode=0o700,
            uid=self.privileged_uid,
            gid=self.privileged_gid,
        )
        snapshot = update_directory / "edge.db"
        if snapshot.exists() or snapshot.is_symlink():
            _require_regular_file(snapshot, "business database snapshot")
            _verify_sqlite_database(snapshot)
            return {
                "updateUid": update_uid,
                "disposition": "ALREADY_CREATED",
                "snapshotSize": _require_regular_file(
                    snapshot,
                    "business database snapshot",
                ).st_size,
            }

        temporary = update_directory / f".edge.db.{os.getpid()}.tmp"
        _reject_existing_path(temporary, "snapshot temporary file")
        try:
            _sqlite_backup(
                self.database_path,
                temporary,
                uid=self.updater_uid,
                gid=self.updater_gid,
                mode=0o600,
            )
            _verify_sqlite_database(temporary)
            os.replace(temporary, snapshot)
            _fsync_directory(update_directory)
        except Exception:
            _unlink_regular_if_present(temporary, "snapshot temporary file")
            raise
        return {
            "updateUid": update_uid,
            "disposition": "CREATED",
            "snapshotSize": _require_regular_file(
                snapshot,
                "business database snapshot",
            ).st_size,
        }

    def restore_database(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        self._require_service_inactive()
        snapshot = self.snapshot_root / update_uid / "edge.db"
        _require_real_directory(snapshot.parent, "snapshot update directory")
        _require_regular_file(snapshot, "business database snapshot")
        _verify_sqlite_database(snapshot)
        database_parent = self.database_path.parent
        _require_real_directory(database_parent, "business database directory")
        if self.database_path.exists() or self.database_path.is_symlink():
            _require_regular_file(self.database_path, "business database")
        temporary = database_parent / f".edge.db.restore.{update_uid}.tmp"
        _unlink_regular_if_present(temporary, "database restore temporary file")
        try:
            _sqlite_backup(
                snapshot,
                temporary,
                uid=self.business_uid,
                gid=self.business_gid,
                mode=0o600,
            )
            _verify_sqlite_database(temporary)
            for suffix in ("-wal", "-shm"):
                _unlink_regular_if_present(
                    Path(f"{self.database_path}{suffix}"),
                    "business database sidecar",
                )
            os.replace(temporary, self.database_path)
            _fsync_directory(database_parent)
        except Exception:
            _unlink_regular_if_present(temporary, "database restore temporary file")
            raise
        return {
            "updateUid": update_uid,
            "disposition": "RESTORED",
            "databaseSize": _require_regular_file(
                self.database_path,
                "business database",
            ).st_size,
        }

    def install_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        release_uid = _require_uuid4(payload["releaseUid"], "releaseUid")
        self._require_service_inactive()
        _require_real_directory(self.release_root, "business release root")
        source_update = self.staging_root / update_uid
        source = source_update / release_uid
        _require_real_directory(self.staging_root, "business staging root")
        _require_real_directory(source_update, "business staging update directory")
        _require_real_directory(source, "business staged release")

        destination = self.release_root / release_uid
        if destination.exists() or destination.is_symlink():
            self._require_matching_release(destination, update_uid, release_uid)
            return {
                "updateUid": update_uid,
                "releaseUid": release_uid,
                "disposition": "ALREADY_INSTALLED",
            }

        temporary = self.release_root / f".install-{update_uid}-{release_uid}"
        if temporary.exists() or temporary.is_symlink():
            _remove_safe_tree(temporary)
        os.mkdir(temporary, 0o750)
        os.chown(temporary, self.privileged_uid, self.business_gid)
        try:
            counter = _CopyCounter()
            _copy_safe_tree(
                source,
                temporary,
                destination_uid=self.privileged_uid,
                destination_gid=self.business_gid,
                counter=counter,
            )
            marker = {
                "schemaVersion": 1,
                "updateUid": update_uid,
                "releaseUid": release_uid,
            }
            _write_fixed_json(
                temporary / RELEASE_MARKER,
                marker,
                uid=self.privileged_uid,
                gid=self.business_gid,
                mode=0o640,
            )
            os.rename(temporary, destination)
            _fsync_directory(self.release_root)
        except Exception:
            if temporary.exists() or temporary.is_symlink():
                _remove_safe_tree(temporary)
            raise
        return {
            "updateUid": update_uid,
            "releaseUid": release_uid,
            "disposition": "INSTALLED",
            "fileCount": counter.files,
            "installedBytes": counter.bytes,
        }

    def activate_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        release_uid = _require_uuid4(payload["releaseUid"], "releaseUid")
        self._require_service_inactive()
        target = self.release_root / release_uid
        self._require_matching_release(target, None, release_uid)
        old_release_uid = _read_release_link(self.current_link, self.release_root)
        if old_release_uid == release_uid:
            return {
                "updateUid": update_uid,
                "releaseUid": release_uid,
                "previousReleaseUid": _read_release_link(
                    self.previous_link,
                    self.release_root,
                ),
                "disposition": "ALREADY_ACTIVE",
            }
        if old_release_uid is not None:
            _atomic_release_link(
                self.previous_link,
                self.release_root,
                old_release_uid,
                update_uid,
            )
        _atomic_release_link(
            self.current_link,
            self.release_root,
            release_uid,
            update_uid,
        )
        return {
            "updateUid": update_uid,
            "releaseUid": release_uid,
            "previousReleaseUid": old_release_uid,
            "disposition": "ACTIVATED",
        }

    def rollback_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        release_uid = _require_uuid4(payload["releaseUid"], "releaseUid")
        self._require_service_inactive()
        target = self.release_root / release_uid
        self._require_matching_release(target, None, release_uid)
        replaced_release_uid = _read_release_link(
            self.current_link,
            self.release_root,
        )
        _atomic_release_link(
            self.current_link,
            self.release_root,
            release_uid,
            update_uid,
        )
        if replaced_release_uid is not None and replaced_release_uid != release_uid:
            _atomic_release_link(
                self.previous_link,
                self.release_root,
                replaced_release_uid,
                update_uid,
            )
        return {
            "updateUid": update_uid,
            "releaseUid": release_uid,
            "replacedReleaseUid": replaced_release_uid,
            "disposition": "ROLLED_BACK",
        }

    def cleanup_staging(self, payload: dict[str, Any]) -> dict[str, Any]:
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        update_directory = self.staging_root / update_uid
        if not update_directory.exists() and not update_directory.is_symlink():
            disposition = "ALREADY_CLEAN"
        else:
            _remove_safe_tree(update_directory)
            disposition = "CLEANED"
        # The snapshot is deliberately retained.  Only the updater can decide
        # when it no longer carries rollback responsibility.
        return {
            "updateUid": update_uid,
            "disposition": disposition,
            "snapshotRetained": True,
        }

    def _ensure_snapshot_root(self) -> None:
        _require_real_directory(self.updater_root, "updater state root")
        metadata = _require_real_directory(
            self.snapshot_root,
            "business database snapshot root",
        )
        if (
            metadata.st_uid != self.privileged_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LocalControlActionError(
                "FIXED_PATH_UNSAFE",
                "business database snapshot root is not helper-controlled",
            )

    def _require_matching_release(
        self,
        release_path: Path,
        update_uid: str | None,
        release_uid: str,
    ) -> None:
        _require_real_directory(release_path, "installed business release")
        marker_path = release_path / RELEASE_MARKER
        marker = _read_fixed_json(marker_path)
        expected_fields = {"schemaVersion", "updateUid", "releaseUid"}
        if set(marker) != expected_fields:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "installed business release marker is invalid",
            )
        try:
            marker_update_uid = _require_uuid4(marker["updateUid"], "updateUid")
            marker_release_uid = _require_uuid4(marker["releaseUid"], "releaseUid")
        except LocalControlActionError as error:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "installed business release marker identity is invalid",
            ) from error
        if marker.get("schemaVersion") != 1 or marker_release_uid != release_uid:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "installed business release marker does not match",
            )
        if update_uid is not None and marker_update_uid != update_uid:
            raise LocalControlActionError(
                "RELEASE_IDENTITY_CONFLICT",
                "installed business release belongs to another update",
            )

    def _require_service_inactive(self) -> None:
        if self._service_state() != "INACTIVE":
            raise LocalControlActionError(
                "BUSINESS_RUNTIME_ACTIVE",
                "fixed business service must be stopped first",
            )

    def _systemctl(self, operation: str) -> None:
        if operation not in {"start", "stop"}:
            raise AssertionError("unsupported internal systemctl operation")
        result = self._invoke_fixed_command(
            [SYSTEMCTL, operation, "--", BUSINESS_SERVICE],
        )
        if int(getattr(result, "returncode", 1)) != 0:
            raise LocalControlActionError(
                "SERVICE_CONTROL_FAILED",
                "fixed business service control failed",
            )

    def _service_state(self) -> str:
        result = self._invoke_fixed_command(
            [
                SYSTEMCTL,
                "show",
                "--property=ActiveState",
                "--value",
                "--",
                BUSINESS_SERVICE,
            ],
        )
        if int(getattr(result, "returncode", 1)) != 0:
            return "UNKNOWN"
        value = str(getattr(result, "stdout", "")).strip().lower()
        return {
            "active": "ACTIVE",
            "activating": "ACTIVATING",
            "deactivating": "DEACTIVATING",
            "inactive": "INACTIVE",
            "failed": "FAILED",
        }.get(value, "UNKNOWN")

    def _invoke_fixed_command(self, argv: list[str]) -> Any:
        try:
            return self._run_command(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=SYSTEMCTL_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception as error:
            raise LocalControlActionError(
                "SERVICE_CONTROL_FAILED",
                "fixed business service control could not execute",
            ) from error


class _CopyCounter:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0

    def add_file(self, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.files > MAX_RELEASE_FILES or self.bytes > MAX_RELEASE_BYTES:
            raise LocalControlActionError(
                "RELEASE_TREE_TOO_LARGE",
                "staged business release exceeds fixed limits",
            )


def _copy_safe_tree(
    source: Path,
    destination: Path,
    *,
    destination_uid: int,
    destination_gid: int,
    counter: _CopyCounter,
) -> None:
    source_fd = _open_directory(source)
    destination_fd = _open_directory(destination)
    try:
        _copy_directory_fd(
            source_fd,
            destination_fd,
            destination_uid=destination_uid,
            destination_gid=destination_gid,
            counter=counter,
        )
        os.fsync(destination_fd)
    finally:
        os.close(destination_fd)
        os.close(source_fd)


def _copy_directory_fd(
    source_fd: int,
    destination_fd: int,
    *,
    destination_uid: int,
    destination_gid: int,
    counter: _CopyCounter,
) -> None:
    for name in sorted(os.listdir(source_fd)):
        if name in {".", "..", RELEASE_MARKER} or "/" in name or "\x00" in name:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "staged business release contains a reserved entry",
            )
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            os.mkdir(name, 0o750, dir_fd=destination_fd)
            child_source_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=source_fd,
            )
            child_destination_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=destination_fd,
            )
            try:
                opened = os.fstat(child_source_fd)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise LocalControlActionError(
                        "RELEASE_TREE_CHANGED",
                        "staged business release changed during installation",
                    )
                os.fchmod(child_destination_fd, 0o750)
                os.fchown(
                    child_destination_fd,
                    destination_uid,
                    destination_gid,
                )
                _copy_directory_fd(
                    child_source_fd,
                    child_destination_fd,
                    destination_uid=destination_uid,
                    destination_gid=destination_gid,
                    counter=counter,
                )
                os.fsync(child_destination_fd)
            finally:
                os.close(child_destination_fd)
                os.close(child_source_fd)
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "staged business release contains a link or special file",
            )
        counter.add_file(metadata.st_size)
        source_file_fd = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_fd,
        )
        destination_file_fd: int | None = None
        try:
            opened = os.fstat(source_file_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino)
                != (metadata.st_dev, metadata.st_ino)
            ):
                raise LocalControlActionError(
                    "RELEASE_TREE_CHANGED",
                    "staged business release changed during installation",
                )
            destination_file_fd = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=destination_fd,
            )
            _copy_file_descriptors(source_file_fd, destination_file_fd)
            finished = os.fstat(source_file_fd)
            if (
                finished.st_size != metadata.st_size
                or finished.st_mtime_ns != metadata.st_mtime_ns
            ):
                raise LocalControlActionError(
                    "RELEASE_TREE_CHANGED",
                    "staged business release changed during installation",
                )
            destination_mode = 0o750 if metadata.st_mode & 0o111 else 0o640
            os.fchmod(destination_file_fd, destination_mode)
            os.fchown(destination_file_fd, destination_uid, destination_gid)
            os.fsync(destination_file_fd)
        finally:
            if destination_file_fd is not None:
                os.close(destination_file_fd)
            os.close(source_file_fd)


def _copy_file_descriptors(source_fd: int, destination_fd: int) -> None:
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            return
        offset = 0
        while offset < len(chunk):
            written = os.write(destination_fd, chunk[offset:])
            if written <= 0:
                raise OSError("business release copy made no progress")
            offset += written


def _remove_safe_tree(path: Path) -> None:
    parent = path.parent
    _require_real_directory(parent, "removal parent directory")
    parent_fd = _open_directory(parent)
    try:
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "fixed removal target is not a real directory",
            )
        child_fd = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise LocalControlActionError(
                    "RELEASE_TREE_CHANGED",
                    "fixed removal target changed during cleanup",
                )
            _remove_directory_contents_fd(child_fd)
        finally:
            os.close(child_fd)
        os.rmdir(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _remove_directory_contents_fd(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise LocalControlActionError(
                        "RELEASE_TREE_CHANGED",
                        "fixed removal tree changed during cleanup",
                    )
                _remove_directory_contents_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            os.unlink(name, dir_fd=directory_fd)
        else:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "fixed removal tree contains a link or special file",
            )


def _atomic_release_link(
    link_path: Path,
    release_root: Path,
    release_uid: str,
    update_uid: str,
) -> None:
    _require_real_directory(release_root / release_uid, "business release target")
    existing = _read_release_link(link_path, release_root)
    if existing == release_uid:
        return
    temporary = link_path.parent / f".{link_path.name}.{update_uid}.tmp"
    if temporary.exists() or temporary.is_symlink():
        metadata = temporary.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            raise LocalControlActionError(
                "RELEASE_LINK_INVALID",
                "business release link temporary path is unsafe",
            )
        temporary.unlink()
    relative_target = Path("releases") / release_uid
    os.symlink(str(relative_target), temporary)
    os.replace(temporary, link_path)
    _fsync_directory(link_path.parent)


def _read_release_link(link_path: Path, release_root: Path) -> str | None:
    try:
        metadata = link_path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(metadata.st_mode):
        raise LocalControlActionError(
            "RELEASE_LINK_INVALID",
            "business release selector is not a symbolic link",
        )
    target = os.readlink(link_path)
    parts = Path(target).parts
    if len(parts) != 2 or parts[0] != "releases":
        raise LocalControlActionError(
            "RELEASE_LINK_INVALID",
            "business release selector target is invalid",
        )
    release_uid = _require_uuid4(parts[1], "releaseUid")
    _require_real_directory(release_root / release_uid, "business release target")
    return release_uid


def _sqlite_backup(
    source: Path,
    destination: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    destination_descriptor: int | None = None
    try:
        destination_descriptor = os.open(
            destination,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        destination_identity = os.fstat(destination_descriptor)
        source_connection = sqlite3.connect(
            f"file:{source.as_posix()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        destination_connection = sqlite3.connect(
            f"file:/proc/self/fd/{destination_descriptor}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        destination_connection.execute("PRAGMA journal_mode=OFF")
        source_connection.backup(destination_connection)
        destination_connection.commit()
        os.fchmod(destination_descriptor, mode)
        os.fchown(destination_descriptor, uid, gid)
        os.fsync(destination_descriptor)
        current = destination.lstat()
        if (current.st_dev, current.st_ino) != (
            destination_identity.st_dev,
            destination_identity.st_ino,
        ):
            raise LocalControlActionError(
                "FIXED_PATH_CHANGED",
                "database backup destination changed during creation",
            )
    except sqlite3.Error as error:
        raise LocalControlActionError(
            "DATABASE_SNAPSHOT_FAILED",
            "fixed business database backup failed",
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _verify_sqlite_database(path: Path) -> None:
    metadata = _require_regular_file(path, "SQLite database")
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise LocalControlActionError(
                "FIXED_PATH_CHANGED",
                "fixed SQLite database changed before verification",
            )
        connection = sqlite3.connect(
            f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1",
            uri=True,
            timeout=5.0,
        )
        rows = connection.execute("PRAGMA quick_check").fetchall()
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise LocalControlActionError(
                "FIXED_PATH_CHANGED",
                "fixed SQLite database changed during verification",
            )
    except sqlite3.Error as error:
        raise LocalControlActionError(
            "DATABASE_INTEGRITY_FAILED",
            "fixed SQLite database integrity check failed",
        ) from error
    finally:
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)
    if rows != [("ok",)]:
        raise LocalControlActionError(
            "DATABASE_INTEGRITY_FAILED",
            "fixed SQLite database integrity check did not pass",
        )


def _write_fixed_json(
    path: Path,
    document: dict[str, Any],
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    payload = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        mode,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("business release marker write made no progress")
            offset += written
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_fixed_json(path: Path) -> dict[str, Any]:
    _require_regular_file(path, "business release marker")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LocalControlActionError(
                "RELEASE_TREE_INVALID",
                "installed business release marker is unsafe",
            )
        payload = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    if len(payload) > 4096:
        raise LocalControlActionError(
            "RELEASE_TREE_INVALID",
            "installed business release marker is too large",
        )
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalControlActionError(
            "RELEASE_TREE_INVALID",
            "installed business release marker is invalid",
        ) from error
    if not isinstance(result, dict):
        raise LocalControlActionError(
            "RELEASE_TREE_INVALID",
            "installed business release marker must be an object",
        )
    return result


def _require_real_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise LocalControlActionError(
            "FIXED_PATH_NOT_READY",
            f"{label} does not exist",
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise LocalControlActionError(
            "FIXED_PATH_UNSAFE",
            f"{label} is not a real directory",
        )
    return metadata


def _create_or_require_directory(
    path: Path,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        _require_real_directory(path, "fixed helper directory")
    os.chmod(path, mode)
    os.chown(path, uid, gid)


def _require_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise LocalControlActionError(
            "FIXED_PATH_NOT_READY",
            f"{label} does not exist",
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise LocalControlActionError(
            "FIXED_PATH_UNSAFE",
            f"{label} is not a single regular file",
        )
    return metadata


def _reject_existing_path(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        raise LocalControlActionError(
            "FIXED_PATH_CONFLICT",
            f"{label} already exists",
        )


def _unlink_regular_if_present(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise LocalControlActionError(
            "FIXED_PATH_UNSAFE",
            f"{label} is not a single regular file",
        )
    path.unlink()


def _open_directory(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LocalControlActionError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise LocalControlActionError(
            "REQUEST_INVALID",
            f"{field} must be a UUIDv4",
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise LocalControlActionError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    return value

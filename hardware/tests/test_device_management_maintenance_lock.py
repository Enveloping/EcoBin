from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from system.device_management_maintenance_installer import (
    MaintenanceInstallError,
    _LiveLock,
    _prepare_live_lock_directory,
)


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="the live maintenance lock is a Linux-root boundary",
)


def test_live_lock_creates_private_persistent_inode_and_excludes_peer(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "run/ecobin-device-management-maintenance/installer.lock"
    lock.parent.parent.mkdir(mode=0o755)
    _prepare_live_lock_directory(lock)

    with _LiveLock(lock):
        parent = lock.parent.lstat()
        details = lock.lstat()
        assert stat.S_ISDIR(parent.st_mode)
        assert stat.S_IMODE(parent.st_mode) == 0o700
        assert (parent.st_uid, parent.st_gid) == (0, 0)
        assert stat.S_ISREG(details.st_mode)
        assert stat.S_IMODE(details.st_mode) == 0o600
        assert details.st_nlink == 1
        assert (details.st_uid, details.st_gid) == (0, 0)
        with pytest.raises(MaintenanceInstallError, match="another maintenance"):
            with _LiveLock(lock):
                pass

    assert lock.is_file()
    with _LiveLock(lock):
        pass


@pytest.mark.parametrize("unsafe_kind", ("symlink", "hardlink", "mode", "owner"))
def test_live_lock_rejects_unsafe_existing_inode(
    tmp_path: Path, unsafe_kind: str
) -> None:
    lock = tmp_path / "run/ecobin-device-management-maintenance/installer.lock"
    lock.parent.parent.mkdir(mode=0o755)
    _prepare_live_lock_directory(lock)
    target = tmp_path / "target"
    target.write_bytes(b"")
    os.chmod(target, 0o600)
    if unsafe_kind == "symlink":
        lock.symlink_to(target)
    elif unsafe_kind == "hardlink":
        os.link(target, lock)
    else:
        lock.write_bytes(b"")
        os.chmod(lock, 0o644 if unsafe_kind == "mode" else 0o600)
        if unsafe_kind == "owner":
            os.chown(lock, 65534, 65534)

    with pytest.raises(MaintenanceInstallError, match="lock (path|file metadata)"):
        with _LiveLock(lock):
            pass


def test_live_lock_directory_rejects_symlink_and_broad_permissions(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked_lock = tmp_path / "linked/installer.lock"
    linked_lock.parent.symlink_to(real, target_is_directory=True)
    with pytest.raises(MaintenanceInstallError, match="directory metadata"):
        _prepare_live_lock_directory(linked_lock)

    broad_lock = tmp_path / "broad/installer.lock"
    broad_lock.parent.mkdir(mode=0o700)
    os.chmod(broad_lock.parent, 0o755)
    with pytest.raises(MaintenanceInstallError, match="directory metadata"):
        _prepare_live_lock_directory(broad_lock)

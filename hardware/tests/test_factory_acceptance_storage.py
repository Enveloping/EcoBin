from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from factory.acceptance_storage import (
    AcceptanceStorageError,
    AtomicJsonFile,
    ExclusiveFileLock,
)


class PowerLoss(BaseException):
    pass


def test_atomic_json_keeps_previous_commit_when_crash_precedes_replace(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "private" / "state.json").absolute()
    stable = AtomicJsonFile(path)
    stable.write({"revision": 1, "phase": "SAFE"})

    def fault(point: str) -> None:
        if point == "state.after_file_fsync":
            raise PowerLoss()

    crashing = AtomicJsonFile(
        path,
        fault_hook=fault,
        fault_prefix="state",
    )
    with pytest.raises(PowerLoss):
        crashing.write({"revision": 2, "phase": "MAY_HAVE_BEEN_SENT"})
    assert stable.read() == {"revision": 1, "phase": "SAFE"}
    assert not list(path.parent.glob("*.tmp"))


def test_atomic_json_is_never_partial_when_crash_follows_replace(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "private" / "state.json").absolute()
    stable = AtomicJsonFile(path)
    stable.write({"revision": 1})

    def fault(point: str) -> None:
        if point == "state.after_replace":
            raise PowerLoss()

    crashing = AtomicJsonFile(
        path,
        fault_hook=fault,
        fault_prefix="state",
    )
    with pytest.raises(PowerLoss):
        crashing.write({"revision": 2, "nested": {"valid": True}})
    assert json.loads(path.read_text("utf-8")) == {
        "nested": {"valid": True},
        "revision": 2,
    }


def test_atomic_json_is_private_on_posix(tmp_path: Path) -> None:
    path = (tmp_path / "private" / "report.json").absolute()
    document = AtomicJsonFile(path)
    document.write({"status": "PASSED"})
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_atomic_json_can_preserve_an_existing_shared_parent_mode(
    tmp_path: Path,
) -> None:
    shared = (tmp_path / "shared-ecobin-parent").absolute()
    shared.mkdir()
    if os.name != "nt":
        shared.chmod(0o755)
    path = shared / "device-capabilities.json"

    AtomicJsonFile(
        path,
        chmod_existing_parent=False,
    ).write({"schemaVersion": 1})

    assert AtomicJsonFile(
        path,
        chmod_existing_parent=False,
    ).read() == {"schemaVersion": 1}
    if os.name != "nt":
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership bits are required")
def test_atomic_json_can_publish_a_group_read_only_runtime_fact(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "shared" / "device-capabilities.json").absolute()

    document = AtomicJsonFile(
        path,
        file_mode=0o640,
        owner_uid=os.geteuid(),
        owner_gid=os.getegid(),
    )
    document.write({"schemaVersion": 1})

    metadata = path.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o640
    assert metadata.st_uid == os.geteuid()
    assert metadata.st_gid == os.getegid()
    assert document.read() == {"schemaVersion": 1}


def test_malformed_or_non_object_json_is_rejected(tmp_path: Path) -> None:
    path = (tmp_path / "private" / "state.json").absolute()
    path.parent.mkdir()
    path.write_text("[]", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    with pytest.raises(AcceptanceStorageError, match="root must be an object"):
        AtomicJsonFile(path).read()


def test_symlink_target_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(AcceptanceStorageError, match="symlink"):
        AtomicJsonFile(link.absolute()).read()


def test_relative_state_or_report_path_is_rejected() -> None:
    with pytest.raises(AcceptanceStorageError, match="must be absolute"):
        AtomicJsonFile(Path("relative-state.json"))


def test_lock_does_not_chmod_an_existing_shared_parent(tmp_path: Path) -> None:
    shared = (tmp_path / "shared-lock-parent").absolute()
    shared.mkdir()
    if os.name != "nt":
        shared.chmod(0o755)
    lock = ExclusiveFileLock(shared / "factory.lock")
    with lock:
        assert lock.acquired is True
    if os.name != "nt":
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755

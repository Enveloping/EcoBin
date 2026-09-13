"""Test-only capture must not reopen, change or recover the failing database."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from hardware.tests.sqlite_failure_evidence import capture_failure_evidence


def test_default_evidence_is_outside_the_disposable_test_session(tmp_path):
    db = tmp_path / "edge.db"
    db.write_bytes(b"synthetic failure evidence")
    result = capture_failure_evidence(db, sqlite3.OperationalError("synthetic failure"), child_returncode=1)
    destination = Path(result["directory"])
    assert not destination.is_relative_to(tmp_path.parent)
    assert (destination / "edge.db").read_bytes() == db.read_bytes()


def test_separate_failures_never_overwrite_an_earlier_capture(tmp_path):
    db = tmp_path / "edge.db"
    error = sqlite3.OperationalError("synthetic failure")
    db.write_bytes(b"first failure")
    first = capture_failure_evidence(db, error, child_returncode=1, evidence_root=tmp_path / "evidence")
    db.write_bytes(b"second failure")
    second = capture_failure_evidence(db, error, child_returncode=1, evidence_root=tmp_path / "evidence")
    assert first["directory"] != second["directory"]
    assert (Path(first["directory"]) / "edge.db").read_bytes() == b"first failure"
    assert (Path(second["directory"]) / "edge.db").read_bytes() == b"second failure"


def test_temp_directory_failure_does_not_replace_original_sqlite_error(tmp_path, monkeypatch):
    from hardware.tests import sqlite_failure_evidence as capture

    def denied():
        raise PermissionError(13, "synthetic temp directory unavailable")

    db = tmp_path / "edge.db"
    db.write_bytes(b"original")
    error = sqlite3.OperationalError("original SQLite failure")
    error.sqlite_errorcode = 1546
    monkeypatch.setattr(capture.tempfile, "gettempdir", denied)
    result = capture_failure_evidence(db, error, child_returncode=1)
    assert result["sqlite_code"] == 1546 and result["directory_error"]["errno"] == 13
    assert db.read_bytes() == b"original"


def test_capture_preserves_all_observed_files_and_original_failure(tmp_path):
    db = tmp_path / "edge.db"
    originals = {db.name + suffix: (b"synthetic evidence" + suffix.encode()) * 3
        for suffix in ("", "-wal", "-shm")}
    for name, content in originals.items():
        (tmp_path / name).write_bytes(content)
    error = sqlite3.OperationalError("synthetic truncate failure")
    error.sqlite_errorcode = 1546
    error.sqlite_errorname = "SQLITE_IOERR_TRUNCATE"
    result = capture_failure_evidence(db, error, child_returncode=1, evidence_root=tmp_path / "evidence")
    assert result["sqlite_code"] == 1546 and result["child_returncode"] == 1
    for name, content in originals.items():
        assert (tmp_path / name).read_bytes() == content
        assert (Path(result["directory"]) / name).read_bytes() == content
        assert result["files"][name]["copied_sha256"] == hashlib.sha256(content).hexdigest()
        assert result["files"][name]["size_before"] == result["files"][name]["size_after"] == len(content)
    assert json.loads((Path(result["directory"]) / "manifest.json").read_text()) == result


def test_missing_wal_is_recorded_without_creating_or_reopening_it(tmp_path):
    db = tmp_path / "edge.db"
    db.write_bytes(b"original")
    result = capture_failure_evidence(db, sqlite3.OperationalError("synthetic"), child_returncode=1, evidence_root=tmp_path / "evidence")
    assert result["files"]["edge.db"]["copied_size"] == 8
    assert result["files"]["edge.db-wal"]["capture_error"]["type"] == "FileNotFoundError"
    assert not (tmp_path / "edge.db-wal").exists()


@pytest.mark.parametrize("failure", ["directory", "copy", "manifest"])
def test_evidence_write_failure_is_returned_without_replacing_sqlite_error(tmp_path, monkeypatch, failure):
    from hardware.tests import sqlite_failure_evidence as capture

    def denied(*args, **kwargs):
        raise PermissionError(13, "synthetic evidence write denied")

    db = tmp_path / "edge.db"
    db.write_bytes(b"original")
    if failure == "directory":
        monkeypatch.setattr(Path, "mkdir", denied)
    elif failure == "copy":
        monkeypatch.setattr(capture.shutil, "copyfile", denied)
    else:
        monkeypatch.setattr(Path, "write_text", denied)
    error = sqlite3.OperationalError("original error")
    error.sqlite_errorcode = 1546
    result = capture_failure_evidence(db, error, child_returncode=1, evidence_root=tmp_path / "evidence")
    assert result["sqlite_code"] == 1546 and db.read_bytes() == b"original"
    detail = result["files"]["edge.db"]["capture_error"] if failure == "copy" else result[failure + "_error"]
    assert detail["errno"] == 13

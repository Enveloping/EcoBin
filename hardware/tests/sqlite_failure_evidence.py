"""Synthetic-test diagnostics only; never open SQLite or recover the source.

Copies are sequential observations AFTER an error, not an atomic/pre-error
snapshot. File-size observations cannot rule out concurrent same-size writes.
Evidence errors are recorded so they never replace the original SQLite failure.
"""
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path


def _os_error(exc: OSError) -> dict:
    return {"type": type(exc).__name__, "errno": exc.errno,
        "winerror": getattr(exc, "winerror", None)}


def capture_failure_evidence(db_path: Path, error: sqlite3.Error, *, child_returncode: int,
        evidence_root: Path | None = None) -> dict:
    # Keep real failure copies outside pytest's numbered/automatically retained
    # session directories. Explicit roots let capture-unit tests stay isolated.
    # These are synthetic diagnostics, not permanent production archives; OS or
    # operator cleanup of the system temp directory can still remove them.
    result = {"sqlite_code": getattr(error, "sqlite_errorcode", None),
        "sqlite_name": getattr(error, "sqlite_errorname", None),
        "sqlite_version": sqlite3.sqlite_version, "python_version": sys.version,
        "platform": sys.platform, "child_returncode": child_returncode,
        "capture_kind": "sequential-post-error-file-copies", "directory": None, "files": {}}
    try:
        root = evidence_root if evidence_root is not None else Path(tempfile.gettempdir()) / "ecobin-sqlite-failure-evidence"
        destination = root / uuid.uuid4().hex
        result["directory"] = str(destination)
        destination.mkdir(parents=True)  # Never overwrite an earlier failure's evidence.
    except OSError as exc:
        result["directory_error"] = _os_error(exc)
        return result
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(db_path) + suffix)
        entry = result["files"][source.name] = {}
        try:
            entry["size_before"] = source.stat().st_size
            copied = destination / source.name
            shutil.copyfile(source, copied)
            entry["copied_size"] = copied.stat().st_size
            entry["copied_sha256"] = hashlib.sha256(copied.read_bytes()).hexdigest()
            entry["size_after"] = source.stat().st_size
        except OSError as exc:
            entry["capture_error"] = _os_error(exc)
    try:
        (destination / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        result["manifest_error"] = _os_error(exc)
    return result

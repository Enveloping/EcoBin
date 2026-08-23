#!/usr/bin/env python3
"""Create the exact inventory lock for a prebuilt EcoBin software payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
from typing import Any


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "hardware/system"))
from image_software_installer import (  # noqa: E402
    RUNTIME_APP_FILES,
    ImageSoftwareError,
    load_and_validate_payload,
)


RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
LOCK_NAME = "software-payload.lock.json"


class PayloadLockError(RuntimeError):
    pass


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: pathlib.Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pending = [root]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            relative = child.relative_to(root).as_posix()
            if relative == LOCK_NAME:
                continue
            details = child.lstat()
            mode = stat.S_IMODE(details.st_mode)
            entry: dict[str, Any] = {"path": relative, "mode": f"{mode:04o}"}
            if stat.S_ISDIR(details.st_mode):
                if os.name == "posix" and mode & 0o022:
                    raise PayloadLockError(f"payload entry is group/world writable: {relative}")
                entry["type"] = "directory"
                pending.append(child)
            elif stat.S_ISREG(details.st_mode):
                if os.name == "posix" and mode & 0o022:
                    raise PayloadLockError(f"payload entry is group/world writable: {relative}")
                if details.st_nlink != 1:
                    raise PayloadLockError(f"payload file is hard-linked: {relative}")
                entry.update(
                    type="file",
                    size=details.st_size,
                    sha256=_sha256(child),
                )
            elif stat.S_ISLNK(details.st_mode):
                entry.update(type="symlink", target=os.readlink(child))
            else:
                raise PayloadLockError(f"payload contains a special entry: {relative}")
            entries.append(entry)
    return sorted(entries, key=lambda entry: entry["path"])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-dir", required=True, type=pathlib.Path)
    parser.add_argument("--payload-id", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--hardware-runtime-release-id", required=True)
    parser.add_argument("--enrollment-release-id", required=True)
    parser.add_argument("--remote-support-release-id", required=True)
    parser.add_argument("--factory-test-release-id", required=True)
    parser.add_argument("--first-boot-release-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = args.payload_dir.resolve(strict=True)
        if args.payload_dir.is_symlink() or not payload.is_dir():
            raise PayloadLockError("payload directory is unsafe")
        lock_path = payload / LOCK_NAME
        if os.path.lexists(lock_path):
            raise PayloadLockError("payload lock already exists and will not be overwritten")
        identities = {
            "hardwareRuntime": args.hardware_runtime_release_id,
            "enrollment": args.enrollment_release_id,
            "remoteSupport": args.remote_support_release_id,
            "factoryTest": args.factory_test_release_id,
            "firstBoot": args.first_boot_release_id,
        }
        if not RELEASE_ID.fullmatch(args.payload_id):
            raise PayloadLockError("payload ID is invalid")
        if not GIT_COMMIT.fullmatch(args.git_commit):
            raise PayloadLockError("Git commit is invalid")
        for name, release_id in identities.items():
            if not RELEASE_ID.fullmatch(release_id):
                raise PayloadLockError(f"component release ID is invalid: {name}")
        document = {
            "schemaVersion": 1,
            "lockState": "LOCKED",
            "payloadId": args.payload_id,
            "sourceGitCommit": args.git_commit,
            "components": {
                "hardwareRuntime": {
                    "releaseId": identities["hardwareRuntime"],
                    "root": "components/hardware-runtime",
                },
                "enrollment": {
                    "releaseId": identities["enrollment"],
                    "venv": "components/enrollment-venv",
                },
                "remoteSupport": {
                    "releaseId": identities["remoteSupport"],
                    "venv": "components/remote-support-venv",
                },
                "factoryTest": {
                    "releaseId": identities["factoryTest"],
                    "venv": "components/factory-test-venv",
                },
                "firstBoot": {"releaseId": identities["firstBoot"]},
            },
            "entries": _inventory(payload),
        }
        lock_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.chmod(lock_path, 0o644)
        digest = _sha256(lock_path)
        load_and_validate_payload(
            payload,
            expected_sha256=digest,
            expected_git_commit=args.git_commit,
        )
        runtime_app = payload / "components/hardware-runtime/app"
        actual_runtime_files = {
            path.relative_to(runtime_app).as_posix()
            for path in runtime_app.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if actual_runtime_files != set(RUNTIME_APP_FILES):
            raise PayloadLockError("hardware runtime app allowlist is not exact")
        for name in RUNTIME_APP_FILES:
            if (
                (runtime_app / name).read_bytes()
                != (REPOSITORY_ROOT / "hardware" / name).read_bytes()
            ):
                raise PayloadLockError(
                    "hardware runtime source differs from the repository commit"
                )
    except (OSError, PayloadLockError, ImageSoftwareError) as exc:
        if "lock_path" in locals() and os.path.lexists(lock_path):
            lock_path.unlink()
        print(f"software-payload-lock=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"software-payload-lock=PASS sha256={digest} path={lock_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

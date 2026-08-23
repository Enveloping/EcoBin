#!/usr/bin/env python3
"""Capture and compare a root filesystem without following mounted-tree links."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import sys
from typing import Any


class InventoryError(RuntimeError):
    pass


_STRUCTURAL_EXT_FLAGS = 0x00001000 | 0x00080000  # INDEX, EXTENTS


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _xattrs(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError as exc:
        raise InventoryError(f"cannot list xattrs for {path}: {exc}") from exc
    for name in sorted(names):
        try:
            result[name] = os.getxattr(path, name, follow_symlinks=False).hex()
        except OSError as exc:
            raise InventoryError(f"cannot read xattr {name!r} for {path}: {exc}") from exc
    return result


def _inode_policy(path: pathlib.Path, metadata: os.stat_result) -> tuple[int, int]:
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        return 0, 0
    try:
        import array
        import fcntl

        flags = array.array("L", [0])
        fsx = bytearray(28)
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            fcntl.ioctl(descriptor, 0x80086601, flags, True)  # FS_IOC_GETFLAGS
            try:
                fcntl.ioctl(descriptor, 0x801c581f, fsx, True)  # FS_IOC_FSGETXATTR
                project_id = int.from_bytes(fsx[12:16], sys.byteorder)
            except OSError:
                project_id = 0
        finally:
            os.close(descriptor)
    except (ImportError, OSError) as exc:
        raise InventoryError(f"cannot inspect inode flags/project ID for {path}: {exc}") from exc
    unsupported = int(flags[0]) & ~_STRUCTURAL_EXT_FLAGS
    if unsupported:
        raise InventoryError(
            f"unsupported persistent inode flags 0x{unsupported:x} on {path}; "
            "e2fsprogs 1.47.0 mke2fs -d cannot preserve them"
        )
    if project_id:
        raise InventoryError(f"non-zero project ID {project_id} on {path} is unsupported")
    return int(flags[0]) & _STRUCTURAL_EXT_FLAGS, project_id


def _kind(mode: int) -> str:
    for predicate, name in (
        (stat.S_ISREG, "file"), (stat.S_ISDIR, "directory"),
        (stat.S_ISLNK, "symlink"), (stat.S_ISFIFO, "fifo"),
        (stat.S_ISCHR, "char"), (stat.S_ISBLK, "block"),
        (stat.S_ISSOCK, "socket"),
    ):
        if predicate(mode):
            return name
    raise InventoryError(f"unsupported inode type: 0o{mode:o}")


def capture(root: pathlib.Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir() or root == pathlib.Path(root.anchor):
        raise InventoryError("root must be a non-symlink staging directory, never a filesystem root")
    entries: list[dict[str, Any]] = []
    pending = [root]
    while pending:
        path = pending.pop()
        metadata = path.lstat()
        relative = "/" if path == root else "/" + path.relative_to(root).as_posix()
        kind = _kind(metadata.st_mode)
        structural_flags, project_id = _inode_policy(path, metadata)
        entry: dict[str, Any] = {
            "path": relative,
            "kind": kind,
            "inode": metadata.st_ino,
            "device": metadata.st_dev,
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "size": metadata.st_size,
            "links": metadata.st_nlink,
            "xattrs": _xattrs(path),
            "structuralFlags": structural_flags,
            "projectId": project_id,
            "atimeNs": metadata.st_atime_ns,
            "mtimeNs": metadata.st_mtime_ns,
            "ctimeNs": metadata.st_ctime_ns,
        }
        if kind == "file":
            entry["sha256"] = _sha256(path)
            entry["sparse"] = metadata.st_size > 0 and metadata.st_blocks * 512 < metadata.st_size
        elif kind == "symlink":
            entry["target"] = os.readlink(path)
        elif kind in {"char", "block"}:
            entry["rdevMajor"] = os.major(metadata.st_rdev)
            entry["rdevMinor"] = os.minor(metadata.st_rdev)
        entries.append(entry)
        if kind == "directory":
            with os.scandir(path) as scan:
                children = sorted((path / item.name for item in scan), key=lambda p: os.fsencode(p.name), reverse=True)
            pending.extend(children)
    entries.sort(key=lambda item: os.fsencode(item["path"]))
    link_groups: dict[tuple[int, int], list[str]] = {}
    for entry in entries:
        if entry["kind"] != "directory" and entry["links"] > 1:
            link_groups.setdefault((entry["device"], entry["inode"]), []).append(entry["path"])
    groups = sorted((sorted(paths) for paths in link_groups.values()), key=lambda value: value[0])
    return {"schemaVersion": 1, "entries": entries, "hardlinkGroups": groups}


def semantic_projection(value: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for original in value.get("entries", []):
        entry = {key: item for key, item in original.items() if key not in {"inode", "device", "links", "atimeNs", "mtimeNs", "ctimeNs", "structuralFlags", "sparse"}}
        if entry.get("kind") not in {"file", "symlink"}:
            entry.pop("size", None)
        entries.append(entry)
    return {"schemaVersion": 1, "entries": entries, "hardlinkGroups": value.get("hardlinkGroups", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--root", type=pathlib.Path, required=True)
    capture_parser.add_argument("--output", type=pathlib.Path, required=True)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--expected", type=pathlib.Path, required=True)
    compare_parser.add_argument("--actual", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            if args.output.exists():
                raise InventoryError("inventory output already exists")
            value = capture(args.root)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
                stream.write("\n")
        else:
            expected = json.loads(args.expected.read_text(encoding="utf-8"))
            actual = json.loads(args.actual.read_text(encoding="utf-8"))
            if semantic_projection(expected) != semantic_projection(actual):
                raise InventoryError("rebuilt rootfs semantic inventory differs from the staged source")
            actual_by_path = {entry["path"]: entry for entry in actual.get("entries", [])}
            for entry in expected.get("entries", []):
                if entry.get("kind") == "file" and entry.get("sparse") is True:
                    if actual_by_path.get(entry["path"], {}).get("sparse") is not True:
                        raise InventoryError(
                            f"rebuilt rootfs expanded sparse file: {entry['path']}"
                        )
    except (OSError, UnicodeError, json.JSONDecodeError, InventoryError) as exc:
        print(f"rootfs-inventory=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"rootfs-inventory=PASS command={args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

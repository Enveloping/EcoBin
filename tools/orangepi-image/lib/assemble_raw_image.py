#!/usr/bin/env python3
"""Assemble every byte of a compact raw image from a locked prefix and root partition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import sys


def _regular_single(path: pathlib.Path, label: str) -> os.stat_result:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError(f"{label} must be a regular single-link file")
    return value


def _copy_exact(source, target, count: int, digest=None) -> None:
    remaining = count
    while remaining:
        block = source.read(min(1024 * 1024, remaining))
        if not block:
            raise ValueError("input ended before the locked byte boundary")
        target.write(block)
        if digest is not None:
            digest.update(block)
        remaining -= len(block)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", type=pathlib.Path, required=True)
    parser.add_argument("--root-partition", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--layout", type=pathlib.Path, required=True)
    args = parser.parse_args()
    descriptor = None
    created_output = False
    try:
        source_metadata = _regular_single(args.source_image, "source image")
        root_metadata = _regular_single(args.root_partition, "root partition")
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        geometry = layout["sourceGeometry"]
        prefix_bytes = int(geometry["bootPrefixBytes"])
        expected_root_bytes = int(geometry["rootPartition"]["sectorCount"]) * int(geometry["logicalSectorBytes"])
        expected_total = int(layout["compactImage"]["fixedRawImageBytes"])
        if source_metadata.st_size != expected_total or root_metadata.st_size != expected_root_bytes or prefix_bytes + expected_root_bytes != expected_total:
            raise ValueError("input sizes do not cover the locked raw image exactly")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(args.output, flags, 0o600)
        created_output = True
        prefix_digest = hashlib.sha256()
        with args.source_image.open("rb", buffering=0) as source, os.fdopen(descriptor, "wb", buffering=0) as output, args.root_partition.open("rb", buffering=0) as root:
            descriptor = None
            _copy_exact(source, output, prefix_bytes, prefix_digest)
            if prefix_digest.hexdigest() != geometry["bootPrefixSha256"]:
                raise ValueError("source boot prefix SHA-256 differs from the lock")
            _copy_exact(root, output, expected_root_bytes)
            if root.read(1):
                raise ValueError("root partition contains trailing bytes")
            output.flush()
            os.fsync(output.fileno())
        if args.output.stat().st_size != expected_total:
            raise ValueError("assembled raw image length differs from the lock")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if created_output:
            try:
                args.output.unlink()
            except OSError:
                pass
        print(f"raw-image-assembly=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"raw-image-assembly=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify a raw DOS image against the byte and partition geometry lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys


def hash_range(path: pathlib.Path, count: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        remaining = count
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("raw image ended inside the requested range")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--layout", type=pathlib.Path, required=True)
    parser.add_argument("--require-source-sha", action="store_true")
    args = parser.parse_args()
    try:
        if not args.image.is_file() or args.image.is_symlink():
            raise ValueError("image must be a regular non-symlink file")
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        geometry = layout["sourceGeometry"]
        partition = geometry["rootPartition"]
        if args.image.stat().st_size != geometry["rawImageBytes"]:
            raise ValueError("raw image length differs from source geometry")
        if hash_range(args.image, geometry["bootPrefixBytes"]) != geometry["bootPrefixSha256"]:
            raise ValueError("boot prefix SHA-256 differs from source geometry")
        if args.require_source_sha and hash_range(args.image, geometry["rawImageBytes"]) != geometry["rawImageSha256"]:
            raise ValueError("raw source SHA-256 differs from source geometry")
        result = subprocess.run(["sfdisk", "--json", str(args.image)], capture_output=True, text=True, encoding="utf-8", check=False)
        if result.returncode:
            raise RuntimeError("sfdisk could not read the locked partition table")
        table = json.loads(result.stdout)["partitiontable"]
        partitions = table["partitions"]
        if table.get("label") != "dos" or str(table.get("id", "")).lower().removeprefix("0x") != geometry["diskIdentifier"]:
            raise ValueError("DOS partition-table identity differs")
        if table.get("sectorsize", 512) != geometry["logicalSectorBytes"] or len(partitions) != partition["number"]:
            raise ValueError("partition count/sector size differs")
        root = partitions[partition["number"] - 1]
        if root.get("start") != partition["startSector"] or root.get("size") != partition["sectorCount"] or str(root.get("type", "")).lower().removeprefix("0x") != partition["typeCode"]:
            raise ValueError("root partition geometry/type differs")
        if (root["start"] + root["size"]) * geometry["logicalSectorBytes"] != geometry["rawImageBytes"]:
            raise ValueError("root partition does not end at raw-image EOF")
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"raw-layout-verification=FAIL: {exc}", file=sys.stderr)
        return 2
    print("raw-layout-verification=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

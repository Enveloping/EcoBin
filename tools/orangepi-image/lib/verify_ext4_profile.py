#!/usr/bin/env python3
"""Fail closed unless an ext4 image has the exact locked construction profile."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import os
import time


def _size_bytes(value: str) -> int:
    suffixes = {"k": 1024, "M": 1024**2, "G": 1024**3}
    if value and value[-1] in suffixes:
        return int(value[:-1]) * suffixes[value[-1]]
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=pathlib.Path, required=True)
    parser.add_argument("--layout", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        root = layout["rootFilesystem"]
        profile = root["buildProfile"]
        environment = os.environ.copy()
        environment.update({"LC_ALL": "C", "TZ": "UTC"})
        result = subprocess.run(["dumpe2fs", "-h", str(args.device)], capture_output=True, text=True, encoding="utf-8", check=False, env=environment)
        if result.returncode:
            raise RuntimeError("dumpe2fs failed")
        fields = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        exact = {
            "Filesystem volume name": root["filesystemLabel"], "Filesystem UUID": root["filesystemUuid"],
            "Block count": str(profile["blockCount"]), "Reserved block count": str(profile["reservedBlockCount"]),
            "Block size": str(profile["blockSize"]), "Blocks per group": str(profile["blocksPerGroup"]),
            "Inode count": str(profile["inodeCount"]), "Inode size": str(profile["inodeSize"]),
            "Inodes per group": str(profile["inodesPerGroup"]), "Inode blocks per group": str(profile["inodeBlocksPerGroup"]),
            "Flex block group size": str(profile["flexBlockGroupSize"]), "Directory Hash Seed": profile["directoryHashSeed"],
            "Default directory hash": profile["directoryHashAlgorithm"], "Errors behavior": profile["errorBehavior"],
            "Maximum mount count": str(profile["maximumMountCount"]), "Check interval": str(profile["checkIntervalSeconds"]) + " (<none>)",
            "Filesystem state": "clean", "Filesystem OS type": "Linux",
        }
        differences = [f"{key}={fields.get(key)!r}" for key, value in exact.items() if fields.get(key) != value]
        if set(fields.get("Filesystem features", "").split()) != set(profile["features"]):
            differences.append("Filesystem features")
        if set(fields.get("Default mount options", "").split()) != set(profile["defaultMountOptions"]):
            differences.append("Default mount options")
        if set(fields.get("Filesystem flags", "").split()) != set(profile["filesystemFlags"]):
            differences.append("Filesystem flags")
        if _size_bytes(fields.get("Total journal size", "0")) != profile["journalSizeBytes"]:
            differences.append("Total journal size")
        expected_time = time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(profile["sourceDateEpoch"]))
        for key in ("Filesystem created", "Last write time", "Last checked"):
            if fields.get(key) != expected_time:
                differences.append(key)
        if differences:
            raise ValueError("ext4 profile differs: " + ", ".join(differences))
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ext4-profile-verification=FAIL: {exc}", file=sys.stderr)
        return 2
    print("ext4-profile-verification=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

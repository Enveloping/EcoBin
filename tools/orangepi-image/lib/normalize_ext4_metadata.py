#!/usr/bin/env python3
"""Generate and verify a path-free debugfs inode timestamp normalization batch."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys


FIELDS = ("atime", "mtime", "ctime", "crtime")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=pathlib.Path, required=True)
    parser.add_argument("--inventory", type=pathlib.Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.epoch < 0 or not args.device.is_file() or args.device.is_symlink():
            raise ValueError("device must be a regular non-symlink partition image and epoch non-negative")
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        inodes = sorted({int(entry["inode"]) for entry in inventory["entries"]})
        if not inodes or any(inode <= 0 for inode in inodes):
            raise ValueError("inventory has invalid inode identities")
        commands = []
        for inode in inodes:
            for field in FIELDS:
                commands.append(f"set_inode_field <{inode}> {field} {args.epoch}")
                commands.append(f"set_inode_field <{inode}> {field}_extra 0")
        commands.extend(f"stat <{inode}>" for inode in inodes)
        deterministic_environment = os.environ.copy()
        deterministic_environment.update({"LC_ALL": "C", "TZ": "UTC", "E2FSPROGS_FAKE_TIME": str(args.epoch)})
        result = subprocess.run(
            ["debugfs", "-w", "-f", "-", str(args.device)], input="\n".join(commands) + "\n",
            text=True, encoding="utf-8", capture_output=True, check=False, env=deterministic_environment,
        )
        combined = result.stdout + result.stderr
        if result.returncode or "Command not found" in combined or "File not found" in combined:
            raise RuntimeError("debugfs normalization failed: " + combined[-2000:])
        # A second, read-only batch is parsed to prove all fields and extra bits.
        verify = subprocess.run(
            ["debugfs", "-f", "-", str(args.device)], input="\n".join(f"stat <{inode}>" for inode in inodes) + "\n",
            text=True, encoding="utf-8", capture_output=True, check=False, env=deterministic_environment,
        )
        if verify.returncode:
            raise RuntimeError("debugfs verification failed")
        expected_hex = f"0x{args.epoch:08x}:00000000"
        for field in ("atime", "mtime", "ctime", "crtime"):
            if verify.stdout.count(f"{field}:") != len(inodes) or verify.stdout.count(expected_hex) < len(inodes) * 4:
                raise RuntimeError("normalized inode timestamp verification failed")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ext4-normalization=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"ext4-normalization=PASS inodes={len(inodes)} epoch={args.epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

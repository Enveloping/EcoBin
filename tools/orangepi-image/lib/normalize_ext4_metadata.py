#!/usr/bin/env python3
"""Generate and verify a path-free debugfs inode timestamp normalization batch."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys


FIELDS = ("atime", "mtime", "ctime", "crtime")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=pathlib.Path, required=True)
    parser.add_argument("--inventory", type=pathlib.Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--allow-block-device", action="store_true")
    args = parser.parse_args()
    try:
        device_stat = args.device.lstat()
        supported_device = stat.S_ISREG(device_stat.st_mode) or (
            args.allow_block_device and stat.S_ISBLK(device_stat.st_mode)
        )
        if (
            args.epoch < 0
            or args.epoch > 0x7FFFFFFF
            or args.device.is_symlink()
            or not supported_device
        ):
            raise ValueError(
                "device must be a regular non-symlink partition image (or an "
                "explicitly allowed block device) and epoch must fit ext4 "
                "epoch-zero timestamps"
            )
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        inodes = sorted({int(entry["inode"]) for entry in inventory["entries"]})
        if not inodes or any(inode <= 0 for inode in inodes):
            raise ValueError("inventory has invalid inode identities")
        timestamp_hex = f"0x{args.epoch:08x}"
        commands = []
        for inode in inodes:
            for field in FIELDS:
                # debugfs treats a bare decimal time value as a date expression.
                # An explicit hexadecimal timestamp avoids locale/date parsing.
                commands.append(f"set_inode_field <{inode}> {field} {timestamp_hex}")
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
        expected_hex = f"{timestamp_hex}:00000000"
        for field in ("atime", "mtime", "ctime", "crtime"):
            if verify.stdout.count(f"{field}: {expected_hex}") != len(inodes):
                raise RuntimeError(
                    f"normalized inode {field} timestamp verification failed"
                )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ext4-normalization=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"ext4-normalization=PASS inodes={len(inodes)} epoch={args.epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

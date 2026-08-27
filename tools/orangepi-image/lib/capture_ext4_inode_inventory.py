#!/usr/bin/env python3
"""Capture only inode numbers from one mounted ext4 tree.

The sealing flow needs every allocated path inode so it can restore the locked
timestamp profile after injecting factory secrets.  This intentionally records
neither paths nor file contents, so the temporary inventory cannot contain K1,
the setup access-point passphrase, or a digest of either secret.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import stat
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        root_stat = args.root.lstat()
        if args.root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("root must be a mounted non-symlink directory")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("output already exists")

        inodes = {root_stat.st_ino}
        root_device = root_stat.st_dev
        for directory, directory_names, file_names in os.walk(
            args.root, topdown=True, followlinks=False
        ):
            directory_path = pathlib.Path(directory)
            for name in directory_names + file_names:
                item_stat = os.lstat(directory_path / name)
                if item_stat.st_dev != root_device:
                    raise ValueError("mounted tree crosses a filesystem boundary")
                inodes.add(item_stat.st_ino)
        if not inodes or any(inode <= 0 for inode in inodes):
            raise ValueError("mounted tree returned invalid inode identities")

        payload = {
            "entries": [{"inode": inode} for inode in sorted(inodes)],
        }
        descriptor = os.open(
            args.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, TypeError, ValueError) as exc:
        print(f"ext4-inode-inventory=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"ext4-inode-inventory=PASS inodes={len(inodes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

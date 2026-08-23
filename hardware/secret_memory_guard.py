"""Fail closed before image-resident factory secrets enter process memory."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


MAX_PROC_SWAPS_BYTES = 64 * 1024
EXPECTED_HEADER = ("Filename", "Type", "Size", "Used", "Priority")


class SecretMemoryGuardError(RuntimeError):
    """The host cannot prove that secrets will remain out of swap."""


def require_no_active_swap(path: str | os.PathLike[str] = "/proc/swaps") -> None:
    """Require a readable Linux ``/proc/swaps`` with no active entries.

    K1 is deliberately shipped in the factory image. Reading it while any
    swap device or swap file is active can copy key material back to persistent
    TF-card storage, where unlinking ``enrollment.key`` would not remove it.
    Missing or malformed kernel evidence is therefore a hard failure.
    """

    resolved = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as error:
        raise SecretMemoryGuardError(
            "cannot prove swap is disabled before factory-key use"
        ) from error
    try:
        chunks: list[bytes] = []
        remaining = MAX_PROC_SWAPS_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        document = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(document) > MAX_PROC_SWAPS_BYTES:
        raise SecretMemoryGuardError("/proc/swaps exceeds the safety bound")
    try:
        lines = document.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise SecretMemoryGuardError("/proc/swaps is not valid ASCII") from error
    if not lines or tuple(lines[0].split()) != EXPECTED_HEADER:
        raise SecretMemoryGuardError("/proc/swaps header is invalid")
    if any(line.strip() for line in lines[1:]):
        raise SecretMemoryGuardError(
            "active swap is forbidden while the factory enrollment key exists"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proc-swaps", default="/proc/swaps")
    args = parser.parse_args(argv)
    require_no_active_swap(args.proc_swaps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

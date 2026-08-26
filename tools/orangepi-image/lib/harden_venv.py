#!/usr/bin/env python3
"""Harden one root-owned Python virtual environment after locked installation."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "hardware/install"))

from runtime_release import (  # noqa: E402
    ReleaseValidationError,
    harden_venv_permissions,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if (
            sys.platform != "linux"
            or os.geteuid() != 0
            or sys.version_info[:2] != (3, 11)
        ):
            raise RuntimeError(
                "venv hardening requires Linux root and Python 3.11"
            )
        harden_venv_permissions(args.venv)
    except (OSError, RuntimeError, ReleaseValidationError) as exc:
        print(f"venv-permission-harden=FAIL: {exc}", file=sys.stderr)
        return 2
    print("venv-permission-harden=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

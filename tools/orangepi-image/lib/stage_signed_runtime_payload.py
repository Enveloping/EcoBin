#!/usr/bin/env python3
"""Verify, extract, and make one signed runtime release payload-ready."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "hardware/install"))

from runtime_release import (  # noqa: E402
    ReleaseValidationError,
    audit_installed_venv,
    harden_installed_venv_permissions,
    safe_extract_archive_stream,
    validate_release_tree,
    validate_install_complete_marker,
    verified_archive_stream,
    write_install_complete_marker,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=pathlib.Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--signature", required=True, type=pathlib.Path)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--trusted-public-keys-directory", required=True, type=pathlib.Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--destination", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def _run(argv: list[str]) -> None:
    subprocess.run(
        argv,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=900,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if sys.platform != "linux" or os.geteuid() != 0 or sys.version_info[:2] != (3, 11):
            raise RuntimeError("signed runtime staging requires Linux root and Python 3.11")
        if os.path.lexists(args.destination):
            raise RuntimeError("runtime payload destination already exists")
        args.destination.mkdir(mode=0o700)
        try:
            with verified_archive_stream(
                args.archive,
                expected_sha256=args.expected_sha256,
                signature_path=args.signature,
                signing_key_id=args.signing_key_id,
                trusted_public_keys_directory=args.trusted_public_keys_directory,
            ) as verified:
                extracted_release_id = safe_extract_archive_stream(
                    verified,
                    args.destination,
                    expected_sha256=args.expected_sha256,
                )
            if extracted_release_id != args.release_id:
                raise RuntimeError("signed archive release ID differs")
            validate_release_tree(args.destination, expected_release_id=args.release_id)
            _run(
                [
                    "uv",
                    "venv",
                    "--python",
                    "/usr/bin/python3.11",
                    "--relocatable",
                    str(args.destination / ".venv"),
                ]
            )
            _run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(args.destination / ".venv/bin/python"),
                    "--no-index",
                    "--require-hashes",
                    "--find-links",
                    str(args.destination / "wheelhouse"),
                    "--requirement",
                    str(args.destination / "requirements-offline.txt"),
                ]
            )
            harden_installed_venv_permissions(args.destination)
            _run(
                [
                    str(args.destination / ".venv/bin/python"),
                    "-c",
                    "import cryptography,cv2,paho.mqtt.client,serial,qcloud_cos,sys; "
                    "assert sys.version_info[:2] == (3, 11)",
                ]
            )
            audit_installed_venv(
                args.destination,
                trusted_python_targets=(pathlib.Path("/usr/bin/python3.11"),),
            )
            write_install_complete_marker(args.destination, args.release_id)
            validate_install_complete_marker(args.destination, args.release_id)
            # Keep the incomplete staging root private.  Publish traversal only
            # after the signed release, installed environment and completion
            # marker have all passed their checks.
            os.chmod(args.destination, 0o755)
        except Exception:
            shutil.rmtree(args.destination)
            raise
    except (OSError, RuntimeError, ReleaseValidationError, subprocess.SubprocessError) as exc:
        print(f"runtime-payload-stage=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"runtime-payload-stage=PASS release_id={args.release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

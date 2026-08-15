"""systemd entry point for first-boot enrollment."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from device_credentials import load_device_credentials
from secure_files import unlink_and_fsync


logger = logging.getLogger("enrollment-bootstrap")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend-url",
        default=os.getenv("ECOBIN_ENROLLMENT_BACKEND_URL", ""),
    )
    parser.add_argument(
        "--state",
        default="/var/lib/ecobin/enrollment-state.json",
    )
    parser.add_argument(
        "--credentials",
        default="/etc/ecobin/device-credentials.json",
    )
    parser.add_argument(
        "--global-key",
        default="/etc/ecobin/enrollment.key",
    )
    parser.add_argument(
        "--ssh-host-private-key",
        default="/etc/ssh/ssh_host_ed25519_key",
    )
    parser.add_argument(
        "--ssh-host-public-key",
        default="/etc/ssh/ssh_host_ed25519_key.pub",
    )
    parser.add_argument("--cleanup-file", action="append", default=[])
    parser.add_argument(
        "--enrollment-mode",
        choices=("SELF_ENROLLMENT", "LEGACY_ADOPTION"),
        default=os.getenv("ECOBIN_ENROLLMENT_MODE", "SELF_ENROLLMENT"),
    )
    parser.add_argument(
        "--enrollment-key-id",
        default=os.getenv("ECOBIN_ENROLLMENT_KEY_ID", "K1"),
    )
    parser.add_argument(
        "--legacy-onenet-secret-file",
        default=os.getenv("ECOBIN_LEGACY_ONENET_SECRET_FILE", ""),
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    credentials_path = Path(args.credentials)
    cleanup_files = tuple(Path(path) for path in args.cleanup_file)
    if (
        args.enrollment_mode == "LEGACY_ADOPTION"
        and args.legacy_onenet_secret_file
    ):
        # Once formal credentials are durable, retaining the legacy OneNet
        # proof file only creates a second long-lived secret copy. Include it
        # in the same idempotent, power-loss-safe cleanup sequence.
        cleanup_files += (Path(args.legacy_onenet_secret_file),)
    if credentials_path.exists():
        load_device_credentials(credentials_path, required=True)
        unlink_and_fsync(args.global_key)
        unlink_and_fsync(args.state)
        for cleanup_file in cleanup_files:
            unlink_and_fsync(cleanup_file)
        logger.info("verified existing credentials and completed bootstrap cleanup")
        return 0
    if not args.backend_url:
        raise SystemExit("ECOBIN_ENROLLMENT_BACKEND_URL is required")

    # Keep imports containing key-generation logic out of the permanent
    # bootstrap supervisor.  Production removes device_enrollment.py after a
    # verified credential bundle is installed.
    from device_enrollment import DeviceEnrollmentClient, EnrollmentPaths

    legacy_secret = None
    if args.enrollment_mode == "LEGACY_ADOPTION":
        if not args.legacy_onenet_secret_file:
            raise SystemExit(
                "LEGACY_ADOPTION requires --legacy-onenet-secret-file"
            )
        legacy_secret = Path(args.legacy_onenet_secret_file).read_text(
            encoding="utf-8"
        ).strip()

    paths = EnrollmentPaths(
        state=Path(args.state),
        credentials=Path(args.credentials),
        global_key=Path(args.global_key),
        ssh_host_private_key=Path(args.ssh_host_private_key),
        ssh_host_public_key=Path(args.ssh_host_public_key),
        cleanup_files=cleanup_files,
    )
    DeviceEnrollmentClient(
        backend_base_url=args.backend_url,
        paths=paths,
        enrollment_key_id=args.enrollment_key_id,
        enrollment_mode=args.enrollment_mode,
        legacy_onenet_secret=legacy_secret,
        timeout_seconds=args.timeout_seconds,
    ).run_once()
    logger.info("device enrollment credentials are installed and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

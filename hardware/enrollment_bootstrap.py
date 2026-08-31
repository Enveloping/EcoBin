"""systemd entry point for first-boot enrollment."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from device_credentials import load_device_credentials
from factory_progress import (
    DEFAULT_ENROLLMENT_PROGRESS_PATH,
    ENROLLMENT_ERROR_CODES,
    EnrollmentProgressWriter,
)
from secure_files import unlink_and_fsync
from secret_memory_guard import SecretMemoryGuardError, require_no_active_swap


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
    parser.add_argument(
        "--legacy-hardware-sn",
        default=os.getenv("ECOBIN_LEGACY_HARDWARE_SN", ""),
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--proc-swaps", default="/proc/swaps")
    parser.add_argument(
        "--progress",
        default=os.getenv(
            "ECOBIN_ENROLLMENT_PROGRESS_PATH",
            str(DEFAULT_ENROLLMENT_PROGRESS_PATH),
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    progress = EnrollmentProgressWriter(args.progress)
    current_phase = "IDENTITY_PREPARATION"

    def report(
        phase: str,
        *,
        last_error_code: str | None = None,
        retryable: bool = False,
    ) -> None:
        nonlocal current_phase
        current_phase = phase
        try:
            progress.report(
                phase,
                last_error_code=last_error_code,
                retryable=retryable,
            )
        except Exception as error:
            # The progress projection is diagnostic.  Its filesystem failure
            # must not turn an otherwise power-loss-safe registration into a
            # second device identity or prevent K1 cleanup.
            logger.error(
                "could not update enrollment progress: %s",
                type(error).__name__,
            )

    report("IDENTITY_PREPARATION")
    try:
        # Keep this check inside the bootstrap as well as systemd's
        # ExecStartPre: a manual invocation must not bypass the K1
        # memory-safety boundary.
        require_no_active_swap(args.proc_swaps)
        credentials_path = Path(args.credentials)
        cleanup_files = tuple(Path(path) for path in args.cleanup_file)
        if (
            args.enrollment_mode == "LEGACY_ADOPTION"
            and args.legacy_onenet_secret_file
        ):
            # Once formal credentials are durable, retaining the legacy
            # OneNet proof file only creates a second long-lived secret copy.
            cleanup_files += (Path(args.legacy_onenet_secret_file),)
        if credentials_path.exists():
            report("CREDENTIAL_INSTALLATION")
            load_device_credentials(credentials_path, required=True)
            report("K1_CLEANUP")
            unlink_and_fsync(args.global_key)
            unlink_and_fsync(args.state)
            for cleanup_file in cleanup_files:
                unlink_and_fsync(cleanup_file)
            report("COMPLETE")
            logger.info(
                "verified existing credentials and completed bootstrap cleanup"
            )
            return 0
        if not args.backend_url:
            report("FAILED", last_error_code="BACKEND_URL_MISSING")
            raise SystemExit("ECOBIN_ENROLLMENT_BACKEND_URL is required")

        # Keep imports containing key-generation logic out of the permanent
        # bootstrap supervisor. Production removes device_enrollment.py after
        # a verified credential bundle is installed.
        from device_enrollment import DeviceEnrollmentClient, EnrollmentPaths

        legacy_secret = None
        if args.enrollment_mode == "LEGACY_ADOPTION":
            if not args.legacy_onenet_secret_file:
                report("FAILED", last_error_code="ENROLLMENT_KEY_INVALID")
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
            legacy_hardware_sn=args.legacy_hardware_sn or None,
            progress_callback=report,
            timeout_seconds=args.timeout_seconds,
        ).run_once()
        logger.info("device enrollment credentials are installed and verified")
        return 0
    except SecretMemoryGuardError:
        report("FAILED", last_error_code="ACTIVE_SWAP_DETECTED")
        raise
    except Exception as error:
        retryable = any(
            base.__name__ == "EnrollmentRetryableError"
            for base in type(error).__mro__
        )
        error_code = getattr(error, "code", None)
        if error_code not in ENROLLMENT_ERROR_CODES:
            error_code = (
                "CREDENTIAL_INSTALL_FAILED"
                if current_phase == "CREDENTIAL_INSTALLATION"
                else "K1_CLEANUP_FAILED"
                if current_phase == "K1_CLEANUP"
                else "ENROLLMENT_INTERNAL_ERROR"
            )
        report(
            "RETRY_WAIT" if retryable else "FAILED",
            last_error_code=error_code,
            retryable=retryable,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())

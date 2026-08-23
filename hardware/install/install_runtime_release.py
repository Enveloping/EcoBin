#!/usr/bin/env python3
"""Install and atomically activate one signed-channel hardware archive.

The caller must supply the archive SHA-256 obtained from the trusted release
channel.  This slice verifies integrity; release-signature distribution remains
an outer image/update-channel responsibility.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO

try:
    from .runtime_release import (
        INSTALL_COMPLETE_MARKER,
        ReleaseValidationError,
        activate_with_rollback,
        audit_installed_venv,
        create_incoming_directory,
        fsync_release_tree,
        installed_current_release,
        nonblocking_install_lock,
        recover_pending_activation,
        remove_incoming_directory,
        safe_extract_archive_stream,
        validate_install_complete_marker,
        validate_release_tree,
        verified_archive_stream,
        write_install_complete_marker,
    )
except ImportError:  # pragma: no cover - direct execution on the device
    from runtime_release import (  # type: ignore[no-redef]
        INSTALL_COMPLETE_MARKER,
        ReleaseValidationError,
        activate_with_rollback,
        audit_installed_venv,
        create_incoming_directory,
        fsync_release_tree,
        installed_current_release,
        nonblocking_install_lock,
        recover_pending_activation,
        remove_incoming_directory,
        safe_extract_archive_stream,
        validate_install_complete_marker,
        validate_release_tree,
        verified_archive_stream,
        write_install_complete_marker,
    )


DEFAULT_RELEASES = Path("/opt/ecobin/hardware/releases")
DEFAULT_CURRENT = Path("/opt/ecobin/hardware/current")
DEFAULT_SERVICE = "ecobin-hardware.service"
VENV_CREATE_TIMEOUT_SECONDS = 120.0
PIP_INSTALL_TIMEOUT_SECONDS = 900.0
PIP_CHECK_TIMEOUT_SECONDS = 120.0
IMPORT_SMOKE_TIMEOUT_SECONDS = 60.0
SYSTEMCTL_SHOW_TIMEOUT_SECONDS = 10.0
# The unit may spend up to 15 seconds stopping and 180 seconds starting.  This
# command timeout is separate from --health-timeout-seconds, which begins only
# after Type=notify has reported READY and covers the post-READY stability
# observation window.
SYSTEMCTL_RESTART_TIMEOUT_SECONDS = 225.0
SYSTEMCTL_STOP_TIMEOUT_SECONDS = 30.0
IMPORT_SMOKE = (
    "import importlib; "
    "[importlib.import_module(name) for name in "
    "('cryptography','cv2','paho.mqtt.client','serial','qcloud_cos','main')]"
)


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    timeout_seconds: float,
    operation: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # Never include argv, stdout or stderr here: an archive path or child
        # process output may contain deployment credentials.
        raise RuntimeError(f"{operation} timed out") from None
    except subprocess.CalledProcessError:
        # Captured child output and argv may contain deployment paths or
        # environment-derived values.  Report only the bounded operation.
        raise RuntimeError(f"{operation} failed") from None


def _venv_python(release: Path) -> Path:
    return release / ".venv" / "bin" / "python"


def _trusted_python_targets() -> tuple[Path, ...]:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("runtime installation requires Python 3.11")
    candidates = {
        Path(sys.executable).resolve(strict=True),
        Path(getattr(sys, "_base_executable", sys.executable)).resolve(
            strict=True
        ),
    }
    return tuple(sorted(candidates))


def _audit_installed_environment(release: Path) -> None:
    audit_installed_venv(
        release,
        trusted_python_targets=_trusted_python_targets(),
    )


def _pip_check_installed_release(release: Path) -> None:
    python = _venv_python(release)
    if not python.exists():
        raise RuntimeError("installed release has no Python interpreter")
    _run(
        [str(python), "-m", "pip", "check"],
        timeout_seconds=PIP_CHECK_TIMEOUT_SECONDS,
        operation="installed dependency validation",
    )


def _prepare_offline_environment(release: Path) -> None:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("runtime installation requires Python 3.11")
    _run(
        [sys.executable, "-m", "venv", str(release / ".venv")],
        timeout_seconds=VENV_CREATE_TIMEOUT_SECONDS,
        operation="Python virtual environment creation",
    )
    python = _venv_python(release)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--require-hashes",
            "--find-links",
            str(release / "wheelhouse"),
            "--requirement",
            str(release / "requirements-offline.txt"),
        ],
        timeout_seconds=PIP_INSTALL_TIMEOUT_SECONDS,
        operation="offline dependency installation",
    )
    _pip_check_installed_release(release)


def _smoke_installed_release(release: Path) -> None:
    python = _venv_python(release)
    if not python.exists():
        raise RuntimeError("installed release has no Python interpreter")
    environment = dict(os.environ)
    environment["ECOBIN_CONFIG_MODE"] = "production"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("ECOBIN_DOTENV_PATH", None)
    _run(
        [str(python), "-c", IMPORT_SMOKE],
        cwd=release / "app",
        environment=environment,
        timeout_seconds=IMPORT_SMOKE_TIMEOUT_SECONDS,
        operation="installed runtime smoke test",
    )


def stage_archive(
    *,
    archive: Path,
    expected_sha256: str,
    releases_directory: Path,
    current_link: Path = DEFAULT_CURRENT,
    signature: Path,
    signing_key_id: str,
    trusted_public_keys_directory: Path,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> str:
    # This context performs SHA-256 first, then Ed25519 verification, without
    # creating or modifying the release store.  Extraction below reuses the
    # cryptographically bound private snapshot, so a path replacement or
    # in-place mutation cannot swap in a different archive after verification.
    with verified_archive_stream(
        archive,
        expected_sha256=expected_sha256,
        signature_path=signature,
        signing_key_id=signing_key_id,
        trusted_public_keys_directory=trusted_public_keys_directory,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    ) as verified_archive:
        return _stage_verified_archive(
            archive=verified_archive,
            expected_sha256=expected_sha256,
            releases_directory=releases_directory,
            current_link=current_link,
        )


def _stage_verified_archive(
    *,
    archive: BinaryIO,
    expected_sha256: str,
    releases_directory: Path,
    current_link: Path,
) -> str:
    releases_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    current_release = installed_current_release(
        current_link,
        releases_directory,
    )
    if current_release is not None:
        current_root = releases_directory / current_release
        validate_release_tree(
            current_root,
            expected_release_id=current_release,
        )
        # A current directory without this marker may be the half-published
        # side of a power loss.  Never delete or replace it automatically.
        validate_install_complete_marker(current_root, current_release)
        _audit_installed_environment(current_root)

    incoming = create_incoming_directory(releases_directory, "extract")
    try:
        archive_release_id = safe_extract_archive_stream(
            archive,
            incoming,
            expected_sha256=expected_sha256,
        )
        manifest = validate_release_tree(
            incoming,
            expected_release_id=archive_release_id,
        )
        release_id = manifest["ECOBIN_RELEASE_ID"]
        final = releases_directory / release_id

        if final.exists():
            if final.is_symlink() or not final.is_dir():
                raise ReleaseValidationError(
                    "existing release destination is not a regular directory"
                )
            marker = final / ".venv" / INSTALL_COMPLETE_MARKER
            if not os.path.lexists(marker):
                if current_release == release_id:
                    raise ReleaseValidationError(
                        "current installed release is incomplete"
                    )
                _remove_incomplete_final(final, releases_directory)
            else:
                validate_release_tree(final, expected_release_id=release_id)
                for metadata_name in ("manifest.env", "SHA256SUMS"):
                    if not _same_file(
                        incoming / metadata_name,
                        final / metadata_name,
                    ):
                        raise ReleaseValidationError(
                            "existing release ID has different content"
                        )
                validate_install_complete_marker(final, release_id)
                _audit_installed_environment(final)
                _pip_check_installed_release(final)
                _smoke_installed_release(final)
                remove_incoming_directory(incoming, releases_directory)
                return release_id

        _prepare_offline_environment(incoming)
        _smoke_installed_release(incoming)
        _audit_installed_environment(incoming)
        os.chmod(incoming, 0o755)
        fsync_release_tree(incoming)
        os.replace(incoming, final)
        _fsync_directory(releases_directory)
        validate_release_tree(final, expected_release_id=release_id)
        _audit_installed_environment(final)
        _pip_check_installed_release(final)
        _smoke_installed_release(final)
        write_install_complete_marker(final, release_id)
        validate_install_complete_marker(final, release_id)
        return release_id
    finally:
        if incoming.exists():
            remove_incoming_directory(incoming, releases_directory)


def _remove_incomplete_final(
    final: Path,
    releases_directory: Path,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    if (
        final.is_symlink()
        or not final.is_dir()
        or final.parent.resolve(strict=True)
        != releases_directory.resolve(strict=True)
    ):
        raise ReleaseValidationError(
            "refusing to remove an unexpected incomplete release"
        )
    releases_details = releases_directory.stat()
    final_details = final.stat()
    if (
        releases_details.st_uid != expected_uid
        or releases_details.st_gid != expected_gid
        or stat.S_IMODE(releases_details.st_mode) & 0o022
        or final_details.st_uid != expected_uid
        or final_details.st_gid != expected_gid
        or stat.S_IMODE(final_details.st_mode) & 0o022
    ):
        raise ReleaseValidationError(
            "refusing to remove an untrusted incomplete release"
        )
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise ReleaseValidationError(
            "platform cannot safely remove an incomplete release"
        )
    shutil.rmtree(final)
    _fsync_directory(releases_directory)


def _same_file(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as first_stream, second.open("rb") as second_stream:
        while True:
            first_chunk = first_stream.read(1024 * 1024)
            second_chunk = second_stream.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def _service_snapshot(service: str) -> dict[str, str]:
    result = _run(
        [
            "systemctl",
            "show",
            service,
            "--property=ActiveState",
            "--property=SubState",
            "--property=MainPID",
            "--property=NRestarts",
            "--property=InvocationID",
        ],
        timeout_seconds=SYSTEMCTL_SHOW_TIMEOUT_SECONDS,
        operation="systemd service state query",
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def restart_and_verify_service(
    service: str,
    *,
    timeout_seconds: float,
    stable_seconds: float,
) -> None:
    # Type=notify makes restart return only after main.py reports READY=1.
    # systemd may reset NRestarts during this explicit restart, so the health
    # baseline must be captured from the new invocation, never from the old
    # one.
    _run(
        ["systemctl", "restart", service],
        timeout_seconds=SYSTEMCTL_RESTART_TIMEOUT_SECONDS,
        operation="systemd service restart",
    )
    baseline = _service_snapshot(service)
    try:
        baseline_main_pid = int(baseline["MainPID"])
        baseline_restarts = int(baseline["NRestarts"])
    except (KeyError, ValueError) as error:
        raise RuntimeError(
            f"{service} returned an invalid post-restart service baseline"
        ) from error
    baseline_invocation = baseline.get("InvocationID", "")
    if (
        baseline.get("ActiveState") != "active"
        or baseline.get("SubState") != "running"
        or baseline_main_pid <= 0
        or baseline_restarts < 0
        or not baseline_invocation
    ):
        raise RuntimeError(
            f"{service} returned an invalid post-restart service baseline"
        )

    stable_since: float | None = time.monotonic()
    deadline = stable_since + timeout_seconds
    if stable_seconds == 0:
        return
    while time.monotonic() < deadline:
        snapshot = _service_snapshot(service)
        try:
            current_main_pid = int(snapshot.get("MainPID", "0"))
            current_restarts = int(snapshot.get("NRestarts", "-1"))
        except ValueError:
            current_main_pid = 0
            current_restarts = -1
        if snapshot.get("InvocationID") != baseline_invocation:
            raise RuntimeError(
                f"{service} started a different systemd invocation during "
                "the stability window"
            )
        if current_restarts != baseline_restarts:
            raise RuntimeError(
                f"{service} changed its automatic restart counter during "
                "the stability window"
            )
        active = (
            snapshot.get("ActiveState") == "active"
            and snapshot.get("SubState") == "running"
            and current_main_pid > 0
        )
        now = time.monotonic()
        if active:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stable_seconds:
                return
        else:
            stable_since = None
        time.sleep(min(0.5, max(0.0, deadline - now)))
    raise RuntimeError(
        f"{service} did not remain active in one systemd invocation for "
        f"{stable_seconds:g} seconds"
    )


def activate_release(
    *,
    releases_directory: Path,
    current_link: Path,
    release_id: str,
    service: str,
    timeout_seconds: float,
    stable_seconds: float,
) -> str | None:
    def verify(recovered_release: str | None) -> None:
        if recovered_release is None:
            _stop_and_verify_service(service)
        else:
            restart_and_verify_service(
                service,
                timeout_seconds=timeout_seconds,
                stable_seconds=stable_seconds,
            )

    return activate_with_rollback(
        releases_directory=releases_directory,
        current_link=current_link,
        release_id=release_id,
        health_check=lambda _release: restart_and_verify_service(
            service,
            timeout_seconds=timeout_seconds,
            stable_seconds=stable_seconds,
        ),
        rollback_health_check=verify,
    )


def _stop_and_verify_service(service: str) -> None:
    _run(
        ["systemctl", "stop", service],
        timeout_seconds=SYSTEMCTL_STOP_TIMEOUT_SECONDS,
        operation="systemd service stop",
    )
    snapshot = _service_snapshot(service)
    try:
        main_pid = int(snapshot.get("MainPID", "0"))
    except ValueError:
        main_pid = -1
    if snapshot.get("ActiveState") != "inactive" or main_pid != 0:
        raise RuntimeError("systemd service did not reach its stopped state")


def recover_activation(
    *,
    releases_directory: Path,
    current_link: Path,
    service: str,
    timeout_seconds: float,
    stable_seconds: float,
) -> str | None:
    def verify(recovered_release: str | None) -> None:
        if recovered_release is None:
            _stop_and_verify_service(service)
        else:
            restart_and_verify_service(
                service,
                timeout_seconds=timeout_seconds,
                stable_seconds=stable_seconds,
            )

    return recover_pending_activation(
        releases_directory=releases_directory,
        current_link=current_link,
        health_check=verify,
    )


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--signing-key-id")
    parser.add_argument("--trusted-public-keys-directory", type=Path)
    parser.add_argument("--releases-directory", type=Path, default=DEFAULT_RELEASES)
    parser.add_argument("--current-link", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument(
        "--health-timeout-seconds",
        type=float,
        default=30.0,
        help=(
            "post-READY stability-window deadline; this is not the total "
            "install or systemd start timeout"
        ),
    )
    parser.add_argument(
        "--stable-seconds",
        type=float,
        default=5.0,
        help="continuous healthy time required after READY",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="verify and install without changing current or starting systemd",
    )
    parser.add_argument(
        "--recover-pending",
        action="store_true",
        help="roll back an interrupted activation before starting hardware",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise SystemExit("runtime releases can only be installed on Linux")
    if os.geteuid() != 0:
        raise SystemExit("runtime release installation must run as root")
    if args.health_timeout_seconds <= 0 or args.stable_seconds <= 0:
        raise SystemExit("health durations must be positive")
    if args.stable_seconds > args.health_timeout_seconds:
        raise SystemExit("stable duration cannot exceed health timeout")
    releases_parent = Path(os.path.abspath(args.releases_directory.parent))
    current_parent = Path(os.path.abspath(args.current_link.parent))
    if releases_parent != current_parent:
        raise SystemExit(
            "releases directory and current link must share one lock parent"
        )
    if args.recover_pending:
        if any(
            value is not None
            for value in (
                args.archive,
                args.expected_sha256,
                args.signature,
                args.signing_key_id,
                args.trusted_public_keys_directory,
            )
        ):
            raise SystemExit(
                "recovery does not accept release or signing inputs"
            )
        if args.stage_only:
            raise SystemExit("recovery and stage-only are mutually exclusive")
    elif any(
        value is None
        for value in (
            args.archive,
            args.expected_sha256,
            args.signature,
            args.signing_key_id,
            args.trusted_public_keys_directory,
        )
    ):
        raise SystemExit(
            "installation requires archive, digest, signature and signing key ID"
        )

    previous_umask = os.umask(0o077)
    try:
        with nonblocking_install_lock(args.current_link):
            if args.recover_pending:
                recovered = recover_activation(
                    releases_directory=args.releases_directory,
                    current_link=args.current_link,
                    service=args.service,
                    timeout_seconds=args.health_timeout_seconds,
                    stable_seconds=args.stable_seconds,
                )
                print(
                    "hardware runtime recovery complete; current: "
                    f"{recovered or 'none'}"
                )
                return 0

            release_id = stage_archive(
                archive=args.archive,
                expected_sha256=args.expected_sha256,
                releases_directory=args.releases_directory,
                current_link=args.current_link,
                signature=args.signature,
                signing_key_id=args.signing_key_id,
                trusted_public_keys_directory=args.trusted_public_keys_directory,
            )
            if args.stage_only:
                print(f"hardware runtime staged: {release_id}")
                return 0
            previous = activate_release(
                releases_directory=args.releases_directory,
                current_link=args.current_link,
                release_id=release_id,
                service=args.service,
                timeout_seconds=args.health_timeout_seconds,
                stable_seconds=args.stable_seconds,
            )
            print(
                f"hardware runtime active: {release_id}; "
                f"previous: {previous or 'none'}"
            )
            return 0
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())

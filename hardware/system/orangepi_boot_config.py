"""Safely enable the Orange Pi Zero 3 UART5 device-tree overlay."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


UART5_OVERLAY = "ph-uart5"
UART5_CONFLICT = "ph-pwm12"
UART5_DEVICE = "ttyS5"
UART5_GETTY_UNITS = (
    "serial-getty@ttyS5.service",
    "getty@ttyS5.service",
)
OVERLAYS_PATTERN = re.compile(r"^(?P<indent>\s*)overlays\s*=(?P<value>.*)$")
UART5_CONSOLE_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9_])console=(?:/dev/)?ttyS5(?:[,\s\x00]|$)",
)


class OrangePiBootConfigError(RuntimeError):
    """The mounted image cannot be changed without crossing a safety gate."""


@dataclass(frozen=True)
class OverlayUpdateResult:
    path: Path
    overlays: tuple[str, ...]
    changed: bool


def configure_uart5_for_image(rootfs: Path) -> OverlayUpdateResult:
    """Give UART5 exclusively to EcoBin and mask Linux console owners.

    This is an offline image-build operation.  It rejects an image whose boot
    arguments already route a console to ttyS5, then enables the board overlay
    and masks both systemd getty unit spellings.  Runtime code must not call it.
    """

    root = _validated_rootfs(Path(rootfs))
    _reject_uart5_console(root)
    mask_paths = _validated_getty_mask_paths(root)
    result = enable_uart5_overlay(root)
    for mask_path in mask_paths:
        if not os.path.lexists(mask_path):
            os.symlink("/dev/null", mask_path)
            _fsync_directory(mask_path.parent)
    return result


def enable_uart5_overlay(rootfs: Path) -> OverlayUpdateResult:
    """Enable ``ph-uart5`` inside an explicit, non-host image root.

    Unrelated overlays and line endings are preserved.  Ambiguous boot files,
    the PH2/PH3 PWM conflict, a missing DTBO, and paths escaping the mounted
    image are rejected before any write occurs.
    """

    root = _validated_rootfs(Path(rootfs))
    boot = _inside_root(root, root / "boot", must_exist=True)
    env_path = root / "boot" / "orangepiEnv.txt"
    if env_path.is_symlink():
        raise OrangePiBootConfigError("orangepiEnv.txt must not be a symlink")
    env_path = _inside_root(root, env_path, must_exist=True)
    if not env_path.is_file():
        raise OrangePiBootConfigError("orangepiEnv.txt is not a regular file")
    _require_uart5_dtbo(root, boot)

    original = env_path.read_bytes()
    try:
        content = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OrangePiBootConfigError(
            "orangepiEnv.txt is not valid UTF-8"
        ) from error

    updated, overlays = _updated_content(content)
    encoded = updated.encode("utf-8")
    changed = encoded != original
    if changed:
        _atomic_write(env_path, encoded)
    return OverlayUpdateResult(env_path, tuple(overlays), changed)


def _validated_rootfs(rootfs: Path) -> Path:
    if not rootfs.is_absolute():
        raise OrangePiBootConfigError("rootfs path must be absolute")
    root = rootfs.resolve(strict=True)
    filesystem_root = Path(root.anchor).resolve(strict=True)
    if root == filesystem_root:
        raise OrangePiBootConfigError("refusing to edit the host filesystem root")
    marker = _inside_root(root, root / "etc" / "os-release", must_exist=True)
    if not marker.is_file():
        raise OrangePiBootConfigError("rootfs is missing etc/os-release")
    return root


def _inside_root(root: Path, path: Path, *, must_exist: bool) -> Path:
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as error:
        raise OrangePiBootConfigError(f"required image path is missing: {path}") from error
    if not resolved.is_relative_to(root):
        raise OrangePiBootConfigError("image path escapes the explicit rootfs")
    return resolved


def _require_uart5_dtbo(root: Path, boot: Path) -> None:
    matches: list[Path] = []
    for candidate in boot.rglob(f"*{UART5_OVERLAY}*.dtbo"):
        try:
            resolved = _inside_root(root, candidate, must_exist=True)
        except OrangePiBootConfigError:
            continue
        if resolved.is_file():
            matches.append(resolved)
    if not matches:
        raise OrangePiBootConfigError("ph-uart5 DTBO is missing from the image")


def _reject_uart5_console(root: Path) -> None:
    boot = _inside_root(root, root / "boot", must_exist=True)
    candidates = (
        boot / "orangepiEnv.txt",
        boot / "boot.cmd",
        boot / "boot.scr",
        boot / "extlinux" / "extlinux.conf",
    )
    for candidate in candidates:
        if not os.path.lexists(candidate):
            continue
        if candidate.is_symlink():
            raise OrangePiBootConfigError(
                f"boot argument file must not be a symlink: {candidate.name}"
            )
        resolved = _inside_root(root, candidate, must_exist=True)
        if not resolved.is_file():
            raise OrangePiBootConfigError(
                f"boot argument path is not a regular file: {candidate.name}"
            )
        if UART5_CONSOLE_PATTERN.search(resolved.read_bytes()):
            raise OrangePiBootConfigError(
                "boot arguments reserve ttyS5 for a Linux console"
            )


def _validated_getty_mask_paths(root: Path) -> tuple[Path, ...]:
    systemd_parent = root / "etc" / "systemd"
    _inside_root(root, systemd_parent, must_exist=True)
    systemd_root = systemd_parent / "system"
    if not os.path.lexists(systemd_root):
        systemd_root.mkdir(mode=0o755)
        _fsync_directory(systemd_parent)
    if systemd_root.is_symlink():
        raise OrangePiBootConfigError(
            "/etc/systemd/system must not be a symlink"
        )
    systemd_root = _inside_root(root, systemd_root, must_exist=True)
    if not systemd_root.is_dir():
        raise OrangePiBootConfigError(
            "/etc/systemd/system is not a directory"
        )

    masks: list[Path] = []
    for unit in UART5_GETTY_UNITS:
        mask = systemd_root / unit
        if os.path.lexists(mask):
            if not mask.is_symlink() or os.readlink(mask) != "/dev/null":
                raise OrangePiBootConfigError(
                    f"existing UART5 getty override is not a systemd mask: {unit}"
                )
        else:
            _inside_root(root, mask, must_exist=False)
        masks.append(mask)
    return tuple(masks)


def _updated_content(content: str) -> tuple[str, list[str]]:
    lines = content.splitlines(keepends=True)
    matches: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        match = OVERLAYS_PATTERN.match(body)
        if match:
            matches.append((index, match))
    if len(matches) > 1:
        raise OrangePiBootConfigError("orangepiEnv.txt has duplicate overlays keys")

    if matches:
        index, match = matches[0]
        value, comment = _split_inline_comment(match.group("value"))
        overlays = _deduplicated_tokens(value)
        _reject_conflict(overlays)
        if UART5_OVERLAY not in overlays:
            overlays.append(UART5_OVERLAY)
        newline = _line_ending(lines[index])
        rendered = f"{match.group('indent')}overlays={' '.join(overlays)}"
        if comment:
            rendered += f" {comment}"
        lines[index] = rendered + newline
    else:
        overlays = [UART5_OVERLAY]
        newline = _preferred_line_ending(content)
        if content and not content.endswith(("\n", "\r")):
            lines.append(newline)
        lines.append(f"overlays={UART5_OVERLAY}{newline}")
    return "".join(lines), overlays


def _split_inline_comment(value: str) -> tuple[str, str]:
    before, marker, after = value.partition("#")
    if not marker:
        return value, ""
    return before, f"#{after}".rstrip()


def _deduplicated_tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in value.split():
        if token not in result:
            result.append(token)
    return result


def _reject_conflict(overlays: list[str]) -> None:
    if UART5_CONFLICT in overlays:
        raise OrangePiBootConfigError(
            "ph-pwm12 conflicts with UART5 on PH2/PH3"
        )


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _preferred_line_ending(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _atomic_write(path: Path, content: bytes) -> None:
    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
        if os.name == "posix":
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enable ph-uart5 in a mounted Orange Pi image",
    )
    parser.add_argument(
        "--rootfs",
        type=Path,
        required=True,
        help="absolute path to the mounted image root (never /)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        configure_uart5_for_image(args.rootfs)
    except OrangePiBootConfigError as error:
        raise SystemExit(f"UART5 overlay configuration failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

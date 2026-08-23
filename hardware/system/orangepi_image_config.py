"""Install immutable board-safety files into an offline Orange Pi image."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Sequence

try:
    from .orangepi_boot_config import (
        OrangePiBootConfigError,
        configure_uart5_for_image,
    )
except ImportError:  # pragma: no cover - direct execution by image builder
    from orangepi_boot_config import (  # type: ignore[no-redef]
        OrangePiBootConfigError,
        configure_uart5_for_image,
    )


SAFE_GPIO_DESTINATION = Path("usr/lib/ecobin/mcu_safe_gpio.py")
SAFE_GPIO_UNIT_DESTINATION = Path(
    "etc/systemd/system/ecobin-mcu-safe-gpio.service"
)
SAFE_GPIO_ENABLE_LINK = Path(
    "etc/systemd/system/multi-user.target.wants/ecobin-mcu-safe-gpio.service"
)
SAFE_GPIO_ENABLE_TARGET = "../ecobin-mcu-safe-gpio.service"
EXPAND_ROOTFS_DESTINATION = Path("usr/lib/ecobin/expand-rootfs.sh")
EXPAND_ROOTFS_UNIT_DESTINATION = Path(
    "etc/systemd/system/ecobin-expand-rootfs.service"
)
IMAGE_LAYOUT_DESTINATION = Path("usr/share/ecobin/image-layout.json")


class OrangePiImageConfigError(RuntimeError):
    """The offline image cannot be configured without crossing a safety gate."""


def configure_board_image(
    rootfs: Path,
    *,
    safe_gpio_source: Path,
    safe_gpio_unit_source: Path,
    expand_rootfs_source: Path,
    expand_rootfs_unit_source: Path,
    image_layout_source: Path,
) -> None:
    """Configure UART5 and install the early GPIO helper outside releases.

    The helper deliberately lives in ``/usr/lib/ecobin`` instead of under the
    switchable ``/opt/ecobin/hardware/current`` link.  It must remain available
    when a runtime activation is incomplete or has been rolled back.
    """

    root = _validated_rootfs(rootfs)
    gpio_source = _validated_source(safe_gpio_source, "safe GPIO helper")
    unit_source = _validated_source(
        safe_gpio_unit_source,
        "safe GPIO systemd unit",
    )
    expansion_source = _validated_source(
        expand_rootfs_source,
        "root filesystem expansion helper",
    )
    expansion_unit_source = _validated_source(
        expand_rootfs_unit_source,
        "root filesystem expansion systemd unit",
    )
    layout_source = _validated_source(
        image_layout_source,
        "image layout",
    )

    configure_uart5_for_image(root)
    gpio_destination = _destination(root, SAFE_GPIO_DESTINATION)
    unit_destination = _destination(root, SAFE_GPIO_UNIT_DESTINATION)
    expansion_destination = _destination(root, EXPAND_ROOTFS_DESTINATION)
    expansion_unit_destination = _destination(
        root,
        EXPAND_ROOTFS_UNIT_DESTINATION,
    )
    layout_destination = _destination(root, IMAGE_LAYOUT_DESTINATION)
    enable_link = _destination(
        root,
        SAFE_GPIO_ENABLE_LINK,
        allow_symlink=True,
    )

    _atomic_copy(gpio_source, gpio_destination, mode=0o644)
    _atomic_copy(unit_source, unit_destination, mode=0o644)
    _atomic_copy(expansion_source, expansion_destination, mode=0o755)
    _atomic_copy(expansion_unit_source, expansion_unit_destination, mode=0o644)
    _atomic_copy(layout_source, layout_destination, mode=0o644)
    if os.path.lexists(enable_link):
        if (
            not enable_link.is_symlink()
            or os.readlink(enable_link) != SAFE_GPIO_ENABLE_TARGET
        ):
            raise OrangePiImageConfigError(
                "safe GPIO service has an unexpected enable link"
            )
    else:
        os.symlink(SAFE_GPIO_ENABLE_TARGET, enable_link)
        _fsync_directory(enable_link.parent)


def _validated_rootfs(rootfs: Path) -> Path:
    raw = Path(rootfs)
    if not raw.is_absolute():
        raise OrangePiImageConfigError("rootfs path must be absolute")
    try:
        root = raw.resolve(strict=True)
    except OSError as error:
        raise OrangePiImageConfigError("rootfs path is missing") from error
    if root == Path(root.anchor).resolve(strict=True):
        raise OrangePiImageConfigError("refusing to configure the host root")
    marker = root / "etc" / "os-release"
    if not marker.is_file() or marker.is_symlink():
        raise OrangePiImageConfigError("rootfs has no regular os-release")
    _require_inside(root, marker, strict=True)
    return root


def _validated_source(source: Path, label: str) -> Path:
    path = Path(source)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise OrangePiImageConfigError(f"{label} is missing") from error
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OrangePiImageConfigError(f"{label} must be a regular file")
    if metadata.st_nlink != 1:
        raise OrangePiImageConfigError(f"{label} must not be hard-linked")
    return resolved


def _destination(
    root: Path,
    relative: Path,
    *,
    allow_symlink: bool = False,
) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise OrangePiImageConfigError("image destination is unsafe")
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise OrangePiImageConfigError(
                    f"image destination parent is unsafe: {component}"
                )
            _require_inside(root, current, strict=True)
        else:
            current.mkdir(mode=0o755)
            _fsync_directory(current.parent)
    destination = current / relative.name
    _require_inside(root, destination, strict=False)
    if (
        os.path.lexists(destination)
        and destination.is_symlink()
        and not allow_symlink
    ):
        raise OrangePiImageConfigError("image destination must not be a symlink")
    return destination


def _require_inside(root: Path, path: Path, *, strict: bool) -> Path:
    try:
        resolved = path.resolve(strict=strict)
    except OSError as error:
        raise OrangePiImageConfigError("image path cannot be resolved") from error
    if not resolved.is_relative_to(root):
        raise OrangePiImageConfigError("image path escapes the mounted rootfs")
    return resolved


def _atomic_copy(source: Path, destination: Path, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_handle, os.fdopen(
            descriptor,
            "wb",
        ) as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
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
        description="Configure EcoBin board files in a mounted Orange Pi image",
    )
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--safe-gpio-source", type=Path, required=True)
    parser.add_argument("--safe-gpio-unit-source", type=Path, required=True)
    parser.add_argument("--expand-rootfs-source", type=Path, required=True)
    parser.add_argument("--expand-rootfs-unit-source", type=Path, required=True)
    parser.add_argument("--image-layout-source", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        configure_board_image(
            args.rootfs,
            safe_gpio_source=args.safe_gpio_source,
            safe_gpio_unit_source=args.safe_gpio_unit_source,
            expand_rootfs_source=args.expand_rootfs_source,
            expand_rootfs_unit_source=args.expand_rootfs_unit_source,
            image_layout_source=args.image_layout_source,
        )
    except (OrangePiBootConfigError, OrangePiImageConfigError) as error:
        raise SystemExit(f"board image configuration failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

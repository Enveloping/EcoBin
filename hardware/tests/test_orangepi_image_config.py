from __future__ import annotations

import os
from pathlib import Path

import pytest

from system.orangepi_image_config import (
    OrangePiImageConfigError,
    configure_board_image,
)


HARDWARE = Path(__file__).resolve().parents[1]
REPOSITORY = HARDWARE.parent


def _board_sources() -> dict[str, Path]:
    return {
        "safe_gpio_source": HARDWARE / "system/mcu_safe_gpio.py",
        "safe_gpio_unit_source": HARDWARE / "ecobin-mcu-safe-gpio.service",
        "expand_rootfs_source": (
            REPOSITORY / "tools/orangepi-image/expand-rootfs.sh"
        ),
        "expand_rootfs_unit_source": HARDWARE / "ecobin-expand-rootfs.service",
        "image_layout_source": (
            REPOSITORY / "tools/orangepi-image/image-layout.json"
        ),
    }


def _rootfs(tmp_path: Path) -> Path:
    root = tmp_path / "rootfs"
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(
        "ID=debian\nVERSION_ID=12\n",
        encoding="utf-8",
    )
    overlay = root / "boot/dtb/allwinner/overlay/sun50i-h616-ph-uart5.dtbo"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"dtbo")
    (root / "boot/orangepiEnv.txt").write_text(
        "verbosity=1\n",
        encoding="utf-8",
    )
    return root.resolve()


@pytest.mark.skipif(os.name == "nt", reason="image enable links require POSIX")
def test_board_component_is_immutable_and_independent_of_current_release(
    tmp_path: Path,
):
    root = _rootfs(tmp_path)

    configure_board_image(
        root,
        **_board_sources(),
    )
    configure_board_image(
        root,
        **_board_sources(),
    )

    helper = root / "usr/lib/ecobin/mcu_safe_gpio.py"
    unit = root / "etc/systemd/system/ecobin-mcu-safe-gpio.service"
    enabled = (
        root
        / "etc/systemd/system/multi-user.target.wants"
        / "ecobin-mcu-safe-gpio.service"
    )
    assert helper.read_bytes() == (HARDWARE / "system/mcu_safe_gpio.py").read_bytes()
    assert unit.read_bytes() == (
        HARDWARE / "ecobin-mcu-safe-gpio.service"
    ).read_bytes()
    assert oct(helper.stat().st_mode & 0o777) == "0o644"
    assert oct(unit.stat().st_mode & 0o777) == "0o644"
    assert enabled.is_symlink()
    assert os.readlink(enabled) == "../ecobin-mcu-safe-gpio.service"
    assert "/opt/ecobin/hardware/current" not in unit.read_text(encoding="utf-8")
    expansion = root / "usr/lib/ecobin/expand-rootfs.sh"
    expansion_unit = root / "etc/systemd/system/ecobin-expand-rootfs.service"
    layout = root / "usr/share/ecobin/image-layout.json"
    assert expansion.read_bytes() == (
        REPOSITORY / "tools/orangepi-image/expand-rootfs.sh"
    ).read_bytes()
    assert expansion_unit.read_bytes() == (
        HARDWARE / "ecobin-expand-rootfs.service"
    ).read_bytes()
    assert layout.read_bytes() == (
        REPOSITORY / "tools/orangepi-image/image-layout.json"
    ).read_bytes()
    assert oct(expansion.stat().st_mode & 0o777) == "0o755"
    assert oct(expansion_unit.stat().st_mode & 0o777) == "0o644"
    assert oct(layout.stat().st_mode & 0o777) == "0o644"
    assert not (
        root
        / "etc/systemd/system/multi-user.target.wants"
        / "ecobin-expand-rootfs.service"
    ).exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink setup requires POSIX")
def test_board_component_rejects_destination_parent_symlink_escape(
    tmp_path: Path,
):
    root = _rootfs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "usr").mkdir()
    (root / "usr/lib").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OrangePiImageConfigError, match="parent is unsafe"):
        configure_board_image(
            root,
            **_board_sources(),
        )

    assert not any(outside.iterdir())


def test_board_component_refuses_host_root():
    with pytest.raises(OrangePiImageConfigError, match="host root"):
        configure_board_image(
            Path(Path.cwd().anchor),
            **_board_sources(),
        )

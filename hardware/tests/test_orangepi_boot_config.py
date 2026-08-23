from __future__ import annotations

import os
from pathlib import Path

import pytest

from system.orangepi_boot_config import (
    OrangePiBootConfigError,
    configure_uart5_for_image,
    enable_uart5_overlay,
)


def _image_root(tmp_path: Path, content: bytes) -> tuple[Path, Path]:
    root = tmp_path / "image-root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(
        "ID=debian\nVERSION_ID=12\n",
        encoding="utf-8",
    )
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    overlay_dir = root / "boot" / "dtb" / "allwinner" / "overlay"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "sun50i-h616-ph-uart5.dtbo").write_bytes(b"dtbo")
    env_path = root / "boot" / "orangepiEnv.txt"
    env_path.write_bytes(content)
    return root.resolve(), env_path


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (b"verbosity=1\n", b"verbosity=1\noverlays=ph-uart5\n"),
        (
            b"verbosity=1\noverlays=usbhost2 i2c3\n",
            b"verbosity=1\noverlays=usbhost2 i2c3 ph-uart5\n",
        ),
        (
            b"overlays=ph-uart5 ph-uart5 usbhost2\n",
            b"overlays=ph-uart5 usbhost2\n",
        ),
        (
            b"overlays=usbhost2 ph-uart5\n",
            b"overlays=usbhost2 ph-uart5\n",
        ),
        (
            b"verbosity=1\r\noverlays=usbhost2 # keep\r\n",
            b"verbosity=1\r\noverlays=usbhost2 ph-uart5 # keep\r\n",
        ),
    ],
)
def test_uart5_overlay_update_is_structured_and_idempotent(
    tmp_path: Path,
    original: bytes,
    expected: bytes,
):
    root, env_path = _image_root(tmp_path, original)

    first = enable_uart5_overlay(root)
    second = enable_uart5_overlay(root)

    assert env_path.read_bytes() == expected
    assert first.changed is (original != expected)
    assert not second.changed
    assert second.overlays.count("ph-uart5") == 1


def test_uart5_overlay_rejects_pwm_conflict_before_writing(tmp_path: Path):
    original = b"overlays=usbhost2 ph-pwm12\n"
    root, env_path = _image_root(tmp_path, original)

    with pytest.raises(OrangePiBootConfigError, match="conflicts"):
        enable_uart5_overlay(root)

    assert env_path.read_bytes() == original


def test_uart5_overlay_rejects_duplicate_overlays_keys(tmp_path: Path):
    original = b"overlays=usbhost2\noverlays=i2c3\n"
    root, env_path = _image_root(tmp_path, original)

    with pytest.raises(OrangePiBootConfigError, match="duplicate"):
        enable_uart5_overlay(root)

    assert env_path.read_bytes() == original


def test_uart5_overlay_rejects_missing_dtbo_before_writing(tmp_path: Path):
    original = b"verbosity=1\n"
    root, env_path = _image_root(tmp_path, original)
    next((root / "boot").rglob("*ph-uart5*.dtbo")).unlink()

    with pytest.raises(OrangePiBootConfigError, match="DTBO is missing"):
        enable_uart5_overlay(root)

    assert env_path.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="systemd masks require POSIX symlinks")
def test_image_configuration_masks_getty_and_is_idempotent(tmp_path: Path):
    root, env_path = _image_root(tmp_path, b"verbosity=1\n")

    first = configure_uart5_for_image(root)
    second = configure_uart5_for_image(root)

    assert first.changed
    assert not second.changed
    assert env_path.read_bytes() == b"verbosity=1\noverlays=ph-uart5\n"
    for unit in ("serial-getty@ttyS5.service", "getty@ttyS5.service"):
        mask = root / "etc" / "systemd" / "system" / unit
        assert mask.is_symlink()
        assert os.readlink(mask) == "/dev/null"


@pytest.mark.parametrize(
    "console_argument",
    [
        b"extraargs=console=ttyS5,115200\n",
        b"console=/dev/ttyS5,115200n8\n",
    ],
)
def test_image_configuration_rejects_uart5_console_before_writing(
    tmp_path: Path,
    console_argument: bytes,
):
    root, env_path = _image_root(tmp_path, console_argument)

    with pytest.raises(OrangePiBootConfigError, match="Linux console"):
        configure_uart5_for_image(root)

    assert env_path.read_bytes() == console_argument
    assert not any(
        os.path.lexists(
            root / "etc" / "systemd" / "system" / unit
        )
        for unit in ("serial-getty@ttyS5.service", "getty@ttyS5.service")
    )


def test_image_configuration_rejects_unmasked_getty_before_writing(
    tmp_path: Path,
):
    root, env_path = _image_root(tmp_path, b"verbosity=1\n")
    getty = root / "etc" / "systemd" / "system" / "serial-getty@ttyS5.service"
    getty.write_text("[Service]\n", encoding="utf-8")

    with pytest.raises(OrangePiBootConfigError, match="not a systemd mask"):
        configure_uart5_for_image(root)

    assert env_path.read_bytes() == b"verbosity=1\n"


def test_uart5_overlay_refuses_the_host_filesystem_root():
    filesystem_root = Path(Path.cwd().anchor)

    with pytest.raises(OrangePiBootConfigError, match="host filesystem root"):
        enable_uart5_overlay(filesystem_root)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs Windows privilege")
def test_uart5_overlay_rejects_boot_symlink_escaping_rootfs(tmp_path: Path):
    outside = tmp_path / "outside-boot"
    outside.mkdir()
    (outside / "orangepiEnv.txt").write_text("verbosity=1\n", encoding="utf-8")
    root = tmp_path / "image-root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text("ID=debian\n", encoding="utf-8")
    (root / "boot").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OrangePiBootConfigError, match="escapes"):
        enable_uart5_overlay(root.resolve())

"""Validated, non-secret configuration bound into a P7 acceptance report."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Mapping


DEFAULT_OUTSIDE_CAMERA = (
    "/dev/v4l/by-id/"
    "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
)
DEFAULT_INSIDE_CAMERA = (
    "/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0"
)


class AcceptanceConfigurationError(ValueError):
    """The installed hardware configuration is not safe for factory use."""


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default)).strip()
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise AcceptanceConfigurationError(f"{name} must be an integer")
    return int(raw)


def _text(values: Mapping[str, str], name: str, default: str) -> str:
    value = values.get(name, default).strip()
    if not value or len(value) > 255 or any(ord(char) < 0x20 for char in value):
        raise AcceptanceConfigurationError(f"{name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class AcceptanceConfiguration:
    schema_version: int
    serial_port: str
    serial_baudrate: int
    protocol_mode: str
    uart_port_count: int
    boot0_wpi: int
    reset_wpi: int
    boot0_active_level: int
    reset_active_level: int
    hardware_compatibility: str
    stm32flash_path: str
    gpio_path: str
    outside_camera: str
    inside_camera: str
    weight_target_grams: int
    weight_tolerance_grams: int
    weight_stable_sample_count: int
    weight_stable_max_spread_grams: int
    weight_sample_interval_ms: int
    weight_sample_timeout_ms: int
    camera_review_ttl_ms: int
    state_path: str
    report_path: str
    photo_directory: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "AcceptanceConfiguration":
        simulated = _text(values, "ECOBIN_MCU_SIMULATED", "false").lower()
        if simulated not in {"false", "0", "no"}:
            raise AcceptanceConfigurationError(
                "factory acceptance forbids a simulated MCU"
            )
        config = cls(
            schema_version=1,
            serial_port=_text(values, "ECOBIN_SERIAL_PORT", "/dev/ttyS5"),
            serial_baudrate=_integer(
                values, "ECOBIN_SERIAL_BAUDRATE", 115200
            ),
            protocol_mode=_text(
                values, "ECOBIN_MCU_PROTOCOL", "fixed-frame"
            ).lower(),
            uart_port_count=_integer(values, "ECOBIN_UART_PORT_COUNT", 1),
            boot0_wpi=_integer(values, "ECOBIN_MCU_BOOT0_WPI", 2),
            reset_wpi=_integer(values, "ECOBIN_MCU_RESET_WPI", 5),
            boot0_active_level=_integer(
                values, "ECOBIN_MCU_BOOT0_ACTIVE_LEVEL", 1
            ),
            reset_active_level=_integer(
                values, "ECOBIN_MCU_RESET_ACTIVE_LEVEL", 1
            ),
            hardware_compatibility=_text(
                values,
                "ECOBIN_MCU_HARDWARE_COMPATIBILITY",
                "ECOBIN_MAINBOARD_V1.1",
            ),
            stm32flash_path=_text(
                values, "ECOBIN_STM32FLASH_PATH", "/usr/bin/stm32flash"
            ),
            gpio_path=_text(values, "ECOBIN_GPIO_PATH", "/usr/bin/gpio"),
            outside_camera=_text(
                values, "ECOBIN_CAMERA_OUTSIDE", DEFAULT_OUTSIDE_CAMERA
            ),
            inside_camera=_text(
                values, "ECOBIN_CAMERA_INSIDE", DEFAULT_INSIDE_CAMERA
            ),
            weight_target_grams=_integer(
                values, "ECOBIN_FACTORY_TEST_WEIGHT_GRAMS", 500
            ),
            weight_tolerance_grams=_integer(
                values, "ECOBIN_FACTORY_TEST_WEIGHT_TOLERANCE_GRAMS", 10
            ),
            weight_stable_sample_count=_integer(
                values, "ECOBIN_FACTORY_WEIGHT_STABLE_SAMPLE_COUNT", 3
            ),
            weight_stable_max_spread_grams=_integer(
                values, "ECOBIN_FACTORY_WEIGHT_STABLE_MAX_SPREAD_GRAMS", 2
            ),
            weight_sample_interval_ms=_integer(
                values, "ECOBIN_FACTORY_WEIGHT_SAMPLE_INTERVAL_MS", 100
            ),
            weight_sample_timeout_ms=_integer(
                values, "ECOBIN_FACTORY_WEIGHT_SAMPLE_TIMEOUT_MS", 3000
            ),
            camera_review_ttl_ms=_integer(
                values, "ECOBIN_FACTORY_CAMERA_REVIEW_TTL_MS", 300000
            ),
            state_path=_text(
                values,
                "ECOBIN_FACTORY_TEST_STATE_PATH",
                "/var/lib/ecobin/factory-test/state.json",
            ),
            report_path=_text(
                values,
                "ECOBIN_FACTORY_TEST_REPORT_PATH",
                "/var/lib/ecobin/factory-test/report.json",
            ),
            photo_directory=_text(
                values,
                "ECOBIN_FACTORY_TEST_PHOTO_DIR",
                "/run/ecobin/factory-test/photos",
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.serial_port != "/dev/ttyS5":
            raise AcceptanceConfigurationError("factory UART must be /dev/ttyS5")
        if self.serial_baudrate != 115200 or self.protocol_mode != "fixed-frame":
            raise AcceptanceConfigurationError(
                "factory UART must use fixed-frame 115200"
            )
        if self.uart_port_count != 1:
            raise AcceptanceConfigurationError("factory UART port count must be 1")
        if (
            self.boot0_wpi,
            self.reset_wpi,
            self.boot0_active_level,
            self.reset_active_level,
        ) != (2, 5, 1, 1):
            raise AcceptanceConfigurationError(
                "factory BOOT0/NRST mapping must be wPi 2/5 active-high"
            )
        if not self.hardware_compatibility or len(self.hardware_compatibility) > 64:
            raise AcceptanceConfigurationError("hardware compatibility is invalid")
        for name, value in (
            ("stm32flash", self.stm32flash_path),
            ("gpio", self.gpio_path),
            ("state", self.state_path),
            ("report", self.report_path),
            ("photos", self.photo_directory),
        ):
            if not PurePosixPath(value).is_absolute():
                raise AcceptanceConfigurationError(f"{name} path must be absolute")
        if self.outside_camera == self.inside_camera:
            raise AcceptanceConfigurationError("camera roles must be distinct")
        for camera in (self.outside_camera, self.inside_camera):
            if not camera.startswith("/dev/v4l/by-id/"):
                raise AcceptanceConfigurationError(
                    "factory cameras must use stable V4L by-id paths"
                )
        if (self.weight_target_grams, self.weight_tolerance_grams) != (500, 10):
            raise AcceptanceConfigurationError(
                "factory weight gate must remain 500g +/-10g"
            )
        if (
            self.weight_stable_sample_count,
            self.weight_stable_max_spread_grams,
            self.weight_sample_interval_ms,
            self.weight_sample_timeout_ms,
        ) != (3, 2, 100, 3000):
            raise AcceptanceConfigurationError(
                "factory stable-weight gate must remain 3 samples / 2g spread / 100ms / 3000ms"
            )
        if self.camera_review_ttl_ms != 300000:
            raise AcceptanceConfigurationError(
                "factory camera review TTL must remain 300000ms"
            )
        state = PurePosixPath(self.state_path)
        report = PurePosixPath(self.report_path)
        photo = PurePosixPath(self.photo_directory)
        expected_root = PurePosixPath("/var/lib/ecobin/factory-test")
        if expected_root not in state.parents or expected_root not in report.parents:
            raise AcceptanceConfigurationError(
                "factory state and report must stay under their isolated root"
            )
        volatile_root = PurePosixPath("/run/ecobin/factory-test")
        if volatile_root not in photo.parents:
            raise AcceptanceConfigurationError(
                "factory photos must stay under the volatile isolated root"
            )

    def digest_document(self) -> dict[str, object]:
        """Return the exact non-secret hardware/test parameters being proven."""

        document = asdict(self)
        for key in ("state_path", "report_path", "photo_directory"):
            document.pop(key)
        return document

    def digest(self) -> str:
        encoded = json.dumps(
            self.digest_document(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def parse_environment_file(text: str) -> dict[str, str]:
    """Parse the simple KEY=value subset used by `/etc/ecobin/hardware.env`."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AcceptanceConfigurationError(
                f"hardware.env line {line_number} is invalid"
            )
        name, value = line.split("=", 1)
        if re.fullmatch(r"ECOBIN_[A-Z0-9_]+", name) is None:
            raise AcceptanceConfigurationError(
                f"hardware.env line {line_number} has an invalid name"
            )
        if name in values:
            raise AcceptanceConfigurationError(
                f"hardware.env contains duplicate {name}"
            )
        if value.startswith(("'", '"')) or "$" in value or "`" in value:
            raise AcceptanceConfigurationError(
                f"hardware.env {name} uses unsupported expansion or quoting"
            )
        values[name] = value
    return values

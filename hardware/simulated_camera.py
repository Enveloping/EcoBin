"""Explicit, dependency-free camera sources for hardware-free edge tests."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

SIMULATED_CAMERA_PREFIX = "simulated://"
_SIMULATED_CAMERA_PATTERN = re.compile(
    r"^simulated://[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
)

# Small valid JPEG used as the visual payload.  A per-capture JPEG COM segment
# is inserted before EOI so outside/inside captures and repeated captures have
# different content hashes without requiring OpenCV, Pillow, or a V4L2 device.
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.\' \"$#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00{1\xc0q@\xe2\x01\x00\x00\x00\x00"
    b"\x00\x00\x00"
    b"\xff\xd9"
)


def is_simulated_camera_source(source: object) -> bool:
    return (
        isinstance(source, str)
        and _SIMULATED_CAMERA_PATTERN.fullmatch(source) is not None
    )


def simulated_camera_name(source: str) -> str:
    if not is_simulated_camera_source(source):
        raise ValueError(
            "simulated camera source must match "
            "simulated://[A-Za-z0-9._-]+"
        )
    return source[len(SIMULATED_CAMERA_PREFIX):]


def capture_simulated_camera(path: str, source: str) -> None:
    """Write a valid, uniquely identifiable JPEG for one simulated capture."""
    camera_name = simulated_camera_name(source)
    captured_at = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    comment = (
        f"EcoBin simulated camera={camera_name} "
        f"capturedAt={captured_at} captureUid={uuid.uuid4()}"
    ).encode("ascii")
    comment_segment = (
        b"\xff\xfe"
        + (len(comment) + 2).to_bytes(2, "big", signed=False)
        + comment
    )
    jpeg = _MINIMAL_JPEG[:-2] + comment_segment + _MINIMAL_JPEG[-2:]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as output:
        output.write(jpeg)

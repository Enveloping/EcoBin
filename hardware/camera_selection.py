"""Resolve stable camera roles with current hardware preferred over backups."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable


V4L_BY_ID_PREFIX = "/dev/v4l/by-id/"
SIMULATED_PREFIX = "simulated://"

CURRENT_OUTSIDE_CAMERA = (
    V4L_BY_ID_PREFIX
    + "usb-HSK-260323-J_UNIQUESKY_CAR_CAMERA-video-index0"
)
CURRENT_INSIDE_CAMERA = (
    V4L_BY_ID_PREFIX
    + "usb-Generic_USB_Camera_200901010001-video-index0"
)
LEGACY_OUTSIDE_CAMERA = (
    V4L_BY_ID_PREFIX
    + "usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0"
)
LEGACY_INSIDE_CAMERA = (
    V4L_BY_ID_PREFIX
    + "usb-icSpring_icspring_camera-video-index0"
)


class CameraSelectionError(ValueError):
    """The configured camera role candidates are ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class CameraRoleSelection:
    outside_source: str
    inside_source: str
    outside_uses_fallback: bool
    inside_uses_fallback: bool


def _validate_source(source: str, role: str) -> str:
    if (
        not isinstance(source, str)
        or not source
        or len(source) > 255
        or any(ord(character) < 0x20 for character in source)
    ):
        raise CameraSelectionError(f"{role} camera source is invalid")
    if not source.startswith((V4L_BY_ID_PREFIX, SIMULATED_PREFIX)):
        raise CameraSelectionError(
            f"{role} camera must use a stable V4L by-id path "
            "or an explicit simulated source"
        )
    return source


def _candidates(primary: str, fallback: str, role: str) -> tuple[str, ...]:
    primary = _validate_source(primary.strip(), role)
    fallback = fallback.strip()
    if not fallback:
        return (primary,)
    fallback = _validate_source(fallback, f"legacy {role}")
    if fallback == primary:
        return (primary,)
    return (primary, fallback)


def _is_available(source: str, exists: Callable[[str], bool]) -> bool:
    return source.startswith(SIMULATED_PREFIX) or exists(source)


def resolve_camera_roles(
    *,
    outside_primary: str,
    inside_primary: str,
    outside_fallback: str = "",
    inside_fallback: str = "",
    exists: Callable[[str], bool] | None = None,
) -> CameraRoleSelection:
    """Select each role once, preferring its current model when present.

    If neither candidate currently exists, the primary path is retained.  The
    service's normal device probe then fails closed with that precise role;
    silently borrowing the other role's camera is never allowed.
    """

    outside_candidates = _candidates(
        outside_primary,
        outside_fallback,
        "outside",
    )
    inside_candidates = _candidates(
        inside_primary,
        inside_fallback,
        "inside",
    )
    overlap = set(outside_candidates).intersection(inside_candidates)
    if overlap:
        raise CameraSelectionError(
            "outside and inside camera candidates must be distinct"
        )

    path_exists = os.path.exists if exists is None else exists

    def choose(candidates: tuple[str, ...]) -> tuple[str, bool]:
        for index, candidate in enumerate(candidates):
            if _is_available(candidate, path_exists):
                return candidate, index > 0
        return candidates[0], False

    outside, outside_uses_fallback = choose(outside_candidates)
    inside, inside_uses_fallback = choose(inside_candidates)
    if outside == inside:
        raise CameraSelectionError(
            "outside and inside cameras resolved to the same source"
        )
    return CameraRoleSelection(
        outside_source=outside,
        inside_source=inside,
        outside_uses_fallback=outside_uses_fallback,
        inside_uses_fallback=inside_uses_fallback,
    )

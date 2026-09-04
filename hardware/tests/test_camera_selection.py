from __future__ import annotations

import pytest

from camera_selection import (
    CURRENT_INSIDE_CAMERA,
    CURRENT_OUTSIDE_CAMERA,
    LEGACY_INSIDE_CAMERA,
    LEGACY_OUTSIDE_CAMERA,
    CameraSelectionError,
    resolve_camera_roles,
)


def _selection(present: set[str]):
    return resolve_camera_roles(
        outside_primary=CURRENT_OUTSIDE_CAMERA,
        inside_primary=CURRENT_INSIDE_CAMERA,
        outside_fallback=LEGACY_OUTSIDE_CAMERA,
        inside_fallback=LEGACY_INSIDE_CAMERA,
        exists=lambda source: source in present,
    )


def test_current_camera_models_win_when_current_and_legacy_are_both_present() -> None:
    selection = _selection(
        {
            CURRENT_OUTSIDE_CAMERA,
            CURRENT_INSIDE_CAMERA,
            LEGACY_OUTSIDE_CAMERA,
            LEGACY_INSIDE_CAMERA,
        }
    )

    assert selection.outside_source == CURRENT_OUTSIDE_CAMERA
    assert selection.inside_source == CURRENT_INSIDE_CAMERA
    assert selection.outside_uses_fallback is False
    assert selection.inside_uses_fallback is False


def test_each_role_independently_uses_its_legacy_model_as_fallback() -> None:
    selection = _selection({LEGACY_OUTSIDE_CAMERA, LEGACY_INSIDE_CAMERA})

    assert selection.outside_source == LEGACY_OUTSIDE_CAMERA
    assert selection.inside_source == LEGACY_INSIDE_CAMERA
    assert selection.outside_uses_fallback is True
    assert selection.inside_uses_fallback is True


def test_missing_role_keeps_primary_path_so_the_normal_probe_fails_closed() -> None:
    selection = _selection(set())

    assert selection.outside_source == CURRENT_OUTSIDE_CAMERA
    assert selection.inside_source == CURRENT_INSIDE_CAMERA
    assert selection.outside_uses_fallback is False
    assert selection.inside_uses_fallback is False


def test_simulated_sources_are_explicitly_available_without_filesystem_nodes() -> None:
    selection = resolve_camera_roles(
        outside_primary="simulated://outside",
        inside_primary="simulated://inside",
        exists=lambda _source: False,
    )

    assert selection.outside_source == "simulated://outside"
    assert selection.inside_source == "simulated://inside"


@pytest.mark.parametrize(
    "outside,inside",
    (
        ("/dev/video0", CURRENT_INSIDE_CAMERA),
        (CURRENT_OUTSIDE_CAMERA, "/dev/video1"),
        (CURRENT_OUTSIDE_CAMERA, CURRENT_OUTSIDE_CAMERA),
    ),
)
def test_unsafe_or_overlapping_role_candidates_are_rejected(
    outside: str,
    inside: str,
) -> None:
    with pytest.raises(CameraSelectionError):
        resolve_camera_roles(
            outside_primary=outside,
            inside_primary=inside,
        )

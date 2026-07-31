from __future__ import annotations

import hashlib

import cv2
import pytest

from simulated_camera import (
    capture_simulated_camera,
    is_simulated_camera_source,
    simulated_camera_name,
)


@pytest.mark.parametrize(
    "source",
    [
        "simulated://outside",
        "simulated://inside",
        "simulated://camera-01",
        "simulated://camera_test",
    ],
)
def test_accepts_explicit_simulated_camera_names(source):
    assert is_simulated_camera_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "simulated://",
        "simulated:///outside",
        "simulated://outside/path",
        "simulated://two words",
        "/dev/video0",
        1,
        None,
    ],
)
def test_rejects_ambiguous_simulated_camera_sources(source):
    assert not is_simulated_camera_source(source)


def test_simulated_capture_writes_decodable_and_distinct_jpegs(tmp_path):
    outside = tmp_path / "outside.jpg"
    inside = tmp_path / "inside.jpg"

    capture_simulated_camera(
        str(outside),
        "simulated://outside",
    )
    capture_simulated_camera(
        str(inside),
        "simulated://inside",
    )

    outside_bytes = outside.read_bytes()
    inside_bytes = inside.read_bytes()
    assert outside_bytes.startswith(b"\xff\xd8")
    assert outside_bytes.endswith(b"\xff\xd9")
    assert b"camera=outside" in outside_bytes
    assert b"camera=inside" in inside_bytes
    assert hashlib.sha256(outside_bytes).digest() != hashlib.sha256(
        inside_bytes
    ).digest()
    assert cv2.imread(str(outside)) is not None
    assert cv2.imread(str(inside)) is not None


def test_each_capture_has_a_unique_content_identity(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"

    capture_simulated_camera(str(first), "simulated://outside")
    capture_simulated_camera(str(second), "simulated://outside")

    assert first.read_bytes() != second.read_bytes()
    assert simulated_camera_name("simulated://outside") == "outside"

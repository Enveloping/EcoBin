from __future__ import annotations

import subprocess
import threading
import time

import pytest

import camera_capture
from camera_capture import (
    EXPOSURE_INITIAL_DISCARD_SECONDS,
    EXPOSURE_STABILITY_FRAMES,
    EXPOSURE_STABILITY_RELATIVE_RANGE,
    EXPOSURE_STABILITY_TIMEOUT_SECONDS,
    read_exposure_stable_frame,
    run_camera_captures,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeFrame:
    def __init__(self, name: str, brightness: float) -> None:
        self.name = name
        self.brightness = brightness

    def mean(self) -> float:
        return self.brightness


class FakeCv2:
    COLOR_BGR2GRAY = 6

    @staticmethod
    def cvtColor(frame: FakeFrame, conversion: int) -> FakeFrame:
        assert conversion == FakeCv2.COLOR_BGR2GRAY
        return frame


class FakeCapture:
    def __init__(
        self,
        clock: FakeClock,
        brightnesses: list[float | None],
        *,
        frame_seconds: float,
    ) -> None:
        self.clock = clock
        self.brightnesses = iter(brightnesses)
        self.frame_seconds = frame_seconds
        self.read_count = 0

    def read(self):
        self.read_count += 1
        self.clock.advance(self.frame_seconds)
        try:
            brightness = next(self.brightnesses)
        except StopIteration:
            brightness = None
        if brightness is None:
            return False, None
        return True, FakeFrame(f"frame-{self.read_count}", brightness)


def _read(
    brightnesses: list[float | None],
    *,
    frame_seconds: float = 0.1,
) -> tuple[FakeFrame | None, FakeCapture, FakeClock]:
    clock = FakeClock()
    capture = FakeCapture(
        clock,
        brightnesses,
        frame_seconds=frame_seconds,
    )
    frame = read_exposure_stable_frame(
        capture,
        FakeCv2,
        monotonic=clock.monotonic,
    )
    return frame, capture, clock


def test_exposure_constants_are_the_fixed_device_policy() -> None:
    assert EXPOSURE_INITIAL_DISCARD_SECONDS == 0.5
    assert EXPOSURE_STABILITY_TIMEOUT_SECONDS == 5.0
    assert EXPOSURE_STABILITY_FRAMES == 5
    assert EXPOSURE_STABILITY_RELATIVE_RANGE == 0.05


def test_discards_frames_for_half_a_second_then_uses_five_frame_window() -> None:
    frame, capture, clock = _read(
        [250, 250, 250, 250, 250, 100, 101, 99, 100, 100]
    )

    assert frame is not None
    assert frame.name == "frame-10"
    assert capture.read_count == 10
    assert clock.value == pytest.approx(1.0)


def test_exactly_five_percent_is_not_stable_but_less_than_five_is() -> None:
    frame, capture, _clock = _read(
        [
            250,
            250,
            250,
            250,
            250,
            97.5,
            100,
            100,
            100,
            102.5,
            100,
        ]
    )

    assert frame is not None
    assert frame.name == "frame-11"
    assert capture.read_count == 11


def test_timeout_returns_last_valid_frame_without_quality_gate() -> None:
    frame, capture, clock = _read(
        [250, 100, 200, 100, 200, 100],
        frame_seconds=1.0,
    )

    assert frame is not None
    assert frame.name == "frame-6"
    assert capture.read_count == 6
    assert clock.value == pytest.approx(6.0)


@pytest.mark.parametrize("brightness", (0.0, 255.0))
def test_stable_all_black_or_white_frame_is_accepted(brightness: float) -> None:
    frame, _capture, _clock = _read(
        [brightness] * 10,
    )

    assert frame is not None
    assert frame.brightness == brightness


def test_no_valid_sampling_frame_returns_none() -> None:
    frame, _capture, _clock = _read(
        [None] * 6,
        frame_seconds=1.0,
    )

    assert frame is None


def test_camera_tasks_run_concurrently_and_report_independent_failures() -> None:
    barrier = threading.Barrier(2)
    thread_ids: set[int] = set()

    def successful_capture() -> None:
        thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)

    def failed_capture() -> None:
        thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)
        raise RuntimeError("camera failed")

    outcomes = run_camera_captures({
        "OUTSIDE": successful_capture,
        "INSIDE": failed_capture,
    })

    assert len(thread_ids) == 2
    assert outcomes["OUTSIDE"].error is None
    assert isinstance(outcomes["INSIDE"].error, RuntimeError)


def test_stuck_camera_task_has_a_bounded_group_wait() -> None:
    capture_started = threading.Event()
    release_capture = threading.Event()

    def stuck_capture() -> None:
        capture_started.set()
        release_capture.wait()

    started_at = time.monotonic()
    try:
        outcomes = run_camera_captures(
            {"OUTSIDE": stuck_capture},
            timeout_seconds=0.05,
        )
    finally:
        release_capture.set()

    assert capture_started.is_set()
    assert time.monotonic() - started_at < 1.0
    error = outcomes["OUTSIDE"].error
    assert isinstance(error, camera_capture.CameraCaptureError)
    assert error.code == "CAMERA_CAPTURE_TIMEOUT"


def test_v4l2_capture_process_is_killed_at_its_hard_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    blocked_worker = tmp_path / "blocked-camera-worker.py"
    blocked_worker.write_text(
        "import time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(camera_capture, "__file__", str(blocked_worker))
    destination = tmp_path / "capture.jpg"

    started_at = time.monotonic()
    with pytest.raises(camera_capture.CameraCaptureError) as raised:
        camera_capture.capture_v4l2_jpeg(
            "/dev/v4l/by-id/blocked-camera",
            destination,
            timeout_seconds=0.1,
        )

    assert time.monotonic() - started_at < 2.0
    assert raised.value.code == "CAMERA_CAPTURE_TIMEOUT"
    assert not destination.exists()


def test_exposure_analysis_error_is_normalized_by_capture_worker(
    tmp_path,
) -> None:
    clock = FakeClock()

    class BadFrameCapture:
        def __init__(self) -> None:
            self.released = False

        @staticmethod
        def isOpened() -> bool:
            return True

        def read(self):
            clock.advance(0.1)
            return True, object()

        def release(self) -> None:
            self.released = True

    capture = BadFrameCapture()

    class BrokenAnalysisCv2:
        CAP_V4L2 = 200
        COLOR_BGR2GRAY = 6

        @staticmethod
        def VideoCapture(source, backend):
            assert source == "/dev/v4l/by-id/broken-frame-camera"
            assert backend == BrokenAnalysisCv2.CAP_V4L2
            return capture

        @staticmethod
        def cvtColor(_frame, _conversion):
            raise ValueError("damaged frame")

        @staticmethod
        def imwrite(_destination, _frame):
            raise AssertionError("a damaged frame must not be written")

    with pytest.raises(camera_capture.CameraCaptureError) as raised:
        camera_capture._capture_v4l2_jpeg_in_process(
            "/dev/v4l/by-id/broken-frame-camera",
            tmp_path / "capture.jpg",
            cv2_module=BrokenAnalysisCv2,
            monotonic=clock.monotonic,
        )

    assert raised.value.code == "CAMERA_CAPTURE_FAILED"
    assert capture.released

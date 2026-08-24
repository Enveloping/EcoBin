"""Shared bounded exposure sampling and dual-camera concurrency."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


EXPOSURE_INITIAL_DISCARD_SECONDS = 0.5
EXPOSURE_STABILITY_TIMEOUT_SECONDS = 5.0
EXPOSURE_STABILITY_FRAMES = 5
EXPOSURE_STABILITY_RELATIVE_RANGE = 0.05
CAMERA_CAPTURE_PROCESS_TIMEOUT_SECONDS = 8.0
CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS = 9.0

CAMERA_CAPTURE_TIMEOUT = "CAMERA_CAPTURE_TIMEOUT"
CAMERA_CAPTURE_PROCESS_FAILED = "CAMERA_CAPTURE_PROCESS_FAILED"
CAMERA_CAPTURE_FAILED = "CAMERA_CAPTURE_FAILED"
CAMERA_OPEN_FAILED = "CAMERA_OPEN_FAILED"
CAMERA_WRITE_FAILED = "CAMERA_WRITE_FAILED"
OPENCV_NOT_INSTALLED = "OPENCV_NOT_INSTALLED"

_WORKER_EXIT_CODE_BY_ERROR = {
    OPENCV_NOT_INSTALLED: 20,
    CAMERA_OPEN_FAILED: 21,
    CAMERA_CAPTURE_FAILED: 22,
    CAMERA_WRITE_FAILED: 23,
    CAMERA_CAPTURE_PROCESS_FAILED: 24,
}
_WORKER_ERROR_BY_EXIT_CODE = {
    exit_code: error_code
    for error_code, exit_code in _WORKER_EXIT_CODE_BY_ERROR.items()
}


class CameraCaptureError(RuntimeError):
    """Stable failure raised at the bounded camera-process boundary."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code:
            raise ValueError("camera capture error code is required")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CameraCaptureOutcome:
    """The independent result of one camera task in a paired capture."""

    error: Exception | None = None


def read_exposure_stable_frame(
    capture: Any,
    cv2: Any,
    *,
    monotonic: Callable[[], float] | None = None,
) -> Any | None:
    """Return the latest exposure-stable frame or the last frame at timeout.

    Frames read during the initial half-second deliberately do not participate
    in either stability detection or timeout fallback.  Exposure stability is
    the only quality criterion: a consistently black or white stream is stable.
    """

    clock = monotonic or time.monotonic
    discard_deadline = clock() + EXPOSURE_INITIAL_DISCARD_SECONDS
    while clock() < discard_deadline:
        capture.read()

    stability_deadline = clock() + EXPOSURE_STABILITY_TIMEOUT_SECONDS
    brightness_window: deque[float] = deque(
        maxlen=EXPOSURE_STABILITY_FRAMES
    )
    last_frame = None
    while clock() < stability_deadline:
        ok, candidate = capture.read()
        if not ok or candidate is None:
            continue
        last_frame = candidate
        grayscale = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
        brightness_window.append(float(grayscale.mean()))
        if len(brightness_window) < EXPOSURE_STABILITY_FRAMES:
            continue
        window_mean = sum(brightness_window) / len(brightness_window)
        relative_range = (
            max(brightness_window) - min(brightness_window)
        ) / max(abs(window_mean), 1.0)
        if relative_range < EXPOSURE_STABILITY_RELATIVE_RANGE:
            return last_frame
    return last_frame


def _capture_v4l2_jpeg_in_process(
    source: str | int,
    destination: str | os.PathLike[str],
    *,
    cv2_module: Any | None = None,
    monotonic: Callable[[], float] | None = None,
) -> None:
    """Open, sample, and write one camera inside a disposable worker."""

    cv2 = cv2_module
    if cv2 is None:
        try:
            import cv2 as imported_cv2
        except ImportError as error:
            raise CameraCaptureError(OPENCV_NOT_INSTALLED) from error
        cv2 = imported_cv2

    capture = None
    try:
        try:
            if isinstance(source, str):
                capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
            else:
                capture = cv2.VideoCapture(source)
            opened = bool(capture.isOpened())
        except Exception as error:
            raise CameraCaptureError(CAMERA_OPEN_FAILED) from error
        if not opened:
            raise CameraCaptureError(CAMERA_OPEN_FAILED)
        try:
            frame = read_exposure_stable_frame(
                capture,
                cv2,
                monotonic=monotonic,
            )
        except CameraCaptureError:
            raise
        except Exception as error:
            raise CameraCaptureError(CAMERA_CAPTURE_FAILED) from error
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                # The disposable process closes the device on exit.  A release
                # failure must not hide the more useful open/read failure.
                pass

    if frame is None:
        raise CameraCaptureError(CAMERA_CAPTURE_FAILED)
    try:
        written = bool(cv2.imwrite(os.fspath(destination), frame))
    except Exception as error:
        raise CameraCaptureError(CAMERA_WRITE_FAILED) from error
    if not written:
        raise CameraCaptureError(CAMERA_WRITE_FAILED)


def _serialize_camera_source(source: str | int) -> tuple[str, str]:
    if isinstance(source, bool):
        raise CameraCaptureError(CAMERA_OPEN_FAILED)
    if isinstance(source, int):
        return "index", str(source)
    if isinstance(source, str) and source:
        return "path", source
    raise CameraCaptureError(CAMERA_OPEN_FAILED)


def capture_v4l2_jpeg(
    source: str | int,
    destination: str | os.PathLike[str],
    *,
    timeout_seconds: float = CAMERA_CAPTURE_PROCESS_TIMEOUT_SECONDS,
    runner: Callable[..., Any] | None = None,
) -> None:
    """Capture through a child process that is killed on a hard timeout.

    A Python thread cannot interrupt a V4L2 ``read()`` blocked inside OpenCV.
    The child process owns the camera handle, so ``subprocess.run`` can kill and
    reap that process if any open/read/release/write call exceeds the boundary.
    """

    if timeout_seconds <= 0:
        raise ValueError("camera capture timeout must be positive")
    source_kind, source_value = _serialize_camera_source(source)
    destination_path = os.path.abspath(os.fspath(destination))
    worker_path = os.path.abspath(__file__)
    execute = runner or subprocess.run
    command = [
        sys.executable,
        worker_path,
        "--capture-v4l2",
        source_kind,
        source_value,
        destination_path,
    ]
    try:
        completed = execute(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CameraCaptureError(CAMERA_CAPTURE_TIMEOUT) from error
    except OSError as error:
        raise CameraCaptureError(CAMERA_CAPTURE_PROCESS_FAILED) from error

    return_code = int(getattr(completed, "returncode", -1))
    if return_code != 0:
        code = _WORKER_ERROR_BY_EXIT_CODE.get(
            return_code,
            CAMERA_CAPTURE_PROCESS_FAILED,
        )
        raise CameraCaptureError(code)
    try:
        if os.path.getsize(destination_path) <= 0:
            raise OSError("captured file is empty")
    except OSError as error:
        raise CameraCaptureError(CAMERA_CAPTURE_FAILED) from error


def run_camera_captures(
    tasks: Mapping[str, Callable[[], None]],
    *,
    timeout_seconds: float = CAMERA_CAPTURE_GROUP_TIMEOUT_SECONDS,
) -> dict[str, CameraCaptureOutcome]:
    """Run at most one outside/inside pair and retain independent failures."""

    if not tasks:
        return {}
    if len(tasks) > 2:
        raise ValueError("a camera capture group can contain at most two tasks")
    if timeout_seconds <= 0:
        raise ValueError("camera capture group timeout must be positive")

    completed = {name: threading.Event() for name in tasks}
    task_errors: dict[str, BaseException] = {}
    error_lock = threading.Lock()

    def execute_task(
        name: str,
        task: Callable[[], None],
    ) -> None:
        try:
            task()
        except BaseException as error:
            with error_lock:
                task_errors[name] = error
        finally:
            completed[name].set()

    deadline = time.monotonic() + timeout_seconds
    threads = []
    for name, task in tasks.items():
        thread = threading.Thread(
            target=execute_task,
            args=(name, task),
            name=f"camera-capture-{name.lower()}",
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    for name in tasks:
        remaining = max(0.0, deadline - time.monotonic())
        completed[name].wait(remaining)

    outcomes: dict[str, CameraCaptureOutcome] = {}
    fatal_error: BaseException | None = None
    for name in tasks:
        if not completed[name].is_set():
            outcomes[name] = CameraCaptureOutcome(
                error=CameraCaptureError(CAMERA_CAPTURE_TIMEOUT),
            )
            continue
        error = task_errors.get(name)
        if error is None:
            outcomes[name] = CameraCaptureOutcome()
        elif isinstance(error, Exception):
            outcomes[name] = CameraCaptureOutcome(error=error)
        elif fatal_error is None:
            fatal_error = error

    if fatal_error is not None:
        raise fatal_error
    return outcomes


def _capture_worker_main(arguments: list[str]) -> int:
    if len(arguments) != 4 or arguments[0] != "--capture-v4l2":
        return _WORKER_EXIT_CODE_BY_ERROR[CAMERA_CAPTURE_PROCESS_FAILED]
    source_kind, source_value, destination = arguments[1:]
    if source_kind == "index":
        try:
            source: str | int = int(source_value)
        except ValueError:
            return _WORKER_EXIT_CODE_BY_ERROR[CAMERA_OPEN_FAILED]
    elif source_kind == "path":
        source = source_value
    else:
        return _WORKER_EXIT_CODE_BY_ERROR[CAMERA_OPEN_FAILED]
    try:
        _capture_v4l2_jpeg_in_process(source, destination)
    except CameraCaptureError as error:
        return _WORKER_EXIT_CODE_BY_ERROR.get(
            error.code,
            _WORKER_EXIT_CODE_BY_ERROR[CAMERA_CAPTURE_PROCESS_FAILED],
        )
    except Exception:
        return _WORKER_EXIT_CODE_BY_ERROR[CAMERA_CAPTURE_PROCESS_FAILED]
    return 0


if __name__ == "__main__":
    raise SystemExit(_capture_worker_main(sys.argv[1:]))

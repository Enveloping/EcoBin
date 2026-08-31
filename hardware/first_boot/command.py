from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
import time
from typing import Literal, Sequence


_LONG_DIGITS = re.compile(r"(?<!\d)\d{15,22}(?!\d)")
_MAX_CAPTURE = 64 * 1024


@dataclass(frozen=True)
class CommandResult:
    return_code: int
    stdout: str
    elapsed_ms: int | None = None
    failure_kind: Literal["TIMEOUT", "EXEC_ERROR"] | None = None


def redact_command_output(raw: bytes) -> str:
    decoded = raw[:_MAX_CAPTURE].decode("utf-8", errors="replace")
    return _LONG_DIGITS.sub("[REDACTED]", decoded)


class CommandRunner:
    """Run fixed binaries without a shell, with bounded time and output."""

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        if not argv or not os.path.isabs(argv[0]):
            raise ValueError("external command must use an absolute executable path")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("external command timeout is invalid")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(argv),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                124,
                "",
                max(0, int((time.monotonic() - started) * 1000)),
                "TIMEOUT",
            )
        except OSError:
            return CommandResult(
                124,
                "",
                max(0, int((time.monotonic() - started) * 1000)),
                "EXEC_ERROR",
            )
        # stderr is intentionally discarded.  Callers expose stable error
        # codes only, never command text or modem identifiers.
        return CommandResult(
            completed.returncode,
            redact_command_output(completed.stdout),
            max(0, int((time.monotonic() - started) * 1000)),
        )

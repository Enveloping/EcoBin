from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Sequence


_LONG_DIGITS = re.compile(r"(?<!\d)\d{15,22}(?!\d)")
_MAX_CAPTURE = 64 * 1024


@dataclass(frozen=True)
class CommandResult:
    return_code: int
    stdout: str


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
        except (OSError, subprocess.TimeoutExpired):
            return CommandResult(124, "")
        # stderr is intentionally discarded.  Callers expose stable error
        # codes only, never command text or modem identifiers.
        return CommandResult(completed.returncode, redact_command_output(completed.stdout))

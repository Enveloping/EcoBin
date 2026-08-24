"""Clock-quality sampling and bounded NTP self-repair for the edge runtime.

Wall-clock timestamps are evidence only when the operating system reports an
NTP-synchronised clock.  Stable identifiers and SQLite sequences remain the
ordering authority regardless of this module's result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import subprocess
import time
from typing import Callable


MINIMUM_REASONABLE_UNIX_TIME = 1_735_689_600  # 2025-01-01T00:00:00Z
CLOCK_QUALITIES = frozenset({"SYNCED", "ESTIMATED", "UNAVAILABLE"})
_CHRONY_OFFSET = re.compile(
    r"^Last offset\s*:\s*([+-]?[0-9]+(?:\.[0-9]+)?)\s+seconds$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ClockSample:
    quality: str
    occurred_at: str | None
    offset_millis: int | None = None
    raw_observed_at: str | None = None

    @property
    def trusted(self) -> bool:
        return self.quality == "SYNCED"


def sample_clock() -> ClockSample:
    """Sample clock value and trust once so one fact cannot mix states."""

    now = time.time()
    if now < MINIMUM_REASONABLE_UNIX_TIME:
        return ClockSample(
            "UNAVAILABLE", None, None, _format_utc(now)
        )
    quality = _clock_quality()
    occurred_at = _format_utc(now) if quality == "SYNCED" else None
    return ClockSample(
        quality,
        occurred_at,
        _clock_offset_millis(quality),
        _format_utc(now),
    )


def event_clock_fields() -> dict[str, str | None]:
    sample = sample_clock()
    return {
        "occurredAt": sample.occurred_at,
        "clockQuality": sample.quality,
    }


def raw_utc_now() -> str:
    """Return raw wall-clock evidence without claiming that it is trusted."""

    return _format_utc(time.time())


def local_deadline_reference() -> datetime | None:
    """Return a comparison clock only when NTP trust is currently proven."""

    sample = sample_clock()
    if not sample.trusted or sample.occurred_at is None:
        return None
    return datetime.fromisoformat(
        sample.occurred_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)


class ClockHealthMonitor:
    """Track sustained clock degradation and rate-limit NTP repair attempts."""

    def __init__(
        self,
        *,
        warning_after_seconds: float = 300.0,
        repair_interval_seconds: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
        sampler: Callable[[], ClockSample] = sample_clock,
        repair: Callable[[], str] | None = None,
    ) -> None:
        if warning_after_seconds <= 0 or repair_interval_seconds <= 0:
            raise ValueError("clock monitor intervals must be positive")
        self.warning_after_seconds = float(warning_after_seconds)
        self.repair_interval_seconds = float(repair_interval_seconds)
        self._monotonic = monotonic
        self._sampler = sampler
        self._repair = repair or attempt_ntp_repair
        self._unsynced_since: float | None = None
        self._last_repair_at: float | None = None
        self._warning_active = False
        self.last_repair_state = "NOT_ATTEMPTED"

    def poll(self) -> dict[str, object]:
        now = self._monotonic()
        sample = self._sampler()
        recovered = False
        warning_due = False
        if sample.trusted:
            recovered = self._warning_active
            self._unsynced_since = None
            self._warning_active = False
            self.last_repair_state = "SYNCHRONIZED"
        else:
            if self._unsynced_since is None:
                self._unsynced_since = now
            if (
                self._last_repair_at is None
                or now - self._last_repair_at
                >= self.repair_interval_seconds
            ):
                self._last_repair_at = now
                self.last_repair_state = self._repair()
            if (
                not self._warning_active
                and now - self._unsynced_since
                >= self.warning_after_seconds
            ):
                self._warning_active = True
                warning_due = True
        return {
            "sample": sample,
            "warning_due": warning_due,
            "warning_active": self._warning_active,
            "recovered": recovered,
            "repair_state": self.last_repair_state,
        }


def attempt_ntp_repair() -> str:
    """Enable/restart an installed NTP provider; never set time directly."""

    if _is_windows():
        return "NOT_APPLICABLE"
    provider = _select_ntp_provider()
    if provider is None:
        return "PROVIDER_UNAVAILABLE"
    enabled = _run(
        ["systemctl", "enable", "--now", provider], timeout=15
    )
    restarted = _run(
        ["systemctl", "restart", provider], timeout=15
    )
    return (
        "REPAIR_REQUESTED"
        if enabled.returncode == 0 and restarted.returncode == 0
        else "REPAIR_FAILED"
    )


def _clock_quality() -> str:
    if _is_windows():
        return "SYNCED"
    if os.path.isfile("/run/systemd/timesync/synchronized"):
        return "SYNCED"
    result = _run(
        [
            "timedatectl",
            "show",
            "--property=NTPSynchronized",
            "--value",
        ],
        timeout=2,
    )
    if result.returncode == 0 and result.stdout.strip().lower() == "yes":
        return "SYNCED"
    return "ESTIMATED"


def _clock_offset_millis(quality: str) -> int | None:
    if quality != "SYNCED" or _is_windows():
        return None
    result = _run(["chronyc", "tracking"], timeout=2)
    if result.returncode != 0:
        return None
    match = _CHRONY_OFFSET.search(result.stdout)
    if match is None:
        return None
    try:
        return round(float(match.group(1)) * 1000)
    except ValueError:
        return None


def _select_ntp_provider() -> str | None:
    providers = ("chrony.service", "chronyd.service", "systemd-timesyncd.service")
    for provider in providers:
        active = _run(
            ["systemctl", "is-active", "--quiet", provider], timeout=3
        )
        if active.returncode == 0:
            return provider
    for provider in providers:
        available = _run(
            ["systemctl", "list-unit-files", provider, "--no-legend"],
            timeout=3,
        )
        if available.returncode == 0 and provider in available.stdout:
            return provider
    return None


def _is_windows() -> bool:
    return os.name == "nt"


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 127, "", "")


def _format_utc(unix_time: float) -> str:
    return datetime.fromtimestamp(
        unix_time, timezone.utc
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "CLOCK_QUALITIES",
    "ClockHealthMonitor",
    "ClockSample",
    "attempt_ntp_repair",
    "event_clock_fields",
    "local_deadline_reference",
    "raw_utc_now",
    "sample_clock",
]

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .command import CommandRunner


class TimeSyncResult(str, Enum):
    SYNCED = "SYNCED"
    PENDING = "PENDING"
    FAILED = "FAILED"


TIME_SYNC_FAILURE_REASON_CODES = frozenset(
    {
        "TIME_TRUST_QUERY_FAILED",
        "CHRONY_ONLINE_FAILED",
        "CHRONY_ACTIVITY_FAILED",
        "CHRONY_SOURCES_UNAVAILABLE",
        "CHRONY_REFRESH_FAILED",
        "CHRONY_BURST_FAILED",
        "CHRONY_WAITSYNC_FAILED",
        "TIME_SYNC_INTERNAL_ERROR",
    }
)
TIME_SYNC_PROJECTION_CODES = TIME_SYNC_FAILURE_REASON_CODES | {
    "TIME_SYNC_PENDING"
}


@dataclass(frozen=True)
class TimeSyncOutcome:
    state: TimeSyncResult
    reason_code: str

    def __post_init__(self) -> None:
        allowed = {
            TimeSyncResult.SYNCED: {"NONE"},
            TimeSyncResult.PENDING: {"TIME_SYNC_PENDING"},
            TimeSyncResult.FAILED: TIME_SYNC_FAILURE_REASON_CODES,
        }.get(self.state, frozenset())
        if self.reason_code not in allowed:
            raise ValueError("time sync outcome state and reason are inconsistent")


@dataclass(frozen=True)
class _ChronyActivity:
    online: int
    offline: int
    burst_to_online: int
    burst_to_offline: int
    unknown: int

    @property
    def resolved(self) -> int:
        return self.online + self.offline + self.bursting

    @property
    def bursting(self) -> int:
        return self.burst_to_online + self.burst_to_offline


_ACTIVITY_LINES = {
    "online": re.compile(r"^(\d+) sources? online$"),
    "offline": re.compile(r"^(\d+) sources? offline$"),
    "burst_to_online": re.compile(
        r"^(\d+) sources? doing burst \(return to online\)$"
    ),
    "burst_to_offline": re.compile(
        r"^(\d+) sources? doing burst \(return to offline\)$"
    ),
    "unknown": re.compile(r"^(\d+) sources? with unknown address$"),
}


class ChronyTimeSynchronizer:
    """Drive the installed chrony daemon only inside a proven uplink window."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def synchronize(self) -> TimeSyncOutcome:
        trusted = self._time_trusted()
        if trusted is None:
            return TimeSyncOutcome(
                TimeSyncResult.FAILED,
                "TIME_TRUST_QUERY_FAILED",
            )
        if trusted:
            return TimeSyncOutcome(TimeSyncResult.SYNCED, "NONE")

        online = self._runner.run(
            ("/usr/bin/chronyc", "online"), timeout_seconds=5
        )
        if online.return_code != 0:
            return TimeSyncOutcome(
                TimeSyncResult.FAILED,
                "CHRONY_ONLINE_FAILED",
            )

        activity = self._activity()
        if activity is None:
            return TimeSyncOutcome(
                TimeSyncResult.FAILED,
                "CHRONY_ACTIVITY_FAILED",
            )
        if activity.resolved == 0:
            if activity.unknown == 0:
                # chronyd has no configured source at all.  Refresh cannot
                # create one, so keeping any uplink open would not converge.
                return TimeSyncOutcome(
                    TimeSyncResult.FAILED,
                    "CHRONY_SOURCES_UNAVAILABLE",
                )
            # DNS is intentionally unavailable under the early emergency
            # firewall.  Refresh only when chronyd still has no resolved
            # source after the proven cellular DNS window opens.  Repeating
            # refresh for an already-resolved pool discards cold-boot samples.
            refresh = self._runner.run(
                ("/usr/bin/chronyc", "refresh"), timeout_seconds=5
            )
            if refresh.return_code != 0:
                return TimeSyncOutcome(
                    TimeSyncResult.FAILED,
                    "CHRONY_REFRESH_FAILED",
                )

        # A burst is asynchronous.  Do not stack another one while chronyd is
        # still completing the previous coordinator cycle.  Three bounded
        # observation windows keep this service responsive; PENDING tells the
        # caller to retain the restricted NTP egress window between cycles.
        for attempt in range(3):
            if activity.bursting == 0:
                burst = self._runner.run(
                    ("/usr/bin/chronyc", "burst", "4/8"),
                    timeout_seconds=5,
                )
                if burst.return_code != 0:
                    return TimeSyncOutcome(
                        TimeSyncResult.FAILED,
                        "CHRONY_BURST_FAILED",
                    )
            wait = self._runner.run(
                (
                    "/usr/bin/chronyc",
                    "waitsync",
                    "15",
                    "0",
                    "0",
                    "1",
                ),
                timeout_seconds=20,
            )
            # chronyc returns 1 when max-tries is reached without sync.  That
            # is a normal pending result; launch/IPC failures remain terminal
            # for this coordinator cycle.
            if wait.return_code not in {0, 1}:
                return TimeSyncOutcome(
                    TimeSyncResult.FAILED,
                    "CHRONY_WAITSYNC_FAILED",
                )
            trusted = self._time_trusted()
            if trusted is None:
                return TimeSyncOutcome(
                    TimeSyncResult.FAILED,
                    "TIME_TRUST_QUERY_FAILED",
                )
            if trusted:
                return TimeSyncOutcome(TimeSyncResult.SYNCED, "NONE")
            if attempt < 2:
                activity = self._activity()
                if activity is None:
                    return TimeSyncOutcome(
                        TimeSyncResult.FAILED,
                        "CHRONY_ACTIVITY_FAILED",
                    )
        return TimeSyncOutcome(TimeSyncResult.PENDING, "TIME_SYNC_PENDING")

    def _time_trusted(self) -> bool | None:
        result = self._runner.run(
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            timeout_seconds=5,
        )
        if result.return_code != 0:
            return None
        value = result.stdout.strip().lower()
        if value not in {"yes", "no"}:
            return None
        return value == "yes"

    def _activity(self) -> _ChronyActivity | None:
        result = self._runner.run(
            ("/usr/bin/chronyc", "activity"), timeout_seconds=5
        )
        if result.return_code != 0:
            return None
        counts: dict[str, int] = {}
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            for name, pattern in _ACTIVITY_LINES.items():
                match = pattern.fullmatch(line)
                if match is not None:
                    counts[name] = int(match.group(1))
                    break
        if counts.keys() != _ACTIVITY_LINES.keys():
            return None
        return _ChronyActivity(**counts)

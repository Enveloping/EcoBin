from __future__ import annotations

import pytest

from first_boot.command import CommandResult
from first_boot.time_sync import (
    ChronyTimeSynchronizer,
    TimeSyncOutcome,
    TimeSyncResult,
)


_RESOLVED_ACTIVITY = """200 OK
1 sources online
0 sources offline
0 sources doing burst (return to online)
0 sources doing burst (return to offline)
0 sources with unknown address
"""
_BURSTING_ACTIVITY = """200 OK
0 sources online
0 sources offline
1 sources doing burst (return to online)
0 sources doing burst (return to offline)
0 sources with unknown address
"""
_UNKNOWN_ACTIVITY = """200 OK
0 sources online
0 sources offline
0 sources doing burst (return to online)
0 sources doing burst (return to offline)
4 sources with unknown address
"""
_EMPTY_ACTIVITY = """200 OK
0 sources online
0 sources offline
0 sources doing burst (return to online)
0 sources doing burst (return to offline)
0 sources with unknown address
"""


@pytest.mark.parametrize(
    ("state", "reason_code"),
    (
        (TimeSyncResult.SYNCED, "CHRONY_ONLINE_FAILED"),
        (TimeSyncResult.PENDING, "NONE"),
        (TimeSyncResult.FAILED, "NONE"),
        (TimeSyncResult.FAILED, "UNRECOGNIZED_REASON"),
    ),
)
def test_time_sync_outcome_rejects_inconsistent_states(
    state: TimeSyncResult,
    reason_code: str,
) -> None:
    with pytest.raises(ValueError, match="time sync outcome"):
        TimeSyncOutcome(state, reason_code)


class _Runner:
    def __init__(
        self,
        *,
        clock_states: tuple[bool, ...],
        command_failures: frozenset[tuple[str, ...]] = frozenset(),
        activity_states: tuple[str, ...] = (),
        command_failure_code: int = 1,
    ) -> None:
        self._clock_states = iter(clock_states)
        self._failures = command_failures
        self._failure_code = command_failure_code
        self._activity_states = iter(activity_states)
        self._last_activity = (
            activity_states[-1] if activity_states else _RESOLVED_ACTIVITY
        )
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, timeout_seconds))
        if command in self._failures:
            return CommandResult(self._failure_code, "")
        if command[0] == "/usr/bin/timedatectl":
            return CommandResult(0, "yes\n" if next(self._clock_states) else "no\n")
        if command == ("/usr/bin/chronyc", "activity"):
            try:
                self._last_activity = next(self._activity_states)
            except StopIteration:
                pass
            return CommandResult(0, self._last_activity)
        if command[:2] == ("/usr/bin/chronyc", "waitsync"):
            return CommandResult(1, "")
        return CommandResult(0, "")


def test_already_trusted_clock_never_mutates_chrony_state() -> None:
    runner = _Runner(clock_states=(True,))

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.SYNCED
    )
    assert runner.calls == [
        (
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            5,
        )
    ]


def test_untrusted_clock_bursts_waits_and_rechecks_canonical_fact() -> None:
    runner = _Runner(clock_states=(False, True))

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.SYNCED
    )

    assert runner.calls == [
        (
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            5,
        ),
        (("/usr/bin/chronyc", "online"), 5),
        (("/usr/bin/chronyc", "activity"), 5),
        (("/usr/bin/chronyc", "burst", "4/8"), 5),
        (("/usr/bin/chronyc", "waitsync", "15", "0", "0", "1"), 20),
        (
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            5,
        ),
    ]
    assert all(call[0][0].startswith("/") for call in runner.calls)
    assert all(0 < call[1] <= 30 for call in runner.calls)


def test_chrony_command_failure_stops_bootstrap_and_fails_closed() -> None:
    failed = ("/usr/bin/chronyc", "online")
    runner = _Runner(
        clock_states=(False,),
        command_failures=frozenset({failed}),
        command_failure_code=2,
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_ONLINE_FAILED"
    assert [call[0] for call in runner.calls][-1] == failed
    assert not any("burst" in call[0] for call in runner.calls)


def test_chrony_activity_failure_has_a_stable_reason_code() -> None:
    failed = ("/usr/bin/chronyc", "activity")
    runner = _Runner(
        clock_states=(False,),
        command_failures=frozenset({failed}),
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_ACTIVITY_FAILED"


def test_chrony_burst_failure_has_a_stable_reason_code() -> None:
    failed = ("/usr/bin/chronyc", "burst", "4/8")
    runner = _Runner(
        clock_states=(False,),
        command_failures=frozenset({failed}),
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_BURST_FAILED"


def test_chrony_waitsync_failure_has_a_stable_reason_code() -> None:
    failed = ("/usr/bin/chronyc", "waitsync", "15", "0", "0", "1")
    runner = _Runner(
        clock_states=(False,),
        command_failures=frozenset({failed}),
        command_failure_code=2,
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_WAITSYNC_FAILED"


def test_time_trust_query_failure_has_a_stable_reason_code() -> None:
    failed = (
        "/usr/bin/timedatectl",
        "show",
        "--property=NTPSynchronized",
        "--value",
    )
    runner = _Runner(
        clock_states=(),
        command_failures=frozenset({failed}),
        command_failure_code=2,
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "TIME_TRUST_QUERY_FAILED"


def test_second_burst_can_accumulate_samples_without_refreshing_sources() -> None:
    runner = _Runner(clock_states=(False, False, True))

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.SYNCED
    )
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "refresh")
    ) == 0
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "burst", "4/8")
    ) == 2


def test_all_waitsync_attempts_require_timedatectl_confirmation() -> None:
    runner = _Runner(clock_states=(False, False, False, False))

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.PENDING
    )
    assert [call[0][0] for call in runner.calls].count("/usr/bin/timedatectl") == 4
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "refresh")
    ) == 0
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "burst", "4/8")
    ) == 3


def test_resolved_sources_are_not_refreshed_on_each_bounded_sync_window() -> None:
    """Keep chronyd measurements across coordinator retry windows.

    Cold-boot HIL proved that refreshing an already-resolved pool replaces the
    sources after their first measurement.  A new synchronizer instance is
    constructed on every cellular coordinator loop, so this assertion must
    hold without relying on in-memory state in the synchronizer.
    """

    runner = _Runner(clock_states=(False, False, False, False))

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.PENDING
    )
    assert ("/usr/bin/chronyc", "refresh") not in [
        call[0] for call in runner.calls
    ]


def test_unresolved_pool_is_refreshed_once_before_starting_a_burst() -> None:
    runner = _Runner(
        clock_states=(False, True),
        activity_states=(_UNKNOWN_ACTIVITY,),
    )

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.SYNCED
    )
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "refresh")
    ) == 1


def test_refresh_failure_for_unresolved_pool_fails_closed() -> None:
    failed = ("/usr/bin/chronyc", "refresh")
    runner = _Runner(
        clock_states=(False,),
        activity_states=(_UNKNOWN_ACTIVITY,),
        command_failures=frozenset({failed}),
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_REFRESH_FAILED"
    assert [call[0] for call in runner.calls][-1] == failed


def test_missing_chrony_sources_fail_without_retaining_a_dead_sync_window() -> None:
    runner = _Runner(
        clock_states=(False,),
        activity_states=(_EMPTY_ACTIVITY,),
    )

    outcome = ChronyTimeSynchronizer(runner).synchronize()

    assert outcome.state is TimeSyncResult.FAILED
    assert outcome.reason_code == "CHRONY_SOURCES_UNAVAILABLE"
    commands = [call[0] for call in runner.calls]
    assert ("/usr/bin/chronyc", "refresh") not in commands
    assert ("/usr/bin/chronyc", "burst", "4/8") not in commands


def test_existing_burst_is_observed_before_starting_another_one() -> None:
    runner = _Runner(
        clock_states=(False, False, True),
        activity_states=(_BURSTING_ACTIVITY, _RESOLVED_ACTIVITY),
    )

    assert (
        ChronyTimeSynchronizer(runner).synchronize().state
        is TimeSyncResult.SYNCED
    )
    commands = [call[0] for call in runner.calls]
    first_wait = commands.index(
        ("/usr/bin/chronyc", "waitsync", "15", "0", "0", "1")
    )
    first_burst = commands.index(("/usr/bin/chronyc", "burst", "4/8"))
    assert first_wait < first_burst
    assert commands.count(("/usr/bin/chronyc", "burst", "4/8")) == 1

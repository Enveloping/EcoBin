from __future__ import annotations

from first_boot.command import CommandResult
from first_boot.time_sync import ChronyTimeSynchronizer


class _Runner:
    def __init__(
        self,
        *,
        clock_states: tuple[bool, ...],
        command_failures: frozenset[tuple[str, ...]] = frozenset(),
    ) -> None:
        self._clock_states = iter(clock_states)
        self._failures = command_failures
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, timeout_seconds))
        if command[0] == "/usr/bin/timedatectl":
            return CommandResult(0, "yes\n" if next(self._clock_states) else "no\n")
        return CommandResult(1 if command in self._failures else 0, "")


def test_already_trusted_clock_never_mutates_chrony_state() -> None:
    runner = _Runner(clock_states=(True,))

    assert ChronyTimeSynchronizer(runner).synchronize()
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


def test_untrusted_clock_refreshes_bursts_waits_and_rechecks_canonical_fact() -> None:
    runner = _Runner(clock_states=(False, True))

    assert ChronyTimeSynchronizer(runner).synchronize()

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
        (("/usr/bin/chronyc", "refresh"), 5),
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
    failed = ("/usr/bin/chronyc", "refresh")
    runner = _Runner(
        clock_states=(False,),
        command_failures=frozenset({failed}),
    )

    assert not ChronyTimeSynchronizer(runner).synchronize()
    assert [call[0] for call in runner.calls][-1] == failed
    assert not any("burst" in call[0] for call in runner.calls)


def test_second_burst_can_accumulate_samples_without_refreshing_sources() -> None:
    runner = _Runner(clock_states=(False, False, True))

    assert ChronyTimeSynchronizer(runner).synchronize()
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "refresh")
    ) == 1
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "burst", "4/8")
    ) == 2


def test_all_waitsync_attempts_require_timedatectl_confirmation() -> None:
    runner = _Runner(clock_states=(False, False, False, False))

    assert not ChronyTimeSynchronizer(runner).synchronize()
    assert [call[0][0] for call in runner.calls].count("/usr/bin/timedatectl") == 4
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "refresh")
    ) == 1
    assert [call[0] for call in runner.calls].count(
        ("/usr/bin/chronyc", "burst", "4/8")
    ) == 3

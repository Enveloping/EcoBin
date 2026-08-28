from __future__ import annotations

from .command import CommandRunner


class ChronyTimeSynchronizer:
    """Drive the installed chrony daemon only inside a proven uplink window."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def synchronize(self) -> bool:
        if self._time_trusted():
            return True
        setup_commands = (
            (("/usr/bin/chronyc", "online"), 5),
            (("/usr/bin/chronyc", "refresh"), 5),
        )
        for command, timeout in setup_commands:
            result = self._runner.run(command, timeout_seconds=timeout)
            if result.return_code != 0:
                return False
        # Cold-boot HIL showed that a weak initial packet window can leave
        # every source with only one measurement.  Re-resolving the pool on
        # the next service loop then replaces those sources and loses the
        # samples.  Keep one resolved source set and allow three bounded
        # bursts to accumulate enough measurements for source selection.
        for _attempt in range(3):
            burst = self._runner.run(
                ("/usr/bin/chronyc", "burst", "4/8"),
                timeout_seconds=5,
            )
            if burst.return_code != 0:
                return False
            self._runner.run(
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
            if self._time_trusted():
                return True
        return False

    def _time_trusted(self) -> bool:
        result = self._runner.run(
            (
                "/usr/bin/timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ),
            timeout_seconds=5,
        )
        return result.return_code == 0 and result.stdout.strip().lower() == "yes"

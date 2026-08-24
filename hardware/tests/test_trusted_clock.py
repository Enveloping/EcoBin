from __future__ import annotations

import subprocess

import trusted_clock
from trusted_clock import ClockHealthMonitor, ClockSample


def test_sample_clock_marks_implausible_epoch_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(trusted_clock.time, "time", lambda: 1_700_000_000.0)

    sample = trusted_clock.sample_clock()

    assert sample.quality == "UNAVAILABLE"
    assert sample.occurred_at is None
    assert sample.offset_millis is None
    assert sample.raw_observed_at == "2023-11-14T22:13:20.000Z"


def test_sample_clock_preserves_raw_time_without_claiming_trust(monkeypatch) -> None:
    monkeypatch.setattr(trusted_clock.time, "time", lambda: 1_800_000_000.0)
    monkeypatch.setattr(trusted_clock, "_clock_quality", lambda: "ESTIMATED")

    sample = trusted_clock.sample_clock()

    assert sample == ClockSample(
        "ESTIMATED",
        None,
        None,
        "2027-01-15T08:00:00.000Z",
    )


def test_monitor_repairs_immediately_but_warns_only_after_five_minutes() -> None:
    elapsed = [0.0]
    samples = [
        ClockSample("ESTIMATED", None, None, "2026-01-01T00:00:00.000Z"),
        ClockSample("ESTIMATED", None, None, "2026-01-01T00:04:59.000Z"),
        ClockSample("ESTIMATED", None, None, "2026-01-01T00:05:00.000Z"),
        ClockSample(
            "SYNCED",
            "2026-01-01T00:06:00.000Z",
            1,
            "2026-01-01T00:06:00.000Z",
        ),
    ]
    repairs: list[str] = []
    monitor = ClockHealthMonitor(
        monotonic=lambda: elapsed[0],
        sampler=lambda: samples.pop(0),
        repair=lambda: repairs.append("repair") or "REPAIR_REQUESTED",
    )

    first = monitor.poll()
    elapsed[0] = 299.0
    before_warning = monitor.poll()
    elapsed[0] = 300.0
    warning = monitor.poll()
    elapsed[0] = 360.0
    recovered = monitor.poll()

    assert first["warning_due"] is False
    assert before_warning["warning_active"] is False
    assert warning["warning_due"] is True
    assert warning["warning_active"] is True
    assert recovered["recovered"] is True
    assert recovered["warning_active"] is False
    assert repairs == ["repair", "repair"]


def test_ntp_repair_only_controls_the_selected_time_service(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(trusted_clock, "_is_windows", lambda: False)
    monkeypatch.setattr(
        trusted_clock,
        "_select_ntp_provider",
        lambda: "chrony.service",
    )

    def run(command: list[str], *, timeout: float):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(trusted_clock, "_run", run)

    assert trusted_clock.attempt_ntp_repair() == "REPAIR_REQUESTED"
    assert commands == [
        ["systemctl", "enable", "--now", "chrony.service"],
        ["systemctl", "restart", "chrony.service"],
    ]
    assert all("date" not in command for command in commands)

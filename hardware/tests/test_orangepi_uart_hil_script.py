from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "orangepi_uart_hil.ps1"
)
PWSH = shutil.which("pwsh")


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    if PWSH is None:
        pytest.skip("PowerShell 7 is unavailable")
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(SCRIPT),
            *arguments,
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


def test_state_dry_run_builds_non_physical_query():
    result = run_script(
        "State",
        "-DryRun",
        "-EdgeBootId",
        "123",
        "-ConfigVersion",
        "456",
    )

    assert result.returncode == 0, result.stderr
    assert "action=State edgeBootId=123 configVersion=456" in result.stdout
    assert "{0}" not in result.stdout
    assert '""' not in result.stdout
    assert "--query-state" in result.stdout
    assert "--run-door-cycle" not in result.stdout
    assert "--safe-close-only" not in result.stdout


def test_door_cycle_requires_explicit_physical_confirmation():
    result = run_script(
        "DoorCycle",
        "-DryRun",
        "-EdgeBootId",
        "123",
        "-ConfigVersion",
        "456",
    )

    assert result.returncode != 0
    assert "ConfirmPhysicalAction" in (result.stdout + result.stderr)


def test_full_dry_run_contains_idempotency_door_and_snapshot_steps():
    result = run_script(
        "Full",
        "-DryRun",
        "-ConfirmPhysicalAction",
        "-EdgeBootId",
        "123",
        "-ConfigVersion",
        "456",
        "-DoorTravelWaitMs",
        "35000",
    )

    assert result.returncode == 0, result.stderr
    assert "--repeat-sample-configuration" in result.stdout
    assert "--run-door-cycle" in result.stdout
    assert "--query-state" in result.stdout
    assert "--door-travel-wait-ms 35000" in result.stdout


def test_safe_close_dry_run_never_opens_or_applies_configuration():
    result = run_script(
        "SafeClose",
        "-DryRun",
        "-EdgeBootId",
        "123",
        "-ConfigVersion",
        "456",
    )

    assert result.returncode == 0, result.stderr
    assert "--safe-close-only" in result.stdout
    assert "--query-state" in result.stdout
    assert "--run-door-cycle" not in result.stdout
    assert "--apply-sample-configuration" not in result.stdout

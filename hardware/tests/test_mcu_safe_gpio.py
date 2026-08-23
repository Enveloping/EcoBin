from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from system.mcu_safe_gpio import (
    GpioCommandError,
    SafeMcuGpioInitializer,
    WiringOpGpio,
    establish_safe_gpio_fact,
)


HARDWARE = Path(__file__).resolve().parents[1]


class RecordingRunner:
    def __init__(self, *, readback: dict[str, str] | None = None):
        self.calls: list[list[str]] = []
        self.readback = readback or {"2": "0\n", "5": "0\n"}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        stdout = ""
        if argv[1] == "read":
            stdout = self.readback[argv[2]]
        return SimpleNamespace(returncode=0, stdout=stdout)


def test_safe_gpio_is_idempotent_and_never_asserts_the_reset_gate():
    runner = RecordingRunner()
    initializer = SafeMcuGpioInitializer(
        WiringOpGpio("/usr/local/bin/gpio", command_runner=runner)
    )

    initializer.establish()
    initializer.establish()

    one_run = [
        ["/usr/local/bin/gpio", "mode", "2", "out"],
        ["/usr/local/bin/gpio", "write", "2", "0"],
        ["/usr/local/bin/gpio", "read", "2"],
        ["/usr/local/bin/gpio", "mode", "5", "out"],
        ["/usr/local/bin/gpio", "write", "5", "0"],
        ["/usr/local/bin/gpio", "read", "5"],
    ]
    assert runner.calls == one_run * 2
    assert [
        call for call in runner.calls if call[1:3] == ["write", "5"]
    ] == [
        ["/usr/local/bin/gpio", "write", "5", "0"],
        ["/usr/local/bin/gpio", "write", "5", "0"],
    ]


def test_safe_gpio_rejects_readback_mismatch_without_ever_writing_high():
    runner = RecordingRunner(readback={"2": "0\n", "5": "1\n"})
    initializer = SafeMcuGpioInitializer(
        WiringOpGpio("/usr/local/bin/gpio", command_runner=runner)
    )

    with pytest.raises(GpioCommandError, match="reset gate"):
        initializer.establish()

    writes = [call for call in runner.calls if call[1] == "write"]
    assert writes
    assert all(call[-1] == "0" for call in writes)
    assert writes.index(
        ["/usr/local/bin/gpio", "write", "2", "0"]
    ) < writes.index(
        ["/usr/local/bin/gpio", "write", "5", "0"]
    )


def test_safe_gpio_does_not_release_reset_if_boot0_cannot_be_proved_low():
    runner = RecordingRunner(readback={"2": "1\n", "5": "0\n"})
    initializer = SafeMcuGpioInitializer(
        WiringOpGpio("/usr/local/bin/gpio", command_runner=runner)
    )

    with pytest.raises(GpioCommandError, match="BOOT0"):
        initializer.establish()

    assert not any(call[1:3] == ["write", "5"] for call in runner.calls)


def test_safe_gpio_command_failure_is_bounded_and_cleanup_stays_low():
    calls: list[list[str]] = []
    failed = False

    def run(argv, **kwargs):
        nonlocal failed
        calls.append(list(argv))
        if argv[1:4] == ["write", "5", "0"] and not failed:
            failed = True
            return SimpleNamespace(returncode=1, stdout="ignored")
        stdout = "0\n" if argv[1] == "read" else ""
        return SimpleNamespace(returncode=0, stdout=stdout)

    initializer = SafeMcuGpioInitializer(
        WiringOpGpio("/usr/local/bin/gpio", command_runner=run)
    )

    with pytest.raises(GpioCommandError):
        initializer.establish()

    writes = [call for call in calls if call[1] == "write"]
    assert writes
    assert all(call[-1] == "0" for call in writes)


def test_current_boot_fact_is_published_only_after_gpio_readback(
    monkeypatch, tmp_path: Path
):
    calls: list[str] = []

    class Initializer:
        def establish(self):
            calls.append("established")

    monkeypatch.setattr(
        "system.mcu_safe_gpio._remove_previous_fact",
        lambda _path: calls.append("removed"),
    )
    monkeypatch.setattr(
        "system.mcu_safe_gpio._read_boot_id",
        lambda _path: "12345678-1234-4234-8234-123456789abc",
    )

    def write(_path, payload):
        calls.append("written")
        document = json.loads(payload)
        assert document["boot0"] == {"wpi": 2, "level": 0}
        assert document["resetGate"] == {"wpi": 5, "level": 0}

    monkeypatch.setattr("system.mcu_safe_gpio._atomic_write_root_fact", write)

    establish_safe_gpio_fact(
        Initializer(),  # type: ignore[arg-type]
        tmp_path / "boot-safe.json",
        tmp_path / "boot-id",
    )

    assert calls == ["removed", "established", "written"]


def test_failed_gpio_readback_never_publishes_current_boot_fact(
    monkeypatch, tmp_path: Path
):
    class Initializer:
        def establish(self):
            raise GpioCommandError("readback failed")

    writes: list[bytes] = []
    monkeypatch.setattr(
        "system.mcu_safe_gpio._remove_previous_fact", lambda _path: None
    )
    monkeypatch.setattr(
        "system.mcu_safe_gpio._atomic_write_root_fact",
        lambda _path, payload: writes.append(payload),
    )

    with pytest.raises(GpioCommandError):
        establish_safe_gpio_fact(
            Initializer(),  # type: ignore[arg-type]
            tmp_path / "boot-safe.json",
            tmp_path / "boot-id",
        )

    assert writes == []


def test_safe_gpio_systemd_unit_is_an_early_strict_oneshot():
    unit = (HARDWARE / "ecobin-mcu-safe-gpio.service").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in unit
    assert "User=root" in unit
    assert "EnvironmentFile=" not in unit
    assert "After=local-fs.target" in unit
    assert "Before=network-pre.target" in unit
    assert (
        "ExecStart=/usr/bin/python3 /usr/lib/ecobin/mcu_safe_gpio.py "
        "--gpio-path /usr/bin/gpio --fact-path "
        "/run/ecobin/mcu-safe-gpio/boot-safe.json --boot-id-path "
        "/proc/sys/kernel/random/boot_id"
    ) in unit
    assert "RuntimeDirectory=ecobin/mcu-safe-gpio" in unit
    assert "RuntimeDirectoryMode=0700" in unit
    assert "RuntimeDirectoryPreserve=yes" in unit
    assert "/opt/ecobin/hardware/current" not in unit
    assert "ConditionPathExists" not in unit
    assert "RemainAfterExit=yes" not in unit

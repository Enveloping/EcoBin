from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MCU_USER_ROOT = REPOSITORY_ROOT / "hardware_mcu" / "USER"
MCU_TEST_SOURCE = (
    REPOSITORY_ROOT
    / "hardware_mcu"
    / "tests"
    / "test_mcu_update_execution.c"
)


def _clang_executable() -> str:
    discovered = shutil.which("clang")
    if discovered:
        return discovered
    installed = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if installed.is_file():
        return str(installed)
    pytest.skip("Clang is required for the host-side MCU transition test")


def test_mcu_update_execution_transition_with_clang(tmp_path: Path) -> None:
    executable = tmp_path / "mcu_update_execution_test.exe"
    compile_result = subprocess.run(
        [
            _clang_executable(),
            "-std=c90",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(MCU_USER_ROOT),
            str(MCU_USER_ROOT / "mcu_update_execution.c"),
            str(MCU_TEST_SOURCE),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    run_result = subprocess.run(
        [str(executable)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stderr

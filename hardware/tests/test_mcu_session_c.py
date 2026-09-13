"""Candidate session core: real C state transitions, no serial port or GPIO."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USER = ROOT / "hardware_mcu" / "USER"


def test_mcu_session_core(tmp_path: Path) -> None:
    clang = shutil.which("clang")
    if not clang:
        installed = Path(r"C:\Program Files\LLVM\bin\clang.exe")
        if not installed.is_file():
            pytest.skip("Clang required for MCU host behavior tests")
        clang = str(installed)
    executable = tmp_path / "mcu_session.exe"
    compiled = subprocess.run(
        [clang, "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic",
         "-I", str(USER), str(USER / "mcu_session.c"),
         str(ROOT / "hardware_mcu/tests/test_mcu_session.c"), "-o", str(executable)],
        capture_output=True, text=True, timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mcu_session_target_compile(tmp_path: Path) -> None:
    """Compile, not execute: this is not a linked firmware or a hardware test."""
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required for Cortex-M3 candidate compile check")
    for source in (USER / "mcu_session.c", ROOT / "hardware_mcu/tests/test_mcu_session.c"):
        compiled = subprocess.run(
            [str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
             "-I", str(USER), "-c", str(source), "-o", str(tmp_path / (source.stem + ".o"))],
            capture_output=True, text=True, timeout=30,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        assert not compiled.stdout.strip() and not compiled.stderr.strip(), compiled.stdout + compiled.stderr

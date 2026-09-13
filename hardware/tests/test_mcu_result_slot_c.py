"""Real C result ownership, fed the same bytes used by EdgeStore tests."""
import subprocess
from pathlib import Path

import pytest
from hardware.tests.test_native_result_handoff import ROOT, result_payload


def test_mcu_result_slot_host(tmp_path):
    compiler = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if not compiler.is_file():
        pytest.skip("Clang required")
    user = ROOT / "hardware_mcu/USER"
    executable = tmp_path / "result.exe"
    compiled = subprocess.run([str(compiler), "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic",
        "-I", str(user), str(user / "mcu_result_slot.c"), str(ROOT / "hardware_mcu/tests/test_mcu_result_slot.c"),
        "-o", str(executable)], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    run = subprocess.run([str(executable), result_payload().hex(), result_payload(resultSequence=4).hex()],
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr


def test_mcu_result_slot_target_compile(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC 5 required")
    user = ROOT / "hardware_mcu/USER"
    result = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
        "-I", str(user), "-c", str(user / "mcu_result_slot.c"), "-o", str(tmp_path / "result.o")],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stdout.strip() and not result.stderr.strip()

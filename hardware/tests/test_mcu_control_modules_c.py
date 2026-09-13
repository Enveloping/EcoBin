"""Exercise MCU control interfaces with a host compiler, without real GPIO."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USER = ROOT / "hardware_mcu" / "USER"


@pytest.mark.parametrize("module", ["door_control", "runtime_clock", "clean_lock", "actuator_runtime", "native_delivery_cycle", "native_clean_pulse", "native_recovery_close", "weight_measurement", "scale_reader", "smoke_monitor"])
def test_mcu_control_module(module: str, tmp_path: Path) -> None:
    clang = shutil.which("clang")
    if not clang:
        installed = Path(r"C:\Program Files\LLVM\bin\clang.exe")
        if not installed.is_file():
            pytest.skip("Clang required for MCU host behavior tests")
        clang = str(installed)
    executable = tmp_path / f"{module}.exe"
    source = "actuator_runtime" if module in ("native_delivery_cycle", "native_clean_pulse", "native_recovery_close") else module
    dependencies = ["door_control", "clean_lock", "runtime_clock"] if source == "actuator_runtime" else []
    compiler_options = []
    if module == "smoke_monitor":
        dependencies = ["runtime_clock"]
        compiler_options = ["-include", str(ROOT / "hardware_mcu" / "tests" / "fake_adc.h")]
    compiled = subprocess.run(
        [clang, "-std=c90", "-Wall", "-Wextra", "-Werror", "-pedantic",
         *compiler_options,
         "-I", str(USER), str(USER / f"{source}.c"),
         *(str(USER / f"{dependency}.c") for dependency in dependencies),
         str(ROOT / "hardware_mcu" / "tests" / f"test_{module}.c"),
         "-o", str(executable)],
        capture_output=True, text=True, timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr

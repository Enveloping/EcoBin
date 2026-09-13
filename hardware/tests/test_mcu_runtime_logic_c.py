from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MCU_USER_ROOT = REPOSITORY_ROOT / "hardware_mcu" / "USER"
MCU_TEST_SOURCE = (
    REPOSITORY_ROOT / "hardware_mcu" / "tests" / "test_mcu_runtime_logic.c"
)
MCU_MAIN_SOURCE = MCU_USER_ROOT / "main.c"
MCU_USART_SOURCE = MCU_USER_ROOT / "usart1.c"


def _clang_executable() -> str:
    discovered = shutil.which("clang")
    if discovered:
        return discovered
    installed = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if installed.is_file():
        return str(installed)
    pytest.skip("Clang is required for the host-side MCU runtime test")


def test_mcu_runtime_logic_with_clang(tmp_path: Path) -> None:
    executable = tmp_path / "mcu_runtime_logic_test.exe"
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


def test_runtime_source_connects_validity_and_removes_debug_uart_frames() -> None:
    main_source = MCU_MAIN_SOURCE.read_text(encoding="utf-8")
    usart_source = MCU_USART_SOURCE.read_text(
        encoding="utf-8", errors="replace"
    )
    compact_main = re.sub(r"\s+", "", main_source)

    assert '#include"actuator_runtime.h"' in compact_main
    assert "g_weight_valid" in main_source
    assert "ActuatorRuntime_SetDoorTarget" in main_source
    assert "McuRuntime_DirectionAfterLimits" not in main_source
    assert "lock_timer_ticks" not in main_source
    assert "g_tick_count" not in main_source
    assert "Vision_SendPushRod(" not in main_source
    assert "voidVision_SendPushRod(" not in re.sub(r"\s+", "", usart_source)

    delivery_start = main_source.index("case 0xAA:")
    delivery_end = main_source.index("case 0xBB:", delivery_start)
    delivery_block = re.sub(
        r"\s+", "", main_source[delivery_start:delivery_end]
    )
    assert "g_weight_valid" in delivery_block
    assert "data==0x00" not in delivery_block

    clean_start = main_source.index("case 0xEE:")
    clean_end = main_source.index("case 0xF0:", clean_start)
    clean_block = re.sub(r"\s+", "", main_source[clean_start:clean_end])
    assert "g_weight_valid" in clean_block
    assert "data==0x00" not in clean_block

    self_test_start = main_source.index("case 0xF0:")
    self_test_end = main_source.index("case 0xF2:", self_test_start)
    self_test_block = re.sub(
        r"\s+", "", main_source[self_test_start:self_test_end]
    )
    assert "if(g_weight_valid)" in self_test_block
    assert "report_weight" in self_test_block


def test_screen_repeat_unlock_requires_active_clean_operation() -> None:
    main_source = MCU_MAIN_SOURCE.read_text(encoding="utf-8")
    repeat_unlock_start = main_source.index("case 0x07:")
    repeat_unlock_end = main_source.index(
        "}  /* end switch */", repeat_unlock_start
    )
    repeat_unlock_block = re.sub(
        r"\s+", "", main_source[repeat_unlock_start:repeat_unlock_end]
    )

    assert "if(cleaning_state==CLEAN_WAIT_CONFIRM&&" in repeat_unlock_block
    assert "cleaning_pre_weight_valid)" in repeat_unlock_block
    assert "ActuatorRuntime_Unlock(CLEAN_LOCK_PULSE_MS)" in repeat_unlock_block
    assert "cleaning_state=CLEAN_LOCK_ON;" in repeat_unlock_block
    assert "SUO=1;" not in repeat_unlock_block


def test_clean_screen_returns_home_only_after_result_is_accepted() -> None:
    main_source = MCU_MAIN_SOURCE.read_text(encoding="utf-8")
    clean_finish_start = main_source.index("case 0x05:")
    clean_finish_end = main_source.index(
        "}  /* end switch */", clean_finish_start
    )
    clean_finish_block = re.sub(
        r"\s+", "", main_source[clean_finish_start:clean_finish_end]
    )

    accepted_start = clean_finish_block.index(
        "if(cleaning_state==CLEAN_WAIT_CONFIRM&&"
        "cleaning_pre_weight_valid&&g_weight_valid){"
    )
    accepted_end = clean_finish_block.index("}matched=1;", accepted_start)
    accepted_block = clean_finish_block[accepted_start:accepted_end]

    result_send = accepted_block.index("Vision_SendCleaningResult(")
    page_change = accepted_block.index('UART3_SendPage("page0");')
    assert result_send < page_change


def test_clean_start_selects_dedicated_clean_screen() -> None:
    main_source = MCU_MAIN_SOURCE.read_text(encoding="utf-8")
    clean_start = main_source.index("case 0xEE:")
    clean_end = main_source.index("case 0xF0:", clean_start)
    clean_block = re.sub(r"\s+", "", main_source[clean_start:clean_end])

    assert 'UART3_SendPage("page8");' in clean_block
    assert 'UART3_SendPage("page1");' not in clean_block

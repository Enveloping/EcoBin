"""Actual thin board transport C tests plus explicit firmware-source integration."""
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_actual_native_serial_buffer_and_single_scale_request(tmp_path):
    compiler = Path('C:/Program Files/LLVM/bin/clang.exe')
    if not compiler.is_file():
        pytest.skip('cached Clang required')
    executable = tmp_path / 'board-serial.exe'
    completed = subprocess.run([str(compiler), '-std=c99', '-Wall', '-Wextra', '-Werror',
        '-I', str(ROOT / 'hardware_mcu/USER'), str(ROOT / 'hardware_mcu/USER/native_serial_buffer.c'),
        str(ROOT / 'hardware_mcu/tests/native_serial_buffer_test.c'), '-o', str(executable)],
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    subprocess.run([str(executable)], check=True, timeout=10)


def test_main_uses_one_native_parser_and_real_drivers():
    source = (ROOT / 'hardware_mcu/USER/main.c').read_text(encoding='utf-8')
    for symbol in ('McuControlEndpoint_Feed', 'McuWorkPreparation_Poll', 'McuDeliveryExecution_Attach',
                   'McuCleanExecution_Attach', 'NativeUsart_SendScaleQuery', 'McuWeightRun_FinishOwnedAttempt',
                   'McuWeightRun_FinishIdleAttempt', 'McuDeliveryExecution_CloseCurrent'):
        assert symbol in source
    assert 'Vision_Process(' not in source
    assert 'McuSafeCloseExecution_Attach' not in source
    assert 'McuCleanExecution_Confirm(' not in source
    assert 'ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE)' in source
    assert source.index('    tick_init();') < source.index('    (void)ADC1_TryInit();')
    assert 'scale_poll();\n        hmi_poll();' in source


def test_native_keil_links_generated_protocol_once_and_uses_real_memory_limits():
    import xml.etree.ElementTree as ET
    target = ET.parse(ROOT / 'hardware_mcu/USER/STM32-DEMO.uvprojx').getroot().find('Targets/Target')
    names = [item.text for item in target.findall('Groups/Group/Files/File/FileName')]
    assert names.count('main.c') == 1 and 'main_legacy.c' not in names
    assert names.count('ecobin_uart_protocol.c') == 1
    assert 'mcu_safe_close_execution.c' not in names
    assert 'native_serial_buffer.c' in names and 'ultrasonic_stm32.c' in names
    linker = (ROOT / 'hardware_mcu/native_firmware.sct').read_text(encoding='utf-8')
    assert '0x08000000 0x10000' in linker and '0x20000000 0x5000' in linker
    assert 'startup_stm32f10x_md.o (RESET, +First)' in linker

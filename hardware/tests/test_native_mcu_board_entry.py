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
                   'McuWeightRun_FinishIdleAttempt', 'McuDeliveryExecution_CloseCurrent',
                   'McuDeliveryExecution_RequestSelection',
                   'McuWorkPreparation_AttachDeviceEntryUrl', 'UART3_TrySendQRCode',
                   'McuEnvironmentMonitor_StartUltrasonic', 'McuEnvironmentMonitor_PollUltrasonic'):
        assert symbol in source
    assert 'Vision_Process(' not in source
    assert 'McuSafeCloseExecution_Attach' not in source
    assert 'McuCleanExecution_Confirm(' not in source
    assert 'ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE)' in source
    assert source.index('    tick_init();') < source.index('    (void)ADC1_TryInit();')
    assert 'scale_poll();\n        hmi_poll();' in source
    idle_monitor = source[
        source.index('static void idle_fullness_poll(void)'):
        source.index('static void hmi_poll(void)')
    ]
    assert idle_monitor.index('McuEnvironmentMonitor_PollUltrasonic') < idle_monitor.index(
        'control.work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING'
    ) < idle_monitor.index('RuntimeClock_PeriodDue') < idle_monitor.index(
        'McuEnvironmentMonitor_StartUltrasonic'
    )
    for guard in ('preparation.fullness_enabled', 'preparation.recovery_active',
                  'preparation.baseline_active', 'control.work.result.held',
                  'snapshot.update_latched'):
        assert guard in idle_monitor
    assert 'preparation.fullness.present' not in idle_monitor
    assert 'IDLE_FULLNESS_PERIOD_MS 1000u' in source
    assert 'McuFullnessRun_Interrupt' not in idle_monitor
    assert 'UltrasonicReader_' not in idle_monitor
    foreground = source[source.index('    for (;;) {'):]
    assert foreground.index('control_poll();') < foreground.index(
        'idle_fullness_poll();'
    ) < foreground.index('McuWorkPreparation_Poll')


def test_hmi_url_writer_has_one_bounded_atomic_queue_operation():
    source = (ROOT / 'hardware_mcu/USER/usart3.c').read_text(encoding='utf-8')
    assert 'static const uint8_t prefix[] = "page0.qr0.txt=\\\"";' in source
    assert "static const uint8_t suffix[] = {'\\\"', 0xffu, 0xffu, 0xffu};" in source
    helper = source[source.index('static uint8_t hmi_try_write'):
                    source.index('/* UART3 接收缓冲区 */')]
    assert 'NativeRx_WriteAtomic(&NativeHmiTx, spans, count)' in helper
    assert helper.index('__disable_irq();') < helper.index('NativeRx_WriteAtomic') < helper.index('__set_PRIMASK(previous);')
    block = source[source.index('uint8_t UART3_TrySendQRCode'):]
    assert 'return hmi_try_write(spans, 3u);' in block
    assert 'UART3_SendByte' not in block
    # A later display update may be rejected, but cannot erase an already
    # accepted QR command from the TX ring.
    assert 'NativeRx_DiscardOverflow(&NativeHmiTx)' not in source
    assert 'NativeRx_PushIrq(&NativeHmiTx' not in source
    assert 'USART_IT_TC' not in source and 'USART_FLAG_TC' not in source
    assert source.count('hmi_try_write(') == 5


def test_hmi_page_and_live_display_batches_retire_state_only_after_queue_success():
    source = (ROOT / 'hardware_mcu/USER/main.c').read_text(encoding='utf-8')
    display = source[source.index('static void display_poll(void)'):
                     source.index('static void display_live_weight(void)')]
    live = source[source.index('static void display_live_weight(void)'):
                  source.index('int main(void)')]

    assert 'UART3_CommandBatchAppendPage(&display_batch, "page0")' in display
    assert 'UART3_CommandBatchAppendPage(&display_batch, "page6")' in display
    assert 'UART3_CommandBatchAppendPage(&display_batch, "page7")' in display
    assert 'UART3_CommandBatchAppendPage(&display_batch, "page8")' in display
    queued = display.index('if (has_batch && !UART3_TrySendBatch(&display_batch)) return;')
    assert queued < display.index('display_phase = phase;')
    assert queued < display.index('display_status = status;')
    assert queued < display.index('displayed_scale_attempt = preparation.weight.attempt_sequence;')
    assert 'UART3_SendPage(' not in display
    assert 'UART3_SendScreenVal(' not in display
    assert 'UART3_SendVisible(' not in display
    assert 'control.work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN' in live
    assert live.index('if (UART3_TrySendBatch(&display_batch)) {') < live.index(
        'displayed_scale_attempt = observation.attempt_sequence;'
    )


def test_actual_uart3_batch_and_qr_queue_with_clang(tmp_path):
    compiler = Path('C:/Program Files/LLVM/bin/clang.exe')
    if not compiler.is_file():
        pytest.skip('cached Clang required')
    executable = tmp_path / 'uart3-atomic-batch.exe'
    completed = subprocess.run([
        str(compiler), '-std=c99', '-Wall', '-Wextra', '-Werror',
        '-D__CC_ARM', '-DUSE_STDPERIPH_DRIVER',
        '-D__get_PRIMASK=HostGetPrimask',
        '-D__disable_irq=HostDisableIrq', '-D__set_PRIMASK=HostSetPrimask',
        '-include', str(ROOT / 'hardware_mcu/tests/uart3_host_intrinsics.h'),
        '-I', str(ROOT / 'hardware_mcu/USER'),
        '-I', str(ROOT / 'hardware_mcu/CMSIS'),
        '-I', str(ROOT / 'hardware_mcu/FWlib/inc'),
        str(ROOT / 'hardware_mcu/USER/native_serial_buffer.c'),
        str(ROOT / 'hardware_mcu/USER/usart3.c'),
        str(ROOT / 'hardware_mcu/tests/uart3_atomic_batch_test.c'),
        '-o', str(executable),
    ], text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    subprocess.run([str(executable)], check=True, timeout=10)


def test_native_keil_links_generated_protocol_once_and_uses_real_memory_limits():
    import xml.etree.ElementTree as ET
    target = ET.parse(ROOT / 'hardware_mcu/USER/STM32-DEMO.uvprojx').getroot().find('Targets/Target')
    c_options = target.find('TargetOption/TargetArmAds/Cads')
    assert c_options.findtext('uC99') == '1'
    assert c_options.findtext('OneElfS') == '1'  # lets the linker remove unused generated-code sections
    names = [item.text for item in target.findall('Groups/Group/Files/File/FileName')]
    assert names.count('main.c') == 1 and 'main_legacy.c' not in names
    assert names.count('ecobin_uart_protocol.c') == 1
    assert 'mcu_safe_close_execution.c' not in names
    assert 'native_serial_buffer.c' in names and 'ultrasonic_stm32.c' in names
    assert names.count('mcu_device_entry_url.c') == 1
    linker = (ROOT / 'hardware_mcu/native_firmware.sct').read_text(encoding='utf-8')
    assert '0x08000000 0x10000' in linker and '0x20000000 0x5000' in linker
    assert 'startup_stm32f10x_md.o (RESET, +First)' in linker

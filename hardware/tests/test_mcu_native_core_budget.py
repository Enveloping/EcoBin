"""Link native cores together against real Cortex-M3 Flash/RAM capacity.

This is not production firmware, startup/driver integration or stack proof.
Never flash/execute the temporary image; only inspect the toolchain map.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_all_native_cores_link_within_target_capacity(tmp_path):
    compiler = Path(r"C:\D\002-Tools\004-DevTool\Keil5\ARM\ARMCC\bin\armcc.exe")
    if not compiler.is_file():
        pytest.skip("ARMCC5 and armlink required")
    user = ROOT / "hardware_mcu/USER"
    sources = [user / (name + ".c") for name in ("mcu_control_endpoint", "mcu_work_preparation", "mcu_device_entry_url", "mcu_opening_gate", "mcu_delivery_execution", "mcu_clean_execution", "mcu_safe_close_execution", "mcu_configuration", "mcu_config_collection",
        "mcu_session", "mcu_work_state", "mcu_result_slot", "mcu_result_builder", "mcu_process_measurement", "mcu_process_event_slot", "mcu_actuator_event_journal", "mcu_device_facts",
        "mcu_weight_run", "weight_measurement", "scale_reader", "actuator_runtime", "door_control", "clean_lock", "runtime_clock",
        "mcu_environment_monitor", "smoke_monitor", "ultrasonic_reader", "ultrasonic_stm32", "mcu_environment_ultrasonic", "mcu_fullness_run")]
    sources.extend(ROOT / "hardware_mcu/FWlib/src" / (name + ".c") for name in ("stm32f10x_tim", "stm32f10x_exti", "stm32f10x_rcc"))
    sources.append(ROOT / "hardware_mcu/CMSIS/core_cm3.c")
    sources.append(ROOT / "hardware_mcu/tests/native_core_budget.c")
    sources.append(ROOT / "contracts/uart/generated/c/ecobin_uart_protocol.c")
    objects = []
    for source in sources:
        output = tmp_path / (source.stem + ".o")
        run = subprocess.run([str(compiler), "--cpu", "Cortex-M3", "--c99", "-O2", "--apcs=interwork",
            "-DSTM32F10X_MD", "-DUSE_STDPERIPH_DRIVER",
            *(["--preinclude", str(ROOT / "hardware_mcu/tests/fake_adc.h")] if source.stem == "smoke_monitor" else []),
            "-I", str(user), "-I", str(ROOT / "hardware_mcu/CMSIS"), "-I", str(ROOT / "hardware_mcu/FWlib/inc"),
            "-c", str(source), "-o", str(output)], capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout + run.stderr
        assert not run.stdout.strip() and not run.stderr.strip()
        objects.append(str(output))
    run = subprocess.run([str(compiler.with_name("armlink.exe")), "--cpu", "Cortex-M3",
        "--scatter", str(ROOT / "hardware_mcu/tests/native_core_budget.sct"), "--entry", "NativeBudget_Entry",
        "--info", "sizes,totals", "--callgraph", "--map", "--symbols",
        "--output", str(tmp_path / "DO_NOT_FLASH_native_budget.axf"), *objects],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stderr.strip(), run.stderr
    for line in run.stdout.splitlines():
        if re.search(r"Total (RO|RW|ROM)|Grand Totals|Image component|Execution Region", line):
            print(line.strip())
    # Roots are kept, not optimized away into a deceptively small empty image.
    for symbol in ("McuEnvironmentMonitor_PollSmoke", "SmokeMonitor_UpdateSample",
                   "McuWorkPreparation_AttachFullness", "McuProcessMeasurement_BuildWorkEventWithFullness",
                   "McuFullnessRun_Begin", "McuFullnessRun_Poll", "McuFullnessRun_Copy", "McuFullnessRun_Interrupt", "McuFullnessRun_Retire",
                   "UltrasonicReader_Claim", "UltrasonicReader_Release", "UltrasonicReader_CancelOwned",
                   "UltrasonicReader_BeginOwned", "UltrasonicReader_CopyOwned", "UltrasonicReader_RetireOwned",
                   "UltrasonicStm32_Init", "TIM4_IRQHandler", "EXTI15_10_IRQHandler", "UltrasonicReader_Begin",
                   "McuEnvironmentMonitor_StartUltrasonic", "McuEnvironmentMonitor_PollUltrasonic",
                   "McuConfiguration_ReadFullnessPolicy", "McuConfigCollection_ReadFullnessPolicy",
                   "McuWeightRun_CopyObservation", "McuDeviceFacts_PublishScaleObservation",
                   "McuControlEndpoint_Feed", "McuWorkPreparation_Attach", "McuWorkPreparation_Poll",
                   "McuWorkPreparation_AttachDeviceEntryUrl", "McuDeviceEntryUrl_Receive", "McuDeviceEntryUrl_CopyResult",
                   "McuControlEndpoint_ReserveActuatorEvents", "McuControlEndpoint_PublishActuatorEvent",
                   "McuControlEndpoint_CancelActuatorEvents", "McuControlEndpoint_ConfirmActuatorEventSaved",
                   "McuActuatorEventJournal_Query", "McuActuatorEventJournal_Saved",
                   "McuDeliveryExecution_Attach", "McuDeliveryExecution_Select", "McuWorkPreparation_AttachActions", "McuWorkPreparation_PollMeasurement", "ActuatorRuntime_BeginDeliveryCycle",
                   "McuCleanExecution_Attach", "McuCleanExecution_Request", "McuCleanExecution_Confirm",
                   "McuSafeCloseExecution_Attach", "McuWorkPreparation_AttachRecovery", "ActuatorRuntime_BeginRecoveryClose",
                   "ActuatorRuntime_RecoveryClose", "ActuatorRuntime_ReleaseRecoveryClose",
                   "ActuatorRuntime_BeginCleanPulse", "ActuatorRuntime_CleanPulse", "ActuatorRuntime_ReleaseCleanPulse",
                   "McuWorkPreparation_CopyStart", "McuOpeningGate_Evaluate", "McuWorkState_CanBegin", "McuConfiguration_Receive", "McuResultBuilder_Complete",
                   "McuProcessMeasurement_BuildWorkEvent", "McuActuatorEventJournal_Reserve", "McuActuatorEventJournal_Freeze",
                   "McuActuatorEventJournal_ConfirmSaved", "McuWeightRun_Begin", "McuWeightRun_FinishOwnedAttempt",
                   "McuWeightRun_Interrupt", "WeightMeasurement_Interrupt", "WeightMeasurement_Poll", "ActuatorRuntime_Tick"):
        assert symbol in run.stdout

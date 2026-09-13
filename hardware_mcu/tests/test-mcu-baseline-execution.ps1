[CmdletBinding()]
param(
    [string]$Compiler = 'C:/Program Files/LLVM/bin/clang.exe',
    [string]$OutputDirectory = (Join-Path $env:TEMP 'ecobin-mcu-baseline-test')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$user = Join-Path $root 'USER'
$repo = Split-Path -Parent $root
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) { throw "Missing compiler: $Compiler" }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$executable = Join-Path $output 'test-mcu-baseline-execution.exe'
$sources = @(
    'mcu_control_endpoint', 'mcu_actuator_event_journal', 'mcu_work_preparation',
    'mcu_configuration', 'mcu_config_collection', 'mcu_session', 'mcu_work_state',
    'mcu_result_slot', 'mcu_result_builder', 'mcu_process_measurement',
    'mcu_process_event_slot', 'mcu_device_facts', 'mcu_weight_run',
    'weight_measurement', 'scale_reader', 'actuator_runtime', 'door_control',
    'clean_lock', 'runtime_clock', 'mcu_fullness_run', 'ultrasonic_reader',
    'mcu_environment_ultrasonic'
) | ForEach-Object { Join-Path $user ($_.ToString() + '.c') }
& $Compiler -std=c11 -Wall -Wextra -Werror -DECOBIN_UART_SHARED_PAYLOAD_VALIDATOR=1 `
    -I $user @sources `
    (Join-Path $repo 'contracts/uart/generated/c/ecobin_uart_protocol.c') `
    (Join-Path $PSScriptRoot 'test_mcu_baseline_execution.c') -o $executable
if ($LASTEXITCODE -ne 0) { throw 'Baseline execution test compile failed' }
& $executable
if ($LASTEXITCODE -ne 0) { throw 'Baseline execution test failed' }
Write-Output 'MCU baseline execution tests passed.'

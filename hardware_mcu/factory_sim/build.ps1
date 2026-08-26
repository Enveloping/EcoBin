[CmdletBinding()]
param(
    [string]$LlvmBin = "C:\Program Files\LLVM\bin",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$buildRoot = Join-Path $projectRoot "build"
$hostRoot = Join-Path $buildRoot "host"
$firmwareRoot = Join-Path $buildRoot "firmware"

if ($Clean -and (Test-Path -LiteralPath $buildRoot)) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $hostRoot, $firmwareRoot | Out-Null

$clang = Join-Path $LlvmBin "clang.exe"
$objcopy = Join-Path $LlvmBin "llvm-objcopy.exe"
$sizeTool = Join-Path $LlvmBin "llvm-size.exe"
foreach ($tool in @($clang, $objcopy, $sizeTool)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw "缺少 LLVM 工具: $tool"
    }
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败（exit=$LASTEXITCODE）: $Program $($Arguments -join ' ')"
    }
}

$include = Join-Path $projectRoot "include"
$core = Join-Path $projectRoot "src\factory_sim.c"
$test = Join-Path $projectRoot "tests\factory_sim_test.c"
$testExe = Join-Path $hostRoot "factory_sim_tests.exe"

Invoke-Checked $clang @(
    "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
    "-I$include", $core, $test, "-o", $testExe
)
Invoke-Checked $testExe @()

$targetFlags = @(
    "--target=arm-none-eabi", "-mcpu=cortex-m3", "-mthumb", "-std=c11", "-Os",
    "-ffreestanding", "-fno-builtin", "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
    "-Wall", "-Wextra", "-Werror", "-I$include"
)
$sources = @(
    (Join-Path $projectRoot "src\factory_sim.c"),
    (Join-Path $projectRoot "src\main.c"),
    (Join-Path $projectRoot "src\startup.c")
)
$objects = @()
foreach ($source in $sources) {
    $sourceBaseName = [System.IO.Path]::GetFileNameWithoutExtension($source)
    $object = Join-Path $firmwareRoot ($sourceBaseName + ".o")
    Invoke-Checked $clang ($targetFlags + @("-c", $source, "-o", $object))
    $objects += $object
}

$elf = Join-Path $firmwareRoot "ecobin-factory-sim.elf"
$map = Join-Path $firmwareRoot "ecobin-factory-sim.map"
$linkerScript = Join-Path $projectRoot "stm32f103c8.ld"
Invoke-Checked $clang (@(
    "--target=arm-none-eabi", "-mcpu=cortex-m3", "-mthumb", "-nostdlib",
    "-fuse-ld=lld", "-Wl,-T,$linkerScript", "-Wl,--gc-sections",
    "-Wl,-Map=$map"
) + $objects + @("-o", $elf))

$hex = Join-Path $firmwareRoot "ecobin-factory-sim.hex"
$bin = Join-Path $firmwareRoot "ecobin-factory-sim.bin"
Invoke-Checked $objcopy @("-O", "ihex", $elf, $hex)
Invoke-Checked $objcopy @("-O", "binary", $elf, $bin)

$sizeOutput = & $sizeTool --format=berkeley $elf
if ($LASTEXITCODE -ne 0) {
    throw "llvm-size 执行失败"
}
$sizeLine = $sizeOutput | Where-Object { $_ -match '^\s*\d+\s+\d+\s+\d+\s+' } |
    Select-Object -Last 1
if (-not $sizeLine -or $sizeLine -notmatch '^\s*(\d+)\s+(\d+)\s+(\d+)\s+') {
    throw "无法解析 llvm-size 输出: $($sizeOutput -join [Environment]::NewLine)"
}
$textBytes = [uint64]$Matches[1]
$dataBytes = [uint64]$Matches[2]
$bssBytes = [uint64]$Matches[3]
$flashBytes = [uint64](Get-Item -LiteralPath $bin).Length
$ramBytes = $dataBytes + $bssBytes
if ($flashBytes -gt 65536) {
    throw "固件 Flash 使用 $flashBytes 字节，超过 STM32F103C8 的 65536 字节"
}
if ($ramBytes -gt 20480) {
    throw "固件 RAM 使用 $ramBytes 字节，超过 STM32F103C8 的 20480 字节"
}

$sizeOutput | Write-Host
Write-Host "Flash: $flashBytes / 65536 bytes; RAM: $ramBytes / 20480 bytes"
Write-Host "ELF: $elf"
Write-Host "HEX: $hex"
Write-Host "BIN: $bin"

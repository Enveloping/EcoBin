[CmdletBinding()]
param(
    [string]$Toolchain = 'C:/D/002-Tools/004-DevTool/Keil5/ARM/ARMCC/bin',
    [string]$OutputDirectory = (Join-Path $env:TEMP 'ecobin-native-firmware'),
    [switch]$LegacyFixedFrame
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$userRoot = Join-Path $root 'USER'
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
foreach ($name in @('armcc.exe', 'armasm.exe', 'armlink.exe', 'fromelf.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Toolchain $name) -PathType Leaf)) { throw "Missing cached tool: $name" }
}
[xml]$project = Get-Content -LiteralPath (Join-Path $userRoot 'STM32-DEMO.uvprojx') -Raw
$target = $project.Project.Targets.Target | Where-Object { $_.TargetName -eq 'USART1-DEMO' }
if (-not $target) { throw 'Native firmware target missing' }
$objects = @()
$modeDefine = @()
if ($LegacyFixedFrame) { $modeDefine = @('-DECOBIN_LEGACY_FIXED_FRAME') }
foreach ($file in $target.Groups.Group.Files.File) {
    $source = [IO.Path]::GetFullPath((Join-Path $userRoot $file.FilePath))
    if ($LegacyFixedFrame -and $file.FileName -eq 'main.c') { $source = Join-Path $userRoot 'main_legacy.c' }
    $name = [IO.Path]::GetFileNameWithoutExtension($source)
    $object = Join-Path $output ($name + '.o')
    if ($objects -contains $object) { throw "Duplicate object $name" }
    if ($file.FileType -eq '2') {
        & (Join-Path $Toolchain 'armasm.exe') --cpu Cortex-M3 --apcs=interwork --pd '__MICROLIB SETA 1' $source -o $object
    } elseif ($file.FileType -eq '1') {
        & (Join-Path $Toolchain 'armcc.exe') --cpu Cortex-M3 --c99 -O2 --split_sections --apcs=interwork -D__MICROLIB -DSTM32F10X_MD -DUSE_STDPERIPH_DRIVER @modeDefine -I $userRoot -I (Join-Path $root 'CMSIS') -I (Join-Path $root 'FWlib/inc') -c $source -o $object
    } else { continue }
    if ($LASTEXITCODE -ne 0) { throw "Compile failed: $source" }
    $objects += $object
}
$basename = if ($LegacyFixedFrame) { 'ecobin-fixed-frame' } else { 'ecobin-native' }
$image = Join-Path $output ($basename + '.axf')
$map = Join-Path $output ($basename + '.map')
& (Join-Path $Toolchain 'armlink.exe') --cpu Cortex-M3 --library_type=microlib --scatter (Join-Path $root 'native_firmware.sct') --entry Reset_Handler --info sizes,totals --callgraph --map --symbols --list $map --output $image @objects
if ($LASTEXITCODE -ne 0) { throw 'Complete native firmware link failed' }
& (Join-Path $Toolchain 'fromelf.exe') --bin --output (Join-Path $output ($basename + '.bin')) $image
if ($LASTEXITCODE -ne 0) { throw 'BIN conversion failed' }
& (Join-Path $Toolchain 'fromelf.exe') --i32 --output (Join-Path $output ($basename + '.hex')) $image
if ($LASTEXITCODE -ne 0) { throw 'HEX conversion failed' }
Get-Content -LiteralPath $map | Select-String 'Total RO  Size|Total RW  Size|Total ROM Size' | ForEach-Object { $_.Line.Trim() }
Write-Output "Complete firmware: $image"
Write-Output "Map and callgraph: $map"
Write-Output 'Local compile only. Flashing/physical verification are separately authorized.'

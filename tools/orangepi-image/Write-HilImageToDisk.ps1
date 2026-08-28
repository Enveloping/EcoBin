[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [string]$ImagePath,

    [Parameter(Mandatory)]
    [ValidateRange(1, 1024)]
    [int]$DiskNumber,

    [Parameter(Mandatory)]
    [ValidateRange(1, 1024)]
    [int]$ConfirmDiskNumber,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._:-]{1,128}$')]
    [string]$ExpectedDiskSerial,

    [Parameter(Mandatory)]
    [ValidateRange(1, [long]::MaxValue)]
    [long]$ExpectedDiskBytes,

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedImageSha256,

    [Parameter(Mandatory)]
    [string]$ProgressLogPath,

    [Parameter(Mandatory)]
    [string]$ResultPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Write-FlashLog {
    param([Parameter(Mandatory)][string]$Message)

    $line = '{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $Message
    [IO.File]::AppendAllText(
        $script:ProgressLogPath,
        $line + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Output $line
}

if (-not (Test-IsAdministrator)) {
    throw 'Administrator privileges are required for raw disk access.'
}
if ($DiskNumber -ne $ConfirmDiskNumber) {
    throw 'DiskNumber and ConfirmDiskNumber must be identical.'
}

$ImagePath = [IO.Path]::GetFullPath($ImagePath)
$ProgressLogPath = [IO.Path]::GetFullPath($ProgressLogPath)
$ResultPath = [IO.Path]::GetFullPath($ResultPath)
$ExpectedImageSha256 = $ExpectedImageSha256.ToLowerInvariant()

foreach ($outputPath in @($ProgressLogPath, $ResultPath)) {
    $parent = Split-Path -Parent $outputPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $outputPath) {
        throw "Refusing to overwrite existing output: $outputPath"
    }
}

$startedAt = [DateTime]::UtcNow
Write-FlashLog 'START validation'

$image = Get-Item -LiteralPath $ImagePath
if (
    -not $image.Exists -or
    $image.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)
) {
    throw 'Image must be a regular non-reparse-point file.'
}
$imageBytes = [long]$image.Length
$actualImageSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $ImagePath
).Hash.ToLowerInvariant()
if ($actualImageSha256 -cne $ExpectedImageSha256) {
    throw 'Source image SHA-256 differs from the confirmed value.'
}

$disk = Get-Disk -Number $DiskNumber
$diskSerial = ([string]$disk.SerialNumber).Trim()
if ($diskSerial -cne $ExpectedDiskSerial) {
    throw 'Target disk serial number differs from the confirmed value.'
}
if ([long]$disk.Size -ne $ExpectedDiskBytes) {
    throw 'Target disk capacity differs from the confirmed value.'
}
if ($disk.IsBoot -or $disk.IsSystem) {
    throw 'Refusing to overwrite a boot or system disk.'
}
if ([string]$disk.BusType -cne 'USB') {
    throw 'Refusing to overwrite a non-USB disk.'
}
if ($disk.IsOffline -or $disk.IsReadOnly) {
    throw 'Target disk must be online and writable.'
}
if ($imageBytes -gt [long]$disk.Size) {
    throw 'Image is larger than the target disk.'
}

foreach ($partition in @(Get-Partition -DiskNumber $DiskNumber)) {
    $driveLetter = [string]$partition.DriveLetter
    $volume = $null
    try {
        $volume = $partition | Get-Volume -ErrorAction Stop
    }
    catch {
        # An unrecognised Linux filesystem may not expose a Windows volume.
    }
    if (
        -not [string]::IsNullOrWhiteSpace($driveLetter) -and
        [int][char]$driveLetter[0] -ne 0
    ) {
        throw 'Target disk has a mounted drive letter.'
    }
    if (
        $null -ne $volume -and
        -not [string]::IsNullOrWhiteSpace([string]$volume.FileSystem)
    ) {
        throw 'Target disk contains a filesystem mounted by Windows.'
    }
}

Write-FlashLog (
    'VALIDATED disk={0} serial={1} diskBytes={2} imageBytes={3}' -f
    $DiskNumber, $diskSerial, $disk.Size, $imageBytes
)

$rawDiskPath = '\\.\PhysicalDrive{0}' -f $DiskNumber
if (-not $PSCmdlet.ShouldProcess(
    "$rawDiskPath serial=$diskSerial",
    "overwrite and fully reread $imageBytes bytes"
)) {
    throw 'Raw disk write was not confirmed.'
}

$bufferSize = 4MB
$reportInterval = [long]128MB
$buffer = [byte[]]::new($bufferSize)
$writtenBytes = [long]0
$readbackBytes = [long]0
$readbackSha256 = $null

try {
    Write-FlashLog 'WRITE started'
    $source = [IO.FileStream]::new(
        $ImagePath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        $bufferSize,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $target = [IO.FileStream]::new(
            $rawDiskPath,
            [IO.FileMode]::Open,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::ReadWrite,
            $bufferSize,
            [IO.FileOptions]::WriteThrough
        )
        try {
            $nextReport = $reportInterval
            while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $target.Write($buffer, 0, $count)
                $writtenBytes += $count
                if (
                    $writtenBytes -ge $nextReport -or
                    $writtenBytes -eq $imageBytes
                ) {
                    Write-FlashLog (
                        "WRITE bytes=$writtenBytes total=$imageBytes"
                    )
                    while ($nextReport -le $writtenBytes) {
                        $nextReport += $reportInterval
                    }
                }
            }
            if ($writtenBytes -ne $imageBytes) {
                throw 'Written byte count differs from image size.'
            }
            $target.Flush($true)
        }
        finally {
            $target.Dispose()
        }
    }
    finally {
        $source.Dispose()
    }
    Write-FlashLog 'WRITE completed and flushed'

    Start-Sleep -Milliseconds 1000
    Write-FlashLog 'READBACK started'
    $hasher = [Security.Cryptography.IncrementalHash]::CreateHash(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    )
    try {
        $reader = [IO.FileStream]::new(
            $rawDiskPath,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite,
            $bufferSize,
            [IO.FileOptions]::SequentialScan
        )
        try {
            $nextReport = $reportInterval
            while ($readbackBytes -lt $imageBytes) {
                $remaining = $imageBytes - $readbackBytes
                $wanted = [int][Math]::Min([long]$buffer.Length, $remaining)
                $count = $reader.Read($buffer, 0, $wanted)
                if ($count -le 0) {
                    throw 'Unexpected end of target during full readback.'
                }
                $hasher.AppendData($buffer, 0, $count)
                $readbackBytes += $count
                if (
                    $readbackBytes -ge $nextReport -or
                    $readbackBytes -eq $imageBytes
                ) {
                    Write-FlashLog (
                        "READBACK bytes=$readbackBytes total=$imageBytes"
                    )
                    while ($nextReport -le $readbackBytes) {
                        $nextReport += $reportInterval
                    }
                }
            }
        }
        finally {
            $reader.Dispose()
        }
        $readbackSha256 = [BitConverter]::ToString(
            $hasher.GetHashAndReset()
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }

    if (
        $readbackBytes -ne $imageBytes -or
        $readbackSha256 -cne $ExpectedImageSha256
    ) {
        throw 'Full target readback differs from the source image.'
    }
    Write-FlashLog "READBACK verified sha256=$readbackSha256"
}
catch {
    Write-FlashLog "FAIL $($_.Exception.Message)"
    throw
}

$completedAt = [DateTime]::UtcNow
$result = [ordered]@{
    schemaVersion = 1
    artifactClass = 'HIL_FACTORY_LOGIN_IMAGE_FLASH_RESULT'
    startedAt = $startedAt.ToString('o')
    completedAt = $completedAt.ToString('o')
    diskNumber = $DiskNumber
    diskSerialNumber = $diskSerial
    diskBytes = [long]$disk.Size
    imageFileName = $image.Name
    imageBytes = $imageBytes
    expectedImageSha256 = $ExpectedImageSha256
    writtenBytes = $writtenBytes
    fullReadbackBytes = $readbackBytes
    fullReadbackSha256 = $readbackSha256
    rootPasswordLocked = $true
    result = 'PASS'
}
[IO.File]::WriteAllText(
    $ResultPath,
    ($result | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)
Write-FlashLog "PASS result=$ResultPath"
$result | ConvertTo-Json -Depth 4

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ImageZst,

    [Parameter(Mandatory)]
    [string]$ReleaseChecksums,

    [Parameter(Mandatory)]
    [string]$SignatureFile,

    [Parameter(Mandatory)]
    [string]$PublicKeyFile,

    [Parameter(Mandatory)]
    [string]$Manifest,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9][a-z0-9_-]{0,63}$')]
    [string]$SigningKeyId,

    [Parameter(Mandatory)]
    [ValidatePattern('^/dev/[A-Za-z0-9._/-]+$')]
    [string]$WslDevice,

    [Parameter(Mandatory)]
    [ValidatePattern('^/dev/[A-Za-z0-9._/-]+$')]
    [string]$ConfirmWslDevice,

    [string]$Distribution
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($WslDevice -cne $ConfirmWslDevice) {
    throw 'WslDevice and ConfirmWslDevice must be identical.'
}
if ($null -eq (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL2 is required. Map the removable disk into WSL before continuing.'
}

$scriptDirectory = Split-Path -Parent $PSCommandPath

function Convert-ToWslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)
    $arguments = @()
    if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
        $arguments += @('-d', $Distribution)
    }
    $arguments += @('--', 'wslpath', '-a', '-u', $WindowsPath)
    $converted = (& wsl.exe @arguments).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Unable to convert Windows path for WSL: $WindowsPath"
    }
    return $converted
}

$releaseFiles = @{
    ImageZst = [IO.Path]::GetFullPath($ImageZst)
    ReleaseChecksums = [IO.Path]::GetFullPath($ReleaseChecksums)
    SignatureFile = [IO.Path]::GetFullPath($SignatureFile)
    PublicKeyFile = [IO.Path]::GetFullPath($PublicKeyFile)
    Manifest = [IO.Path]::GetFullPath($Manifest)
}
foreach ($path in $releaseFiles.Values) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required signed-release file does not exist: $path"
    }
}

$wslArguments = @()
if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
    $wslArguments += @('-d', $Distribution)
}
$wslArguments += @(
    '--', 'sudo', 'bash',
    (Convert-ToWslPath (Join-Path $scriptDirectory 'flash-and-verify.sh')),
    '--image-zst', (Convert-ToWslPath $releaseFiles.ImageZst),
    '--checksums-file', (Convert-ToWslPath $releaseFiles.ReleaseChecksums),
    '--signature-file', (Convert-ToWslPath $releaseFiles.SignatureFile),
    '--public-key-file', (Convert-ToWslPath $releaseFiles.PublicKeyFile),
    '--manifest', (Convert-ToWslPath $releaseFiles.Manifest),
    '--signing-key-id', $SigningKeyId,
    '--device', $WslDevice,
    '--confirm-device', $ConfirmWslDevice
)
& wsl.exe @wslArguments
if ($LASTEXITCODE -ne 0) {
    throw "WSL signed image verification, card write, or reread failed with exit code $LASTEXITCODE."
}

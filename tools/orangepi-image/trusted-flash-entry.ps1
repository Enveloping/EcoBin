[CmdletBinding(DefaultParameterSetName = 'Write')]
param(
    [Parameter(Mandatory)][string]$ReleaseDirectory,
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9][a-z0-9_-]{0,63}$')]
    [string]$SigningKeyId,
    [Parameter(Mandatory, ParameterSetName = 'Write')]
    [ValidatePattern('^/dev/[A-Za-z0-9._/-]+$')]
    [string]$WslDevice,
    [Parameter(Mandatory, ParameterSetName = 'Write')]
    [ValidatePattern('^/dev/[A-Za-z0-9._/-]+$')]
    [string]$ConfirmWslDevice,
    [Parameter(Mandatory, ParameterSetName = 'Verify')][switch]$VerifyOnly,
    [string]$Distribution
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSCmdlet.ParameterSetName -eq 'Write' -and $WslDevice -cne $ConfirmWslDevice) {
    throw 'WslDevice and ConfirmWslDevice must be identical.'
}
if ($null -eq (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL2 is required. Install this trusted tool in a root-owned WSL path.'
}

function Convert-ToWslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)
    $arguments = @()
    if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
        $arguments += @('-d', $Distribution)
    }
    $arguments += @('--', 'wslpath', '-a', '-u', ([IO.Path]::GetFullPath($WindowsPath)))
    $converted = (& wsl.exe @arguments).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Unable to convert Windows path for WSL: $WindowsPath"
    }
    return $converted
}

$trustedShell = '/usr/local/lib/ecobin-image-factory/trusted-flash-entry.sh'
$wslArguments = @()
if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
    $wslArguments += @('-d', $Distribution)
}
$wslArguments += @(
    '--', 'sudo', 'bash', $trustedShell,
    '--release-dir', (Convert-ToWslPath $ReleaseDirectory),
    '--signing-key-id', $SigningKeyId
)
if ($PSCmdlet.ParameterSetName -eq 'Verify') {
    $wslArguments += '--verify-only'
} else {
    $wslArguments += @('--device', $WslDevice, '--confirm-device', $ConfirmWslDevice)
}
& wsl.exe @wslArguments
if ($LASTEXITCODE -ne 0) {
    throw "Trusted WSL release verification or card write failed with exit code $LASTEXITCODE."
}

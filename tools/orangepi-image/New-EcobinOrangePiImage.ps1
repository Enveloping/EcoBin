[CmdletBinding(DefaultParameterSetName = 'Build')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [string]$SourceArtifact,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [string]$OutputDirectory,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [ValidatePattern('^[a-z0-9][a-z0-9._-]{0,63}$')]
    [string]$ReleaseId,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [string]$SoftwarePayloadDirectory,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$SoftwarePayloadLockSha256,

    [Parameter(Mandatory, ParameterSetName = 'Build')]
    [Parameter(ParameterSetName = 'Validate')]
    [string]$TargetMediaQualificationEvidence,

    [Parameter(Mandatory, ParameterSetName = 'Validate')]
    [switch]$ValidateOnly,

    [string]$ConfigDirectory,
    [string]$Distribution,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $PSCommandPath
if ([string]::IsNullOrWhiteSpace($ConfigDirectory)) {
    $ConfigDirectory = $scriptDirectory
}
$ConfigDirectory = [IO.Path]::GetFullPath($ConfigDirectory)
if (-not (Test-Path -LiteralPath $ConfigDirectory -PathType Container)) {
    throw "Config directory does not exist: $ConfigDirectory"
}

function Invoke-Wsl {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $wslArguments = @()
    if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
        $wslArguments += @('-d', $Distribution)
    }
    $wslArguments += @('--')
    $wslArguments += $Arguments
    & wsl.exe @wslArguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code $LASTEXITCODE."
    }
}

function Convert-ToWslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)

    $wslArguments = @()
    if (-not [string]::IsNullOrWhiteSpace($Distribution)) {
        $wslArguments += @('-d', $Distribution)
    }
    $wslArguments += @('--', 'wslpath', '-a', '-u', $WindowsPath)
    $converted = (& wsl.exe @wslArguments).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Unable to convert Windows path for WSL: $WindowsPath"
    }
    return $converted
}

if ($null -eq (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL2 is required to run the Linux image builder.'
}

$buildScript = Convert-ToWslPath (Join-Path $scriptDirectory 'build-image.sh')
$builderLauncher = Convert-ToWslPath (Join-Path $scriptDirectory 'run-builder.sh')
$configPath = Convert-ToWslPath $ConfigDirectory

if ($ValidateOnly) {
    $validationArguments = @(
        'bash', $buildScript, '--validate-only', '--config-dir', $configPath)
    if (-not [string]::IsNullOrWhiteSpace($TargetMediaQualificationEvidence)) {
        $targetEvidencePath = [IO.Path]::GetFullPath($TargetMediaQualificationEvidence)
        if (-not (Test-Path -LiteralPath $targetEvidencePath -PathType Leaf)) {
            throw "Target-media qualification evidence does not exist: $targetEvidencePath"
        }
        $validationArguments += @(
            '--target-media-qualification-evidence',
            (Convert-ToWslPath $targetEvidencePath))
    }
    Invoke-Wsl -Arguments $validationArguments
    return
}

$sourcePath = [IO.Path]::GetFullPath($SourceArtifact)
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Source artifact does not exist: $sourcePath"
}
$softwarePayloadPath = [IO.Path]::GetFullPath($SoftwarePayloadDirectory)
if (-not (Test-Path -LiteralPath $softwarePayloadPath -PathType Container)) {
    throw "Software payload directory does not exist: $softwarePayloadPath"
}
$softwarePayloadLock = Join-Path $softwarePayloadPath 'software-payload.lock.json'
if (-not (Test-Path -LiteralPath $softwarePayloadLock -PathType Leaf)) {
    throw "Software payload lock does not exist: $softwarePayloadLock"
}
$outputFullPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputFullPath) {
    throw "Output directory already exists and will not be overwritten: $outputFullPath"
}
$outputParent = Split-Path -Parent $outputFullPath
if ([string]::IsNullOrWhiteSpace($outputParent)) {
    throw 'Output directory must have a parent directory.'
}
New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
$outputWslParent = Convert-ToWslPath $outputParent
$outputWslPath = (
    $outputWslParent.TrimEnd('/') + '/' +
        [IO.Path]::GetFileName($outputFullPath))
$sourceWslPath = Convert-ToWslPath $sourcePath
$softwarePayloadWslPath = Convert-ToWslPath $softwarePayloadPath
$targetEvidencePath = [IO.Path]::GetFullPath($TargetMediaQualificationEvidence)
if (-not (Test-Path -LiteralPath $targetEvidencePath -PathType Leaf)) {
    throw "Target-media qualification evidence does not exist: $targetEvidencePath"
}
$targetEvidenceWslPath = Convert-ToWslPath $targetEvidencePath

$arguments = @(
    'bash', $builderLauncher,
    '--source', $sourceWslPath,
    '--output-dir', $outputWslPath,
    '--release-id', $ReleaseId,
    '--version', $Version,
    '--software-payload', $softwarePayloadWslPath,
    '--software-payload-sha256', $SoftwarePayloadLockSha256,
    '--target-media-qualification-evidence', $targetEvidenceWslPath
)
if ($AllowDirty) {
    $arguments += '--allow-dirty'
}
Invoke-Wsl -Arguments $arguments

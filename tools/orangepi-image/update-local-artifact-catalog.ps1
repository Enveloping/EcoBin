[CmdletBinding()]
param(
    [string] $ArtifactRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $repositoryRoot 'hardware\image-artifacts'
}
$resolvedArtifactRoot = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$resolvedLocalRoot = (Resolve-Path -LiteralPath (Join-Path $resolvedArtifactRoot 'local')).Path
if (-not $resolvedArtifactRoot.StartsWith(
        $repositoryRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'ArtifactRoot must remain inside this repository.'
}

function Get-RelativeArtifactPath([string] $Path) {
    return $Path.Substring($resolvedArtifactRoot.Length + 1).Replace('\', '/')
}

function Get-LocalClassification([string] $RelativePath, [string] $Extension) {
    if ($RelativePath -match '^local/factory-secret/builds/.+\.img$') {
        return 'SECRET_BEARING_FACTORY_IMAGE'
    }
    if ($RelativePath -match '^local/candidates/.+\.img$') {
        return 'NO_SECRET_CANDIDATE_IMAGE'
    }
    if ($RelativePath -match '^local/upstream-cache/') {
        return 'UPSTREAM_SOURCE_ARTIFACT'
    }
    if ($RelativePath -match '^local/source-bundles/') {
        return 'SOURCE_SNAPSHOT'
    }
    if ($Extension -eq '.log') {
        return 'LOCAL_DIAGNOSTIC'
    }
    return 'LOCAL_ARTIFACT'
}

$localArtifacts = [System.Collections.Generic.List[object]]::new()
$localFiles = Get-ChildItem -LiteralPath $resolvedLocalRoot -Recurse -Force -File |
    Where-Object Name -ne '.gitkeep' |
    Sort-Object FullName
foreach ($file in $localFiles) {
    $relativePath = Get-RelativeArtifactPath $file.FullName
    if ($file.Extension -eq '.env') {
        $localArtifacts.Add([ordered]@{
            path = $relativePath
            bytes = [int64] $file.Length
            classification = 'SECRET_INPUT'
            digestRecorded = $false
        })
        continue
    }
    $localArtifacts.Add([ordered]@{
        path = $relativePath
        bytes = [int64] $file.Length
        classification = Get-LocalClassification $relativePath $file.Extension
        digestRecorded = $true
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    })
}

$trackedArtifacts = [System.Collections.Generic.List[object]]::new()
foreach ($directoryName in @('candidates', 'evidence', 'upstream')) {
    $directory = Join-Path $resolvedArtifactRoot $directoryName
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        continue
    }
    foreach ($file in Get-ChildItem -LiteralPath $directory -Recurse -Force -File |
            Sort-Object FullName) {
        $trackedArtifacts.Add([ordered]@{
            path = Get-RelativeArtifactPath $file.FullName
            bytes = [int64] $file.Length
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        })
    }
}

$catalog = [ordered]@{
    schemaVersion = 1
    artifactClass = 'ORANGEPI_IMAGE_ARTIFACT_CATALOG'
    catalogedAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    policy = [ordered]@{
        trackedContent = 'reproducibility metadata, checksums, detached signatures, public verification keys and redacted qualification evidence'
        localOnlyContent = 'raw images, upstream archives, secret inputs, local diagnostics and source bundles'
        secretInputDigestsRecorded = $false
        rawImagesTrackedByGit = $false
    }
    trackedArtifacts = $trackedArtifacts
    localArtifacts = $localArtifacts
}

$catalogPath = Join-Path $resolvedArtifactRoot 'artifact-catalog.json'
$json = $catalog | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText(
    $catalogPath,
    $json + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)

[pscustomobject]@{
    Catalog = $catalogPath
    TrackedArtifacts = $trackedArtifacts.Count
    LocalArtifacts = $localArtifacts.Count
    LocalBytes = ($localFiles | Measure-Object Length -Sum).Sum
    ProtectedInputs = ($localArtifacts |
        Where-Object classification -eq 'SECRET_INPUT').Count
}

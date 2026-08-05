[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [string]$ReleaseId,
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $PSCommandPath
$repositoryRoot = [IO.Path]::GetFullPath(
    (Join-Path $scriptDirectory '..\..'))
$utf8NoBom = [Text.UTF8Encoding]::new($false)

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter(Mandatory)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
        }
    }
    finally {
        Pop-Location
    }
}

function Copy-TextFileAsLf {
    param(
        [Parameter(Mandatory)]
        [string]$Source,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $content = [IO.File]::ReadAllText($Source)
    $content = $content.Replace("`r`n", "`n").Replace("`r", "`n")
    [IO.File]::WriteAllText($Destination, $content, $utf8NoBom)
}

function Write-LfTextFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string[]]$Lines
    )

    [IO.File]::WriteAllText(
        $Path,
        (($Lines -join "`n") + "`n"),
        $utf8NoBom)
}

$gitCommit = (& git -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $gitCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'Unable to resolve the current Git commit.'
}

$dirtyEntries = @(
    & git -C $repositoryRoot status --porcelain --untracked-files=normal |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
$sourceDirty = $dirtyEntries.Count -gt 0
if ($sourceDirty -and -not $AllowDirty) {
    throw ('The worktree is not clean. Commit or intentionally pass ' +
        '-AllowDirty for a non-production test bundle.')
}

if (-not [string]::IsNullOrWhiteSpace($ReleaseId) -and
    $ReleaseId -cnotmatch '^[a-z0-9][a-z0-9._-]{0,63}$') {
    throw ('ReleaseId must be 1-64 lowercase letters, numbers, dot, ' +
        'underscore, or hyphen.')
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot 'release-output'
}
elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot $OutputDirectory
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

if (-not $SkipBuild) {
    $javaDescription = (& java -version 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $javaDescription -notmatch '"21(?:\.|\")') {
        throw 'Java 21 is required to build the backend release artifact.'
    }

    $nodeVersion = (& node --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(?<major>[0-9]+)\.') {
        throw 'Node.js 20 or newer is required to build the Web artifact.'
    }
    if ([int]$Matches.major -lt 20) {
        throw 'Node.js 20 or newer is required to build the Web artifact.'
    }

    $isWindowsPlatform = [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [Runtime.InteropServices.OSPlatform]::Windows)
    $mavenWrapper = if ($isWindowsPlatform) {
        Join-Path $repositoryRoot 'mvnw.cmd'
    }
    else {
        Join-Path $repositoryRoot 'mvnw'
    }
    $npmCommand = if ($isWindowsPlatform) { 'npm.cmd' } else { 'npm' }

    $mavenArguments = @('clean', 'package')
    if ($SkipTests) {
        $mavenArguments += '-DskipTests'
    }
    Invoke-CheckedCommand `
        -FilePath $mavenWrapper `
        -ArgumentList $mavenArguments `
        -WorkingDirectory $repositoryRoot

    $webRoot = Join-Path $repositoryRoot 'frontend\web'
    Invoke-CheckedCommand `
        -FilePath $npmCommand `
        -ArgumentList @('ci') `
        -WorkingDirectory $webRoot
    Invoke-CheckedCommand `
        -FilePath $npmCommand `
        -ArgumentList @('run', 'build') `
        -WorkingDirectory $webRoot
}

$postBuildDirtyEntries = @(
    & git -C $repositoryRoot status --porcelain --untracked-files=normal |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
$sourceDirty = $postBuildDirtyEntries.Count -gt 0
if ($sourceDirty -and -not $AllowDirty) {
    throw ('The worktree changed during the build. Commit the changes or ' +
        'intentionally pass -AllowDirty for a non-production test bundle.')
}

if ([string]::IsNullOrWhiteSpace($ReleaseId)) {
    $timestamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
    $ReleaseId = "${timestamp}-$($gitCommit.Substring(0, 12))"
    if ($sourceDirty) {
        $ReleaseId += '-dirty'
    }
}

$backendTarget = Join-Path $repositoryRoot 'ecobin-bootstrap\target'
$backendJars = @(
    Get-ChildItem -LiteralPath $backendTarget -File `
        -Filter 'ecobin-bootstrap-*.jar' |
        Where-Object { $_.Name -notlike '*.original' }
)
if ($backendJars.Count -ne 1) {
    throw "Expected exactly one executable backend JAR, found $($backendJars.Count)."
}

$webDist = Join-Path $repositoryRoot 'frontend\web\dist'
if (-not (Test-Path -LiteralPath (Join-Path $webDist 'index.html') -PathType Leaf)) {
    throw 'The Web dist directory is missing index.html.'
}
$webReparsePoint = Get-ChildItem -LiteralPath $webDist -Force -Recurse |
    Where-Object {
        [bool]($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
    } |
    Select-Object -First 1
if ($null -ne $webReparsePoint) {
    throw 'The Web dist directory must not contain links or reparse points.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$bundleName = "ecobin-release-${ReleaseId}"
$bundleDirectory = Join-Path $OutputDirectory $bundleName
$archivePath = Join-Path $OutputDirectory "${bundleName}.tar.gz"
$archiveChecksumPath = "${archivePath}.sha256"
foreach ($candidate in @($bundleDirectory, $archivePath, $archiveChecksumPath)) {
    if (Test-Path -LiteralPath $candidate) {
        throw "Release output already exists and will not be overwritten: $candidate"
    }
}

$backendDirectory = Join-Path $bundleDirectory 'backend'
$webDirectory = Join-Path $bundleDirectory 'web'
$bundleWebDist = Join-Path $webDirectory 'dist'
New-Item -ItemType Directory -Path $backendDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $bundleWebDist -Force | Out-Null

Copy-Item -LiteralPath $backendJars[0].FullName `
    -Destination (Join-Path $backendDirectory 'app.jar')
Copy-TextFileAsLf `
    -Source (Join-Path $repositoryRoot 'deploy\production\runtime-images\backend.Dockerfile') `
    -Destination (Join-Path $backendDirectory 'Dockerfile')
Copy-TextFileAsLf `
    -Source (Join-Path $repositoryRoot 'deploy\production\backend-healthcheck.sh') `
    -Destination (Join-Path $backendDirectory 'backend-healthcheck.sh')

Get-ChildItem -LiteralPath $webDist -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $bundleWebDist -Recurse
}
Copy-TextFileAsLf `
    -Source (Join-Path $repositoryRoot 'deploy\production\runtime-images\web.Dockerfile') `
    -Destination (Join-Path $webDirectory 'Dockerfile')
Copy-TextFileAsLf `
    -Source (Join-Path $repositoryRoot 'frontend\web\nginx.conf') `
    -Destination (Join-Path $webDirectory 'nginx.conf')

$manifestLines = @(
    'ECOBIN_RELEASE_FORMAT_VERSION=1',
    "ECOBIN_RELEASE_ID=${ReleaseId}",
    "ECOBIN_GIT_COMMIT=${gitCommit}",
    "ECOBIN_SOURCE_DIRTY=$($sourceDirty.ToString().ToLowerInvariant())"
)
Write-LfTextFile `
    -Path (Join-Path $bundleDirectory 'manifest.env') `
    -Lines $manifestLines

$checksumEntries = @(
    Get-ChildItem -LiteralPath $bundleDirectory -File -Force -Recurse |
        ForEach-Object {
            $relativePath = [IO.Path]::GetRelativePath(
                $bundleDirectory,
                $_.FullName).Replace('\', '/')
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).
                Hash.ToLowerInvariant()
            "${hash}  ${relativePath}"
        } |
        Sort-Object
)
Write-LfTextFile `
    -Path (Join-Path $bundleDirectory 'SHA256SUMS') `
    -Lines $checksumEntries

$tarCommand = Get-Command tar -ErrorAction SilentlyContinue
if ($null -eq $tarCommand) {
    throw 'tar is required to create the release archive.'
}
Invoke-CheckedCommand `
    -FilePath $tarCommand.Source `
    -ArgumentList @('-C', $OutputDirectory, '-czf', $archivePath, $bundleName) `
    -WorkingDirectory $repositoryRoot

$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).
    Hash.ToLowerInvariant()
Write-LfTextFile `
    -Path $archiveChecksumPath `
    -Lines @("${archiveHash}  $([IO.Path]::GetFileName($archivePath))")

[pscustomobject]@{
    ReleaseId = $ReleaseId
    GitCommit = $gitCommit
    SourceDirty = $sourceDirty
    BundleDirectory = $bundleDirectory
    Archive = $archivePath
    ArchiveChecksum = $archiveChecksumPath
}

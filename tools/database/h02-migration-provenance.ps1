Set-StrictMode -Version Latest

function Get-H02MigrationProvenance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)][string]$MigrationDirectory
    )

    $resolvedRepository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
    $resolvedMigrations = (Resolve-Path -LiteralPath $MigrationDirectory).Path
    $repositoryPrefix = $resolvedRepository.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedMigrations.StartsWith(
        $repositoryPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "MigrationDirectory must be inside RepositoryRoot"
    }

    $relativeDirectory = $resolvedMigrations.Substring(
        $repositoryPrefix.Length).Replace('\', '/')
    $gitRoot = @(& git -C $resolvedRepository rev-parse `
        --show-toplevel 2>&1)
    if ($LASTEXITCODE -ne 0 -or $gitRoot.Count -ne 1) {
        throw "RepositoryRoot is not a readable Git worktree"
    }
    $resolvedGitRoot = (Resolve-Path -LiteralPath (
        $gitRoot[0].ToString().Trim())).Path
    if (-not $resolvedGitRoot.Equals(
        $resolvedRepository,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "RepositoryRoot must be the Git worktree root"
    }

    $dirty = @(& git -C $resolvedRepository status `
        --porcelain=v1 --untracked-files=all -- $relativeDirectory 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect migration Git status"
    }
    if ($dirty.Count -gt 0) {
        throw (
            "H-02 refuses dirty migration files. Commit or restore them first:`n" +
            ($dirty -join "`n")
        )
    }

    $commitOutput = @(& git -C $resolvedRepository rev-parse HEAD 2>&1)
    if ($LASTEXITCODE -ne 0 -or $commitOutput.Count -ne 1) {
        throw "Could not resolve the migration source commit"
    }
    $commit = $commitOutput[0].ToString().Trim()
    if ($commit -notmatch '^[0-9a-f]{40,64}$') {
        throw "Migration source commit is not a valid Git object ID"
    }

    $trackedOutput = @(& git -C $resolvedRepository ls-files `
        -- $relativeDirectory 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not enumerate tracked migration files"
    }
    $trackedMigrations = @(
        $trackedOutput |
            ForEach-Object { $_.ToString().Trim().Replace('\', '/') } |
            Where-Object { $_ -match '\.sql$' } |
            Sort-Object -Unique
    )
    if ($trackedMigrations.Count -eq 0) {
        throw "No tracked SQL migration files were found"
    }

    $manifestLines = foreach ($relativePath in $trackedMigrations) {
        $absolutePath = Join-Path $resolvedRepository (
            $relativePath.Replace('/', [IO.Path]::DirectorySeparatorChar))
        if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
            throw "Tracked migration file is missing: $relativePath"
        }
        $digest = (Get-FileHash -LiteralPath $absolutePath `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $relativePath"
    }
    $manifest = [string]::Join("`n", $manifestLines) + "`n"
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes($manifest)
    $manifestDigest = [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData($manifestBytes)
    ).ToLowerInvariant()

    return [pscustomobject]@{
        Commit = $commit
        RelativeDirectory = $relativeDirectory
        FileCount = $trackedMigrations.Count
        Manifest = $manifest
        ManifestSha256 = $manifestDigest
    }
}

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "../h02-migration-provenance.ps1")

function Invoke-TestGit {
    param(
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    $output = @(& git -C $Repository @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join "`n")"
    }
    return $output
}

function Assert-Throws {
    param(
        [Parameter(Mandatory)][scriptblock]$Action,
        [Parameter(Mandatory)][string]$Scenario
    )
    try {
        & $Action
    }
    catch {
        return
    }
    throw "Expected migration provenance rejection: $Scenario"
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "ecobin-h02-provenance-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    Invoke-TestGit -Repository $testRoot -Arguments @("init", "--quiet") |
        Out-Null
    Invoke-TestGit -Repository $testRoot -Arguments @(
        "config", "user.name", "EcoBin H02 Test"
    ) | Out-Null
    Invoke-TestGit -Repository $testRoot -Arguments @(
        "config", "user.email", "h02-test@invalid.example"
    ) | Out-Null
    $migrationDirectory = Join-Path $testRoot "db/migration"
    New-Item -ItemType Directory -Path $migrationDirectory | Out-Null
    Set-Content -LiteralPath (Join-Path $migrationDirectory "V1__base.sql") `
        -Value "SELECT 1;" -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $testRoot "unrelated.txt") `
        -Value "clean" -Encoding utf8NoBOM
    Invoke-TestGit -Repository $testRoot -Arguments @("add", ".") |
        Out-Null
    Invoke-TestGit -Repository $testRoot -Arguments @(
        "commit", "--quiet", "-m", "fixture"
    ) | Out-Null

    $clean = Get-H02MigrationProvenance `
        -RepositoryRoot $testRoot `
        -MigrationDirectory $migrationDirectory
    if ($clean.FileCount -ne 1 -or
            $clean.ManifestSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Clean migration provenance is incomplete"
    }

    Set-Content -LiteralPath (Join-Path $testRoot "unrelated.txt") `
        -Value "dirty but out of scope" -Encoding utf8NoBOM
    Get-H02MigrationProvenance `
        -RepositoryRoot $testRoot `
        -MigrationDirectory $migrationDirectory | Out-Null

    $migration = Join-Path $migrationDirectory "V1__base.sql"
    Set-Content -LiteralPath $migration -Value "SELECT 2;" `
        -Encoding utf8NoBOM
    Assert-Throws -Scenario "unstaged tracked migration" -Action {
        Get-H02MigrationProvenance `
            -RepositoryRoot $testRoot `
            -MigrationDirectory $migrationDirectory | Out-Null
    }
    Invoke-TestGit -Repository $testRoot -Arguments @(
        "restore", "--", "db/migration/V1__base.sql"
    ) | Out-Null

    $untrackedMigration = Join-Path $migrationDirectory "V2__draft.sql"
    Set-Content -LiteralPath $untrackedMigration -Value "SELECT 2;" `
        -Encoding utf8NoBOM
    Assert-Throws -Scenario "untracked migration" -Action {
        Get-H02MigrationProvenance `
            -RepositoryRoot $testRoot `
            -MigrationDirectory $migrationDirectory | Out-Null
    }
    Remove-Item -LiteralPath $untrackedMigration

    Set-Content -LiteralPath $migration -Value "SELECT 3;" `
        -Encoding utf8NoBOM
    Invoke-TestGit -Repository $testRoot -Arguments @(
        "add", "db/migration/V1__base.sql"
    ) | Out-Null
    Assert-Throws -Scenario "staged migration" -Action {
        Get-H02MigrationProvenance `
            -RepositoryRoot $testRoot `
            -MigrationDirectory $migrationDirectory | Out-Null
    }

    Write-Host "H-02 migration provenance tests passed."
}
finally {
    $resolvedTestRoot = if (Test-Path -LiteralPath $testRoot) {
        (Resolve-Path -LiteralPath $testRoot).Path
    }
    else {
        $null
    }
    $tempPrefix = [IO.Path]::GetFullPath(
        [IO.Path]::GetTempPath()).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        ) + [IO.Path]::DirectorySeparatorChar
    if ($resolvedTestRoot -and $resolvedTestRoot.StartsWith(
        $tempPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}

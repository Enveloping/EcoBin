[CmdletBinding()]
param(
    [ValidateSet('Up', 'Down', 'Restart', 'Logs', 'Validate')]
    [string]$Action = 'Up',

    [switch]$NoBuild,

    [switch]$Foreground
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..')
)
$localSecretsPath = Join-Path `
    $repositoryRoot '.ecobin\application-local-secrets.yml'
$readerPath = Join-Path $PSScriptRoot 'local-secrets.ps1'

. $readerPath

$secrets = Read-EcoBinLocalSecrets -Path $localSecretsPath
$composeValues = [ordered]@{
    ECOBIN_LOCAL_MYSQL_DATABASE = Get-EcoBinRequiredLocalSecret `
        -Secrets $secrets `
        -Name 'localMysqlDatabase'
    ECOBIN_LOCAL_MYSQL_ROOT_PASSWORD = Get-EcoBinRequiredLocalSecret `
        -Secrets $secrets `
        -Name 'localMysqlRootPassword'
    ECOBIN_LOCAL_DB_USERNAME = Get-EcoBinRequiredLocalSecret `
        -Secrets $secrets `
        -Name 'dbUsername'
    ECOBIN_LOCAL_DB_PASSWORD = Get-EcoBinRequiredLocalSecret `
        -Secrets $secrets `
        -Name 'dbPassword'
}

if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found.'
}

$originalValues = @{}
foreach ($entry in $composeValues.GetEnumerator()) {
    $originalValues[$entry.Key] = [Environment]::GetEnvironmentVariable(
        $entry.Key,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        $entry.Key,
        [string]$entry.Value,
        [EnvironmentVariableTarget]::Process
    )
}

Push-Location $repositoryRoot
try {
    $arguments = [Collections.Generic.List[string]]::new()
    $arguments.Add('compose')

    switch ($Action) {
        'Up' {
            $arguments.Add('up')
            if (-not $NoBuild) {
                $arguments.Add('--build')
            }
            if (-not $Foreground) {
                $arguments.Add('--detach')
            }
        }
        'Down' {
            $arguments.Add('down')
        }
        'Restart' {
            $arguments.Add('up')
            if (-not $NoBuild) {
                $arguments.Add('--build')
            }
            $arguments.Add('--detach')
            $arguments.Add('--force-recreate')
        }
        'Logs' {
            $arguments.Add('logs')
            $arguments.Add('--follow')
            $arguments.Add('backend')
        }
        'Validate' {
            $arguments.Add('config')
            $arguments.Add('--quiet')
        }
    }

    Write-Host "[EcoBin] docker $($arguments -join ' ')"
    & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose exited with code $LASTEXITCODE"
    }
} finally {
    Pop-Location
    foreach ($entry in $originalValues.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $entry.Key,
            $entry.Value,
            [EnvironmentVariableTarget]::Process
        )
    }
}

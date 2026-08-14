[CmdletBinding()]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..')
)
$sourcePath = Join-Path $repositoryRoot '.env'
$destinationDirectory = Join-Path $repositoryRoot '.ecobin'
$destinationPath = Join-Path `
    $destinationDirectory 'application-local-secrets.yml'

function Read-DotEnvFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }
        $separator = $line.IndexOf('=')
        if ($separator -le 0) {
            continue
        }

        $key = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2) {
            $singleQuoted = $value.StartsWith("'") -and
                $value.EndsWith("'")
            $doubleQuoted = $value.StartsWith('"') -and
                $value.EndsWith('"')
            if ($singleQuoted -or $doubleQuoted) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }
    return $values
}

function Get-FirstConfiguredValue {
    param(
        [Parameter(Mandatory)]
        [hashtable]$Values,

        [Parameter(Mandatory)]
        [string[]]$Names,

        [string]$Default = ''
    )

    foreach ($name in $Names) {
        if ($Values.ContainsKey($name)) {
            $candidate = [string]$Values[$name]
            if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                return $candidate
            }
        }
    }
    return $Default
}

function ConvertTo-YamlSingleQuotedScalar {
    param([AllowEmptyString()][string]$Value)

    return "'" + $Value.Replace("'", "''") + "'"
}

function New-LocalRandomPassword {
    $bytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).
        TrimEnd('=').
        Replace('+', '-').
        Replace('/', '_')
}

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Legacy .env was not found: $sourcePath"
}
$destinationAlreadyExists = Test-Path -LiteralPath $destinationPath
if ($destinationAlreadyExists -and -not $Force) {
    throw @"
Local YAML already exists: $destinationPath
Review it first. Use -Force only when replacing it is intentional.
"@
}

$legacy = Read-DotEnvFile -Path $sourcePath
$localMysqlRootPassword = Get-FirstConfiguredValue `
    -Values $legacy `
    -Names @('MYSQL_ROOT_PASSWORD')
$generatedLocalMysqlRootPassword = $false
if ([string]::IsNullOrWhiteSpace($localMysqlRootPassword) -and
    $destinationAlreadyExists) {
    . (Join-Path $PSScriptRoot 'local-secrets.ps1')
    $existingSettings = Read-EcoBinLocalSecrets -Path $destinationPath
    $localMysqlRootPassword = [string](
        $existingSettings['localMysqlRootPassword']
    )
}
if ([string]::IsNullOrWhiteSpace($localMysqlRootPassword)) {
    $localMysqlRootPassword = New-LocalRandomPassword
    $generatedLocalMysqlRootPassword = $true
}

$settings = [ordered]@{
    localMysqlDatabase = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('MYSQL_DATABASE') `
        -Default 'ecobin'
    localMysqlRootPassword = $localMysqlRootPassword
    dbUrl = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('dbUrl')
    dbUsername = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('dbUsername', 'DB_RUNTIME_USERNAME')
    dbPassword = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('dbPassword', 'DB_RUNTIME_PASSWORD')
    jwtSecret = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('jwtSecret')
    cosSecretId = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosSecretId')
    cosSecretKey = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosSecretKey')
    cosRegion = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosRegion')
    cosBucketName = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosBucketName')
    cosBaseUrl = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosBaseUrl')
    cosDurationSeconds = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('cosDurationSeconds') `
        -Default '1800'
    iotAccessId = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('iotAccessId')
    iotSecretKey = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('iotSecretKey')
    iotSubscriptionName = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('iotSubscriptionName')
    onenetProductId = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('onenetProductId')
    onenetAccessKey = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('onenetAccessKey')
    defaultPlatformAdminPassword = Get-FirstConfiguredValue `
        -Values $legacy `
        -Names @('defaultPlatformAdminPassword')
}

foreach ($requiredName in @(
        'dbUrl',
        'dbUsername',
        'dbPassword',
        'jwtSecret')) {
    if ([string]::IsNullOrWhiteSpace([string]$settings[$requiredName])) {
        throw "Legacy .env is missing required key: $requiredName"
    }
}

$lines = [Collections.Generic.List[string]]::new()
$lines.Add('# Local-only EcoBin configuration. Git ignores this file.')
$lines.Add('# Switch Fake/Real with the IDEA profile or run-backend.ps1.')
$lines.Add('')
foreach ($entry in $settings.GetEnumerator()) {
    $scalar = ConvertTo-YamlSingleQuotedScalar ([string]$entry.Value)
    $lines.Add("$($entry.Key): $scalar")
}

New-Item -ItemType Directory `
    -Path $destinationDirectory `
    -Force | Out-Null
[IO.File]::WriteAllLines(
    $destinationPath,
    $lines,
    [Text.UTF8Encoding]::new($false)
)

$migratedCount = @(
    $settings.GetEnumerator() |
        Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_.Value)
        }
).Count
Write-Host "[EcoBin] Local YAML created: $destinationPath"
Write-Host "[EcoBin] Migrated $migratedCount configured keys; values were not printed."
if ($generatedLocalMysqlRootPassword) {
    Write-Host '[EcoBin] Generated the missing local Compose initialization password without displaying it.'
}

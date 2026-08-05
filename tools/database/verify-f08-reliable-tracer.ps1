[CmdletBinding()]
param(
    [string]$MySqlImage = "mysql:8.4",
    [string]$ExpectedImageId =
        "sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$migrationDir = Join-Path `
    $repoRoot "ecobin-bootstrap/src/main/resources/db/p0-migration"
$migrationFiles = @(
    Get-ChildItem -LiteralPath $migrationDir -Filter "V*__*.sql" |
        Sort-Object {
            [int]([regex]::Match($_.Name, '^V(\d+)__').Groups[1].Value)
        } |
        Select-Object -ExpandProperty Name
)
$containerName =
    "ecobin-f08-tracer-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$passwordBytes = New-Object byte[] 24
$randomNumberGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($passwordBytes)
}
finally {
    $randomNumberGenerator.Dispose()
}
$rootPassword = [BitConverter]::ToString($passwordBytes).Replace("-", "").ToLowerInvariant()
$containerStarted = $false

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]] $Arguments)
    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
    return $output
}

function Invoke-MySql {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $Database,
        [Parameter(Mandatory)][string] $Sql
    )
    $arguments = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysql", "-uroot", "--batch", "--skip-column-names"
    )
    if ($Database.Length -gt 0) {
        $arguments += "--database=$Database"
    }
    $arguments += @("--execute", $Sql)
    return Invoke-Docker -Arguments $arguments
}

try {
    for ($version = 1; $version -le $migrationFiles.Count; $version++) {
        if ($migrationFiles[$version - 1] -notmatch "^V${version}__") {
            throw "P0 migrations must be contiguous from V1; expected V$version"
        }
    }
    foreach ($file in $migrationFiles) {
        if (-not (Test-Path (Join-Path $migrationDir $file))) {
            throw "Missing P0 migration: $file"
        }
    }

    $actualImageId = (
        Invoke-Docker -Arguments @(
            "image", "inspect", "--format", "{{.Id}}", $MySqlImage
        )
    ).Trim()
    if ($ExpectedImageId -and $actualImageId -ne $ExpectedImageId) {
        throw "MySQL image does not match the reviewed F-06 image"
    }

    Invoke-Docker -Arguments @(
        "run", "--name", $containerName,
        "--env", "MYSQL_ROOT_PASSWORD=$rootPassword",
        "--env", "TZ=UTC",
        "--publish", "127.0.0.1::3306",
        "--detach", $MySqlImage,
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci",
        "--default-time-zone=+00:00",
        "--sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
    ) | Out-Null
    $containerStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $mainProcess = & docker exec $containerName `
                cat /proc/1/comm 2> $null
            $mainProcessExitCode = $LASTEXITCODE
            if ($mainProcessExitCode -eq 0 -and $mainProcess.Trim() -eq "mysqld") {
                & docker exec -e "MYSQL_PWD=$rootPassword" $containerName `
                    mysql -uroot --batch --skip-column-names `
                    --execute "SELECT 1;" *> $null
                $mysqlReadyExitCode = $LASTEXITCODE
            }
            else {
                $mysqlReadyExitCode = 1
            }
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        if ($mysqlReadyExitCode -eq 0) {
                $ready = $true
                break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "MySQL did not become ready within 60 seconds"
    }

    $version = (Invoke-MySql -Database "" -Sql "SELECT VERSION();").Trim()
    if ($version -notmatch "^8\.4\.") {
        throw "F-08 requires MySQL 8.4.x"
    }

    Invoke-Docker -Arguments @(
        "cp", "$migrationDir/.", "${containerName}:/tmp/p0-migration"
    ) | Out-Null
    Invoke-MySql -Database "" -Sql @"
CREATE DATABASE ecobin_f08
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'ecobin_trigger_definer'@'%' ACCOUNT LOCK;
GRANT TRIGGER ON ecobin_f08.* TO 'ecobin_trigger_definer'@'%';
"@ | Out-Null

    foreach ($file in $migrationFiles) {
        Invoke-Docker -Arguments @(
            "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
            "sh", "-lc",
            "mysql -uroot --database=ecobin_f08 < /tmp/p0-migration/$file"
        ) | Out-Null
    }

    $publishedPort = (
        Invoke-Docker -Arguments @(
            "port", $containerName, "3306/tcp"
        )
    ).Trim()
    if ($publishedPort -notmatch ":(\d+)$") {
        throw "Could not resolve the published MySQL port"
    }
    $hostPort = $Matches[1]

    $env:ECOBIN_F08_MYSQL_URL =
        "jdbc:mysql://127.0.0.1:$hostPort/ecobin_f08" +
        "?serverTimezone=UTC&useSSL=false&allowPublicKeyRetrieval=true"
    $env:ECOBIN_F08_MYSQL_USERNAME = "root"
    $env:ECOBIN_F08_MYSQL_PASSWORD = $rootPassword

    & (Join-Path $repoRoot "mvnw.cmd") `
        -pl ecobin-module-operations `
        -am `
        "-Dtest=ReliableInboxMysqlIntegrationTest" `
        "-Dsurefire.failIfNoSpecifiedTests=false" `
        test
    if ($LASTEXITCODE -ne 0) {
        throw "F-08 MySQL integration tests failed"
    }

    [ordered]@{
        mysqlVersion = $version
        migrationCount = $migrationFiles.Count
        testClass = "ReliableInboxMysqlIntegrationTest"
        scenarios = @(
            "atomic receipt and ACK-safe outer transaction isolation",
            "duplicate delivery reuses and wakes the original task",
            "same stable identity with different semantic digest quarantine",
            "exclusive lanes and concurrent SKIP LOCKED claims",
            "expired lease takeover and reclaimed attempt",
            "stale worker completion preserves the replacement lease",
            "business rollback with attempt retry",
            "wakeVersion reuse of the original task",
            "just-in-time claim prevents lease expiry while queued",
            "maximum-in-flight is shared across concurrent runner calls",
            "reliable, device, and fullness alert projections converge",
            "operational overview module ports return zero-safe metrics"
        )
        passed = $true
    } | ConvertTo-Json -Depth 3
}
finally {
    Remove-Item Env:ECOBIN_F08_MYSQL_URL -ErrorAction SilentlyContinue
    Remove-Item Env:ECOBIN_F08_MYSQL_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:ECOBIN_F08_MYSQL_PASSWORD -ErrorAction SilentlyContinue
    if ($containerStarted) {
        & docker rm --force $containerName *> $null
    }
}

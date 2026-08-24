[CmdletBinding()]
param(
    [string]$MySqlImage = "mysql:8.4",
    [string]$ExpectedImageId =
        "sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6",
    [string]$JavaExecutable = "java",
    [string]$MavenExecutable = "mvn.cmd",
    [string]$ApplicationJar = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$migrationDir = Join-Path `
    $repoRoot "ecobin-bootstrap/src/main/resources/db/p0-migration"
$containerName =
    "ecobin-f07-verify-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$databaseNames = [ordered]@{
    Correct = "ecobin_f07_correct"
    ExistingAdminUpgrade = "ecobin_f07_existing_admin_upgrade"
    Empty = "ecobin_f07_empty"
    Legacy = "ecobin_f07_legacy"
    WrongV1 = "ecobin_f07_wrong_v1"
    Failed = "ecobin_f07_failed"
    Low = "ecobin_f07_low"
    FlywayOverride = "ecobin_f07_flyway_override"
}
$missingDatabase = "ecobin_f07_missing"

function New-RandomSecret {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

function Get-FreeTcpPort {
    $listener = [Net.Sockets.TcpListener]::new(
        [Net.IPAddress]::Loopback,
        0)
    $listener.Start()
    try {
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $diagnostic = ($output -join "`n")
        foreach ($secret in @(
            $rootPassword,
            $schemaOwnerPassword,
            $appPassword
        )) {
            if ($secret.Length -gt 0) {
                $diagnostic = $diagnostic.Replace($secret, "[REDACTED]")
            }
        }
        throw (
            "Docker command failed with exit code $LASTEXITCODE`n" +
            $diagnostic
        )
    }
    return @($output)
}

function Invoke-MySql {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Database,
        [Parameter(Mandatory)][string]$Sql
    )

    $arguments = @(
        "exec",
        "-e", "MYSQL_PWD=$rootPassword",
        $containerName,
        "mysql",
        "-uroot",
        "--batch",
        "--skip-column-names"
    )
    if ($Database.Length -gt 0) {
        $arguments += "--database=$Database"
    }
    $arguments += @("--execute", $Sql)
    return (Invoke-Docker -Arguments $arguments) -join "`n"
}

function Invoke-AppMySql {
    param(
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql
    )

    $arguments = @(
        "exec",
        "-e", "MYSQL_PWD=$appPassword",
        $containerName,
        "mysql",
        "-h127.0.0.1",
        "-uecobin_app",
        "--database=$Database",
        "--batch",
        "--skip-column-names",
        "--execute", $Sql
    )
    return (Invoke-Docker -Arguments $arguments) -join "`n"
}

function Assert-AppMySqlRejected {
    param(
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql
    )

    $arguments = @(
        "exec",
        "-e", "MYSQL_PWD=$appPassword",
        $containerName,
        "mysql",
        "-h127.0.0.1",
        "-uecobin_app",
        "--database=$Database",
        "--execute", $Sql
    )
    & docker @arguments *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "ecobin_app unexpectedly executed a forbidden database statement"
    }
}

function Invoke-FlywayMigrate {
    param(
        [Parameter(Mandatory)][string]$Database,
        [string]$Target = ""
    )

    # mvn.cmd is a batch launcher; keep this argument free of '&' so cmd.exe
    # cannot split the native argument before Maven receives it.
    $jdbcUrl = "jdbc:mysql://127.0.0.1:$mysqlPort/$Database"
    $arguments = @(
        "-q",
        "-pl", "ecobin-bootstrap",
        "flyway:migrate",
        "-Dflyway.url=$jdbcUrl",
        "-Dflyway.user=ecobin_schema_owner",
        "-Dflyway.password=$schemaOwnerPassword",
        "-Dflyway.connectRetries=10"
    )
    if ($Target.Length -gt 0) {
        $arguments += "-Dflyway.target=$Target"
    }

    $logPath = Join-Path $logDirectory "flyway-$Database.log"
    & $MavenExecutable @arguments *> $logPath
    if ($LASTEXITCODE -ne 0) {
        $diagnostic = Get-Content -Raw $logPath
        foreach ($secret in @(
            $rootPassword,
            $schemaOwnerPassword,
            $appPassword
        )) {
            $diagnostic = $diagnostic.Replace($secret, "[REDACTED]")
        }
        if ($diagnostic.Length -gt 6000) {
            $diagnostic = $diagnostic.Substring(
                $diagnostic.Length - 6000)
        }
        throw "Flyway migration failed for $Database`n$diagnostic"
    }
}

function Get-Sha256 {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Value
    )

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [Convert]::ToHexString(
            $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
        ).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SchemaFingerprint {
    param([Parameter(Mandatory)][string]$Database)

    $dump = Invoke-Docker -Arguments @(
        "exec",
        "-e", "MYSQL_PWD=$rootPassword",
        $containerName,
        "mysqldump",
        "-uroot",
        "--no-data",
        "--no-tablespaces",
        "--skip-comments",
        "--skip-dump-date",
        "--compact",
        "--triggers",
        $Database
    )
    return Get-Sha256 -Value ($dump -join "`n")
}

function Get-HistoryFingerprint {
    param([Parameter(Mandatory)][string]$Database)

    $exists = Invoke-MySql -Database "" -Sql @"
SELECT COUNT(*)
FROM information_schema.tables
WHERE table_schema = '$Database'
  AND table_name = 'flyway_schema_history';
"@
    if ([int]$exists -eq 0) {
        return "absent"
    }
    $history = Invoke-MySql -Database $Database -Sql @"
SELECT CONCAT_WS(
    '|', installed_rank, COALESCE(version, ''), description, type,
    script, COALESCE(checksum, ''), installed_by, execution_time, success
)
FROM flyway_schema_history
ORDER BY installed_rank;
"@
    return Get-Sha256 -Value $history
}

function Get-BusinessRowCount {
    param([Parameter(Mandatory)][string]$Database)

    $rows = Invoke-MySql -Database $Database -Sql @"
SET SESSION group_concat_max_len = 100000;
SELECT GROUP_CONCAT(
    CONCAT(
        'SELECT ''', table_name, ''' AS table_name, COUNT(*) AS row_count FROM ``',
        table_name, '``'
    )
    SEPARATOR ' UNION ALL '
) INTO @row_count_sql
FROM information_schema.tables
WHERE table_schema = '$Database'
  AND table_type = 'BASE TABLE'
  AND table_name NOT IN (
      'flyway_schema_history',
      'iam_permission_definition',
      'dev_runtime_snapshot_policy',
      'dev_remote_support_port_slot'
  );
PREPARE row_count_statement FROM @row_count_sql;
EXECUTE row_count_statement;
DEALLOCATE PREPARE row_count_statement;
"@
    $total = 0L
    foreach ($line in ($rows -split "\r?\n")) {
        if ($line -match "\t(?<count>\d+)$") {
            $total += [long]$Matches.count
        }
    }
    return $total
}

function Start-TestApplication {
    param(
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][int]$Port,
        [string[]]$AdditionalArguments = @()
    )

    $safeName = $Database -replace "[^a-zA-Z0-9_-]", "_"
    $stdoutPath = Join-Path $logDirectory "$safeName-$Port.stdout.log"
    $stderrPath = Join-Path $logDirectory "$safeName-$Port.stderr.log"
    $jdbcUrl =
        "jdbc:mysql://127.0.0.1:$mysqlPort/$Database" +
        "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC"
    $arguments = @(
        "-jar", $ApplicationJar,
        "--dbUrl=$jdbcUrl",
        "--dbUsername=ecobin_app",
        "--dbPassword=$appPassword",
        "--bagCodeKeyK1=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "--externalMode=fake",
        "--ecobin.identity.default-platform-admin.enabled=false",
        "--ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false",
        "--ecobin.operations.reliable.workers-enabled=false",
        "--onenet.subscription.enabled=false",
        "--onenet.subscription.access-id=",
        "--onenet.subscription.secret-key=",
        "--onenet.subscription.subscription-name=",
        "--onenet.product-id=",
        "--onenet.access-key=",
        "--cos.secret-id=",
        "--cos.secret-key=",
        "--cos.region=",
        "--cos.bucket-name=",
        "--cos.base-url=",
        "--server.port=$Port",
        "--spring.datasource.hikari.connection-timeout=3000",
        "--spring.datasource.hikari.initialization-fail-timeout=1",
        "--spring.main.banner-mode=off",
        "--logging.level.root=INFO"
    ) + $AdditionalArguments

    $process = Start-Process `
        -FilePath $JavaExecutable `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    $script:applicationProcesses += $process
    return [pscustomobject]@{
        Database = $Database
        Process = $process
        StdoutPath = $stdoutPath
        StderrPath = $stderrPath
    }
}

function Get-ApplicationLog {
    param([Parameter(Mandatory)]$Run)

    $stdout = if (Test-Path $Run.StdoutPath) {
        Get-Content -Raw $Run.StdoutPath
    }
    else {
        ""
    }
    $stderr = if (Test-Path $Run.StderrPath) {
        Get-Content -Raw $Run.StderrPath
    }
    else {
        ""
    }
    return "$stdout`n$stderr"
}

function Stop-TestApplication {
    param([Parameter(Mandatory)]$Run)

    $Run.Process.Refresh()
    if (-not $Run.Process.HasExited) {
        Stop-Process -Id $Run.Process.Id -Force
        $Run.Process.WaitForExit()
    }
}

function Assert-ApplicationReady {
    param(
        [Parameter(Mandatory)]$Run,
        [Parameter(Mandatory)][int]$Port,
        [string]$ContextPath = ""
    )

    $uri =
        "http://127.0.0.1:$Port$ContextPath/actuator/health/readiness"
    $lastProbe = "no HTTP response"
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $Run.Process.Refresh()
        if ($Run.Process.HasExited) {
            $diagnostic = Get-ApplicationLog -Run $Run
            foreach ($secret in @(
                $rootPassword,
                $schemaOwnerPassword,
                $appPassword
            )) {
                $diagnostic = $diagnostic.Replace($secret, "[REDACTED]")
            }
            if ($diagnostic.Length -gt 8000) {
                $diagnostic = $diagnostic.Substring(
                    $diagnostic.Length - 8000)
            }
            throw "correct V57 application exited before readiness`n$diagnostic"
        }
        try {
            $response = Invoke-WebRequest `
                -Uri $uri `
                -TimeoutSec 2 `
                -SkipHttpErrorCheck
            $responseContent = if ($response.Content -is [byte[]]) {
                [Text.Encoding]::UTF8.GetString($response.Content)
            }
            else {
                [string]$response.Content
            }
            $lastProbe =
                "HTTP $($response.StatusCode): $responseContent"
            if (
                $response.StatusCode -eq 200 -and
                $responseContent -match '"status"\s*:\s*"UP"'
            ) {
                return
            }
        }
        catch {
            $lastProbe = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    $diagnostic = Get-ApplicationLog -Run $Run
    foreach ($secret in @(
        $rootPassword,
        $schemaOwnerPassword,
        $appPassword
    )) {
        $diagnostic = $diagnostic.Replace($secret, "[REDACTED]")
    }
    if ($diagnostic.Length -gt 8000) {
        $diagnostic = $diagnostic.Substring($diagnostic.Length - 8000)
    }
    throw "correct V57 application did not become ready; " +
        "last probe: $lastProbe`n$diagnostic"
}

function Assert-ApplicationRejected {
    param(
        [Parameter(Mandatory)]$Run,
        [Parameter(Mandatory)][string]$ReasonPattern
    )

    $exited = $Run.Process.WaitForExit(45000)
    if (-not $exited) {
        Stop-TestApplication -Run $Run
        throw "invalid application configuration remained running"
    }
    if ($Run.Process.ExitCode -eq 0) {
        throw "invalid application configuration exited successfully"
    }
    $log = Get-ApplicationLog -Run $Run
    if ($log -notmatch $ReasonPattern) {
        $diagnostic = $log
        foreach ($secret in @(
            $rootPassword,
            $schemaOwnerPassword,
            $appPassword
        )) {
            $diagnostic = $diagnostic.Replace($secret, "[REDACTED]")
        }
        if ($diagnostic.Length -gt 4000) {
            $diagnostic = $diagnostic.Substring(
                $diagnostic.Length - 4000)
        }
        throw (
            "application rejection for $($Run.Database) did not expose " +
            "expected reason '$ReasonPattern'`n$diagnostic"
        )
    }
}

function Assert-ApplicationRejectedBeforeReady {
    param(
        [Parameter(Mandatory)]$Run,
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][string]$ReasonPattern
    )

    $readinessUri =
        "http://127.0.0.1:$Port/actuator/health/readiness"
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $Run.Process.Refresh()
        if ($Run.Process.HasExited) {
            if ($Run.Process.ExitCode -eq 0) {
                throw "invalid application configuration exited successfully"
            }
            $log = Get-ApplicationLog -Run $Run
            if ($log -notmatch $ReasonPattern) {
                throw "application rejection did not expose the expected guard reason"
            }
            return
        }
        try {
            $response = Invoke-WebRequest `
                -Uri $readinessUri `
                -TimeoutSec 1 `
                -SkipHttpErrorCheck
            if ($response.StatusCode -eq 200) {
                Stop-TestApplication -Run $Run
                throw "invalid application configuration became ready"
            }
        }
        catch {
            if ($_.Exception.Message -eq
                    "invalid application configuration became ready") {
                throw
            }
        }
        Start-Sleep -Milliseconds 500
    }
    Stop-TestApplication -Run $Run
    throw "invalid application configuration neither failed nor became ready"
}

$rootPassword = New-RandomSecret
$schemaOwnerPassword = New-RandomSecret
$appPassword = New-RandomSecret
$mysqlPort = Get-FreeTcpPort
$containerStarted = $false
$applicationProcesses = @()
$logDirectory = Join-Path `
    ([IO.Path]::GetTempPath()) `
    "ecobin-f07-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
New-Item -ItemType Directory -Path $logDirectory | Out-Null

try {
    if ($ApplicationJar.Length -eq 0) {
        $jarCandidates = @(
            Get-ChildItem `
                (Join-Path $repoRoot "ecobin-bootstrap/target") `
                -Filter "ecobin-bootstrap-*.jar" `
                -File `
                -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notlike "*.original" }
        )
        if ($jarCandidates.Count -ne 1) {
            throw "build exactly one bootstrap JAR with mvn install -DskipTests"
        }
        $ApplicationJar = $jarCandidates[0].FullName
    }
    $ApplicationJar = (Resolve-Path $ApplicationJar).Path

    $javaCommand = @(
        Get-Command `
            $JavaExecutable `
            -CommandType Application `
            -ErrorAction Stop
    )[0]
    $JavaExecutable = $javaCommand.Source
    $javaVersion = (& $JavaExecutable -version 2>&1) -join "`n"
    if ($javaVersion -notmatch 'version "21\.') {
        throw "F-07 verification requires Java 21"
    }
    $mavenVersion = (& $MavenExecutable -version 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $mavenVersion -notmatch "Java version: 21\.") {
        throw "Maven must run with Java 21"
    }

    $jarExecutable = Join-Path `
        (Split-Path -Parent $JavaExecutable) `
        "jar.exe"
    if (-not (Test-Path $jarExecutable)) {
        $jarExecutable = "jar"
    }
    $jarEntries = (& $jarExecutable tf $ApplicationJar) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect bootstrap JAR"
    }
    if ($jarEntries -match "BOOT-INF/classes/db/migration/") {
        throw "bootstrap JAR still contains the legacy migration epoch"
    }
    $packagedTargetMigrations = [regex]::Matches(
        $jarEntries,
        "(?m)^BOOT-INF/classes/db/p0-migration/V\d+__.+\.sql$"
    ).Count
    if ($packagedTargetMigrations -ne 0) {
        throw "bootstrap JAR must not contain target migration scripts"
    }
    $packagedFlywayLibraries = [regex]::Matches(
        $jarEntries,
        "(?m)^BOOT-INF/lib/(?:flyway-.+|spring-boot-flyway-.+)\.jar$"
    ).Count
    if ($packagedFlywayLibraries -ne 0) {
        throw "bootstrap JAR must not contain Flyway runtime libraries"
    }

    $actualImageId = (
        Invoke-Docker -Arguments @(
            "image", "inspect", "--format", "{{.Id}}", $MySqlImage
        )
    ).Trim()
    if ($ExpectedImageId -and $actualImageId -ne $ExpectedImageId) {
        throw "MySQL image does not match the pinned immutable image ID"
    }

    Invoke-Docker -Arguments @(
        "run",
        "--name", $containerName,
        "--env", "MYSQL_ROOT_PASSWORD=$rootPassword",
        "--env", "TZ=UTC",
        "--publish", "127.0.0.1:${mysqlPort}:3306",
        "--detach",
        $MySqlImage,
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci",
        "--default-time-zone=+00:00",
        "--transaction-isolation=READ-COMMITTED",
        "--log-bin-trust-function-creators=ON",
        "--sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
    ) | Out-Null
    $containerStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $containerLogs = (& docker logs $containerName 2>&1) -join "`n"
        & docker exec `
            -e "MYSQL_PWD=$rootPassword" `
            $containerName `
            mysql -uroot --batch --skip-column-names `
            --execute "SELECT 1;" *> $null
        if ($LASTEXITCODE -eq 0 -and
                $containerLogs -match
                    "MySQL init process done\. Ready for start up\.") {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "MySQL did not become ready"
    }

    $mysqlVersion = Invoke-MySql -Database "" -Sql "SELECT VERSION();"
    if ($mysqlVersion -notmatch "^8\.4\.") {
        throw "verification database is not MySQL 8.4.x"
    }

    $createDatabases = ($databaseNames.Values | ForEach-Object {
        "CREATE DATABASE ``$_`` CHARACTER SET utf8mb4 " +
            "COLLATE utf8mb4_0900_ai_ci;"
    }) -join "`n"
    Invoke-MySql -Database "" -Sql @"
$createDatabases
CREATE USER 'ecobin_trigger_definer'@'%' ACCOUNT LOCK;
CREATE USER 'ecobin_schema_owner'@'%'
    IDENTIFIED BY '$schemaOwnerPassword';
GRANT ALL PRIVILEGES ON ``$($databaseNames.Correct)``.*
    TO 'ecobin_schema_owner'@'%';
GRANT ALL PRIVILEGES ON ``$($databaseNames.ExistingAdminUpgrade)``.*
    TO 'ecobin_schema_owner'@'%';
GRANT ALL PRIVILEGES ON ``$($databaseNames.Low)``.*
    TO 'ecobin_schema_owner'@'%';
GRANT SET_ANY_DEFINER ON *.* TO 'ecobin_schema_owner'@'%';
"@ | Out-Null

    Invoke-FlywayMigrate -Database $databaseNames.Correct
    Invoke-FlywayMigrate `
        -Database $databaseNames.ExistingAdminUpgrade `
        -Target "49"
    Invoke-FlywayMigrate -Database $databaseNames.Low -Target "9"

    $existingAdminUid = "50000000-0000-4000-8000-000000000001"
    $existingAdminPasswordHash =
        "existing-password-hash-must-survive-v50"
    Invoke-MySql `
        -Database $databaseNames.ExistingAdminUpgrade `
        -Sql @"
INSERT INTO iam_platform_admin (
    platform_admin_uid, login_name, password_hash, display_name, enabled,
    failed_login_count, locked_until, auth_version, password_changed_at,
    lock_version, created_at, updated_at
) VALUES (
    '$existingAdminUid', 'enveloping', '$existingAdminPasswordHash',
    'Existing platform administrator', 1, 0, NULL, 7,
    '2026-01-01 00:00:00.000', 11,
    '2026-01-01 00:00:00.000', '2026-01-01 00:00:00.000'
);
"@ | Out-Null
    $existingAdminBeforeV50 = Invoke-MySql `
        -Database $databaseNames.ExistingAdminUpgrade `
        -Sql @"
SELECT CONCAT_WS(
    '|', platform_admin_uid, password_hash, auth_version, lock_version,
    enabled
)
FROM iam_platform_admin
WHERE login_name = 'enveloping';
"@
    Invoke-FlywayMigrate -Database $databaseNames.ExistingAdminUpgrade
    $existingAdminAfterV50 = Invoke-MySql `
        -Database $databaseNames.ExistingAdminUpgrade `
        -Sql @"
SELECT CONCAT_WS(
    '|', platform_admin_uid, password_hash, auth_version, lock_version,
    enabled, admin_kind,
    IF(deleted_at IS NULL, 'NULL', 'NOT_NULL')
)
FROM iam_platform_admin
WHERE login_name = 'enveloping';
"@
    if (
        $existingAdminAfterV50 -ne
            "${existingAdminBeforeV50}|DEFAULT|NULL"
    ) {
        throw (
            "V50 did not preserve and promote the sole existing " +
            "platform administrator"
        )
    }

    $v1Marker = Invoke-MySql -Database $databaseNames.Correct -Sql @"
SELECT CONCAT_WS('|', version, description, script, checksum, success)
FROM flyway_schema_history
WHERE version = '1';
"@
    if (
        $v1Marker -ne
            "1|p0 epoch and iam core|V1__p0_epoch_and_iam_core.sql|229072802|1"
    ) {
        throw "Flyway V1 marker differs from the fixed P0 epoch identity"
    }

    Invoke-MySql -Database "" -Sql @"
CREATE TABLE ``$($databaseNames.Legacy)``.flyway_schema_history
    LIKE ``$($databaseNames.Correct)``.flyway_schema_history;
INSERT INTO ``$($databaseNames.Legacy)``.flyway_schema_history
SELECT * FROM ``$($databaseNames.Correct)``.flyway_schema_history;
UPDATE ``$($databaseNames.Legacy)``.flyway_schema_history
SET description = 'init schema',
    script = 'V1__init_schema.sql',
    checksum = -123
WHERE version = '1';

CREATE TABLE ``$($databaseNames.WrongV1)``.flyway_schema_history
    LIKE ``$($databaseNames.Correct)``.flyway_schema_history;
INSERT INTO ``$($databaseNames.WrongV1)``.flyway_schema_history
SELECT * FROM ``$($databaseNames.Correct)``.flyway_schema_history;
UPDATE ``$($databaseNames.WrongV1)``.flyway_schema_history
SET checksum = 123
WHERE version = '1';

CREATE TABLE ``$($databaseNames.Failed)``.flyway_schema_history
    LIKE ``$($databaseNames.Correct)``.flyway_schema_history;
INSERT INTO ``$($databaseNames.Failed)``.flyway_schema_history
SELECT * FROM ``$($databaseNames.Correct)``.flyway_schema_history;
UPDATE ``$($databaseNames.Failed)``.flyway_schema_history
SET success = 0
WHERE version = '5';

DROP USER 'ecobin_schema_owner'@'%';
CREATE USER 'ecobin_app'@'%' IDENTIFIED BY '$appPassword';
"@ | Out-Null

    foreach ($database in $databaseNames.Values) {
        Invoke-MySql -Database "" -Sql @"
GRANT SELECT ON ``$database``.* TO 'ecobin_app'@'%';
"@ | Out-Null
    }
    Invoke-MySql -Database "" -Sql @"
GRANT ALL PRIVILEGES ON ``$($databaseNames.FlywayOverride)``.*
    TO 'ecobin_app'@'%';
"@ | Out-Null

    $ownerCount = Invoke-MySql -Database "" -Sql @"
SELECT COUNT(*) FROM mysql.user
WHERE user = 'ecobin_schema_owner';
"@
    if ([int]$ownerCount -ne 0) {
        throw "schema owner identity remained after the migration job"
    }
    $definerLocked = Invoke-MySql -Database "" -Sql @"
SELECT account_locked FROM mysql.user
WHERE user = 'ecobin_trigger_definer' AND host = '%';
"@
    if ($definerLocked -ne "Y") {
        throw "trigger definer must exist as a locked non-runtime account"
    }
    $runtimePrincipal = Invoke-AppMySql `
        -Database $databaseNames.Correct `
        -Sql "SELECT CURRENT_USER();"
    if ($runtimePrincipal -ne "ecobin_app@%") {
        throw "runtime connection does not authenticate as ecobin_app"
    }
    Assert-AppMySqlRejected `
        -Database $databaseNames.Correct `
        -Sql "CREATE TABLE forbidden_runtime_ddl (id BIGINT PRIMARY KEY);"
    Assert-AppMySqlRejected `
        -Database $databaseNames.Correct `
        -Sql "DELETE FROM iam_permission_definition;"

    $tableCount = [int](Invoke-MySql `
        -Database "" `
        -Sql @"
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_type = 'BASE TABLE';
"@)
    if ($tableCount -ne 120) {
        throw "correct target must contain 119 domain tables plus Flyway history"
    }
    $permissionCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql "SELECT COUNT(*) FROM iam_permission_definition;")
    if ($permissionCount -ne 76) {
        throw "target permission reference catalog is incomplete"
    }
    $businessRowsBefore = Get-BusinessRowCount `
        -Database $databaseNames.Correct
    if ($businessRowsBefore -ne 0) {
        throw "target database contains business instance data before startup"
    }

    $fingerprintsBefore = @{}
    $historiesBefore = @{}
    foreach ($database in $databaseNames.Values) {
        $fingerprintsBefore[$database] =
            Get-SchemaFingerprint -Database $database
        $historiesBefore[$database] =
            Get-HistoryFingerprint -Database $database
    }

    $correctPort = Get-FreeTcpPort
    $correctRun = Start-TestApplication `
        -Database $databaseNames.Correct `
        -Port $correctPort
    Assert-ApplicationReady -Run $correctRun -Port $correctPort
    $correctLog = Get-ApplicationLog -Run $correctRun
    if (
        $correctLog -notmatch "Target database epoch accepted" -or
        $correctLog -notmatch
            "External adapter boundary accepted mode=FAKE inboundBlocked=true"
    ) {
        throw "correct startup log lacks the two F-07 guard acceptances"
    }
    $blockedIngress = Invoke-WebRequest `
        -Method Post `
        -Uri "http://127.0.0.1:$correctPort/api/iot/delivery/complete" `
        -TimeoutSec 5 `
        -SkipHttpErrorCheck
    if ($blockedIngress.StatusCode -ne 503) {
        throw "Fake application accepted a real device ingress path"
    }
    Stop-TestApplication -Run $correctRun

    $contextPathPort = Get-FreeTcpPort
    $contextPathRun = Start-TestApplication `
        -Database $databaseNames.Correct `
        -Port $contextPathPort `
        -AdditionalArguments @("--server.servlet.context-path=/ctx")
    Assert-ApplicationReady `
        -Run $contextPathRun `
        -Port $contextPathPort `
        -ContextPath "/ctx"
    $contextPathBlockedIngress = Invoke-WebRequest `
        -Method Post `
        -Uri "http://127.0.0.1:$contextPathPort/ctx/api/iot/delivery/complete" `
        -TimeoutSec 5 `
        -SkipHttpErrorCheck
    if ($contextPathBlockedIngress.StatusCode -ne 503) {
        throw "Fake application context path bypassed the ingress block"
    }
    Stop-TestApplication -Run $contextPathRun

    Invoke-MySql -Database "" -Sql @"
GRANT SET_ANY_DEFINER ON *.* TO 'ecobin_app'@'%';
"@ | Out-Null
    $flywayOverridePort = Get-FreeTcpPort
    $flywayOverrideRun = Start-TestApplication `
        -Database $databaseNames.FlywayOverride `
        -Port $flywayOverridePort `
        -AdditionalArguments @("--spring.flyway.enabled=true")
    Assert-ApplicationRejectedBeforeReady `
        -Run $flywayOverrideRun `
        -Port $flywayOverridePort `
        -ReasonPattern "target Flyway history table is missing"
    Invoke-MySql -Database "" -Sql @"
REVOKE SET_ANY_DEFINER ON *.* FROM 'ecobin_app'@'%';
"@ | Out-Null

    $rejectionCases = @(
        @{
            Database = $databaseNames.Empty
            Pattern = "target Flyway history table is missing"
        },
        @{
            Database = $databaseNames.Legacy
            Pattern = "target V1 epoch marker"
        },
        @{
            Database = $databaseNames.WrongV1
            Pattern = "target V1 epoch marker"
        },
        @{
            Database = $databaseNames.Failed
            Pattern = "failed migration at version 5"
        },
        @{
            Database = $databaseNames.Low
            Pattern = "exactly one successful V10"
        }
    )
    foreach ($case in $rejectionCases) {
        $run = Start-TestApplication `
            -Database $case.Database `
            -Port 0
        Assert-ApplicationRejected `
            -Run $run `
            -ReasonPattern $case.Pattern
    }

    $missingRun = Start-TestApplication `
        -Database $missingDatabase `
        -Port 0
    Assert-ApplicationRejected `
        -Run $missingRun `
        -ReasonPattern "Unknown database|Access denied"
    $missingCount = Invoke-MySql -Database "" -Sql @"
SELECT COUNT(*) FROM information_schema.schemata
WHERE schema_name = '$missingDatabase';
"@
    if ([int]$missingCount -ne 0) {
        throw "application startup auto-created a missing database"
    }

    $credentialRun = Start-TestApplication `
        -Database $databaseNames.Correct `
        -Port 0 `
        -AdditionalArguments @(
            "--onenet.product-id=must-be-rejected",
            "--onenet.access-key=must-be-rejected"
        )
    Assert-ApplicationRejected `
        -Run $credentialRun `
        -ReasonPattern "Fake mode rejects all real OneNet and COS credentials"

    $fakeBypassRun = Start-TestApplication `
        -Database $databaseNames.Correct `
        -Port 0 `
        -AdditionalArguments @(
            "--spring.profiles.active=test",
            "--ecobin.external.fake.block-inbound=false"
        )
    Assert-ApplicationRejected `
        -Run $fakeBypassRun `
        -ReasonPattern "Fake external ingress must remain blocked"

    $epochBypassRun = Start-TestApplication `
        -Database $databaseNames.Correct `
        -Port 0 `
        -AdditionalArguments @(
            "--spring.profiles.active=test",
            "--ecobin.database.epoch.test-bypass=true"
        )
    Assert-ApplicationRejected `
        -Run $epochBypassRun `
        -ReasonPattern "only allowed for the test profile with in-memory H2"

    foreach ($database in $databaseNames.Values) {
        $schemaAfter = Get-SchemaFingerprint -Database $database
        $historyAfter = Get-HistoryFingerprint -Database $database
        if (
            $schemaAfter -ne $fingerprintsBefore[$database] -or
            $historyAfter -ne $historiesBefore[$database]
        ) {
            throw "application startup mutated schema or Flyway history for $database"
        }
    }
    $businessRowsAfter = Get-BusinessRowCount `
        -Database $databaseNames.Correct
    if ($businessRowsAfter -ne 0) {
        throw "application startup created business instance data"
    }

    [ordered]@{
        mysqlVersion = [string]$mysqlVersion
        imageId = [string]$actualImageId
        javaVersion = "21"
        artifactSha256 = (
            Get-FileHash -Algorithm SHA256 $ApplicationJar
        ).Hash.ToLowerInvariant()
        packagedTargetMigrations = $packagedTargetMigrations
        packagedLegacyMigrations = 0
        packagedFlywayLibraries = $packagedFlywayLibraries
        v1Checksum = 229072802
        targetVersion = 57
        domainTables = 119
        permissionReferenceRows = $permissionCount
        businessInstanceRows = $businessRowsAfter
        runtimePrincipal = $runtimePrincipal
        schemaOwnerPresentAtRuntime = $false
        triggerDefinerLocked = $true
        runtimeDdlRejected = $true
        runtimeFactDeleteRejected = $true
        correctV57Ready = $true
        existingAdministratorPromotedWithoutCredentialChange = $true
        fakeIngressBlocked = $true
        fakeIngressContextPathBlocked = $true
        fakeCredentialMixRejected = $true
        packagedFakeBypassRejected = $true
        packagedEpochBypassRejected = $true
        flywayOverrideRejectedWithoutMutation = $true
        rejectedSchemas = $rejectionCases.Count
        missingDatabaseRejectedWithoutCreation = $true
        schemaAndHistoryUnchanged = $true
    } | ConvertTo-Json
}
finally {
    foreach ($process in $applicationProcesses) {
        try {
            $process.Refresh()
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force
                $process.WaitForExit()
            }
        }
        catch {
            # Best-effort cleanup for test-only processes.
        }
    }
    if ($containerStarted) {
        & docker rm --force $containerName *> $null
    }
    if (Test-Path $logDirectory) {
        Remove-Item -LiteralPath $logDirectory -Recurse -Force
    }
}

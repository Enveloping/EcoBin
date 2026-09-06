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
    ClockRecoveryUpgrade = "ecobin_f07_clock_recovery_upgrade"
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

function Assert-MySqlRejected {
    param(
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql
    )

    $arguments = @(
        "exec",
        "-e", "MYSQL_PWD=$rootPassword",
        $containerName,
        "mysql",
        "-uroot",
        "--database=$Database",
        "--execute", $Sql
    )
    & docker @arguments *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "MySQL unexpectedly accepted a constraint-breaking statement"
    }
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
      'dev_remote_support_port_slot',
      'dev_edge_software_release_sequence'
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
        "--ecobin.device.business-release.secret-id=",
        "--ecobin.device.business-release.secret-key=",
        "--ecobin.device.business-release.region=",
        "--ecobin.device.business-release.bucket-name=",
        "--ecobin.device.business-release.download-base-url=",
        "--ecobin.device.business-release.remote-dispatch-enabled=false",
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
            throw "correct V68 application exited before readiness`n$diagnostic"
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
    throw "correct V68 application did not become ready; " +
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
GRANT ALL PRIVILEGES ON ``$($databaseNames.ClockRecoveryUpgrade)``.*
    TO 'ecobin_schema_owner'@'%';
GRANT ALL PRIVILEGES ON ``$($databaseNames.Low)``.*
    TO 'ecobin_schema_owner'@'%';
GRANT SET_ANY_DEFINER ON *.* TO 'ecobin_schema_owner'@'%';
"@ | Out-Null

    Invoke-FlywayMigrate -Database $databaseNames.Correct
    Invoke-FlywayMigrate `
        -Database $databaseNames.ExistingAdminUpgrade `
        -Target "49"
    Invoke-FlywayMigrate `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Target "57"
    Invoke-FlywayMigrate -Database $databaseNames.Low -Target "9"

    # Reproduce the V57 compatibility projection that copied backend
    # created_at into channel_created_at when a WeChat query omitted
    # create_time.  The authorization is already terminal, but the old
    # display-package recovery issue is still unresolved.
    Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SET FOREIGN_KEY_CHECKS = 0;
INSERT INTO fund_wechat_transfer_authorization (
    authorization_uid, tenant_id, organization_id, organization_user_id,
    wechat_subject_id,
    merchant_profile_id, miniapp_merchant_binding_id,
    miniapp_channel_id, out_authorization_no, authorization_id,
    mchid_snapshot, appid_snapshot, openid_snapshot, scene_id_snapshot,
    user_display_name_snapshot, user_recv_perception_snapshot,
    authorization_notify_url_snapshot, notify_url_sha256, request_sha256,
    local_state, channel_state, package_info, package_expires_at,
    last_api_error_code, close_reason, state_conflict, submitted_at,
    channel_created_at, confirmation_deadline_at, authorized_at, closed_at,
    channel_updated_at, lock_version, created_at, updated_at
) VALUES (
    '51000000-0000-4000-8000-000000000001',
    51001, 51002, 51003, 51007, 51004, 51005, 51006,
    'V58RECOVERY0001', NULL,
    '1900000001', 'wxv58recoveryappid', 'openid-v58-recovery',
    'scene-v58-recovery', 'V58 recovery user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('11', 32)), UNHEX(REPEAT('22', 32)),
    'EXPIRED', 'WAIT_USER_CONFIRM', NULL, NULL, 'NOT_FOUND',
    'USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION', 0,
    '2026-08-25 00:30:00.000', '2026-08-25 00:00:00.000',
    '2026-08-26 00:00:00.000', NULL,
    '2026-09-25 00:00:00.000', '2026-09-25 00:00:00.000', 0,
    '2026-08-25 00:00:00.000', '2026-09-25 00:00:00.000'
), (
    '51000000-0000-4000-8000-000000000003',
    52001, 52002, 52003, 52007, 52004, 52005, 52006,
    'V58RECOVERY0002', NULL,
    '1900000002', 'wxv58recoveryappid2', 'openid-v58-recovery-2',
    'scene-v58-recovery-2', 'V58 waiting user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('55', 32)), UNHEX(REPEAT('66', 32)),
    'WAIT_USER_CONFIRM', 'WAIT_USER_CONFIRM',
    'authorization-package', '2026-08-25 00:40:00.000',
    NULL, NULL, 0,
    '2026-08-25 00:30:00.000', '2026-08-25 00:00:00.000',
    '2026-08-26 00:00:00.000', NULL, NULL,
    '2026-08-25 00:31:00.000', 0,
    '2026-08-25 00:00:00.000', '2026-08-25 00:31:00.000'
), (
    '51000000-0000-4000-8000-000000000005',
    54001, 54002, 54003, 54007, 54004, 54005, 54006,
    'V58RECOVERY0003', 'WXAUTHV58RECOVERY0003',
    '1900000003', 'wxv58recoveryappid3', 'openid-v58-recovery-3',
    'scene-v58-recovery-3', 'V58 active user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('a1', 32)), UNHEX(REPEAT('a2', 32)),
    'ACTIVE', 'TAKING_EFFECT', NULL, NULL, NULL, NULL, 0,
    '2026-08-25 00:30:00.000', '2026-08-25 00:00:00.000',
    '2026-08-26 00:00:00.000', '2026-08-25 00:31:00.000',
    NULL, '2026-08-25 00:31:00.000', 0,
    '2026-08-25 00:00:00.000', '2026-08-25 00:31:00.000'
), (
    '51000000-0000-4000-8000-000000000007',
    55001, 55002, 55003, 55007, 55004, 55005, 55006,
    'V58RECOVERY0004', NULL,
    '1900000004', 'wxv58recoveryappid4', 'openid-v58-recovery-4',
    'scene-v58-recovery-4', 'V58 recovered waiting user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('b1', 32)), UNHEX(REPEAT('b2', 32)),
    'WAIT_USER_CONFIRM', 'WAIT_USER_CONFIRM',
    'recovered-authorization-package', '2099-01-01 00:10:00.000',
    NULL, NULL, 0,
    '2099-01-01 00:00:00.000', '2099-01-01 00:00:00.000',
    '2099-01-02 00:00:00.000', NULL, NULL,
    '2099-01-01 00:01:00.000', 0,
    '2099-01-01 00:00:00.000', '2099-01-01 00:01:00.000'
), (
    '51000000-0000-4000-8000-000000000009',
    56001, 56002, 56003, 56007, 56004, 56005, 56006,
    'V58RECOVERY0005', 'WXAUTHV58RECOVERY0005',
    '1900000005', 'wxv58recoveryappid5', 'openid-v58-recovery-5',
    'scene-v58-recovery-5', 'V58 recovered active user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('b3', 32)), UNHEX(REPEAT('b4', 32)),
    'ACTIVE', 'TAKING_EFFECT', NULL, NULL, NULL, NULL, 0,
    '2099-01-01 00:00:00.000', '2099-01-01 00:00:00.000',
    '2099-01-02 00:00:00.000', '2099-01-01 00:01:00.000',
    NULL, '2099-01-01 00:01:00.000', 0,
    '2099-01-01 00:00:00.000', '2099-01-01 00:01:00.000'
), (
    '51000000-0000-4000-8000-00000000000b',
    57001, 57002, 57003, 57007, 57004, 57005, 57006,
    'V58RECOVERY0006', NULL,
    '1900000006', 'wxv58recoveryappid6', 'openid-v58-recovery-6',
    'scene-v58-recovery-6', 'V58 unknown user', NULL,
    'https://callback.example/authorization',
    UNHEX(REPEAT('b9', 32)), UNHEX(REPEAT('ba', 32)),
    'UNKNOWN', 'UNRECOGNIZED', NULL, NULL, NULL, NULL, 1,
    '2099-01-01 00:00:00.000', '2099-01-01 00:00:00.000',
    '2099-01-02 00:00:00.000', NULL, NULL,
    '2099-01-01 00:01:00.000', 0,
    '2099-01-01 00:00:00.000', '2099-01-01 00:01:00.000'
);
INSERT INTO ops_reconciliation_issue (
    issue_uid, scope_kind, tenant_id, organization_id, issue_code,
    severity, subject_type, subject_stable_key, dedupe_key, state,
    first_seen_run_id, latest_seen_run_id,
    first_seen_task_attempt_id, latest_seen_task_attempt_id,
    first_seen_at, last_seen_at, discovery_count,
    initial_evidence_sha256, redacted_evidence_summary,
    last_handled_at, last_handled_audit_id,
    system_verified_resolved_at, lock_version, created_at, updated_at
) VALUES (
    '51000000-0000-4000-8000-000000000002',
    'ORGANIZATION', 51001, 51002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0001',
    UNHEX(REPEAT('33', 32)), 'UNRESOLVED', 1, 1, NULL, NULL,
    '2026-08-25 02:00:00.000', '2026-08-25 02:00:00.000', 1,
    UNHEX(REPEAT('44', 32)), 'waiting display package unavailable',
    NULL, NULL, NULL, 0,
    '2026-08-25 02:00:00.000', '2026-08-25 02:00:00.000'
), (
    '51000000-0000-4000-8000-000000000004',
    'ORGANIZATION', 52001, 52002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0002',
    UNHEX(REPEAT('77', 32)), 'UNRESOLVED', 2, 2, NULL, NULL,
    '2026-08-25 00:31:00.000', '2026-08-25 00:31:00.000', 1,
    UNHEX(REPEAT('88', 32)),
    'reason=QUERY_TASK_NOT_WAKEABLE_AFTER_CREATE; channelState=-',
    NULL, NULL, NULL, 0,
    '2026-08-25 00:31:00.000', '2026-08-25 00:31:00.000'
), (
    '51000000-0000-4000-8000-000000000006',
    'ORGANIZATION', 54001, 54002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0003',
    UNHEX(REPEAT('a3', 32)), 'UNRESOLVED', 3, 3, NULL, NULL,
    '2026-08-25 00:31:00.000', '2026-08-25 00:31:00.000', 1,
    UNHEX(REPEAT('a4', 32)),
    'reason=QUERY_TASK_NOT_WAKEABLE; channelState=TAKING_EFFECT',
    NULL, NULL, NULL, 0,
    '2026-08-25 00:31:00.000', '2026-08-25 00:31:00.000'
), (
    '51000000-0000-4000-8000-000000000008',
    'ORGANIZATION', 55001, 55002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0004',
    UNHEX(REPEAT('b5', 32)), 'UNRESOLVED', 4, 4, NULL, NULL,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000', 1,
    UNHEX(REPEAT('b6', 32)),
    'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE; channelState=WAIT_USER_CONFIRM',
    NULL, NULL, NULL, 0,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000'
), (
    '51000000-0000-4000-8000-00000000000a',
    'ORGANIZATION', 56001, 56002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0005',
    UNHEX(REPEAT('b7', 32)), 'UNRESOLVED', 5, 5, NULL, NULL,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000', 1,
    UNHEX(REPEAT('b8', 32)),
    'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE; channelState=TAKING_EFFECT',
    NULL, NULL, NULL, 0,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000'
), (
    '51000000-0000-4000-8000-00000000000c',
    'ORGANIZATION', 56001, 56002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_UNKNOWN_STATE',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0005',
    UNHEX(REPEAT('bb', 32)), 'UNRESOLVED', 6, 6, NULL, NULL,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000', 1,
    UNHEX(REPEAT('bc', 32)),
    'reason=UNKNOWN_CHANNEL_STATE; channelState=UNRECOGNIZED',
    NULL, NULL, NULL, 0,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000'
), (
    '51000000-0000-4000-8000-00000000000d',
    'ORGANIZATION', 57001, 57002,
    'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_UNKNOWN_STATE',
    'CRITICAL', 'WECHAT_TRANSFER_AUTHORIZATION', 'V58RECOVERY0006',
    UNHEX(REPEAT('bd', 32)), 'UNRESOLVED', 7, 7, NULL, NULL,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000', 1,
    UNHEX(REPEAT('be', 32)),
    'reason=UNKNOWN_CHANNEL_STATE; channelState=UNRECOGNIZED',
    NULL, NULL, NULL, 0,
    '2099-01-01 00:01:00.000', '2099-01-01 00:01:00.000'
);
INSERT INTO dev_factory_seal_authorization (
    asset_id, hardware_sn_snapshot, acceptance_generation,
    acceptance_evidence_uid, acceptance_challenge_uid,
    acceptance_evidence_sha256, factory_bag_revision,
    factory_bag_set_sha256, command_uid, reliable_task_uid,
    authorization_status, acknowledged_at, cancelled_at,
    cancellation_reason, completion_event_uid,
    completion_payload_sha256, image_release_id, image_release_sha256,
    factory_report_sha256, authorization_binding_sha256,
    operator_confirmation_uid, completion_clock_quality,
    sealed_at, cleanup_completed_at, completion_received_at,
    created_at, updated_at
) VALUES (
    53001, 'V58-LEGACY-SEAL', 1,
    '53000000-0000-4000-8000-000000000001',
    '53000000-0000-4000-8000-000000000002',
    UNHEX(REPEAT('91', 32)), 1, UNHEX(REPEAT('92', 32)),
    '53000000-0000-4000-8000-000000000003',
    '53000000-0000-4000-8000-000000000004',
    'SEALED', '2026-08-25 00:01:00.000', NULL, NULL,
    '53000000-0000-4000-8000-000000000005',
    UNHEX(REPEAT('93', 32)), 'v56-image-release',
    UNHEX(REPEAT('94', 32)), UNHEX(REPEAT('95', 32)),
    UNHEX(REPEAT('96', 32)),
    '53000000-0000-4000-8000-000000000006', NULL,
    '2026-08-25 00:02:00.000', '2026-08-25 00:03:00.000',
    '2026-08-25 00:04:00.000', '2026-08-25 00:00:00.000',
    '2026-08-25 00:04:00.000'
);
INSERT INTO dev_device_command_event (
    tenant_id, organization_id, asset_id,
    edge_event_id, edge_event_type, command_id,
    delivery_session_id, observed_command_type, observation_stage,
    mcu_command_uid, error_code, created_at
) VALUES (
    58001, 58002, 58003,
    58004, 'DEVICE_COMMAND_OBSERVED', 58005,
    NULL, 'START_CLEAN_OPERATION', 'FAILED',
    '58000000-0000-4000-8000-000000000001',
    'COMMAND_EXPIRED', '2026-08-25 00:05:00.000'
), (
    58001, 58002, 58003,
    58006, 'DEVICE_COMMAND_OBSERVED', 58007,
    NULL, 'START_CLEAN_OPERATION', 'ACCEPTED',
    NULL, NULL, '2026-08-25 00:05:01.000'
);
SET FOREIGN_KEY_CHECKS = 1;
"@ | Out-Null
    Invoke-FlywayMigrate -Database $databaseNames.ClockRecoveryUpgrade
    Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SET FOREIGN_KEY_CHECKS = 0;
INSERT INTO dev_device_command_event (
    tenant_id, organization_id, asset_id,
    edge_event_id, edge_event_type, command_id,
    delivery_session_id, observed_command_type, observation_stage,
    mcu_command_uid, error_code, created_at
) VALUES (
    58001, 58002, 58003,
    58008, 'DEVICE_COMMAND_OBSERVED', 58005,
    NULL, 'START_CLEAN_OPERATION', 'FAILED',
    '58000000-0000-4000-8000-000000000001',
    'EDGE_RESTARTED', '2026-08-25 00:05:02.000'
);
SET FOREIGN_KEY_CHECKS = 1;
"@ | Out-Null
    $qualifiedCommandFailures = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT COUNT(*)
FROM dev_device_command_event
WHERE command_id = 58005
  AND observation_stage = 'FAILED'
  AND error_code IN ('COMMAND_EXPIRED', 'EDGE_RESTARTED');
"@
    if ([int]$qualifiedCommandFailures -ne 2) {
        throw "V58 did not retain both qualified command failure facts"
    }
    Assert-MySqlRejected `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SET FOREIGN_KEY_CHECKS = 0;
INSERT INTO dev_device_command_event (
    tenant_id, organization_id, asset_id,
    edge_event_id, edge_event_type, command_id,
    delivery_session_id, observed_command_type, observation_stage,
    mcu_command_uid, error_code, created_at
) VALUES (
    58001, 58002, 58003,
    58009, 'DEVICE_COMMAND_OBSERVED', 58005,
    NULL, 'START_CLEAN_OPERATION', 'FAILED',
    '58000000-0000-4000-8000-000000000001',
    'EDGE_RESTARTED', '2026-08-25 00:05:03.000'
);
"@
    Assert-MySqlRejected `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SET FOREIGN_KEY_CHECKS = 0;
INSERT INTO dev_device_command_event (
    tenant_id, organization_id, asset_id,
    edge_event_id, edge_event_type, command_id,
    delivery_session_id, observed_command_type, observation_stage,
    mcu_command_uid, error_code, created_at
) VALUES (
    58001, 58002, 58003,
    58010, 'DEVICE_COMMAND_OBSERVED', 58007,
    NULL, 'START_CLEAN_OPERATION', 'ACCEPTED',
    NULL, NULL, '2026-08-25 00:05:04.000'
);
"@
    $clockRecoveryUpgrade = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT CONCAT_WS(
    '|',
    authorization_row.channel_created_at IS NULL,
    authorization_row.confirmation_deadline_at = DATE_ADD(
        authorization_row.created_at,
        INTERVAL 24 HOUR
    ),
    authorization_row.local_state,
    recovery_issue.state,
    recovery_issue.system_verified_resolved_at IS NOT NULL
)
FROM fund_wechat_transfer_authorization authorization_row
JOIN ops_reconciliation_issue recovery_issue
  ON recovery_issue.subject_stable_key =
     authorization_row.out_authorization_no
WHERE authorization_row.out_authorization_no = 'V58RECOVERY0001';
"@
    if ($clockRecoveryUpgrade -ne "1|1|EXPIRED|RESOLVED|1") {
        throw "V58 did not converge the V57 authorization recovery facts"
    }
    $unrecoveredQueryIssue = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT COUNT(*)
FROM ops_reconciliation_issue
WHERE subject_stable_key IN ('V58RECOVERY0002', 'V58RECOVERY0003')
  AND issue_code =
      'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING'
  AND state = 'UNRESOLVED';
"@
    if ([int]$unrecoveredQueryIssue -ne 2) {
        throw "V58 resolved a query-task recovery issue without recovery proof"
    }
    $resolvedDisplayPackageIssues = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT COUNT(*)
FROM ops_reconciliation_issue recovery_issue
JOIN fund_wechat_transfer_authorization authorization_row
  ON authorization_row.out_authorization_no =
     recovery_issue.subject_stable_key
WHERE recovery_issue.subject_stable_key IN (
        'V58RECOVERY0004', 'V58RECOVERY0005'
      )
  AND recovery_issue.issue_code =
      'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING'
  AND recovery_issue.state = 'RESOLVED'
  AND recovery_issue.system_verified_resolved_at IS NOT NULL
  AND authorization_row.channel_created_at IS NULL
  AND (
      (
          authorization_row.out_authorization_no = 'V58RECOVERY0004'
          AND authorization_row.local_state = 'WAIT_USER_CONFIRM'
          AND authorization_row.package_info IS NOT NULL
          AND authorization_row.package_expires_at > UTC_TIMESTAMP(3)
      )
      OR (
          authorization_row.out_authorization_no = 'V58RECOVERY0005'
          AND authorization_row.local_state = 'ACTIVE'
      )
  );
"@
    if ([int]$resolvedDisplayPackageIssues -ne 2) {
        throw "V58 did not resolve recovered display-package issues"
    }
    $unknownStateIssueConvergence = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT COUNT(*)
FROM ops_reconciliation_issue unknown_issue
JOIN fund_wechat_transfer_authorization authorization_row
  ON authorization_row.out_authorization_no =
     unknown_issue.subject_stable_key
WHERE unknown_issue.issue_code =
      'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_UNKNOWN_STATE'
  AND (
      (
          unknown_issue.subject_stable_key = 'V58RECOVERY0005'
          AND authorization_row.local_state = 'ACTIVE'
          AND unknown_issue.state = 'RESOLVED'
          AND unknown_issue.system_verified_resolved_at IS NOT NULL
      )
      OR (
          unknown_issue.subject_stable_key = 'V58RECOVERY0006'
          AND authorization_row.local_state = 'UNKNOWN'
          AND unknown_issue.state = 'UNRESOLVED'
          AND unknown_issue.system_verified_resolved_at IS NULL
      )
  );
"@
    if ([int]$unknownStateIssueConvergence -ne 2) {
        throw "V58 did not preserve UNKNOWN-state issue convergence boundaries"
    }
    Assert-MySqlRejected `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
UPDATE fund_wechat_transfer_authorization
SET channel_state = NULL
WHERE out_authorization_no = 'V58RECOVERY0001';
"@
    Assert-MySqlRejected `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
UPDATE fund_wechat_transfer_authorization
SET last_api_error_code = NULL
WHERE out_authorization_no = 'V58RECOVERY0001';
"@
    $sealClockConstraint = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.ClockRecoveryUpgrade)'
  AND constraint_name = 'ck_dev_factory_seal_status'
  AND REPLACE(LOWER(check_clause), CHAR(96), '')
      LIKE '%completion_clock_quality is not null%';
"@
    if ([int]$sealClockConstraint -ne 1) {
        throw "V58 factory seal constraint permits an absent clock quality"
    }
    $legacySealQuality = Invoke-MySql `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
SELECT completion_clock_quality
FROM dev_factory_seal_authorization
WHERE hardware_sn_snapshot = 'V58-LEGACY-SEAL';
"@
    if ($legacySealQuality -ne "SYNCED") {
        throw "V58 did not upgrade the completed V56 factory-seal fact"
    }
    Assert-MySqlRejected `
        -Database $databaseNames.ClockRecoveryUpgrade `
        -Sql @"
UPDATE dev_factory_seal_authorization
SET completion_clock_quality = NULL
WHERE hardware_sn_snapshot = 'V58-LEGACY-SEAL';
"@

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

    foreach ($database in @(
            $databaseNames.Correct,
            $databaseNames.ExistingAdminUpgrade,
            $databaseNames.ClockRecoveryUpgrade)) {
        Invoke-MySql -Database "" -Sql @"
GRANT TRIGGER ON ``$database``.*
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    id, created_at, updated_at
) ON ``$database``.dev_device_asset
    TO 'ecobin_trigger_definer'@'%';
GRANT INSERT ON ``$database``.dev_device_management_profile
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    asset_id, architecture_generation,
    transition_source_event_uid, transitioned_at
) ON ``$database``.dev_device_management_profile
    TO 'ecobin_trigger_definer'@'%';
GRANT INSERT ON ``$database``.dev_device_compatibility_projection
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    asset_id, architecture_generation, management_state_sequence
) ON ``$database``.dev_device_compatibility_projection
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    release_uid, create_operation_uid, version_name, release_sequence,
    package_object_key, signature_object_key, package_sha256, package_size,
    signature_sha256, signature_bytes, signing_key_id, declaration_id,
    verified_by_platform_admin_id, verified_at,
    created_by_platform_admin_id, created_at
) ON ``$database``.dev_edge_software_release_control
    TO 'ecobin_trigger_definer'@'%';
"@ | Out-Null
    }

    Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
INSERT INTO dev_device_asset (
    asset_uid, device_public_code, hardware_sn,
    model_name, production_batch, registration_source,
    expected_port_count, installation_display_name,
    installation_updated_at,
    tenant_id, tenant_assigned_at,
    organization_id, organization_assigned_at,
    acceptance_status, accepted_at,
    acceptance_evidence_sha256, last_acceptance_evaluated_at,
    acceptance_failure_json,
    lifecycle_status, disabled_at, disable_reason,
    retired_at, retirement_reason, control_version,
    created_at, updated_at
) VALUES (
    '63000000-0000-4000-8000-000000000001',
    'Dv_V63TriggerProbe0000000001', 'V63-TRIGGER-PROBE',
    'EC-M0', 'V63-PROBE', 'PLATFORM_MANUAL',
    2, 'V63 trigger permission probe',
    '2026-09-02 00:00:00.000',
    NULL, NULL, NULL, NULL,
    'PENDING', NULL, NULL, NULL, NULL,
    'NORMAL', NULL, NULL, NULL, NULL, 0,
    '2026-09-02 00:00:00.000', '2026-09-02 00:00:00.000'
);
"@ | Out-Null
    $managementDefaults = Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT CONCAT(
    (SELECT COUNT(*)
     FROM dev_device_management_profile profile
     JOIN dev_device_asset asset ON asset.id = profile.asset_id
     WHERE asset.hardware_sn = 'V63-TRIGGER-PROBE'),
    '|',
    (SELECT COUNT(*)
     FROM dev_device_compatibility_projection projection
     JOIN dev_device_asset asset ON asset.id = projection.asset_id
     WHERE asset.hardware_sn = 'V63-TRIGGER-PROBE')
);
"@
    if ($managementDefaults -ne "1|1") {
        throw "V63 new-asset management defaults were not created"
    }
    Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
DELETE projection
FROM dev_device_compatibility_projection projection
JOIN dev_device_asset asset ON asset.id = projection.asset_id
WHERE asset.hardware_sn = 'V63-TRIGGER-PROBE';
DELETE profile
FROM dev_device_management_profile profile
JOIN dev_device_asset asset ON asset.id = profile.asset_id
WHERE asset.hardware_sn = 'V63-TRIGGER-PROBE';
DELETE FROM dev_device_asset
WHERE hardware_sn = 'V63-TRIGGER-PROBE';
"@ | Out-Null

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
    $historyCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql "SELECT COUNT(*) FROM flyway_schema_history WHERE success = 1;")
    if ($historyCount -ne 68) {
        throw "correct target must contain 68 successful Flyway migrations"
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
    if ($tableCount -ne 132) {
        throw "correct target must contain 131 domain tables plus Flyway history"
    }
    $businessReleaseControlTableCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.tables
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name IN (
      'dev_edge_software_release_sequence',
      'dev_edge_software_release_control',
      'dev_edge_software_release_action',
      'dev_edge_software_rollout',
      'dev_edge_software_deployment',
      'dev_edge_software_rollout_action',
      'dev_edge_software_deployment_progress',
      'dev_edge_software_deployment_cancel_result'
  );
"@)
    if ($businessReleaseControlTableCount -ne 8) {
        throw "V66 business release validation and cancellation tables are incomplete"
    }
    $businessCancellationColumnCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name = 'dev_edge_software_deployment'
  AND column_name IN (
      'cancel_command_uid', 'cancel_reliable_task_uid',
      'cancel_control_sequence', 'cancellation_status', 'cancel_reason',
      'cancel_requested_by_platform_admin_id', 'cancel_requested_at',
      'cancel_result_at'
  );
"@)
    $businessCancellationTriggerCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.triggers
WHERE trigger_schema = '$($databaseNames.Correct)'
  AND trigger_name IN (
      'trg_dev_edge_cancel_result_v66_immutable',
      'trg_dev_edge_cancel_result_v66_no_delete'
  );
"@)
    if ($businessCancellationColumnCount -ne 8 -or
            $businessCancellationTriggerCount -ne 2) {
        throw "V66 business update cancellation facts are incomplete"
    }
    $imageBridgeBaselineColumnCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name = 'dev_edge_software_deployment'
  AND (
      (column_name = 'source_business_baseline_kind'
          AND is_nullable = 'NO'
          AND column_default = 'BUSINESS_RELEASE')
      OR (column_name IN (
              'source_business_release_uid',
              'source_business_release_sequence'
          ) AND is_nullable = 'YES')
  );
"@)
    $imageBridgeBaselineConstraintCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.Correct)'
  AND constraint_name = 'ck_dev_edge_deployment_source_baseline'
  AND LOWER(check_clause) LIKE '%business_release%'
  AND LOWER(check_clause) LIKE '%image_bridge%';
"@)
    if ($imageBridgeBaselineColumnCount -ne 3 -or
            $imageBridgeBaselineConstraintCount -ne 1) {
        throw "V67 image-bridge deployment baseline is incomplete"
    }
    $softwareFactImageBridgeConstraintCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.Correct)'
  AND constraint_name = 'ck_dev_software_fact_process'
  AND LOWER(check_clause) LIKE '%active_business_release_uid%'
  AND LOWER(check_clause) LIKE '%communication-%'
  AND LOWER(check_clause) LIKE '%updater-%'
  AND LOWER(check_clause) LIKE '%regexp_like%'
  AND LOWER(check_clause) LIKE '%substr(%';
"@)
    if ($softwareFactImageBridgeConstraintCount -ne 1) {
        throw "V68 image-bridge software-fact constraint is incomplete"
    }
    $permissionCount = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql "SELECT COUNT(*) FROM iam_permission_definition;")
    if ($permissionCount -ne 76) {
        throw "target permission reference catalog is incomplete"
    }
    $bagLabelLimitConstraints = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.Correct)'
  AND (
      (
          constraint_name = 'ck_rec_bag_label_batch_count_v59'
          AND REPLACE(LOWER(check_clause), CHAR(96), '')
              LIKE '%label_count between 1 and 500%'
      )
      OR (
          constraint_name = 'ck_rec_bag_label_item_sequence_v59'
          AND REPLACE(LOWER(check_clause), CHAR(96), '')
              LIKE '%sequence_no between 1 and 500%'
      )
  );
"@)
    if ($bagLabelLimitConstraints -ne 2) {
        throw "V59 bag-label quantity constraints are not both 1 through 500"
    }
    $mcuRemoteUpdateColumns = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = '$($databaseNames.Correct)'
  AND column_name = 'mcu_remote_update_capable'
  AND table_name IN (
      'dev_device_acceptance_evidence',
      'dev_device_asset'
  )
  AND is_nullable = 'YES'
  AND data_type = 'tinyint';
"@)
    if ($mcuRemoteUpdateColumns -ne 2) {
        throw "V60 MCU remote-update capability columns are incomplete"
    }
    $mcuRemoteUpdateConstraints = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.Correct)'
  AND constraint_name IN (
      'ck_dev_acceptance_mcu_remote_update_v60',
      'ck_dev_asset_mcu_remote_update_v60'
  )
  AND REPLACE(LOWER(check_clause), CHAR(96), '')
      LIKE '%mcu_remote_update_capable%'
  AND (
      constraint_name = 'ck_dev_asset_mcu_remote_update_v60'
      OR REPLACE(LOWER(check_clause), CHAR(96), '')
          LIKE '%mcu_remote_update_capable is not null%'
  );
"@)
    if ($mcuRemoteUpdateConstraints -ne 2) {
        throw "V60 MCU remote-update capability constraints are incomplete"
    }
    $externalRequestIdColumns = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name = 'ops_task_attempt'
  AND column_name = 'external_request_id'
  AND is_nullable = 'YES'
  AND data_type = 'varchar'
  AND character_maximum_length = 128
  AND character_set_name = 'ascii'
  AND collation_name = 'ascii_bin'
  AND column_default IS NULL;
"@)
    if ($externalRequestIdColumns -ne 1) {
        throw "V61 OneNet external request identity column is incomplete"
    }
    $externalRequestIdConstraints = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.check_constraints
WHERE constraint_schema = '$($databaseNames.Correct)'
  AND constraint_name = 'ck_ops_attempt_external_request_v61'
  AND REPLACE(LOWER(check_clause), CHAR(96), '')
      LIKE '%external_request_id%'
  AND REPLACE(LOWER(check_clause), CHAR(96), '')
      LIKE '%result_recorded_at is not null%';
"@)
    if ($externalRequestIdConstraints -ne 1) {
        throw "V61 OneNet external request identity constraint is incomplete"
    }
    $externalRequestIdIndexes = [int](Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT COUNT(*)
FROM information_schema.statistics
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name = 'ops_task_attempt'
  AND column_name = 'external_request_id';
"@)
    if ($externalRequestIdIndexes -ne 0) {
        throw "V61 diagnostic request identity must not be indexed as a business key"
    }
    $factoryProgressTaskIndex = Invoke-MySql `
        -Database $databaseNames.Correct `
        -Sql @"
SELECT GROUP_CONCAT(
    CONCAT(column_name, ':', collation)
    ORDER BY seq_in_index SEPARATOR ','
)
FROM information_schema.statistics
WHERE table_schema = '$($databaseNames.Correct)'
  AND table_name = 'ops_reliable_task'
  AND index_name = 'ix_ops_task_factory_progress'
  AND non_unique = 1;
"@
    if (
        $factoryProgressTaskIndex -ne
            "source_device_asset_id:A,task_type:A,id:D"
    ) {
        throw "V62 factory-progress reliable-task lookup index is incomplete"
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
        targetVersion = 68
        domainTables = 131
        permissionReferenceRows = $permissionCount
        businessInstanceRows = $businessRowsAfter
        runtimePrincipal = $runtimePrincipal
        schemaOwnerPresentAtRuntime = $false
        triggerDefinerLocked = $true
        runtimeDdlRejected = $true
        runtimeFactDeleteRejected = $true
        correctV68Ready = $true
        businessReleaseValidationV65 = $true
        businessUpdateCancellationV66 = $true
        imageBridgeBaselineV67 = $true
        softwareFactImageBridgeV68 = $true
        deviceAssetManagementTriggerReady = $true
        bagLabelBatchLimit500 = $true
        mcuRemoteUpdateCapabilityV60 = $true
        externalRequestIdV61 = $true
        factoryProgressTaskIndexV62 = $true
        clockRecoveryV57UpgradeConverged = $true
        qualifiedCommandFailuresRetained = $true
        authorizationNullableStateFactsRejected = $true
        sealedClockQualityRequired = $true
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

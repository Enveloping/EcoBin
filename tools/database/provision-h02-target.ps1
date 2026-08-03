[CmdletBinding()]
param(
    [string]$MySqlImage =
        "mysql@sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6",
    [string]$ExpectedImageId =
        "sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6",
    [string]$ExpectedMySqlVersion = "8.4.10",
    [string]$JavaHome = "C:\D\002-Tools\004-DevTool\jdk-21.0.10",
    [string]$MavenExecutable = "mvn.cmd",
    [string]$ComposeProjectName = "ecobin-h02",
    [string]$ContainerName = "ecobin-h02-mysql84",
    [string]$VolumeName = "ecobin-h02-mysql84-data",
    [string]$NetworkName = "ecobin-h02-network",
    [ValidateRange(1024, 65535)]
    [int]$HostPort = 13306,
    [ValidatePattern("^[a-z][a-z0-9_]{0,62}$")]
    [string]$DatabaseName = "ecobin",
    [string]$Operator = "enveloping",
    [string]$SecretDirectory = "C:\tmp\ecobin-h02-secrets",
    [string]$EvidenceRoot = "C:\tmp\ecobin-h02-evidence",
    [string]$RemoteHost = "",
    [switch]$UseExistingProductionSecrets,
    [string]$RemoteComposeDirectory = "/etc/ecobin/h02",
    [string]$RemoteRootPasswordPath =
        "/etc/ecobin/secrets/mysql-root-password",
    [string]$RemoteAppPasswordPath =
        "/etc/ecobin/secrets/db-app-password",
    [string]$RemoteBackupPasswordPath =
        "/etc/ecobin/secrets/db-backup-password",
    [switch]$ResumeExistingEmptyEnvironment,
    [switch]$ResumeExistingMigratedEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$localComposeFile = if ($RemoteHost.Length -gt 0) {
    Join-Path $repoRoot "deploy/production/docker-compose.h02-server.yml"
}
else {
    Join-Path $repoRoot "docker-compose.h02.yml"
}
$dockerComposeFile = if ($RemoteHost.Length -gt 0) {
    "$RemoteComposeDirectory/docker-compose.h02-server.yml"
}
else {
    $localComposeFile
}
$grantCatalogPath = Join-Path $PSScriptRoot "h02-runtime-grants.psd1"
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$evidenceDirectory = Join-Path $EvidenceRoot $runId
$flywayConfigPath = Join-Path $SecretDirectory "flyway-owner.conf"
$composeEnvPath = Join-Path $SecretDirectory "compose.stage3.env"
$dockerComposeEnvPath = if ($RemoteHost.Length -gt 0) {
    "$RemoteComposeDirectory/compose.env"
}
else {
    $composeEnvPath
}
$rootPasswordName = if ($UseExistingProductionSecrets) {
    "mysql-root-password"
}
else {
    "root-password.txt"
}
$appPasswordName = if ($UseExistingProductionSecrets) {
    "db-app-password"
}
else {
    "app-password.txt"
}
$backupPasswordName = if ($UseExistingProductionSecrets) {
    "db-backup-password"
}
else {
    "backup-password.txt"
}
$rootPasswordPath = Join-Path $SecretDirectory $rootPasswordName
$appPasswordPath = Join-Path $SecretDirectory $appPasswordName
$backupPasswordPath = Join-Path $SecretDirectory $backupPasswordName
$migrationCompleted = $false
$sshTunnelProcess = $null

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

function ConvertTo-PosixShellArgument {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    return "'" + $Value.Replace("'", "'""'""'") + "'"
}

function Invoke-RemoteCommand {
    param(
        [Parameter(Mandatory)][string]$Command,
        [AllowEmptyString()][string]$InputText,
        [switch]$AllowFailure
    )

    $sshArguments = @(
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        $RemoteHost,
        $Command
    )
    if ($PSBoundParameters.ContainsKey("InputText")) {
        $output = $InputText | & ssh @sshArguments 2>&1
    }
    else {
        $output = & ssh @sshArguments 2>&1
    }
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        $tail = (@($output) | Select-Object -Last 30) -join "`n"
        throw "Remote command failed with exit code $exitCode`n$tail"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($output)
    }
}

function New-RemoteDockerCommand {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $quoted = @($Arguments | ForEach-Object {
        ConvertTo-PosixShellArgument -Value $_
    })
    return "sudo -n docker " + ($quoted -join " ")
}

function Install-RemoteComposeFiles {
    if ($RemoteHost.Length -eq 0) {
        return
    }

    $directoryArgument =
        ConvertTo-PosixShellArgument -Value $RemoteComposeDirectory
    Invoke-RemoteCommand -Command (
        "sudo -n install -d -o root -g root -m 0750 " +
        $directoryArgument
    ) | Out-Null

    $composeTarget =
        ConvertTo-PosixShellArgument -Value $dockerComposeFile
    Invoke-RemoteCommand `
        -Command (
            "sudo -n tee $composeTarget >/dev/null && " +
            "sudo -n chown root:root $composeTarget && " +
            "sudo -n chmod 0644 $composeTarget"
        ) `
        -InputText (Get-Content -Raw -LiteralPath $localComposeFile) |
        Out-Null

    $envTarget =
        ConvertTo-PosixShellArgument -Value $dockerComposeEnvPath
    Invoke-RemoteCommand `
        -Command (
            "sudo -n tee $envTarget >/dev/null && " +
            "sudo -n chown root:root $envTarget && " +
            "sudo -n chmod 0600 $envTarget"
        ) `
        -InputText (Get-Content -Raw -LiteralPath $composeEnvPath) |
        Out-Null
}

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    if ($RemoteHost.Length -gt 0) {
        $result = Invoke-RemoteCommand `
            -Command (New-RemoteDockerCommand -Arguments $Arguments) `
            -AllowFailure
        $output = $result.Output
        $exitCode = $result.ExitCode
    }
    else {
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    if ($exitCode -ne 0) {
        $tail = (@($output) | Select-Object -Last 30) -join "`n"
        throw "Docker command failed with exit code $exitCode`n$tail"
    }
    return @($output)
}

function Test-DockerObject {
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Name
    )

    if ($RemoteHost.Length -gt 0) {
        $result = Invoke-RemoteCommand `
            -Command (New-RemoteDockerCommand -Arguments @(
                $Kind, "inspect", $Name
            )) `
            -AllowFailure
        return $result.ExitCode -eq 0
    }

    & docker $Kind inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}

function Protect-LocalDirectory {
    param([Parameter(Mandatory)][string]$Path)

    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    if ($IsWindows) {
        $identity =
            [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls $Path `
            "/inheritance:r" `
            "/grant:r" "${identity}:(OI)(CI)F" `
            "/grant:r" "*S-1-5-18:(OI)(CI)F" *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not restrict ACLs for $Path"
        }
    }
}

function Write-SecretFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Value
    )

    Set-Content -LiteralPath $Path -Value $Value -NoNewline -Encoding utf8
}

function Invoke-RootSql {
    param(
        [Parameter(Mandatory)][string]$Sql,
        [AllowEmptyString()][string]$Database = ""
    )

    $command =
        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" ' +
        'exec mysql -uroot --batch --skip-column-names'
    if ($Database.Length -gt 0) {
        $command += " --database=$Database"
    }
    if ($RemoteHost.Length -gt 0) {
        $remoteCommand = New-RemoteDockerCommand -Arguments @(
            "exec", "-i", $ContainerName, "sh", "-c", $command
        )
        $result = Invoke-RemoteCommand `
            -Command $remoteCommand `
            -InputText $Sql `
            -AllowFailure
        $output = $result.Output
        $exitCode = $result.ExitCode
    }
    else {
        $output = $Sql |
            & docker exec -i $ContainerName sh -c $command 2>&1
        $exitCode = $LASTEXITCODE
    }
    if ($exitCode -ne 0) {
        $tail = (@($output) | Select-Object -Last 30) -join "`n"
        throw "Root SQL failed with exit code $exitCode`n$tail"
    }
    return (@($output) -join "`n").Trim()
}

function Invoke-ClientSql {
    param(
        [Parameter(Mandatory)][string]$User,
        [Parameter(Mandatory)][string]$PasswordFile,
        [Parameter(Mandatory)][string]$Sql,
        [switch]$ExpectFailure
    )

    $effectivePasswordFile = if ($RemoteHost.Length -gt 0) {
        if ($User -eq "ecobin_app") {
            $RemoteAppPasswordPath
        }
        elseif ($User -eq "ecobin_backup") {
            $RemoteBackupPasswordPath
        }
        else {
            throw "No remote password file mapping for user $User"
        }
    }
    else {
        $PasswordFile
    }
    $mount =
        "type=bind,source=$effectivePasswordFile," +
        "target=/run/secrets/db_password,readonly"
    $command =
        'MYSQL_PWD="$(cat /run/secrets/db_password)" ' +
        'exec mysql -h "$H02_DB_HOST" -u "$H02_DB_USER" ' +
        '--database="$H02_DB_NAME" --batch --skip-column-names'
    $arguments = @(
        "run", "--rm", "--interactive",
        "--network", $NetworkName,
        "--mount", $mount,
        "--env", "H02_DB_HOST=$ContainerName",
        "--env", "H02_DB_USER=$User",
        "--env", "H02_DB_NAME=$DatabaseName",
        $MySqlImage,
        "sh", "-c", $command
    )

    if ($RemoteHost.Length -gt 0) {
        $result = Invoke-RemoteCommand `
            -Command (New-RemoteDockerCommand -Arguments $arguments) `
            -InputText $Sql `
            -AllowFailure
        $output = $result.Output
        $exitCode = $result.ExitCode
    }
    else {
        $output = $Sql | & docker @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    if ($ExpectFailure) {
        if ($exitCode -eq 0) {
            throw "$User unexpectedly executed a forbidden SQL statement"
        }
        return
    }
    if ($exitCode -ne 0) {
        $tail = (@($output) | Select-Object -Last 30) -join "`n"
        throw "$User SQL failed with exit code $exitCode`n$tail"
    }
    return (@($output) -join "`n").Trim()
}

function Invoke-BackupReadProbe {
    $effectivePasswordPath = if ($RemoteHost.Length -gt 0) {
        $RemoteBackupPasswordPath
    }
    else {
        $backupPasswordPath
    }
    $mount =
        "type=bind,source=$effectivePasswordPath," +
        "target=/run/secrets/db_password,readonly"
    $command =
        'MYSQL_PWD="$(cat /run/secrets/db_password)" ' +
        'exec mysqldump -h "$H02_DB_HOST" -u ecobin_backup ' +
        '--single-transaction --skip-triggers --no-tablespaces ' +
        '--no-create-info --compact "$H02_DB_NAME"'
    $arguments = @(
        "run", "--rm",
        "--network", $NetworkName,
        "--mount", $mount,
        "--env", "H02_DB_HOST=$ContainerName",
        "--env", "H02_DB_NAME=$DatabaseName",
        $MySqlImage,
        "sh", "-c", $command
    )
    if ($RemoteHost.Length -gt 0) {
        $result = Invoke-RemoteCommand `
            -Command (New-RemoteDockerCommand -Arguments $arguments) `
            -AllowFailure
        $output = $result.Output
        $exitCode = $result.ExitCode
    }
    else {
        $output = & docker @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    if ($exitCode -ne 0) {
        $tail = (@($output) | Select-Object -Last 30) -join "`n"
        throw "ecobin_backup read probe failed`n$tail"
    }
    return (Get-Sha256 -Value (@($output) -join "`n"))
}

function Get-Sha256 {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

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

function Invoke-FlywayMigration {
    param(
        [Parameter(Mandatory)][int]$Target,
        [Parameter(Mandatory)][string]$OwnerPassword
    )

    $jdbcUrl =
        "jdbc:mysql://127.0.0.1:$HostPort/$DatabaseName" +
        "?sslMode=DISABLED&allowPublicKeyRetrieval=true&serverTimezone=UTC"
    @(
        "flyway.url=$jdbcUrl"
        "flyway.user=ecobin_schema_owner"
        "flyway.password=$OwnerPassword"
        "flyway.connectRetries=10"
    ) | Set-Content -LiteralPath $flywayConfigPath -Encoding utf8

    $logPath = Join-Path $evidenceDirectory "flyway-v1-v$Target.log"
    $previousJavaHome = $env:JAVA_HOME
    $env:JAVA_HOME = $JavaHome
    Push-Location $repoRoot
    try {
        & $MavenExecutable `
            "-q" `
            "-pl" "ecobin-bootstrap" `
            "flyway:migrate" `
            "-Dflyway.configFiles=$($flywayConfigPath.Replace('\', '/'))" `
            "-Dflyway.target=$Target" *> $logPath
        if ($LASTEXITCODE -ne 0) {
            $diagnostic = Get-Content -Raw -LiteralPath $logPath
            $diagnostic =
                $diagnostic.Replace($OwnerPassword, "[REDACTED]")
            if ($diagnostic.Length -gt 6000) {
                $diagnostic = $diagnostic.Substring(
                    $diagnostic.Length - 6000)
            }
            throw "Flyway migration to V$Target failed`n$diagnostic"
        }
    }
    finally {
        Pop-Location
        if ($null -eq $previousJavaHome) {
            Remove-Item Env:JAVA_HOME -ErrorAction SilentlyContinue
        }
        else {
            $env:JAVA_HOME = $previousJavaHome
        }
        if (Test-Path -LiteralPath $flywayConfigPath) {
            Remove-Item -LiteralPath $flywayConfigPath -Force
        }
    }
}

function Quote-Identifier {
    param([Parameter(Mandatory)][string]$Value)
    return "``$Value``"
}

function Assert-GrantCatalog {
    param(
        [Parameter(Mandatory)][hashtable]$Catalog,
        [Parameter(Mandatory)][string[]]$Tables
    )

    $knownTables = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal)
    foreach ($table in $Tables) {
        [void]$knownTables.Add($table)
    }

    $classified = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal)
    $catalogTables = @(
        $Catalog.ReadOnlyTables
        $Catalog.SlotTables
        $Catalog.UpdateColumns.Keys
        $Catalog.PendingUpdateTables
    )
    foreach ($table in $catalogTables) {
        if (-not $knownTables.Contains($table)) {
            throw "Grant catalog references unknown table: $table"
        }
        if (-not $classified.Add($table)) {
            throw "Grant catalog classifies a table more than once: $table"
        }
    }

    foreach ($entry in $Catalog.UpdateColumns.GetEnumerator()) {
        $columns = @($entry.Value)
        if ($columns.Count -eq 0) {
            throw "Update grant has no columns: $($entry.Key)"
        }
        $columnSql =
            "SELECT column_name FROM information_schema.columns " +
            "WHERE table_schema = '$DatabaseName' " +
            "AND table_name = '$($entry.Key)' ORDER BY ordinal_position;"
        $actualColumns = @(
            (Invoke-RootSql -Sql $columnSql) -split "`r?`n" |
                Where-Object { $_.Length -gt 0 }
        )
        foreach ($column in $columns) {
            if ($actualColumns -notcontains $column) {
                throw "Grant catalog references unknown column: " +
                    "$($entry.Key).$column"
            }
        }
    }
}

function New-RuntimeGrantSql {
    param(
        [Parameter(Mandatory)][hashtable]$Catalog,
        [Parameter(Mandatory)][string[]]$Tables
    )

    $database = Quote-Identifier -Value $DatabaseName
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($table in $Tables) {
        $qualified = "$database.$(Quote-Identifier -Value $table)"
        if ($Catalog.ReadOnlyTables -contains $table) {
            $lines.Add(
                "GRANT SELECT ON $qualified TO 'ecobin_app'@'%';")
        }
        else {
            $lines.Add(
                "GRANT SELECT, INSERT ON $qualified TO 'ecobin_app'@'%';")
        }
        $lines.Add(
            "GRANT SELECT ON $qualified TO 'ecobin_backup'@'%';")
    }

    $history =
        "$database.$(Quote-Identifier -Value 'flyway_schema_history')"
    $lines.Add(
        "GRANT SELECT ON $history TO 'ecobin_app'@'%';")
    $lines.Add(
        "GRANT SELECT ON $history TO 'ecobin_backup'@'%';")

    foreach ($table in $Catalog.SlotTables) {
        $qualified = "$database.$(Quote-Identifier -Value $table)"
        $lines.Add(
            "GRANT DELETE ON $qualified TO 'ecobin_app'@'%';")
    }

    foreach ($entry in $Catalog.UpdateColumns.GetEnumerator()) {
        $qualified =
            "$database.$(Quote-Identifier -Value $entry.Key)"
        $columns = @($entry.Value) |
            ForEach-Object { Quote-Identifier -Value $_ }
        $lines.Add(
            "GRANT UPDATE ($($columns -join ', ')) ON $qualified " +
            "TO 'ecobin_app'@'%';")
    }
    return $lines -join "`n"
}

function Get-ImageId {
    param([Parameter(Mandatory)][string]$Reference)
    $output = @(
        Invoke-Docker -Arguments @(
            "image", "inspect", "--format", "{{.Id}}", $Reference
        )
    )
    return $output[0].ToString().Trim()
}

function Assert-HostPortAvailable {
    $listener = [Net.Sockets.TcpListener]::new(
        [Net.IPAddress]::Loopback,
        $HostPort)
    try {
        $listener.Start()
    }
    catch {
        throw "Host port $HostPort is not available"
    }
    finally {
        $listener.Stop()
    }
}

function Start-RemoteDatabaseTunnel {
    if ($RemoteHost.Length -eq 0) {
        return $null
    }

    $ipOutput = @(
        Invoke-Docker -Arguments @(
            "container", "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            $ContainerName
        )
    )
    $containerIp = $ipOutput[0].ToString().Trim()
    if ($containerIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
        throw "Could not determine the target MySQL container IPv4 address"
    }

    $forward = "127.0.0.1:${HostPort}:${containerIp}:3306"
    $process = Start-Process `
        -FilePath (Get-Command ssh).Source `
        -ArgumentList @(
            "-N",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ExitOnForwardFailure=yes",
            "-L", $forward,
            $RemoteHost
        ) `
        -WindowStyle Hidden `
        -PassThru

    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        if ($process.HasExited) {
            throw "SSH database tunnel exited before becoming ready"
        }
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $client.Connect([Net.IPAddress]::Loopback, $HostPort)
            return $process
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
        finally {
            $client.Dispose()
        }
    }

    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "SSH database tunnel did not become ready"
}

if ($SecretDirectory.StartsWith(
    $repoRoot,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "SecretDirectory must be outside the repository"
}
if ($EvidenceRoot.StartsWith(
    $repoRoot,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "EvidenceRoot must be outside the repository"
}
$resumeExistingEnvironment =
    $ResumeExistingEmptyEnvironment -or
    $ResumeExistingMigratedEnvironment
if (
    $ResumeExistingEmptyEnvironment -and
    $ResumeExistingMigratedEnvironment
) {
    throw "Select only one H-02 resume mode"
}
if ($UseExistingProductionSecrets) {
    foreach ($requiredSecret in @(
        $rootPasswordPath,
        $appPasswordPath,
        $backupPasswordPath
    )) {
        if (-not (Test-Path -LiteralPath $requiredSecret)) {
            throw "Existing production secret is missing: $requiredSecret"
        }
    }
}

if ($resumeExistingEnvironment) {
    if (-not (Test-Path -LiteralPath $SecretDirectory)) {
        throw "Resume requires the existing secret directory"
    }
    foreach ($requiredSecret in @(
        $rootPasswordPath,
        $appPasswordPath,
        $backupPasswordPath
    )) {
        if (-not (Test-Path -LiteralPath $requiredSecret)) {
            throw "Resume is missing required local state: $requiredSecret"
        }
    }
    if (-not (Test-DockerObject -Kind "container" -Name $ContainerName)) {
        throw "Resume requires the existing target container"
    }
    if (-not (Test-DockerObject -Kind "volume" -Name $VolumeName)) {
        throw "Resume requires the existing target volume"
    }
    if (-not (Test-DockerObject -Kind "network" -Name $NetworkName)) {
        throw "Resume requires the existing target network"
    }
}
elseif (
    (Test-Path -LiteralPath $SecretDirectory) -and
    -not $UseExistingProductionSecrets
) {
    throw "Secret directory already exists; refusing to overwrite it: " +
        $SecretDirectory
}

$javaExecutable = Join-Path $JavaHome "bin/java.exe"
if (-not (Test-Path -LiteralPath $javaExecutable)) {
    throw "Java 21 executable not found under JavaHome: $JavaHome"
}
$javaVersionOutput = & $javaExecutable -version 2>&1
$javaVersionText = @($javaVersionOutput) -join "`n"
if ($LASTEXITCODE -ne 0 -or $javaVersionText -notmatch 'version "21\.') {
    throw "H-02 migrations require Java 21"
}
$previousJavaHome = $env:JAVA_HOME
try {
    $env:JAVA_HOME = $JavaHome
    $mavenVersionOutput = & $MavenExecutable -version 2>&1
    $mavenVersionText = @($mavenVersionOutput) -join "`n"
    if (
        $LASTEXITCODE -ne 0 -or
        $mavenVersionText -notmatch "Java version: 21\."
    ) {
        throw "Maven is not running with Java 21"
    }
}
finally {
    if ($null -eq $previousJavaHome) {
        Remove-Item Env:JAVA_HOME -ErrorAction SilentlyContinue
    }
    else {
        $env:JAVA_HOME = $previousJavaHome
    }
}

if (-not $resumeExistingEnvironment) {
    if (Test-DockerObject -Kind "container" -Name $ContainerName) {
        throw "Container already exists: $ContainerName"
    }
    if (Test-DockerObject -Kind "volume" -Name $VolumeName) {
        throw "Volume already exists: $VolumeName"
    }
    if (Test-DockerObject -Kind "network" -Name $NetworkName) {
        throw "Network already exists: $NetworkName"
    }
}

$actualImageId = Get-ImageId -Reference $MySqlImage
if ($actualImageId -ne $ExpectedImageId) {
    throw "MySQL image ID does not match the pinned H-02 image"
}

Protect-LocalDirectory -Path $evidenceDirectory

$ownerPassword = New-RandomSecret
if ($resumeExistingEnvironment) {
    $rootPassword =
        Get-Content -Raw -LiteralPath $rootPasswordPath
    $appPassword =
        Get-Content -Raw -LiteralPath $appPasswordPath
    $backupPassword =
        Get-Content -Raw -LiteralPath $backupPasswordPath
}
else {
    Assert-HostPortAvailable
    if ($UseExistingProductionSecrets) {
        $rootPassword =
            Get-Content -Raw -LiteralPath $rootPasswordPath
        $appPassword =
            Get-Content -Raw -LiteralPath $appPasswordPath
        $backupPassword =
            Get-Content -Raw -LiteralPath $backupPasswordPath
    }
    else {
        Protect-LocalDirectory -Path $SecretDirectory
        $rootPassword = New-RandomSecret
        $appPassword = New-RandomSecret
        $backupPassword = New-RandomSecret
        Write-SecretFile -Path $rootPasswordPath -Value $rootPassword
        Write-SecretFile -Path $appPasswordPath -Value $appPassword
        Write-SecretFile -Path $backupPasswordPath -Value $backupPassword
    }
}

$composeRootPasswordPath = if ($RemoteHost.Length -gt 0) {
    $RemoteRootPasswordPath
}
else {
    $rootPasswordPath.Replace('\', '/')
}
@(
    "H02_COMPOSE_PROJECT_NAME=$ComposeProjectName"
    "H02_MYSQL_IMAGE=$MySqlImage"
    "H02_CONTAINER_NAME=$ContainerName"
    "H02_HOST_PORT=$HostPort"
    "H02_VOLUME_NAME=$VolumeName"
    "H02_NETWORK_NAME=$NetworkName"
    "H02_ROOT_PASSWORD_FILE=$composeRootPasswordPath"
) | Set-Content -LiteralPath $composeEnvPath -Encoding utf8

try {
    Install-RemoteComposeFiles
    if ($ResumeExistingEmptyEnvironment) {
        Invoke-Docker -Arguments @(
            "compose",
            "--project-name", $ComposeProjectName,
            "--env-file", $dockerComposeEnvPath,
            "--file", $dockerComposeFile,
            "down", "--remove-orphans"
        ) | Out-Null
        if (-not (Test-DockerObject -Kind "volume" -Name $VolumeName)) {
            throw "Resume unexpectedly removed the persistent target volume"
        }
        Assert-HostPortAvailable
    }

    Invoke-Docker -Arguments @(
        "compose",
        "--project-name", $ComposeProjectName,
            "--env-file", $dockerComposeEnvPath,
            "--file", $dockerComposeFile,
        "up", "--detach", "--wait", "--wait-timeout", "180"
    ) | Out-Null

    $containerImageOutput = @(
        Invoke-Docker -Arguments @(
            "container", "inspect",
            "--format", "{{.Image}}", $ContainerName
        )
    )
    $containerImageId = $containerImageOutput[0].ToString().Trim()
    if ($containerImageId -ne $ExpectedImageId) {
        throw "Running container does not use the pinned image ID"
    }

    $mysqlVersion = Invoke-RootSql -Sql "SELECT VERSION();"
    if ($mysqlVersion -ne $ExpectedMySqlVersion) {
        throw "Expected MySQL $ExpectedMySqlVersion, got $mysqlVersion"
    }

    $database = Quote-Identifier -Value $DatabaseName
    $skipMigration = $false
    if ($ResumeExistingEmptyEnvironment) {
        $existingDatabaseCount = [int](Invoke-RootSql -Sql @"
SELECT COUNT(*) FROM information_schema.schemata
WHERE schema_name = '$DatabaseName';
"@)
        $existingTableCount = [int](Invoke-RootSql -Sql @"
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = '$DatabaseName';
"@)
        if ($existingDatabaseCount -ne 1 -or $existingTableCount -ne 0) {
            throw "Resume is only allowed for the preserved empty target database"
        }
        Invoke-RootSql -Sql @"
ALTER USER 'ecobin_schema_owner'@'%'
    IDENTIFIED BY '$ownerPassword' ACCOUNT UNLOCK;
"@ | Out-Null
    }
    elseif ($ResumeExistingMigratedEnvironment) {
        $existingDomainTableCount = [int](Invoke-RootSql -Sql @"
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = '$DatabaseName'
  AND table_name <> 'flyway_schema_history';
"@)
        $existingHistoryCount = [int](Invoke-RootSql `
            -Database $DatabaseName `
            -Sql "SELECT COUNT(*) FROM flyway_schema_history WHERE success=1;")
        if (
            $existingDomainTableCount -ne 96 -or
            $existingHistoryCount -ne 31
        ) {
            throw "Migrated resume requires the complete V31 target database"
        }
        $migrationCompleted = $true
        $skipMigration = $true
    }
    else {
        Invoke-RootSql -Sql @"
CREATE DATABASE $database
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'ecobin_trigger_definer'@'%' ACCOUNT LOCK;
CREATE USER 'ecobin_schema_owner'@'%'
    IDENTIFIED BY '$ownerPassword';
CREATE USER 'ecobin_app'@'%'
    IDENTIFIED BY '$appPassword';
CREATE USER 'ecobin_backup'@'%'
    IDENTIFIED BY '$backupPassword';
GRANT ALL PRIVILEGES ON $database.*
    TO 'ecobin_schema_owner'@'%';
GRANT SET_ANY_DEFINER ON *.*
    TO 'ecobin_schema_owner'@'%';
"@ | Out-Null
    }

    if (-not $skipMigration) {
        $sshTunnelProcess = Start-RemoteDatabaseTunnel
        Invoke-FlywayMigration -Target 8 -OwnerPassword $ownerPassword

        Invoke-RootSql -Sql @"
GRANT TRIGGER ON $database.*
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    activated_at, appid, tenant_id, organization_id
) ON $database.iam_organization_miniapp
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    tenant_id, organization_id, organization_miniapp_id, openid,
    registered_at, registered_via_deployment_id
) ON $database.iam_organization_user
    TO 'ecobin_trigger_definer'@'%';
"@ | Out-Null

        Invoke-FlywayMigration -Target 31 -OwnerPassword $ownerPassword
        $migrationCompleted = $true

        Invoke-RootSql -Sql @"
ALTER USER 'ecobin_schema_owner'@'%' ACCOUNT LOCK;
"@ | Out-Null
    }

    $tableSql =
        "SELECT table_name FROM information_schema.tables " +
        "WHERE table_schema = '$DatabaseName' " +
        "AND table_type = 'BASE TABLE' " +
        "AND table_name <> 'flyway_schema_history' " +
        "ORDER BY table_name;"
    $tables = @(
        (Invoke-RootSql -Sql $tableSql) -split "`r?`n" |
            Where-Object { $_.Length -gt 0 }
    )
    if ($tables.Count -ne 96) {
        throw "Expected 96 domain tables, got $($tables.Count)"
    }

    $grantCatalog = Import-PowerShellDataFile -Path $grantCatalogPath
    Assert-GrantCatalog -Catalog $grantCatalog -Tables $tables
    $grantSql = New-RuntimeGrantSql `
        -Catalog $grantCatalog `
        -Tables $tables
    Invoke-RootSql -Sql $grantSql | Out-Null

    $configEvidence = Invoke-RootSql -Sql @"
SELECT CONCAT_WS(
    '|',
    @@global.time_zone,
    @@session.time_zone,
    @@global.transaction_isolation,
    @@global.sql_mode,
    @@global.log_bin_trust_function_creators,
    @@character_set_server,
    @@collation_server
);
"@
    $markerEvidence = Invoke-RootSql -Database $DatabaseName -Sql @"
SELECT CONCAT_WS(
    '|', version, description, script, checksum, success
)
FROM flyway_schema_history
WHERE version = '1';
"@
    if (
        $markerEvidence -ne
        "1|p0 epoch and iam core|V1__p0_epoch_and_iam_core.sql|229072802|1"
    ) {
        throw "Target V1 marker does not match the frozen epoch"
    }

    $historyCount = [int](Invoke-RootSql `
        -Database $DatabaseName `
        -Sql "SELECT COUNT(*) FROM flyway_schema_history WHERE success = 1;")
    if ($historyCount -ne 31) {
        throw "Expected thirty-one successful Flyway migrations"
    }
    $permissionCount = [int](Invoke-RootSql `
        -Database $DatabaseName `
        -Sql "SELECT COUNT(*) FROM iam_permission_definition;")
    if ($permissionCount -ne 77) {
        throw "Expected 77 permission definitions"
    }

    $businessCountQueries = @(
        $tables |
            Where-Object { $_ -ne "iam_permission_definition" } |
            ForEach-Object {
                "SELECT COUNT(*) AS row_count FROM " +
                (Quote-Identifier -Value $_)
            }
    )
    $businessCountSql =
        "SELECT SUM(row_count) FROM (`n" +
        ($businessCountQueries -join "`nUNION ALL`n") +
        "`n) AS business_rows;"
    $businessRowCount = [int](Invoke-RootSql `
        -Database $DatabaseName `
        -Sql $businessCountSql)
    if ($businessRowCount -ne 0) {
        throw "Target database contains unexpected business rows"
    }

    $runtimePrincipal = Invoke-ClientSql `
        -User "ecobin_app" `
        -PasswordFile $appPasswordPath `
        -Sql "SELECT CURRENT_USER();"
    if ($runtimePrincipal -ne "ecobin_app@%") {
        throw "Runtime connection is not ecobin_app@%"
    }
    Invoke-ClientSql `
        -User "ecobin_app" `
        -PasswordFile $appPasswordPath `
        -Sql "SELECT COUNT(*) FROM flyway_schema_history;" | Out-Null
    Invoke-ClientSql `
        -User "ecobin_app" `
        -PasswordFile $appPasswordPath `
        -Sql @"
START TRANSACTION;
SELECT locked_task.id
FROM (
    SELECT candidate.id
    FROM ops_reliable_task candidate FORCE INDEX (ix_ops_task_claim)
    WHERE candidate.state = 'PENDING'
      AND EXISTS (
          SELECT 1
          FROM dev_config_version config
          WHERE config.deployment_id =
              candidate.source_device_deployment_id
      )
    ORDER BY candidate.claimable_at, candidate.priority, candidate.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
) locked_task;
ROLLBACK;
"@ | Out-Null
    Invoke-ClientSql `
        -User "ecobin_app" `
        -PasswordFile $appPasswordPath `
        -Sql (
            "UPDATE dev_config_version SET " +
            "device_display_name=device_display_name WHERE 1=0;"
        ) `
        -ExpectFailure
    Invoke-ClientSql `
        -User "ecobin_app" `
        -PasswordFile $appPasswordPath `
        -Sql @"
START TRANSACTION;
INSERT INTO iam_tenant (
    tenant_code, enterprise_name, status,
    created_at, updated_at
) VALUES (
    'h02-permission-probe', 'H-02 permission probe', 'ENABLED',
    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
);
ROLLBACK;
"@ | Out-Null
    foreach ($entry in $grantCatalog.UpdateColumns.GetEnumerator()) {
        $table = Quote-Identifier -Value $entry.Key
        $allowedColumn = Quote-Identifier -Value (@($entry.Value)[0])
        Invoke-ClientSql `
            -User "ecobin_app" `
            -PasswordFile $appPasswordPath `
            -Sql "UPDATE $table SET $allowedColumn=$allowedColumn WHERE 1=0;" |
            Out-Null

        $immutableColumnSql =
            "SELECT column_name FROM information_schema.columns " +
            "WHERE table_schema='$DatabaseName' " +
            "AND table_name='$($entry.Key)' " +
            "AND column_name NOT IN (" +
            ((@($entry.Value) | ForEach-Object { "'$_'" }) -join ",") +
            ") ORDER BY ordinal_position LIMIT 1;"
        $immutableColumn = Invoke-RootSql -Sql $immutableColumnSql
        if ($immutableColumn.Length -eq 0) {
            throw "No immutable update probe column for $($entry.Key)"
        }
        $immutableIdentifier = Quote-Identifier -Value $immutableColumn
        Invoke-ClientSql `
            -User "ecobin_app" `
            -PasswordFile $appPasswordPath `
            -Sql (
                "UPDATE $table SET " +
                "$immutableIdentifier=$immutableIdentifier WHERE 1=0;"
            ) `
            -ExpectFailure
    }
    foreach ($slotTable in $grantCatalog.SlotTables) {
        $slotIdentifier = Quote-Identifier -Value $slotTable
        Invoke-ClientSql `
            -User "ecobin_app" `
            -PasswordFile $appPasswordPath `
            -Sql "DELETE FROM $slotIdentifier WHERE 1=0;" |
            Out-Null
    }

    $forbiddenStatements = @(
        "CREATE TABLE h02_forbidden_ddl (id BIGINT PRIMARY KEY);"
        "GRANT SELECT ON $database.* TO 'ecobin_backup'@'%';"
        "DROP TRIGGER trg_iam_miniapp_activation_immutable;"
        "DELETE FROM iam_permission_definition;"
        "UPDATE rec_bag SET bag_code = bag_code WHERE 1 = 0;"
        "UPDATE fund_wechat_merchant_profile SET mchid = mchid WHERE 1 = 0;"
        "SELECT COUNT(*) FROM mysql.user;"
    )
    foreach ($statement in $forbiddenStatements) {
        Invoke-ClientSql `
            -User "ecobin_app" `
            -PasswordFile $appPasswordPath `
            -Sql $statement `
            -ExpectFailure
    }

    Invoke-ClientSql `
        -User "ecobin_backup" `
        -PasswordFile $backupPasswordPath `
        -Sql "SELECT COUNT(*) FROM iam_permission_definition;" | Out-Null
    $backupDumpSha256 = Invoke-BackupReadProbe
    Invoke-ClientSql `
        -User "ecobin_backup" `
        -PasswordFile $backupPasswordPath `
        -Sql @"
INSERT INTO iam_tenant (
    tenant_code, enterprise_name, status,
    created_at, updated_at
) VALUES (
    'h02-backup-forbidden', 'forbidden', 'ENABLED',
    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
);
"@ `
        -ExpectFailure

    $ownerLocked = Invoke-RootSql -Sql @"
SELECT account_locked FROM mysql.user
WHERE user = 'ecobin_schema_owner' AND host = '%';
"@
    $definerLocked = Invoke-RootSql -Sql @"
SELECT account_locked FROM mysql.user
WHERE user = 'ecobin_trigger_definer' AND host = '%';
"@
    if ($ownerLocked -ne "Y" -or $definerLocked -ne "Y") {
        throw "Schema owner and trigger definer must both be locked"
    }

    $grants = [ordered]@{
        initializerAdmin = Invoke-RootSql -Sql `
            "SHOW GRANTS FOR 'root'@'localhost';"
        schemaOwner = Invoke-RootSql -Sql `
            "SHOW GRANTS FOR 'ecobin_schema_owner'@'%';"
        triggerDefiner = Invoke-RootSql -Sql `
            "SHOW GRANTS FOR 'ecobin_trigger_definer'@'%';"
        app = Invoke-RootSql -Sql `
            "SHOW GRANTS FOR 'ecobin_app'@'%';"
        backup = Invoke-RootSql -Sql `
            "SHOW GRANTS FOR 'ecobin_backup'@'%';"
    }
    foreach ($entry in $grants.GetEnumerator()) {
        Set-Content `
            -LiteralPath (Join-Path $evidenceDirectory "grants-$($entry.Key).txt") `
            -Value $entry.Value `
            -Encoding utf8
    }

    $pendingTables = @($grantCatalog.PendingUpdateTables | Sort-Object)
    Set-Content `
        -LiteralPath (Join-Path $evidenceDirectory "pending-update-tables.txt") `
        -Value $pendingTables `
        -Encoding utf8

    $runtimeGrantMatrixComplete = $pendingTables.Count -eq 0
    $summary = [ordered]@{
        taskId = "H-02"
        phase = if ($runtimeGrantMatrixComplete) {
            "environment-provisioned-runtime-grants-complete"
        }
        else {
            "environment-provisioned-runtime-grants-incomplete"
        }
        recordedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        operator = $Operator
        composeProject = $ComposeProjectName
        container = $ContainerName
        volume = $VolumeName
        network = $NetworkName
        hostBinding = if ($RemoteHost.Length -gt 0) {
            "none; temporary local SSH tunnel to container IPv4"
        }
        else {
            "127.0.0.1:$HostPort"
        }
        database = $DatabaseName
        imageReference = $MySqlImage
        imageId = $containerImageId
        mysqlVersion = $mysqlVersion
        mysqlConfiguration = $configEvidence
        flywayV1Marker = $markerEvidence
        successfulMigrations = $historyCount
        domainTables = $tables.Count
        permissionDefinitions = $permissionCount
        businessRows = $businessRowCount
        schemaOwnerLocked = $ownerLocked -eq "Y"
        triggerDefinerLocked = $definerLocked -eq "Y"
        runtimePrincipal = $runtimePrincipal
        runtimeUpdateGrantTables = @(
            $grantCatalog.UpdateColumns.Keys | Sort-Object
        )
        pendingRuntimeUpdateTables = $pendingTables
        runtimeGrantMatrixComplete = $runtimeGrantMatrixComplete
        backupReadProbeSha256 = $backupDumpSha256
        secretsStoredOutsideRepository = $true
        realIngressEnabled = $false
        oldDatabaseTouched = $false
    }
    $summary |
        ConvertTo-Json -Depth 6 |
        Set-Content `
            -LiteralPath (Join-Path $evidenceDirectory "summary.json") `
            -Encoding utf8

    Write-Host "H-02 target database environment provisioned."
    Write-Host "Container: $ContainerName"
    Write-Host "Volume: $VolumeName"
    Write-Host "Secrets directory: $SecretDirectory"
    Write-Host "Evidence directory: $evidenceDirectory"
    if ($runtimeGrantMatrixComplete) {
        Write-Host "Runtime column-level grant matrix is complete."
    }
    else {
        Write-Warning (
            "H-02 remains in-progress: " +
            "$($pendingTables.Count) tables still require reviewed " +
            "column-level UPDATE grants."
        )
    }
}
catch {
    Write-Error $_
    if (-not $migrationCompleted) {
        Write-Warning (
            "The target may be a failed first-install database. " +
            "It was intentionally preserved. Do not repair it or remove " +
            "container/volume without explicit destructive approval. " +
            "Container=$ContainerName Volume=$VolumeName"
        )
    }
    throw
}
finally {
    if ($null -ne $sshTunnelProcess -and -not $sshTunnelProcess.HasExited) {
        Stop-Process `
            -Id $sshTunnelProcess.Id `
            -Force `
            -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $flywayConfigPath) {
        Remove-Item -LiteralPath $flywayConfigPath -Force
    }
    $rootPassword = $null
    $ownerPassword = $null
    $appPassword = $null
    $backupPassword = $null
}

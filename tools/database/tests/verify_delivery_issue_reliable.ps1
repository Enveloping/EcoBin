param(
    [string]$JavaHome = 'C:/D/002-Tools/004-DevTool/jdk-21.0.10'
)

# Disposable loopback-only MySQL, cached image/dependencies, no production inputs.
# Never dot-source the production provisioning script: only reuse its reviewed GRANT text.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$database = 'ecobin_issue_p1bl'
$name = 'ecobin-issue-test-' + [Guid]::NewGuid().ToString('N')
$containerId = $null
$oldJava = $env:JAVA_HOME
$oldUrl = $env:ECOBIN_ISSUE_RELIABLE_MYSQL_URL

function Invoke-TestSql([string]$Sql) {
    $output = $Sql | & docker exec -i $containerId mysql -uroot --batch --skip-column-names
    if ($LASTEXITCODE -ne 0) { throw 'Isolated MySQL statement failed' }
    return $output
}

Push-Location $root
try {
    if (-not (Test-Path (Join-Path $JavaHome 'bin/java.exe'))) { throw 'Java 21 is required' }
    $env:JAVA_HOME = $JavaHome
    & docker image inspect mysql:8.4.10 --format '{{.Id}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cached mysql:8.4.10 image required; no automatic pull' }
    $containerId = & docker run --pull=never --rm --detach --name $name `
        --publish 127.0.0.1::3306 --env MYSQL_ALLOW_EMPTY_PASSWORD=yes `
        --env MYSQL_DATABASE=$database mysql:8.4.10 --log-bin-trust-function-creators=ON
    if ($LASTEXITCODE -ne 0 -or $containerId -notmatch '^[0-9a-f]{64}$') { throw 'Disposable MySQL startup failed' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 55; $attempt++) {
        # Windows PowerShell 5 treats expected native stderr as an ErrorRecord.
        $ErrorActionPreference = 'Continue'
        try {
            & docker exec $containerId mysql '-h127.0.0.1' -uroot --execute 'SELECT 1' *> $null
            $probeExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = 'Stop' }
        if ($probeExit -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Disposable MySQL did not become ready' }
    $binding = & docker port $containerId 3306/tcp
    if ($binding -notmatch '^127\.0\.0\.1:([0-9]+)$') { throw 'Only loopback database is permitted' }
    $url = "jdbc:mysql://127.0.0.1:$($Matches[1])/$database"
    Invoke-TestSql @"
CREATE USER 'ecobin_trigger_definer'@'%' ACCOUNT LOCK;
CREATE USER 'ecobin_schema_owner'@'%';
CREATE USER 'ecobin_app'@'%';
GRANT ALL PRIVILEGES ON $database.* TO 'ecobin_schema_owner'@'%';
GRANT SET_ANY_DEFINER ON *.* TO 'ecobin_schema_owner'@'%';
"@ | Out-Null
    & ./mvnw.cmd -o -q -pl ecobin-bootstrap flyway:migrate "-Dflyway.url=$url" `
        '-Dflyway.user=ecobin_schema_owner' '-Dflyway.password=' '-Dflyway.target=84'
    if ($LASTEXITCODE -ne 0) { throw 'Full V1..V84 migration failed' }
    $history = Invoke-TestSql "SELECT CONCAT(COUNT(*),':',MAX(CAST(version AS UNSIGNED))) FROM $database.flyway_schema_history WHERE success=1;"
    if ($history -ne '84:84') { throw 'Expected all 84 successful migrations' }
    $tables = @(Invoke-TestSql "SELECT table_name FROM information_schema.tables WHERE table_schema='$database' AND table_name<>'flyway_schema_history' ORDER BY table_name;")
    if ($tables.Count -ne 138) { throw 'Expected 138 domain tables' }
    $catalog = Import-PowerShellDataFile tools/database/h02-runtime-grants.psd1
    if ($catalog.CatalogVersion -ne 42) { throw 'Review this test harness for the new grant catalog' }
    $grants = [Collections.Generic.List[string]]::new()
    foreach ($table in $tables) {
        if ($table -notmatch '^[a-z0-9_]+$') { throw 'Unexpected table identifier' }
        $privileges = if ($catalog.ReadOnlyTables -contains $table) { 'SELECT' } else { 'SELECT, INSERT' }
        $grants.Add("GRANT $privileges ON $database.$table TO 'ecobin_app'@'%';")
    }
    $grants.Add("GRANT SELECT ON $database.flyway_schema_history TO 'ecobin_app'@'%';")
    foreach ($table in $catalog.SlotTables) {
        $grants.Add("GRANT DELETE ON $database.$table TO 'ecobin_app'@'%';")
    }
    foreach ($entry in $catalog.UpdateColumns.GetEnumerator()) {
        $columns = ($entry.Value | ForEach-Object { '`' + $_ + '`' }) -join ','
        $grants.Add("GRANT UPDATE ($columns) ON $database.$($entry.Key) TO 'ecobin_app'@'%';")
    }
    Invoke-TestSql ($grants -join "`n") | Out-Null

    # Read the final converged trigger-definer matrix, not historical upgrade grants.
    $source = Get-Content tools/database/provision-h02-target.ps1 -Raw
    $marker = $source.IndexOf('# Converge the trigger definer')
    if ($marker -lt 0) { throw 'Reviewed trigger grant section missing' }
    $section = $source.Substring($marker)
    $from = $section.IndexOf('GRANT TRIGGER ON')
    $to = $section.IndexOf("ALTER USER 'ecobin_schema_owner'")
    if ($from -lt 0 -or $to -le $from) { throw 'Reviewed trigger grant boundaries missing' }
    $definerSql = $section.Substring($from, $to - $from).Replace('$database', $database)
    foreach ($statement in $definerSql.Split(';')) {
        if ($statement.Trim().Length -gt 0 -and $statement.Trim() -notmatch '^GRANT\s') {
            throw 'Trigger matrix contains non-GRANT SQL; review required'
        }
    }
    Invoke-TestSql $definerSql | Out-Null
    Invoke-TestSql "ALTER USER 'ecobin_schema_owner'@'%' ACCOUNT LOCK;" | Out-Null
    $env:ECOBIN_ISSUE_RELIABLE_MYSQL_URL = $url
    & ./mvnw.cmd -o -q -pl ecobin-bootstrap -am test `
        '-Dtest=DeliveryIssueReliableMysqlIntegrationTest,InterruptedCleanBagRecoveryMysqlConstraintTest,TerminalMcuResultFailureMysqlIntegrationTest' `
        '-Dsurefire.failIfNoSpecifiedTests=false'
    if ($LASTEXITCODE -ne 0) { throw 'Issue reliable integration tests failed' }
    Write-Output 'PASS: V1..V84, 138 domain tables, runtime catalog 42, real issue inbox and terminal-result/clean-bag-recovery constraints'
}
finally {
    $env:JAVA_HOME = $oldJava
    $env:ECOBIN_ISSUE_RELIABLE_MYSQL_URL = $oldUrl
    if ($containerId -match '^[0-9a-f]{64}$') {
        $actual = & docker inspect --format '{{.Id}}|{{.Name}}|{{.Config.Image}}' $containerId 2>$null
        if ($LASTEXITCODE -eq 0 -and $actual -eq "$containerId|/$name|mysql:8.4.10") {
            & docker stop $containerId | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Warning "Could not remove isolated test container $name" }
        } else { Write-Warning 'Container identity mismatch: refusing cleanup' }
    }
    Pop-Location
}

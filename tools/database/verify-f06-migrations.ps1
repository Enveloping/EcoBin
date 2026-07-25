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
    "V1__p0_epoch_and_iam_core.sql",
    "V2__device_inventory_and_configuration.sql",
    "V3__organization_users_and_sessions.sql",
    "V4__device_operations_and_evidence.sql",
    "V5__recycling.sql",
    "V6__funds.sql",
    "V7__operations.sql",
    "V8__cross_module_constraints.sql",
    "V9__immutability_guards.sql",
    "V10__permission_reference_data.sql"
)
$expectedFundsTables = @(
    "fund_active_withdrawal",
    "fund_miniapp_merchant_binding",
    "fund_organization_payout_account",
    "fund_organization_payout_entry",
    "fund_organization_wallet_entry_counter",
    "fund_organization_withdraw_config",
    "fund_organization_withdraw_config_head",
    "fund_payout_gate",
    "fund_payout_gate_event",
    "fund_recharge_order",
    "fund_user_wallet",
    "fund_user_wallet_entry",
    "fund_wallet_adjustment",
    "fund_wechat_merchant_profile",
    "fund_wechat_payment",
    "fund_wechat_payment_observation",
    "fund_wechat_transfer",
    "fund_wechat_transfer_observation",
    "fund_withdrawal_order",
    "fund_withdrawal_review"
)
$expectedOperationsTables = @(
    "ops_alert",
    "ops_audit_log",
    "ops_inbox_message",
    "ops_message_quarantine",
    "ops_reconciliation_action",
    "ops_reconciliation_issue",
    "ops_reconciliation_run",
    "ops_reliable_task",
    "ops_task_attempt"
)
$organizationScopedTables = @(
    "fund_miniapp_merchant_binding",
    "fund_organization_payout_account",
    "fund_organization_payout_entry",
    "fund_organization_wallet_entry_counter",
    "fund_organization_withdraw_config",
    "fund_organization_withdraw_config_head",
    "fund_recharge_order",
    "fund_user_wallet",
    "fund_user_wallet_entry",
    "fund_wallet_adjustment",
    "fund_wechat_payment",
    "fund_wechat_payment_observation",
    "fund_wechat_transfer",
    "fund_wechat_transfer_observation",
    "fund_withdrawal_order",
    "fund_withdrawal_review",
    "fund_active_withdrawal",
    "ops_alert",
    "ops_audit_log",
    "ops_inbox_message",
    "ops_message_quarantine",
    "ops_reconciliation_action",
    "ops_reconciliation_issue",
    "ops_reliable_task"
)
$requiredConstraints = @(
    "fk_dev_delivery_session_delivery_config",
    "fk_dev_delivery_session_bag",
    "fk_dev_occupancy_clean_operation",
    "fk_dev_command_clean_operation",
    "fk_dev_command_fullness_detection",
    "fk_dev_command_baseline_measurement",
    "fk_dev_result_clean_operation",
    "fk_dev_result_fullness_sample",
    "fk_dev_result_baseline_measurement",
    "fk_dev_fault_recovery_audit_scope",
    "fk_fund_user_wallet_gate_entry",
    "fk_fund_payout_gate_current_pause",
    "fk_fund_payout_gate_event_not_enough",
    "fk_fund_payout_gate_event_transfer_merchant",
    "fk_fund_withdrawal_recipient_identity",
    "fk_fund_wechat_payment_binding",
    "fk_fund_wechat_transfer_withdrawal",
    "fk_fund_wallet_adjustment_audit_scope",
    "fk_fund_withdrawal_review_audit_scope",
    "fk_fund_payment_observation_inbox_scope",
    "fk_fund_payment_observation_attempt_scope",
    "fk_fund_transfer_observation_inbox_scope",
    "fk_fund_transfer_observation_attempt_scope",
    "fk_dev_edge_event_inbox_scope",
    "fk_ops_task_inbox_org_scope",
    "fk_ops_attempt_task_org_scope",
    "uq_fund_active_withdrawal_order",
    "uq_fund_wechat_transfer_merchant_id",
    "uq_fund_transfer_observation_gate_trigger",
    "uq_fund_wallet_entry_withdrawal_phase",
    "uq_fund_payout_entry_withdrawal_phase",
    "uq_ops_task_key",
    "uq_ops_attempt_task_no",
    "uq_ops_alert_active_aggregation",
    "uq_ops_reconciliation_issue_active"
)
$singleScopePermissions = @("organization-manager.manage")
$dualScopePermissions = @(
    "tenant.read",
    "tenant.manage",
    "organization.read",
    "organization.manage",
    "miniapp.manage",
    "staff.read",
    "staff.manage",
    "staff.bind",
    "permission.read",
    "permission.manage",
    "user.read",
    "user.freeze",
    "cleaner.manage",
    "device.read",
    "device.manage",
    "device.configuration.manage",
    "delivery.read",
    "review.execute",
    "delivery.correct",
    "wallet.read",
    "clean.read",
    "device.detection.execute",
    "device.recovery.execute",
    "wallet.adjust",
    "fund.read",
    "recharge.create",
    "withdrawal.configuration.manage",
    "withdrawal.read",
    "withdrawal.handle",
    "audit.read",
    "alert.read",
    "alert.acknowledge",
    "reconciliation.read",
    "reconciliation.handle",
    "statistics.read"
)

$containerName =
    "ecobin-f06-validate-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$passwordBytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)
$rootPassword = [Convert]::ToHexString($passwordBytes).ToLowerInvariant()
$containerStarted = $false

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]] $Arguments)
    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
    return $output
}

function Invoke-MySql {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $Database,
        [Parameter(Mandatory)][string] $Sql,
        [switch] $Raw
    )
    $args = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysql", "-uroot", "--batch", "--skip-column-names"
    )
    if ($Database.Length -gt 0) {
        $args += "--database=$Database"
    }
    $args += @("--execute", $Sql)
    $result = Invoke-Docker -Arguments $args
    if ($Raw) {
        return ($result -join "`n")
    }
    return $result
}

function Test-MySqlTransactionalProbe {
    param(
        [Parameter(Mandatory)][string] $Database,
        [Parameter(Mandatory)][string] $Sql
    )
    $transactionalSql = "START TRANSACTION;`n$Sql`nROLLBACK;"
    $args = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysql", "-uroot", "--database=$Database",
        "--execute", $transactionalSql
    )
    & docker @args *> $null
    return $LASTEXITCODE -eq 0
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string] $Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [Convert]::ToHexString(
            $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
        ).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-ExplicitForeignKeyCoverage {
    param([Parameter(Mandatory)][string] $Sql)

    $tables = @{}
    foreach ($statement in ($Sql -split ";")) {
        if (
            $statement -notmatch
                "(?is)\b(?:CREATE|ALTER)\s+TABLE\s+([a-z0-9_]+)"
        ) {
            continue
        }
        $tableName = $Matches[1].ToLowerInvariant()
        if (-not $tables.ContainsKey($tableName)) {
            $tables[$tableName] = [ordered]@{
                Indexes = @()
                ForeignKeys = @()
            }
        }
        foreach ($match in [regex]::Matches(
            $statement,
            "(?is)CONSTRAINT\s+([a-z0-9_]+)\s+FOREIGN\s+KEY\s*\(([^)]+)\)"
        )) {
            $columns = @($match.Groups[2].Value -split "," | ForEach-Object {
                ((($_ -replace "``", "").Trim() -split "\s+")[0]).
                    ToLowerInvariant()
            })
            $tables[$tableName].ForeignKeys += [pscustomobject]@{
                Name = $match.Groups[1].Value
                Columns = $columns
            }
        }
        foreach ($match in [regex]::Matches(
            $statement,
            "(?is)(?:PRIMARY\s+KEY|" +
                "CONSTRAINT\s+[a-z0-9_]+\s+UNIQUE|" +
                "(?:UNIQUE\s+)?INDEX\s+[a-z0-9_]+)\s*\(([^)]+)\)"
        )) {
            $columns = @($match.Groups[1].Value -split "," | ForEach-Object {
                ((($_ -replace "``", "").Trim() -split "\s+")[0]).
                    ToLowerInvariant()
            })
            $tables[$tableName].Indexes += ,$columns
        }
    }

    $foreignKeyCount = 0
    $uncovered = @()
    foreach ($tableName in $tables.Keys) {
        foreach ($foreignKey in $tables[$tableName].ForeignKeys) {
            $foreignKeyCount++
            $covered = $false
            foreach ($index in $tables[$tableName].Indexes) {
                if (
                    $index.Count -ge $foreignKey.Columns.Count -and
                    (
                        $index[0..($foreignKey.Columns.Count - 1)] -join ","
                    ) -eq ($foreignKey.Columns -join ",")
                ) {
                    $covered = $true
                    break
                }
            }
            if (-not $covered) {
                $uncovered += "$tableName.$($foreignKey.Name)"
            }
        }
    }
    if ($uncovered.Count -gt 0) {
        throw "foreign keys lack explicit DDL left-prefix indexes: " +
            ($uncovered -join ", ")
    }
    return $foreignKeyCount
}

function Assert-DirectOrganizationForeignKeys {
    param(
        [Parameter(Mandatory)][string] $Sql,
        [Parameter(Mandatory)][string[]] $TableNames
    )
    foreach ($tableName in $TableNames) {
        $tablePattern =
            "(?is)CREATE\s+TABLE\s+$([regex]::Escape($tableName))\s*" +
            "\((.*?)\)\s*ENGINE="
        $tableMatch = [regex]::Match($Sql, $tablePattern)
        if (-not $tableMatch.Success) {
            throw "organization-scoped table DDL not found: $tableName"
        }
        if (-not [regex]::IsMatch(
            $tableMatch.Groups[1].Value,
            "(?is)FOREIGN\s+KEY\s*\(\s*tenant_id\s*,\s*" +
                "organization_id\s*\)\s*REFERENCES\s+iam_organization\s*" +
                "\(\s*tenant_id\s*,\s*id\s*\)"
        )) {
            throw "table lacks direct organization FK: $tableName"
        }
    }
}

try {
    foreach ($file in $migrationFiles) {
        if (-not (Test-Path (Join-Path $migrationDir $file))) {
            throw "missing migration: $file"
        }
    }

    $allSql = ($migrationFiles | ForEach-Object {
        Get-Content -Raw (Join-Path $migrationDir $_)
    }) -join "`n"
    $v6Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[5])
    $v7Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[6])
    $v8Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[7])
    $v9Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[8])
    $v10Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[9])

    $v6TableCount = ([regex]::Matches(
        $v6Sql, "(?im)^CREATE TABLE\s+"
    )).Count
    $v7TableCount = ([regex]::Matches(
        $v7Sql, "(?im)^CREATE TABLE\s+"
    )).Count
    $totalTableCount = ([regex]::Matches(
        $allSql, "(?im)^CREATE TABLE\s+"
    )).Count
    if ($v6TableCount -ne 20) {
        throw "expected V6 to create 20 tables, found $v6TableCount"
    }
    if ($v7TableCount -ne 9) {
        throw "expected V7 to create 9 tables, found $v7TableCount"
    }
    if ($totalTableCount -ne 83) {
        throw "expected V1-V10 to create 83 tables, found $totalTableCount"
    }
    foreach ($forbidden in @(
        "(?i)IF\s+NOT\s+EXISTS",
        "(?i)FOREIGN_KEY_CHECKS\s*=\s*0"
    )) {
        if ([regex]::IsMatch($allSql, $forbidden)) {
            throw "forbidden migration content matched: $forbidden"
        }
    }
    foreach ($sql in @($v6Sql, $v7Sql)) {
        if ($sql -match "(?im)^\s*(?:INSERT|REPLACE|UPDATE|DELETE)\s+") {
            throw "V6/V7 must not contain DML"
        }
    }
    if (
        $v8Sql -match
            "(?im)^\s*(?:CREATE\s+TABLE|CREATE\s+TRIGGER|" +
            "INSERT|REPLACE|UPDATE|DELETE)\s+"
    ) {
        throw "V8 contains content other than deferred constraints/indexes"
    }
    if (
        ([regex]::Matches($v9Sql, "(?im)^CREATE\s+(?:DEFINER.+\r?\n)?TRIGGER")).
            Count -ne 2 -or
        $v9Sql -match
            "(?im)^\s*(?:CREATE\s+TABLE|INSERT|REPLACE|UPDATE|DELETE)\s+"
    ) {
        throw "V9 must contain exactly the two frozen trigger families"
    }
    if (
        $v10Sql -match
            "(?im)^\s*(?:CREATE|ALTER|DROP|REPLACE|UPDATE|DELETE)\s+" -or
        $v10Sql -notmatch
            "(?im)^\s*INSERT\s+INTO\s+iam_permission_definition\s*\("
    ) {
        throw "V10 must only insert permission reference data"
    }
    foreach ($forbiddenReference in @(
        "sys_tenant",
        "iam_organization\s*\(",
        "mchid\s*,",
        "appid\s*,",
        "password",
        "secret",
        "private_key",
        "api_v3"
    )) {
        if (
            $forbiddenReference -ne "secret" -and
            $v10Sql -match "(?i)$forbiddenReference"
        ) {
            throw "V10 contains non-catalog reference data: $forbiddenReference"
        }
    }

    $explicitForeignKeyIndexCount =
        Get-ExplicitForeignKeyCoverage -Sql $allSql
    Assert-DirectOrganizationForeignKeys `
        -Sql ($v6Sql + "`n" + $v7Sql) `
        -TableNames $organizationScopedTables

    $actualImageId = (
        Invoke-Docker -Arguments @(
            "image", "inspect", "--format", "{{.Id}}", $MySqlImage
        )
    ).Trim()
    if ($ExpectedImageId -and $actualImageId -ne $ExpectedImageId) {
        throw "MySQL image mismatch: expected $ExpectedImageId, got $actualImageId"
    }

    Invoke-Docker -Arguments @(
        "run", "--name", $containerName,
        "--env", "MYSQL_ROOT_PASSWORD=$rootPassword",
        "--env", "TZ=UTC",
        "--detach", $MySqlImage,
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci",
        "--default-time-zone=+00:00",
        "--sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
    ) | Out-Null
    $containerStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker exec -e "MYSQL_PWD=$rootPassword" $containerName `
            mysql -uroot --batch --skip-column-names `
            --execute "SELECT 1;" *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "MySQL did not become ready within 60 seconds"
    }

    $version = Invoke-MySql -Database "" -Sql "SELECT VERSION();"
    if (-not ($version -match "^8\.4\.")) {
        throw "expected MySQL 8.4.x, got $version"
    }

    Invoke-Docker -Arguments @(
        "cp", "$migrationDir/.", "${containerName}:/tmp/p0-migration"
    ) | Out-Null
    Invoke-MySql -Database "" -Sql @"
CREATE DATABASE ecobin_f06_a
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE ecobin_f06_b
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'ecobin_trigger_definer'@'%' ACCOUNT LOCK;
GRANT TRIGGER ON ecobin_f06_a.* TO 'ecobin_trigger_definer'@'%';
GRANT TRIGGER ON ecobin_f06_b.* TO 'ecobin_trigger_definer'@'%';
"@ | Out-Null

    foreach ($database in @("ecobin_f06_a", "ecobin_f06_b")) {
        foreach ($file in $migrationFiles) {
            Invoke-Docker -Arguments @(
                "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
                "sh", "-lc",
                "mysql -uroot --database=$database < /tmp/p0-migration/$file"
            ) | Out-Null
        }
    }

    Invoke-MySql -Database "" -Sql @"
GRANT SELECT (
    activated_at, appid, tenant_id, organization_id
) ON ecobin_f06_a.iam_organization_miniapp
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    activated_at, appid, tenant_id, organization_id
) ON ecobin_f06_b.iam_organization_miniapp
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    tenant_id, organization_id, organization_miniapp_id, openid,
    registered_at, registered_via_deployment_id
) ON ecobin_f06_a.iam_organization_user
    TO 'ecobin_trigger_definer'@'%';
GRANT SELECT (
    tenant_id, organization_id, organization_miniapp_id, openid,
    registered_at, registered_via_deployment_id
) ON ecobin_f06_b.iam_organization_user
    TO 'ecobin_trigger_definer'@'%';
"@ | Out-Null

    $shapeSqlTemplate = @"
SELECT CONCAT_WS(
    '|',
    (SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = '{0}' AND table_type = 'BASE TABLE'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = '{0}' AND constraint_type = 'FOREIGN KEY'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = '{0}' AND constraint_type = 'UNIQUE'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = '{0}' AND constraint_type = 'CHECK'),
    (SELECT COUNT(DISTINCT CONCAT(table_name, ':', index_name))
        FROM information_schema.statistics
        WHERE table_schema = '{0}' AND non_unique = 1),
    (SELECT COUNT(*) FROM information_schema.triggers
        WHERE trigger_schema = '{0}')
);
"@
    $shapeA = Invoke-MySql -Database "ecobin_f06_a" `
        -Sql ($shapeSqlTemplate -f "ecobin_f06_a")
    $shapeB = Invoke-MySql -Database "ecobin_f06_b" `
        -Sql ($shapeSqlTemplate -f "ecobin_f06_b")
    if ($shapeA -ne $shapeB) {
        throw "two empty-schema installations have different object counts"
    }
    $shapeParts = $shapeA -split "\|"
    if ([int]$shapeParts[0] -ne 83 -or [int]$shapeParts[5] -ne 2) {
        throw "installed object counts differ from the F-06 manifest: $shapeA"
    }

    $actualFundsTables = @(Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'ecobin_f06_a'
  AND table_type = 'BASE TABLE'
  AND table_name LIKE 'fund\_%'
ORDER BY table_name;
"@)
    $actualOperationsTables = @(Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'ecobin_f06_a'
  AND table_type = 'BASE TABLE'
  AND table_name LIKE 'ops\_%'
ORDER BY table_name;
"@)
    if (
        @(Compare-Object $expectedFundsTables $actualFundsTables).Count -ne 0
    ) {
        throw "installed funds table list differs from the 20-table manifest"
    }
    if (
        @(Compare-Object $expectedOperationsTables $actualOperationsTables).
            Count -ne 0
    ) {
        throw "installed operations table list differs from the 9-table manifest"
    }

    $actualConstraints = @(Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT constraint_name
FROM information_schema.table_constraints
WHERE constraint_schema = 'ecobin_f06_a'
ORDER BY constraint_name;
"@)
    foreach ($constraint in $requiredConstraints) {
        if ($constraint -notin $actualConstraints) {
            throw "required constraint missing: $constraint"
        }
    }
    if ("uq_fund_merchant_profile_transfer_ref" -in $actualConstraints) {
        throw "mutable merchant configuration remains in a parent candidate key"
    }
    $transferMerchantForeignKey = Invoke-MySql `
        -Database "ecobin_f06_a" -Sql @"
SELECT GROUP_CONCAT(
    CONCAT(column_name, ':', referenced_column_name)
    ORDER BY ordinal_position
)
FROM information_schema.key_column_usage
WHERE constraint_schema = 'ecobin_f06_a'
  AND table_name = 'fund_wechat_transfer'
  AND constraint_name = 'fk_fund_wechat_transfer_merchant';
"@
    if (
        $transferMerchantForeignKey -ne
            "merchant_profile_id:id,mchid_snapshot:mchid"
    ) {
        throw "transfer merchant FK includes mutable configuration: " +
            $transferMerchantForeignKey
    }

    $dumpArgsA = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysqldump", "-uroot", "--no-data", "--skip-comments",
        "--skip-add-drop-table", "--compact", "--skip-set-charset",
        "ecobin_f06_a"
    )
    $dumpArgsB = $dumpArgsA.Clone()
    $dumpArgsB[-1] = "ecobin_f06_b"
    $dumpA = (Invoke-Docker -Arguments $dumpArgsA) -join "`n"
    $dumpB = (Invoke-Docker -Arguments $dumpArgsB) -join "`n"
    if ($dumpA -ne $dumpB) {
        throw "two empty-schema installations have different structures"
    }

    $permissionCount = [int](Invoke-MySql -Database "ecobin_f06_a" `
        -Sql "SELECT COUNT(*) FROM iam_permission_definition;")
    if ($permissionCount -ne 71) {
        throw "expected 71 permission definitions, found $permissionCount"
    }
    foreach ($permission in $singleScopePermissions) {
        $scopes = Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT GROUP_CONCAT(scope_kind ORDER BY scope_kind)
FROM iam_permission_definition
WHERE permission_code = '$permission';
"@
        if ($scopes -ne "TENANT") {
            throw "single-scope permission differs: $permission=$scopes"
        }
    }
    foreach ($permission in $dualScopePermissions) {
        $scopes = Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT GROUP_CONCAT(scope_kind ORDER BY scope_kind)
FROM iam_permission_definition
WHERE permission_code = '$permission';
"@
        if ($scopes -ne "ORGANIZATION,TENANT") {
            throw "dual-scope permission differs: $permission=$scopes"
        }
    }

    Invoke-MySql -Database "ecobin_f06_a" -Sql @"
INSERT INTO iam_tenant (
    id, tenant_code, enterprise_name, status, lock_version,
    created_at, updated_at
) VALUES
    (1, 'tenant_a', 'Tenant A', 'ENABLED', 0,
        '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000');
INSERT INTO iam_organization (
    id, tenant_id, organization_code, organization_name, status,
    lock_version, created_at, updated_at
) VALUES
    (10, 1, 'org_a', 'Organization A', 'ENABLED', 0,
        '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'),
    (11, 1, 'org_b', 'Organization B', 'ENABLED', 0,
        '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000');
INSERT INTO iam_platform_admin (
    id, platform_admin_uid, login_name, password_hash, display_name,
    enabled, failed_login_count, auth_version, password_changed_at,
    lock_version, created_at, updated_at
) VALUES (
    5, '05050505-0505-4505-8505-050505050505',
    'f06admin', 'test-hash', 'F06 Admin', 1, 0, 0,
    '2026-07-25 00:00:00.000', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO iam_organization_miniapp (
    id, tenant_id, organization_id, appid, display_name, login_enabled,
    secret_ref, activated_at, lock_version, configured_at, created_at,
    updated_at
) VALUES (
    20, 1, 10, 'wx-f06-before', 'F06 Miniapp', 0,
    'secret://f06/test', NULL, 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000',
    '2026-07-25 00:00:00.000'
);
UPDATE iam_organization_miniapp
SET appid = 'wx-f06-active', activated_at = '2026-07-25 00:00:01.000',
    login_enabled = 1, lock_version = 1,
    updated_at = '2026-07-25 00:00:01.000'
WHERE id = 20;
INSERT INTO iam_staff_account (
    id, tenant_id, staff_account_uid, account_kind, login_name,
    password_hash, display_name, enabled, failed_login_count, auth_version,
    password_changed_at, lock_version, created_at, updated_at
) VALUES (
    30, 1, '30303030-3030-4030-8030-303030303030', 'STAFF',
    'f06staff', 'test-hash', 'F06 Staff', 1, 0, 0,
    '2026-07-25 00:00:00.000', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO iam_organization_user (
    id, organization_user_uid, tenant_id, organization_id,
    organization_miniapp_id, openid, status, auth_version, lock_version,
    registered_at, created_at, updated_at
) VALUES (
    40, '40404040-4040-4040-8040-404040404040',
    1, 10, 20, 'f06-openid', 'ACTIVE', 0, 0,
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:01.000',
    '2026-07-25 00:00:01.000'
);
INSERT INTO fund_wechat_merchant_profile (
    id, merchant_profile_uid, mchid, merchant_kind, status, scene_id,
    report_type, report_content, transfer_page_style, lock_version,
    created_at, updated_at
) VALUES
    (
        50, '50505050-5050-4050-8050-505050505050', '190000F06',
        'ORDINARY_MERCHANT', 'ENABLED', 'SCENE-F06',
        'RECYCLED_GOODS_NAME', 'MIXED_RECYCLABLES', 'STANDARD', 0,
        '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
    ),
    (
        52, '52525252-5252-4252-8252-525252525252', '190000F06B',
        'ORDINARY_MERCHANT', 'ENABLED', 'SCENE-F06-B',
        'RECYCLED_GOODS_NAME', 'MIXED_RECYCLABLES', 'STANDARD', 0,
        '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
    );
INSERT INTO fund_miniapp_merchant_binding (
    id, binding_uid, tenant_id, organization_id, organization_miniapp_id,
    appid, miniapp_lock_version_snapshot, merchant_profile_id, status,
    verified_by_platform_admin_id, verified_at, lock_version,
    created_at, updated_at
) VALUES (
    51, '51515151-5151-4151-8151-515151515151',
    1, 10, 20, 'wx-f06-active', 1, 50, 'VERIFIED', 5,
    '2026-07-25 00:00:02.000', 0,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
INSERT INTO fund_payout_gate (
    merchant_profile_id, gate_state, lock_version, created_at, updated_at
) VALUES (
    50, 'OPEN', 0,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
INSERT INTO fund_organization_withdraw_config (
    id, tenant_id, organization_id, version_no, content_sha256,
    hard_limit_cent, manual_min_cent, manual_max_cent,
    manual_review_free_threshold_cent, publication_source,
    published_by_staff_account_id, published_at, created_at
) VALUES (
    60, 1, 10, 1, UNHEX(REPEAT('11', 32)),
    20000, 10, 20000, 0, 'STAFF', 30,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
INSERT INTO fund_organization_withdraw_config_head (
    organization_id, tenant_id, current_config_id, current_version_no,
    lock_version, switched_at, updated_at
) VALUES (
    10, 1, 60, 1, 0,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
INSERT INTO fund_organization_wallet_entry_counter (
    organization_id, tenant_id, last_visibility_sequence_no,
    lock_version, updated_at
) VALUES (10, 1, 1, 1, '2026-07-25 00:00:03.000');
INSERT INTO fund_user_wallet (
    id, wallet_uid, tenant_id, organization_id, organization_user_id,
    available_balance_cent, frozen_withdrawal_cent, last_entry_sequence_no,
    delivery_gate_state, lock_version, created_at, updated_at
) VALUES (
    70, '70707070-7070-4070-8070-707070707070',
    1, 10, 40, 100, 0, 1, 'OPEN', 1,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:03.000'
);
INSERT INTO fund_organization_payout_account (
    id, account_uid, tenant_id, organization_id,
    available_payout_cent, frozen_withdrawal_cent, lock_version,
    created_at, updated_at
) VALUES (
    71, '71717171-7171-4171-8171-717171717171',
    1, 10, 10000, 0, 0,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
INSERT INTO ops_audit_log (
    id, audit_uid, request_uid, operation_uid, scope_kind, tenant_id,
    organization_id, actor_kind, staff_account_id, actor_display_snapshot,
    action_code, target_type, target_stable_key, entry_channel, result,
    occurred_at, created_at
) VALUES
    (
        80, '80808080-8080-4080-8080-808080808080',
        '81818181-8181-4181-8181-818181818181',
        '82828282-8282-4282-8282-828282828282',
        'ORGANIZATION', 1, 10, 'STAFF_ACCOUNT', 30, 'F06 Staff',
        'wallet.adjust', 'USER_WALLET',
        '70707070-7070-4070-8070-707070707070',
        'WEB', 'SUCCEEDED',
        '2026-07-25 00:00:03.000', '2026-07-25 00:00:03.000'
    ),
    (
        83, '83838383-8383-4383-8383-838383838383',
        '84848484-8484-4484-8484-848484848484',
        '85858585-8585-4585-8585-858585858585',
        'ORGANIZATION', 1, 10, 'STAFF_ACCOUNT', 30, 'F06 Staff',
        'wallet.adjust', 'USER_WALLET',
        '70707070-7070-4070-8070-707070707070',
        'WEB', 'SUCCEEDED',
        '2026-07-25 00:00:04.000', '2026-07-25 00:00:04.000'
    );
INSERT INTO fund_wallet_adjustment (
    id, adjustment_uid, tenant_id, organization_id, wallet_id,
    amount_delta_cent, available_before_cent, available_after_cent,
    actor_kind, staff_account_id, reason, succeeded_audit_id,
    occurred_at, created_at
) VALUES
    (
        81, '81818181-8181-4181-8181-111111111111',
        1, 10, 70, 100, 0, 100, 'STAFF_ACCOUNT', 30,
        'F06 verification', 80,
        '2026-07-25 00:00:03.000', '2026-07-25 00:00:03.000'
    ),
    (
        84, '84848484-8484-4484-8484-111111111111',
        1, 10, 70, 1, 100, 101, 'STAFF_ACCOUNT', 30,
        'F06 negative source', 83,
        '2026-07-25 00:00:04.000', '2026-07-25 00:00:04.000'
    );
INSERT INTO fund_user_wallet_entry (
    id, entry_uid, tenant_id, organization_id, wallet_id,
    organization_user_id, entry_sequence_no, visibility_sequence_no,
    event_type, available_delta_cent, available_before_cent,
    available_after_cent, frozen_delta_cent, frozen_before_cent,
    frozen_after_cent, adjustment_id, occurred_at, created_at
) VALUES (
    82, '82828282-8282-4282-8282-111111111111',
    1, 10, 70, 40, 1, 1, 'MANUAL_ADJUSTMENT',
    100, 0, 100, 0, 0, 0, 81,
    '2026-07-25 00:00:03.000', '2026-07-25 00:00:03.000'
);
INSERT INTO fund_recharge_order (
    id, recharge_order_no, tenant_id, organization_id,
    created_by_staff_account_id, gross_amount_cent, fee_rate_ppm,
    fee_rounding_mode, fee_amount_cent, net_amount_cent, business_state,
    expires_at, lock_version, created_at, updated_at
) VALUES (
    90, 'F06RECHARGE0001', 1, 10, 30, 100, 6000,
    'CEILING_TO_CENT', 1, 99, 'PENDING_PAYMENT',
    '2026-07-25 00:30:05.000', 0,
    '2026-07-25 00:00:05.000', '2026-07-25 00:00:05.000'
);
INSERT INTO fund_wechat_payment (
    id, payment_uid, tenant_id, organization_id, recharge_order_id,
    merchant_profile_id, miniapp_merchant_binding_id,
    organization_miniapp_id, mchid_snapshot, appid_snapshot, out_trade_no,
    request_amount_cent, currency, description, time_expire,
    notify_url_sha256, request_sha256, lock_version, created_at, updated_at
) VALUES (
    91, '91919191-9191-4191-8191-919191919191',
    1, 10, 90, 50, 51, 20, '190000F06', 'wx-f06-active',
    'F06TRADE00000001', 100, 'CNY', 'F06 recharge',
    '2026-07-25 00:30:05.000', UNHEX(REPEAT('21', 32)),
    UNHEX(REPEAT('22', 32)), 0,
    '2026-07-25 00:00:05.000', '2026-07-25 00:00:05.000'
);
INSERT INTO fund_withdrawal_order (
    id, withdrawal_order_no, tenant_id, organization_id,
    organization_user_id, wallet_id, organization_payout_account_id,
    withdraw_config_id, withdraw_config_version_no,
    hard_limit_cent_snapshot, manual_min_cent_snapshot,
    manual_max_cent_snapshot, manual_review_free_threshold_cent_snapshot,
    amount_cent, miniapp_merchant_binding_id, organization_miniapp_id,
    merchant_profile_id, mchid_snapshot, appid_snapshot, openid_snapshot,
    business_state, negative_balance_pause, post_boundary_risk,
    lock_version, created_at, updated_at
) VALUES (
    100, 'F06WITHDRAW0001', 1, 10, 40, 70, 71, 60, 1,
    20000, 10, 20000, 0, 50, 51, 20, 50,
    '190000F06', 'wx-f06-active', 'f06-openid',
    'PENDING_REVIEW', 0, 0, 0,
    '2026-07-25 00:00:06.000', '2026-07-25 00:00:06.000'
);
INSERT INTO fund_wechat_transfer (
    id, transfer_uid, tenant_id, organization_id, withdrawal_order_id,
    merchant_profile_id, miniapp_merchant_binding_id,
    organization_miniapp_id, out_bill_no, amount_cent, mchid_snapshot,
    appid_snapshot, openid_snapshot, scene_id_snapshot,
    report_type_snapshot, report_content_snapshot, transfer_remark,
    transfer_page_style_snapshot, notify_url_sha256, request_sha256,
    terminal_classification, lock_version, created_at, updated_at
) VALUES (
    110, '11111111-1111-4111-8111-111111111110',
    1, 10, 100, 50, 51, 20, 'F06BILL000000001', 50, '190000F06',
    'wx-f06-active', 'f06-openid', 'SCENE-F06',
    'RECYCLED_GOODS_NAME', 'MIXED_RECYCLABLES', 'F06 transfer',
    'STANDARD', UNHEX(REPEAT('23', 32)), UNHEX(REPEAT('24', 32)),
    'NON_TERMINAL', 0,
    '2026-07-25 00:00:06.000', '2026-07-25 00:00:06.000'
);
INSERT INTO fund_active_withdrawal (
    wallet_id, tenant_id, organization_id, withdrawal_order_id, acquired_at
) VALUES (70, 1, 10, 100, '2026-07-25 00:00:06.000');
INSERT INTO ops_inbox_message (
    id, inbox_uid, scope_kind, tenant_id, organization_id,
    source_namespace, source_principal_key, external_message_id,
    message_kind, normalized_schema_version, raw_transport_body,
    raw_transport_sha256, normalized_payload, normalized_content_sha256,
    authentication_method, authentication_principal_ref, processing_state,
    first_received_at, last_received_at, delivery_count, lock_version,
    created_at, updated_at
) VALUES (
    200, '20202020-2020-4020-8020-202020202020',
    'ORGANIZATION', 1, 10, 'wechat.payment', '190000F06', 'NOTICE-F06-1',
    'WECHAT_PAYMENT_NOTICE', 1, X'7B7D', UNHEX(REPEAT('31', 32)),
    JSON_OBJECT('outTradeNo', 'F06TRADE00000001'), UNHEX(REPEAT('32', 32)),
    'WECHAT_API_V3', 'certificate:f06', 'RECEIVED',
    '2026-07-25 00:00:07.000', '2026-07-25 00:00:07.000',
    1, 0, '2026-07-25 00:00:07.000', '2026-07-25 00:00:07.000'
);
INSERT INTO ops_reliable_task (
    id, task_uid, scope_kind, tenant_id, organization_id,
    task_category, task_type, execution_lane, task_key, target_type,
    target_stable_key, source_inbox_id, payload_schema_version,
    redacted_execution_snapshot, payload_sha256, priority,
    retry_policy_version, max_auto_attempts, state, next_run_at,
    attempt_sequence, consecutive_failure_count, wake_version,
    handled_wake_version, lock_version, created_at, updated_at
) VALUES
    (
        201, '21212121-2121-4121-8121-212121212121',
        'ORGANIZATION', 1, 10, 'INBOX_PROCESSING', 'PROCESS_INBOX',
        'FUNDS', 'PROCESS_INBOX:20202020-2020-4020-8020-202020202020',
        'INBOX', '20202020-2020-4020-8020-202020202020', 200, 1,
        JSON_OBJECT('inboxUid', '20202020-2020-4020-8020-202020202020'),
        UNHEX(REPEAT('33', 32)), 100, 1, 10, 'PENDING',
        '2026-07-25 00:00:08.000', 1, 0, 0, 0, 0,
        '2026-07-25 00:00:07.000', '2026-07-25 00:00:07.000'
    ),
    (
        203, '23232323-2323-4323-8323-232323232323',
        'PLATFORM', NULL, NULL, 'RECONCILIATION',
        'DAILY_RECONCILIATION', 'FUNDS',
        'DAILY_RECONCILIATION:190000F06:2026-07-25',
        'MERCHANT_PROFILE', '190000F06', NULL, 1,
        JSON_OBJECT('businessDate', '2026-07-25'),
        UNHEX(REPEAT('34', 32)), 100, 1, 10, 'PENDING',
        '2026-07-25 00:00:08.000', 0, 0, 0, 0, 0,
        '2026-07-25 00:00:07.000', '2026-07-25 00:00:07.000'
    );
INSERT INTO ops_task_attempt (
    id, attempt_uid, task_id, scope_kind, tenant_id, organization_id,
    attempt_no, lease_token, claimed_wake_version, worker_id,
    claimed_at, lease_until, action_kind, created_at
) VALUES
    (
        202, '22222222-2222-4222-8222-222222222222',
        201, 'ORGANIZATION', 1, 10, 1,
        '24242424-2424-4424-8424-242424242424', 0, 'f06-worker',
        '2026-07-25 00:00:08.000', '2026-07-25 00:01:08.000',
        'PROCESS', '2026-07-25 00:00:08.000'
    ),
    (
        206, '26262626-2626-4626-8626-262626262626',
        201, 'ORGANIZATION', 1, 10, 2,
        '27272727-2727-4727-8727-272727272727', 0, 'f06-worker',
        '2026-07-25 00:00:09.000', '2026-07-25 00:01:09.000',
        'SUBMIT', '2026-07-25 00:00:09.000'
    );
INSERT INTO fund_wechat_transfer_observation (
    id, observation_uid, tenant_id, organization_id, transfer_id,
    observation_type, evidence_source_kind, source_scope_kind,
    source_task_attempt_id, api_error_code, out_bill_no, content_sha256,
    observed_at, created_at
) VALUES
    (
        120, '12121212-1212-4212-8212-121212121120',
        1, 10, 110, 'SUBMIT_RESPONSE', 'TASK_ATTEMPT', 'ORGANIZATION',
        202, 'NOT_ENOUGH', 'F06BILL000000001', UNHEX(REPEAT('25', 32)),
        '2026-07-25 00:00:09.000', '2026-07-25 00:00:09.000'
    ),
    (
        121, '12121212-1212-4212-8212-121212121121',
        1, 10, 110, 'SUBMIT_RESPONSE', 'TASK_ATTEMPT', 'ORGANIZATION',
        206, 'SYSTEM_ERROR', 'F06BILL000000001', UNHEX(REPEAT('26', 32)),
        '2026-07-25 00:00:09.000', '2026-07-25 00:00:09.000'
    );
INSERT INTO ops_reconciliation_run (
    id, run_uid, scope_kind, run_type, merchant_profile_id,
    channel_business_date, snapshot_cutoff_at, coordinator_task_id,
    state, checked_recharge_count, checked_withdrawal_count,
    deterministic_repair_count, lock_version, created_at, updated_at
) VALUES (
    204, '24242424-2424-4424-8424-111111111111',
    'PLATFORM', 'WECHAT_DAILY_TRADE', 50, '2026-07-25',
    '2026-07-25 00:00:09.000', 203, 'CREATED', 0, 0, 0, 0,
    '2026-07-25 00:00:09.000', '2026-07-25 00:00:09.000'
);
"@ | Out-Null

    $negativeCases = @(
        @{
            Name = "withdraw config rejects non-zero review-free threshold"
            Sql = @"
INSERT INTO fund_organization_withdraw_config (
    tenant_id, organization_id, version_no, content_sha256,
    hard_limit_cent, manual_min_cent, manual_max_cent,
    manual_review_free_threshold_cent, publication_source,
    published_at, created_at
) VALUES (
    1, 10, 2, UNHEX(REPEAT('41', 32)),
    20000, 10, 20000, 1, 'SYSTEM',
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "wallet cannot cross organization-user scope"
            Sql = @"
INSERT INTO fund_user_wallet (
    wallet_uid, tenant_id, organization_id, organization_user_id,
    available_balance_cent, frozen_withdrawal_cent, last_entry_sequence_no,
    delivery_gate_state, lock_version, created_at, updated_at
) VALUES (
    '41414141-4141-4141-8141-414141414141',
    1, 11, 40, 0, 0, 0, 'OPEN', 0,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "wallet rejects a negative frozen balance"
            Sql = @"
UPDATE fund_user_wallet
SET frozen_withdrawal_cent = -1,
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 70;
"@
        },
        @{
            Name = "recharge enforces the frozen fee formula"
            Sql = @"
INSERT INTO fund_recharge_order (
    recharge_order_no, tenant_id, organization_id,
    created_by_staff_account_id, gross_amount_cent, fee_rate_ppm,
    fee_rounding_mode, fee_amount_cent, net_amount_cent, business_state,
    expires_at, lock_version, created_at, updated_at
) VALUES (
    'F06RECHARGEBAD1', 1, 10, 30, 100, 6000,
    'CEILING_TO_CENT', 0, 100, 'PENDING_PAYMENT',
    '2026-07-25 00:31:00.000', 0,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "wallet entry rejects broken before-delta-after algebra"
            Sql = @"
INSERT INTO fund_user_wallet_entry (
    entry_uid, tenant_id, organization_id, wallet_id,
    organization_user_id, entry_sequence_no, visibility_sequence_no,
    event_type, available_delta_cent, available_before_cent,
    available_after_cent, frozen_delta_cent, frozen_before_cent,
    frozen_after_cent, adjustment_id, occurred_at, created_at
) VALUES (
    '42424242-4242-4242-8242-424242424242',
    1, 10, 70, 40, 2, 2, 'MANUAL_ADJUSTMENT',
    1, 100, 999, 0, 0, 0, 84,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "payment observation source must be an XOR"
            Sql = @"
INSERT INTO fund_wechat_payment_observation (
    observation_uid, tenant_id, organization_id, payment_id,
    observation_type, evidence_source_kind, source_scope_kind,
    source_inbox_id, source_task_attempt_id, content_sha256,
    observed_at, created_at
) VALUES (
    '43434343-4343-4343-8343-434343434343',
    1, 10, 91, 'CALLBACK', 'INBOX', 'ORGANIZATION',
    200, 202, UNHEX(REPEAT('43', 32)),
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "pending withdrawal cannot be marked long-unsettled"
            Sql = @"
UPDATE fund_withdrawal_order
SET long_unsettled_at = '2026-07-25 00:31:00.000',
    updated_at = '2026-07-25 00:31:00.000'
WHERE id = 100;
"@
        },
        @{
            Name = "payout pause evidence must belong to the same merchant"
            Sql = @"
INSERT INTO fund_payout_gate_event (
    event_uid, merchant_profile_id, event_type,
    triggering_transfer_observation_id, triggering_transfer_id,
    occurred_at, created_at
) VALUES (
    '53535353-5353-4353-8353-535353535353',
    52, 'PAUSED', 120, 110,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "payout pause evidence must classify as NOT_ENOUGH"
            Sql = @"
INSERT INTO fund_payout_gate_event (
    event_uid, merchant_profile_id, event_type,
    triggering_transfer_observation_id, triggering_transfer_id,
    occurred_at, created_at
) VALUES (
    '54545454-5454-4454-8454-545454545454',
    50, 'PAUSED', 121, 110,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "payout gate cannot pause without an immutable event"
            Sql = @"
UPDATE fund_payout_gate
SET gate_state = 'PAUSED_NOT_ENOUGH',
    paused_at = '2026-07-25 00:01:00.000',
    updated_at = '2026-07-25 00:01:00.000'
WHERE merchant_profile_id = 50;
"@
        },
        @{
            Name = "trusted inbox rejects unresolved scope"
            Sql = @"
INSERT INTO ops_inbox_message (
    inbox_uid, scope_kind, source_namespace, source_principal_key,
    external_message_id, message_kind, normalized_schema_version,
    raw_transport_body, raw_transport_sha256, normalized_payload,
    normalized_content_sha256, authentication_method,
    authentication_principal_ref, processing_state,
    first_received_at, last_received_at, delivery_count, lock_version,
    created_at, updated_at
) VALUES (
    '44444444-4444-4444-8444-444444444444',
    'UNRESOLVED', 'wechat.payment', '190000F06', 'NOTICE-BAD',
    'WECHAT_PAYMENT_NOTICE', 1, X'7B7D', UNHEX(REPEAT('44', 32)),
    JSON_OBJECT(), UNHEX(REPEAT('45', 32)), 'WECHAT_API_V3',
    'certificate:f06', 'RECEIVED',
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000',
    1, 0, '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "terminal task cannot retain a lease"
            Sql = @"
INSERT INTO ops_reliable_task (
    task_uid, scope_kind, task_category, task_type, execution_lane,
    task_key, target_type, target_stable_key, payload_schema_version,
    redacted_execution_snapshot, payload_sha256, priority,
    retry_policy_version, max_auto_attempts, state,
    lease_token, lease_worker, lease_until, attempt_sequence,
    consecutive_failure_count, wake_version, handled_wake_version,
    completed_at, lock_version, created_at, updated_at
) VALUES (
    '45454545-4545-4545-8545-454545454545',
    'PLATFORM', 'TIMER', 'BAD_TERMINAL', 'FUNDS',
    'BAD_TERMINAL:45454545', 'TEST', 'bad', 1,
    JSON_OBJECT(), UNHEX(REPEAT('46', 32)), 100, 1, 1, 'DONE',
    '46464646-4646-4646-8646-464646464646', 'bad-worker',
    '2026-07-25 00:02:00.000', 0, 0, 0, 0,
    '2026-07-25 00:01:00.000', 0,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "attempt result fields cannot be partially recorded"
            Sql = @"
INSERT INTO ops_task_attempt (
    attempt_uid, task_id, scope_kind, tenant_id, organization_id,
    attempt_no, lease_token, claimed_wake_version, worker_id,
    claimed_at, lease_until, result_recorded_at, action_kind, created_at
) VALUES (
    '47474747-4747-4747-8747-474747474747',
    201, 'ORGANIZATION', 1, 10, 2,
    '48484848-4848-4848-8848-484848484848', 0, 'f06-worker',
    '2026-07-25 00:01:00.000', '2026-07-25 00:02:00.000',
    '2026-07-25 00:01:01.000', 'PROCESS',
    '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "audit actor must be strongly typed"
            Sql = @"
INSERT INTO ops_audit_log (
    audit_uid, request_uid, scope_kind, tenant_id, organization_id,
    actor_kind, platform_admin_id, staff_account_id, action_code,
    target_type, target_stable_key, entry_channel, result,
    occurred_at, created_at
) VALUES (
    '49494949-4949-4949-8949-494949494949',
    '4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a',
    'ORGANIZATION', 1, 10, 'STAFF_ACCOUNT', 5, 30,
    'invalid.actor', 'TEST', 'bad', 'WEB', 'DENIED',
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "quarantine acknowledgement requires audit evidence"
            Sql = @"
INSERT INTO ops_message_quarantine (
    quarantine_uid, dedupe_key, scope_kind, source_namespace,
    reason_code, raw_transport_sha256, status,
    first_seen_at, last_seen_at, discovery_count, lock_version,
    created_at, updated_at
) VALUES (
    '4b4b4b4b-4b4b-4b4b-8b4b-4b4b4b4b4b4b',
    UNHEX(REPEAT('4b', 32)), 'UNRESOLVED', 'onenet',
    'UNRESOLVED_SCOPE', UNHEX(REPEAT('4c', 32)), 'ACKNOWLEDGED',
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000',
    1, 0, '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "resolved alert requires a source-verified time"
            Sql = @"
INSERT INTO ops_alert (
    alert_uid, scope_kind, tenant_id, organization_id, alert_code,
    category, current_severity, highest_severity, source_kind,
    source_type, source_key, aggregation_key, status,
    first_seen_at, last_seen_at, discovery_count,
    safe_display_parameters, lock_version, created_at, updated_at
) VALUES (
    '4d4d4d4d-4d4d-4d4d-8d4d-4d4d4d4d4d4d',
    'ORGANIZATION', 1, 10, 'F06_TEST', 'TECHNICAL',
    'WARNING', 'WARNING', 'TECHNICAL_CONDITION', 'TEST', 'f06',
    UNHEX(REPEAT('4d', 32)), 'RESOLVED',
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000',
    1, JSON_OBJECT(), 0,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "resolved reconciliation issue requires system verification"
            Sql = @"
INSERT INTO ops_reconciliation_issue (
    issue_uid, scope_kind, tenant_id, organization_id, issue_code,
    severity, subject_type, subject_stable_key, dedupe_key, state,
    first_seen_run_id, latest_seen_run_id, first_seen_at, last_seen_at,
    discovery_count, initial_evidence_sha256, redacted_evidence_summary,
    lock_version, created_at, updated_at
) VALUES (
    '4e4e4e4e-4e4e-4e4e-8e4e-4e4e4e4e4e4e',
    'ORGANIZATION', 1, 10, 'F06_TEST', 'WARNING',
    'RECHARGE_ORDER', 'F06RECHARGE0001', UNHEX(REPEAT('4e', 32)),
    'RESOLVED', 204, 204,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000',
    1, UNHEX(REPEAT('4f', 32)), 'F06 redacted evidence', 0,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
"@
        },
        @{
            Name = "activated miniapp AppID is immutable"
            Sql = @"
UPDATE iam_organization_miniapp
SET appid = 'wx-f06-mutated',
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 20;
"@
        },
        @{
            Name = "activated miniapp activation time is immutable"
            Sql = @"
UPDATE iam_organization_miniapp
SET activated_at = '2026-07-25 00:01:01.000',
    updated_at = '2026-07-25 00:01:01.000'
WHERE id = 20;
"@
        },
        @{
            Name = "organization-user registration time is immutable"
            Sql = @"
UPDATE iam_organization_user
SET registered_at = '2026-07-25 00:00:02.000',
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 40;
"@
        }
    )
    $semanticRegressionFailures = @()
    foreach ($case in $negativeCases) {
        if (
            Test-MySqlTransactionalProbe -Database "ecobin_f06_a" `
                -Sql $case.Sql
        ) {
            $semanticRegressionFailures +=
                "negative test unexpectedly succeeded: $($case.Name)"
        }
    }
    $merchantConfigurationUpdateProbe = @"
UPDATE fund_wechat_merchant_profile
SET scene_id = 'SCENE-F06-PROBE',
    non_secret_config_ref = 'config://f06/merchant/probe',
    lock_version = 1,
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 50;
"@
    if (
        -not (
            Test-MySqlTransactionalProbe -Database "ecobin_f06_a" `
                -Sql $merchantConfigurationUpdateProbe
        )
    ) {
        $semanticRegressionFailures +=
            "positive test unexpectedly failed: merchant configuration " +
            "remains mutable after a transfer snapshot exists"
    }
    $longUnsettledTerminalRetentionProbe = @"
UPDATE fund_withdrawal_order
SET business_state = 'SUCCEEDED',
    channel_boundary_at = '2026-07-25 00:00:06.000',
    long_unsettled_at = '2026-07-25 00:30:06.000',
    channel_terminal_at = '2026-07-25 00:31:00.000',
    ended_at = '2026-07-25 00:31:00.000',
    updated_at = '2026-07-25 00:31:00.000'
WHERE id = 100;
"@
    if (
        -not (
            Test-MySqlTransactionalProbe -Database "ecobin_f06_a" `
                -Sql $longUnsettledTerminalRetentionProbe
        )
    ) {
        $semanticRegressionFailures +=
            "positive test unexpectedly failed: a terminal withdrawal " +
            "retains its prior long-unsettled marker"
    }
    if ($semanticRegressionFailures.Count -gt 0) {
        throw ($semanticRegressionFailures -join [Environment]::NewLine)
    }

    Invoke-MySql -Database "ecobin_f06_a" -Sql @"
UPDATE iam_organization_user
SET phone_e164 = '+8613800000000',
    phone_bound_at = '2026-07-25 00:01:00.000',
    nickname = 'Allowed mutable profile',
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 40;
UPDATE fund_wechat_payment
SET channel_state = 'FUTURE_WECHAT_STATE',
    channel_updated_at = '2026-07-25 00:01:00.000',
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 91;
UPDATE fund_wechat_merchant_profile
SET scene_id = 'SCENE-F06-V2',
    non_secret_config_ref = 'config://f06/merchant/v2',
    lock_version = 1,
    updated_at = '2026-07-25 00:01:00.000'
WHERE id = 50;
INSERT INTO fund_payout_gate_event (
    id, event_uid, merchant_profile_id, event_type,
    triggering_transfer_observation_id, triggering_transfer_id,
    occurred_at, created_at
) VALUES (
    130, '13131313-1313-4313-8313-131313131130',
    50, 'PAUSED', 120, 110,
    '2026-07-25 00:01:00.000', '2026-07-25 00:01:00.000'
);
UPDATE fund_payout_gate
SET gate_state = 'PAUSED_NOT_ENOUGH',
    current_pause_event_id = 130,
    paused_at = '2026-07-25 00:01:00.000',
    lock_version = 1,
    updated_at = '2026-07-25 00:01:00.000'
WHERE merchant_profile_id = 50;
"@ | Out-Null
    $positiveFacts = Invoke-MySql -Database "ecobin_f06_a" -Sql @"
SELECT CONCAT_WS(
    '|',
    (SELECT appid FROM iam_organization_miniapp WHERE id = 20),
    (SELECT phone_e164 FROM iam_organization_user WHERE id = 40),
    (SELECT available_balance_cent FROM fund_user_wallet WHERE id = 70),
    (SELECT COUNT(*) FROM fund_user_wallet_entry WHERE wallet_id = 70),
    (SELECT channel_state FROM fund_wechat_payment WHERE id = 91),
    (SELECT claimable_at FROM ops_reliable_task WHERE id = 201),
    (SELECT scene_id FROM fund_wechat_merchant_profile WHERE id = 50),
    (SELECT scene_id_snapshot FROM fund_wechat_transfer WHERE id = 110),
    (SELECT gate_state FROM fund_payout_gate WHERE merchant_profile_id = 50)
);
"@
    if (
        $positiveFacts -ne
            "wx-f06-active|+8613800000000|100|1|FUTURE_WECHAT_STATE|2026-07-25 00:00:08.000|SCENE-F06-V2|SCENE-F06|PAUSED_NOT_ENOUGH"
    ) {
        throw "positive funds/operations facts differ: $positiveFacts"
    }

    foreach ($file in @(
        "V6__funds.sql",
        "V8__cross_module_constraints.sql",
        "V10__permission_reference_data.sql"
    )) {
        $strictReinstallArgs = @(
            "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
            "sh", "-lc",
            "mysql -uroot --database=ecobin_f06_a < /tmp/p0-migration/$file"
        )
        & docker @strictReinstallArgs *> $null
        if ($LASTEXITCODE -eq 0) {
            throw "strict reinstall unexpectedly succeeded: $file"
        }
    }

    [ordered]@{
        mysqlVersion = [string]$version
        imageId = [string]$actualImageId
        migrationFiles = $migrationFiles.Count
        v6Tables = $v6TableCount
        v7Tables = $v7TableCount
        totalTables = [int]$shapeParts[0]
        foreignKeys = [int]$shapeParts[1]
        uniqueConstraints = [int]$shapeParts[2]
        checks = [int]$shapeParts[3]
        nonUniqueIndexes = [int]$shapeParts[4]
        triggers = [int]$shapeParts[5]
        permissionDefinitions = $permissionCount
        explicitlyIndexedForeignKeys = $explicitForeignKeyIndexCount
        directOrganizationForeignKeyTables = $organizationScopedTables.Count
        structureFingerprintSha256 = Get-Sha256 -Value $dumpA
        structuresEqual = $true
        negativeCases = $negativeCases.Count
        preActivationCorrectionPositive = $true
        mutableUserProfilePositive = $true
        mutableMerchantProfilePositive = $true
        longUnsettledTerminalRetentionPositive = $true
        unknownWechatStatePreservedPositive = $true
        sameMerchantNotEnoughGatePositive = $true
        dualLedgerPositive = $true
        reliableTaskClaimProjectionPositive = $true
        strictReinstallRejected = $true
    } | ConvertTo-Json
}
finally {
    if ($containerStarted) {
        & docker rm --force $containerName *> $null
    }
}

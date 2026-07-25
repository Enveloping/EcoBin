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
    "V5__recycling.sql"
)
$expectedRecyclingTables = @(
    "rec_bag",
    "rec_bag_current_occupancy",
    "rec_bag_occupancy_event",
    "rec_clean_anomaly",
    "rec_clean_operation",
    "rec_clean_photo",
    "rec_clean_record",
    "rec_clean_revision",
    "rec_delivery_anomaly",
    "rec_delivery_order",
    "rec_delivery_photo",
    "rec_delivery_revision",
    "rec_fullness_detection",
    "rec_fullness_event",
    "rec_fullness_sample",
    "rec_organization_clean_config",
    "rec_organization_clean_config_head",
    "rec_organization_clean_record_counter",
    "rec_organization_delivery_config",
    "rec_organization_delivery_config_head",
    "rec_organization_order_counter",
    "rec_port_baseline_measurement",
    "rec_port_capacity_state",
    "rec_port_weight_baseline"
)
$requiredConstraints = @(
    "fk_rec_delivery_order_session",
    "fk_rec_delivery_order_result",
    "fk_rec_delivery_order_current_revision",
    "fk_rec_delivery_revision_previous",
    "fk_rec_clean_operation_completion",
    "fk_rec_clean_operation_old_baseline",
    "fk_rec_clean_record_result_target",
    "fk_rec_clean_record_review_revision",
    "fk_rec_bag_occupancy_reservation",
    "fk_rec_baseline_measurement_result_baseline",
    "fk_rec_baseline_measurement_result_target",
    "fk_rec_fullness_detection_delivery",
    "fk_rec_fullness_detection_clean",
    "fk_rec_fullness_detection_baseline",
    "fk_rec_fullness_detection_initial_sample",
    "fk_rec_fullness_detection_terminal_sample",
    "fk_rec_fullness_sample_result_target",
    "fk_rec_capacity_current_detection",
    "fk_rec_capacity_fullness_event"
)

$containerName =
    "ecobin-f05-validate-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
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

function Expect-MySqlFailure {
    param(
        [Parameter(Mandatory)][string] $Database,
        [Parameter(Mandatory)][string] $Sql,
        [Parameter(Mandatory)][string] $CaseName
    )
    $args = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysql", "-uroot", "--database=$Database", "--execute", $Sql
    )
    & docker @args *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "negative test unexpectedly succeeded: $CaseName"
    }
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
    $v5Sql = Get-Content -Raw (Join-Path $migrationDir $migrationFiles[-1])
    $v5TableCount =
        ([regex]::Matches($v5Sql, "(?im)^CREATE TABLE\s+")).Count
    if ($v5TableCount -ne 24) {
        throw "expected V5 to create 24 tables, found $v5TableCount"
    }
    $totalTableCount =
        ([regex]::Matches($allSql, "(?im)^CREATE TABLE\s+")).Count
    if ($totalTableCount -ne 54) {
        throw "expected V1-V5 to create 54 tables, found $totalTableCount"
    }
    foreach ($forbidden in @(
        "(?i)IF\s+NOT\s+EXISTS",
        "(?i)FOREIGN_KEY_CHECKS\s*=\s*0",
        "(?im)^\s*(?:INSERT|REPLACE|UPDATE|DELETE)\s+",
        "(?i)\bstatus\s+[^,\r\n]+\brec_bag\b"
    )) {
        if ([regex]::IsMatch($v5Sql, $forbidden)) {
            throw "forbidden V5 migration content matched: $forbidden"
        }
    }
    $bagBlock = [regex]::Match(
        $v5Sql,
        "(?is)CREATE\s+TABLE\s+rec_bag\s*\((.*?)\)\s*ENGINE="
    ).Groups[1].Value
    if ($bagBlock -match "(?im)^\s*(?:lifecycle_)?status\s+") {
        throw "rec_bag must not contain a lifecycle/status column"
    }
    foreach ($position in @(
        "BEFORE_INNER", "BEFORE_OUTER", "AFTER_INNER", "AFTER_OUTER",
        "FIRST_OPEN_INNER", "FIRST_OPEN_OUTER",
        "FINAL_CLOSE_INNER", "FINAL_CLOSE_OUTER"
    )) {
        if ($v5Sql -notmatch [regex]::Escape("'$position'")) {
            throw "required photo position missing: $position"
        }
    }
    $explicitForeignKeyIndexCount =
        Get-ExplicitForeignKeyCoverage -Sql $allSql
    Assert-DirectOrganizationForeignKeys `
        -Sql $v5Sql -TableNames $expectedRecyclingTables

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
CREATE DATABASE ecobin_f05_a
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE ecobin_f05_b
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
"@ | Out-Null

    foreach ($database in @("ecobin_f05_a", "ecobin_f05_b")) {
        foreach ($file in $migrationFiles) {
            Invoke-Docker -Arguments @(
                "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
                "sh", "-lc",
                "mysql -uroot --database=$database < /tmp/p0-migration/$file"
            ) | Out-Null
        }
    }

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
        WHERE table_schema = '{0}' AND non_unique = 1)
);
"@
    $shapeA = Invoke-MySql -Database "ecobin_f05_a" `
        -Sql ($shapeSqlTemplate -f "ecobin_f05_a")
    $shapeB = Invoke-MySql -Database "ecobin_f05_b" `
        -Sql ($shapeSqlTemplate -f "ecobin_f05_b")
    if ($shapeA -ne $shapeB) {
        throw "two empty-schema installations have different object counts"
    }
    $shapeParts = $shapeA -split "\|"
    if ([int]$shapeParts[0] -ne 54) {
        throw "installed table count is not 54: $shapeA"
    }

    $actualRecTables = @(Invoke-MySql -Database "ecobin_f05_a" -Sql @"
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'ecobin_f05_a'
  AND table_type = 'BASE TABLE'
  AND table_name LIKE 'rec\_%'
ORDER BY table_name;
"@)
    if (
        @(Compare-Object $expectedRecyclingTables $actualRecTables).Count -ne 0
    ) {
        throw "installed recycling table list differs from the 24-table manifest"
    }

    $actualConstraints = @(Invoke-MySql -Database "ecobin_f05_a" -Sql @"
SELECT constraint_name
FROM information_schema.table_constraints
WHERE constraint_schema = 'ecobin_f05_a'
ORDER BY constraint_name;
"@)
    foreach ($constraint in $requiredConstraints) {
        if ($constraint -notin $actualConstraints) {
            throw "required constraint missing: $constraint"
        }
    }

    $dumpArgsA = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysqldump", "-uroot", "--no-data", "--skip-comments",
        "--skip-add-drop-table", "--compact", "--skip-set-charset",
        "ecobin_f05_a"
    )
    $dumpArgsB = $dumpArgsA.Clone()
    $dumpArgsB[-1] = "ecobin_f05_b"
    $dumpA = (Invoke-Docker -Arguments $dumpArgsA) -join "`n"
    $dumpB = (Invoke-Docker -Arguments $dumpArgsB) -join "`n"
    if ($dumpA -ne $dumpB) {
        throw "two empty-schema installations have different structures"
    }

    Invoke-MySql -Database "ecobin_f05_a" -Sql @"
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
INSERT INTO rec_organization_delivery_config (
    id, tenant_id, organization_id, version_no, content_sha256,
    review_mode, open_balance_floor_cent, max_review_abs_weight_g,
    publication_source, published_by_staff_account_id,
    published_at, created_at
) VALUES (
    100, 1, 10, 1, UNHEX(REPEAT('11', 32)),
    'ALL_MANUAL', -1, 100000, 'SYSTEM', NULL,
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:00.000'
);
INSERT INTO rec_organization_delivery_config_head (
    organization_id, tenant_id, current_config_id, current_version_no,
    lock_version, switched_at, updated_at
) VALUES (
    10, 1, 100, 1, 0,
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:01.000'
);
INSERT INTO rec_organization_order_counter (
    organization_id, tenant_id, last_visibility_sequence_no,
    lock_version, updated_at
) VALUES (10, 1, 0, 0, '2026-07-25 00:00:01.000');
INSERT INTO rec_organization_clean_config (
    id, tenant_id, organization_id, version_no, content_sha256,
    review_mode, operation_timeout_seconds, publication_source,
    published_by_staff_account_id, published_at, created_at
) VALUES (
    200, 1, 10, 1, UNHEX(REPEAT('22', 32)),
    'ALL_MANUAL', 1800, 'SYSTEM', NULL,
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:00.000'
);
INSERT INTO rec_bag (
    id, tenant_id, organization_id, bag_code, registered_at, created_at
) VALUES
    (300, 1, 10, 'BagCase01',
        '2026-07-25 00:00:01.000', '2026-07-25 00:00:01.000'),
    (301, 1, 10, 'bagcase01',
        '2026-07-25 00:00:01.000', '2026-07-25 00:00:01.000');
"@ | Out-Null

    $negativeCases = @(
        @{
            Name = "delivery config rejects a non-M0 review mode"
            Sql = @"
INSERT INTO rec_organization_delivery_config (
    tenant_id, organization_id, version_no, content_sha256,
    review_mode, open_balance_floor_cent, max_review_abs_weight_g,
    publication_source, published_at, created_at
) VALUES (
    1, 10, 2, UNHEX(REPEAT('31', 32)), 'NONE', -1, 100000,
    'SYSTEM', '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "delivery config rejects a zero balance floor"
            Sql = @"
INSERT INTO rec_organization_delivery_config (
    tenant_id, organization_id, version_no, content_sha256,
    review_mode, open_balance_floor_cent, max_review_abs_weight_g,
    publication_source, published_at, created_at
) VALUES (
    1, 10, 2, UNHEX(REPEAT('32', 32)), 'ALL_MANUAL', 0, 100000,
    'SYSTEM', '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "delivery config rejects an excessive review bound"
            Sql = @"
INSERT INTO rec_organization_delivery_config (
    tenant_id, organization_id, version_no, content_sha256,
    review_mode, open_balance_floor_cent, max_review_abs_weight_g,
    publication_source, published_at, created_at
) VALUES (
    1, 10, 2, UNHEX(REPEAT('33', 32)), 'ALL_MANUAL', -1, 1000001,
    'SYSTEM', '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "clean config keeps the frozen 1800-second timeout"
            Sql = @"
INSERT INTO rec_organization_clean_config (
    tenant_id, organization_id, version_no, content_sha256,
    review_mode, operation_timeout_seconds, publication_source,
    published_at, created_at
) VALUES (
    1, 10, 2, UNHEX(REPEAT('34', 32)), 'ALL_MANUAL', 1799,
    'SYSTEM', '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "config cannot reference a missing organization"
            Sql = @"
INSERT INTO rec_organization_delivery_config (
    tenant_id, organization_id, version_no, content_sha256,
    review_mode, open_balance_floor_cent, max_review_abs_weight_g,
    publication_source, published_at, created_at
) VALUES (
    1, 999, 1, UNHEX(REPEAT('35', 32)), 'ALL_MANUAL', -1, 100000,
    'SYSTEM', '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "config head cannot cross organizations"
            Sql = @"
INSERT INTO rec_organization_delivery_config_head (
    organization_id, tenant_id, current_config_id, current_version_no,
    lock_version, switched_at, updated_at
) VALUES (
    11, 1, 100, 1, 0,
    '2026-07-25 00:00:02.000', '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "bag code rejects non URL-safe characters"
            Sql = @"
INSERT INTO rec_bag (
    tenant_id, organization_id, bag_code, registered_at, created_at
) VALUES (
    1, 10, 'bad bag!', '2026-07-25 00:00:02.000',
    '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "bag code remains globally unique"
            Sql = @"
INSERT INTO rec_bag (
    tenant_id, organization_id, bag_code, registered_at, created_at
) VALUES (
    1, 10, 'BagCase01', '2026-07-25 00:00:02.000',
    '2026-07-25 00:00:02.000'
);
"@
        },
        @{
            Name = "bag occupancy requires exactly one typed target"
            Sql = @"
INSERT INTO rec_bag_current_occupancy (
    bag_id, tenant_id, organization_id, occupancy_type,
    port_id, clean_operation_id, acquired_at
) VALUES (
    300, 1, 10, 'PORT_BOUND', NULL, NULL,
    '2026-07-25 00:00:02.000'
);
"@
        }
    )
    foreach ($case in $negativeCases) {
        Expect-MySqlFailure -Database "ecobin_f05_a" `
            -Sql $case.Sql -CaseName $case.Name
    }

    $caseSensitiveBagCount = Invoke-MySql `
        -Database "ecobin_f05_a" `
        -Sql "SELECT COUNT(*) FROM rec_bag WHERE bag_code IN ('BagCase01','bagcase01');"
    if ([int]$caseSensitiveBagCount -ne 2) {
        throw "binary bag-code identity did not preserve case"
    }

    Invoke-MySql -Database "ecobin_f05_a" -Sql @"
INSERT INTO iam_staff_account (
    id, tenant_id, staff_account_uid, account_kind, login_name,
    password_hash, display_name, enabled, failed_login_count, auth_version,
    password_changed_at, lock_version, created_at, updated_at
) VALUES (
    20, 1, '20202020-2020-4020-8020-202020202020', 'STAFF', 'f05-reviewer',
    'test-only-hash', 'F05 Reviewer', 1, 0, 0,
    '2026-07-25 00:00:00.000', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO iam_organization_miniapp (
    id, tenant_id, organization_id, appid, display_name, login_enabled,
    secret_ref, activated_at, lock_version, configured_at, created_at,
    updated_at
) VALUES (
    30, 1, 10, 'wx-f05-test', 'F05 Miniapp', 1, 'secret://f05/test',
    '2026-07-25 00:00:00.000', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000',
    '2026-07-25 00:00:00.000'
);
INSERT INTO iam_organization_user (
    id, organization_user_uid, tenant_id, organization_id,
    organization_miniapp_id, openid, status, auth_version, lock_version,
    registered_at, created_at, updated_at
) VALUES (
    40, '40404040-4040-4040-8040-404040404040',
    1, 10, 30, 'f05-openid', 'ACTIVE', 0, 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000',
    '2026-07-25 00:00:00.000'
);
INSERT INTO dev_device_asset (
    id, hardware_sn, model_name, expected_port_count, lifecycle_status,
    lock_version, created_at, updated_at
) VALUES (
    50, 'F05-SN-A', 'F05 model', 2, 'IN_USE', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO dev_device_deployment (
    id, tenant_id, organization_id, asset_id, public_code, lifecycle_status,
    business_enabled, lock_version, created_at, updated_at
) VALUES (
    60, 1, 10, 50, 'Dp_f05_device_a', 'COMMISSIONING', 0, 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO dev_port (
    id, tenant_id, organization_id, deployment_id, port_no, created_at
) VALUES
    (70, 1, 10, 60, 1, '2026-07-25 00:00:00.000'),
    (71, 1, 10, 60, 2, '2026-07-25 00:00:00.000');
INSERT INTO dev_config_version (
    id, tenant_id, organization_id, deployment_id, version_no, schema_version,
    device_display_name, edge_heartbeat_interval_ms,
    edge_heartbeat_miss_threshold, mcu_heartbeat_interval_ms,
    mcu_heartbeat_miss_threshold, door_close_retry_limit,
    continue_delivery_wait_ms, negative_weight_threshold_g,
    delivery_auto_close_ms, weight_measurement_timeout_ms,
    clean_solenoid_pulse_ms, smoke_monitoring_enabled, content_sha256,
    mcu_payload_sha256, publication_source, published_at, created_at
) VALUES (
    80, 1, 10, 60, 1, 1, 'F05 device', 10000, 3, 1000, 3, 2,
    30000, 500, 60000, 5000, 1000, 1,
    UNHEX(REPEAT('41', 32)), UNHEX(REPEAT('42', 32)),
    'SYSTEM', '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO dev_port_config_snapshot (
    id, tenant_id, organization_id, deployment_id, config_version_id, port_id,
    display_name, business_enabled, unit_price_yuan_per_kg, fullness_mode,
    configured_full_weight_g, delivery_settle_delay_ms,
    fullness_settle_wait_ms, fullness_confirmation_wait_ms,
    door_auto_close_timeout_ms, weight_stable_window_ms,
    weight_maximum_fluctuation_g, weight_required_sample_count,
    weight_measurement_timeout_ms, weight_minimum_g, weight_maximum_g,
    calibration_version, infrared_sample_timeout_ms,
    delivery_door_operation_timeout_ms, created_at
) VALUES
    (
        81, 1, 10, 60, 80, 70, 'Delivery port', 1, 1.0000,
        'INFRARED_OR_WEIGHT', 50000, 1000, 2000, 1000, 60000, 500, 50, 5,
        5000, -100000, 100000, 1, 1000, 5000,
        '2026-07-25 00:00:00.000'
    ),
    (
        82, 1, 10, 60, 80, 71, 'Clean port', 1, 1.0000,
        'INFRARED_OR_WEIGHT', 50000, 1000, 2000, 1000, 60000, 500, 50, 5,
        5000, -100000, 100000, 1, 1000, 5000,
        '2026-07-25 00:00:00.000'
    );
INSERT INTO dev_delivery_session (
    id, session_uid, tenant_id, organization_id, deployment_id, port_id,
    organization_user_id, device_config_version_id, device_config_version_no,
    device_config_content_sha256, device_config_mcu_payload_sha256,
    port_config_snapshot_id, delivery_config_version_id,
    delivery_config_content_sha256, bag_id, bag_code_snapshot, status,
    unit_price_yuan_per_kg, open_balance_floor_cent, max_review_abs_weight_g,
    negative_weight_anomaly_threshold_g, local_end_selection_timeout_ms,
    authorization_expires_at, result_recovery_deadline_at, lock_version,
    created_at, updated_at
) VALUES (
    90, '90909090-9090-4090-8090-909090909090',
    1, 10, 60, 70, 40, 80, 1,
    UNHEX(REPEAT('41', 32)), UNHEX(REPEAT('42', 32)), 81, 100,
    UNHEX(REPEAT('11', 32)), 300, 'BagCase01', 'IN_PROGRESS', 1.0000,
    -1, 100000, 500, 30000,
    '2026-07-25 00:01:00.000', '2026-07-25 00:16:00.000', 0,
    '2026-07-25 00:00:00.000', '2026-07-25 00:00:00.000'
);
INSERT INTO dev_device_command (
    id, command_uid, tenant_id, organization_id, deployment_id, command_type,
    delivery_session_id, payload_schema_version, semantic_payload,
    semantic_payload_sha256, physical_state, queued_at, edge_accepted_at,
    physical_started_at, lock_version, created_at, updated_at
) VALUES (
    91, '91919191-9191-4191-8191-919191919191',
    1, 10, 60, 'START_DELIVERY_SESSION', 90, 1,
    JSON_OBJECT('sessionUid', '90909090-9090-4090-8090-909090909090'),
    UNHEX(REPEAT('51', 32)), 'PHYSICAL_STARTED',
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:02.000',
    '2026-07-25 00:00:03.000', 0,
    '2026-07-25 00:00:01.000', '2026-07-25 00:00:03.000'
);
INSERT INTO dev_edge_event (
    id, event_uid, tenant_id, organization_id, deployment_id,
    edge_event_sequence, event_type, delivery_class, schema_version,
    target_type, target_stable_key_sha256, device_occurred_at, clock_quality,
    backend_received_at, payload_sha256, canonical_sha256, source_inbox_id,
    created_at
) VALUES (
    92, '92929292-9292-4292-8292-929292929292',
    1, 10, 60, 1, 'DELIVERY_COMPLETE', 'RELIABLE_FACT', 1,
    'DELIVERY_SESSION', UNHEX(REPEAT('52', 32)),
    '2026-07-25 00:00:10.000', 'SYNCED', '2026-07-25 00:00:11.000',
    UNHEX(REPEAT('53', 32)), UNHEX(REPEAT('54', 32)), 90001,
    '2026-07-25 00:00:11.000'
);
INSERT INTO dev_physical_result (
    id, tenant_id, organization_id, deployment_id, port_id, edge_event_id,
    edge_event_type, command_id, command_type, reported_config_version_no,
    reported_config_content_sha256, reported_config_mcu_payload_sha256,
    result_type, delivery_session_id, delivery_pre_measurement_uid,
    delivery_pre_measurement_status, delivery_pre_weight_g,
    delivery_pre_last_observed_weight_g, delivery_pre_measurement_elapsed_ms,
    delivery_pre_sample_count, delivery_pre_calibration_version,
    delivery_pre_sensor_health, delivery_pre_fault_code,
    delivery_pre_mcu_boot_id, delivery_pre_mcu_event_sequence,
    delivery_post_measurement_uid, delivery_post_measurement_status,
    delivery_post_weight_g, delivery_post_last_observed_weight_g,
    delivery_post_measurement_elapsed_ms, delivery_post_sample_count,
    delivery_post_calibration_version, delivery_post_sensor_health,
    delivery_post_fault_code, delivery_post_mcu_boot_id,
    delivery_post_mcu_event_sequence, delivery_net_weight_g,
    delivery_final_door_state, delivery_final_door_health,
    delivery_completion_reason, negative_weight_anomaly,
    uart_protocol_major, uart_protocol_minor, created_at
) VALUES (
    93, 1, 10, 60, 70, 92, 'DELIVERY_COMPLETE', 91,
    'START_DELIVERY_SESSION', 1, UNHEX(REPEAT('41', 32)),
    UNHEX(REPEAT('42', 32)), 'DELIVERY', 90,
    '93939393-9393-4393-8393-939393939393', 'STABLE', 10000, 10000,
    1000, 10, 1, 'OK', NULL, 1, 10,
    '94949494-9494-4494-8494-949494949494', 'STABLE', 9000, 9000,
    1000, 10, 1, 'OK', NULL, 1, 11, -1000,
    'CLOSED', 'OK', 'USER_ENDED', 0, 1, 0,
    '2026-07-25 00:00:11.000'
);
START TRANSACTION;
UPDATE rec_organization_order_counter
SET last_visibility_sequence_no = 1, lock_version = 1,
    updated_at = '2026-07-25 00:00:12.000'
WHERE organization_id = 10 AND tenant_id = 1;
INSERT INTO rec_delivery_order (
    id, delivery_order_no, tenant_id, organization_id,
    visibility_sequence_no, delivery_session_id, physical_result_id,
    organization_user_id, deployment_id, port_id, device_config_version_id,
    delivery_config_version_id, delivery_config_version_no,
    delivery_config_content_sha256, unit_price_yuan_per_kg,
    open_balance_floor_cent, bag_id, bag_code_snapshot,
    negative_weight_anomaly_threshold_g, max_review_abs_weight_g,
    initial_weight_status, initial_weight_g, final_weight_status,
    final_weight_g, raw_net_weight_g, negative_weight_anomaly,
    raw_business_weight_kg, raw_amount_cent, raw_calculation_status,
    review_status, current_revision_no, current_revision_id,
    final_business_weight_kg, final_amount_cent, first_approved_at,
    device_occurred_at, backend_received_at, created_at, updated_at
) VALUES (
    94, 'F05-ORDER-0001', 1, 10, 1, 90, 93, 40, 60, 70, 80,
    100, 1, UNHEX(REPEAT('11', 32)), 1.0000, -1, 300, 'BagCase01',
    500, 100000, 'RELIABLE', 10000, 'RELIABLE', 9000, -1000, 0,
    -1.00, -100, 'RELIABLE', 'PENDING', 0, NULL, NULL, NULL, NULL,
    '2026-07-25 00:00:10.000', '2026-07-25 00:00:11.000',
    '2026-07-25 00:00:12.000', '2026-07-25 00:00:12.000'
);
COMMIT;
INSERT INTO rec_delivery_photo (
    tenant_id, organization_id, delivery_order_id, position, status,
    created_at, updated_at
) VALUES
    (1, 10, 94, 'BEFORE_INNER', 'UPLOAD_PENDING',
        '2026-07-25 00:00:12.000', '2026-07-25 00:00:12.000'),
    (1, 10, 94, 'BEFORE_OUTER', 'UPLOAD_PENDING',
        '2026-07-25 00:00:12.000', '2026-07-25 00:00:12.000'),
    (1, 10, 94, 'AFTER_INNER', 'UPLOAD_PENDING',
        '2026-07-25 00:00:12.000', '2026-07-25 00:00:12.000'),
    (1, 10, 94, 'AFTER_OUTER', 'UPLOAD_PENDING',
        '2026-07-25 00:00:12.000', '2026-07-25 00:00:12.000');
INSERT INTO rec_delivery_revision (
    id, revision_uid, tenant_id, organization_id, delivery_order_id,
    revision_no, revision_type, decision_type, after_final_weight_kg,
    after_final_amount_cent, amount_delta_cent, reviewer_kind,
    staff_account_id, request_sha256, reviewed_at, created_at
) VALUES (
    95, '95959595-9595-4595-8595-959595959595',
    1, 10, 94, 1, 'INITIAL_REVIEW', 'ORIGINAL_APPROVED', -1.00, -100,
    -100, 'STAFF', 20, UNHEX(REPEAT('55', 32)),
    '2026-07-25 00:00:13.000', '2026-07-25 00:00:13.000'
);
UPDATE rec_delivery_order
SET review_status = 'APPROVED', current_revision_no = 1,
    current_revision_id = 95, final_business_weight_kg = -1.00,
    final_amount_cent = -100, first_approved_at = '2026-07-25 00:00:13.000',
    updated_at = '2026-07-25 00:00:13.000'
WHERE id = 94;
UPDATE rec_delivery_photo
SET photo_uid = '96969696-9696-4696-8696-969696969696',
    status = 'AVAILABLE',
    object_url = 'https://cos.example/ecobin/Dp_f05_device_a/delivery-session/90909090-9090-4090-8090-909090909090/BEFORE_INNER/96969696-9696-4696-8696-969696969696.jpg',
    sha256 = UNHEX(REPEAT('56', 32)), size_bytes = 1024,
    captured_at = '2026-07-25 00:00:09.000',
    linked_at = '2026-07-25 00:00:14.000',
    updated_at = '2026-07-25 00:00:14.000'
WHERE delivery_order_id = 94 AND position = 'BEFORE_INNER';
INSERT INTO rec_clean_operation (
    id, operation_uid, tenant_id, organization_id, deployment_id, port_id,
    cleaner_organization_user_id, device_config_version_id,
    clean_config_version_id, clean_config_version_no,
    operation_timeout_seconds, old_bag_binding_state, old_baseline_state,
    pre_unlock_weight_status, new_bag_id, new_bag_code_snapshot, status,
    start_authorization_expires_at, reopen_count, recovery_count,
    lock_version, created_at, updated_at
) VALUES (
    100, '10101010-1010-4010-8010-101010101010',
    1, 10, 60, 71, 40, 80, 200, 1, 1800,
    'MISSING', 'MISSING', 'PENDING', 301, 'bagcase01', 'PREPARED',
    '2026-07-25 00:01:00.000', 0, 0, 0,
    '2026-07-25 00:00:15.000', '2026-07-25 00:00:15.000'
);
INSERT INTO rec_bag_current_occupancy (
    bag_id, tenant_id, organization_id, occupancy_type,
    port_id, clean_operation_id, acquired_at
) VALUES
    (300, 1, 10, 'PORT_BOUND', 70, NULL,
        '2026-07-25 00:00:15.000'),
    (301, 1, 10, 'CLEAN_RESERVED', NULL, 100,
        '2026-07-25 00:00:15.000');
INSERT INTO rec_bag_occupancy_event (
    id, event_uid, tenant_id, organization_id, bag_id, port_id,
    clean_operation_id, event_type, occurred_at, created_at
) VALUES
    (
        96, '96969696-9696-4696-8696-111111111111',
        1, 10, 300, 70, NULL, 'INITIAL_INSTALLED',
        '2026-07-25 00:00:15.000', '2026-07-25 00:00:15.000'
    ),
    (
        97, '97979797-9797-4797-8797-979797979797',
        1, 10, 301, 71, 100, 'RESERVED_FOR_CLEAN',
        '2026-07-25 00:00:15.000', '2026-07-25 00:00:15.000'
    );
INSERT INTO rec_port_weight_baseline (
    id, tenant_id, organization_id, port_id, bag_id, version_no, source_type,
    source_bag_event_id, baseline_weight_g, established_at, created_at
) VALUES (
    98, 1, 10, 70, 300, 1, 'INITIAL_BINDING', 96, 10000,
    '2026-07-25 00:00:15.000', '2026-07-25 00:00:15.000'
);
INSERT INTO rec_port_capacity_state (
    port_id, tenant_id, organization_id, deployment_id, baseline_state,
    current_baseline_id, current_baseline_weight_g,
    latest_stable_total_weight_g, raw_net_weight_g,
    displayed_fullness_percent, detection_gate,
    confirmed_fullness_state, lock_version, updated_at
) VALUES (
    70, 1, 10, 60, 'VALID', 98, 10000, 9000, -1000, 0.00,
    'UNKNOWN', 'UNKNOWN', 0, '2026-07-25 00:00:15.000'
);
"@ | Out-Null

    $deliveryFacts = Invoke-MySql -Database "ecobin_f05_a" -Sql @"
SELECT CONCAT_WS(
    '|',
    raw_net_weight_g,
    negative_weight_anomaly,
    final_business_weight_kg,
    final_amount_cent,
    (SELECT COUNT(*) FROM rec_delivery_photo WHERE delivery_order_id = 94),
    (SELECT old_bag_binding_state FROM rec_clean_operation WHERE id = 100),
    (SELECT COUNT(*) FROM rec_bag_current_occupancy
        WHERE clean_operation_id = 100),
    (SELECT raw_net_weight_g FROM rec_port_capacity_state WHERE port_id = 70),
    (SELECT displayed_fullness_percent
        FROM rec_port_capacity_state WHERE port_id = 70)
)
FROM rec_delivery_order
WHERE id = 94;
"@
    if ($deliveryFacts -ne "-1000|0|-1.00|-100|4|MISSING|1|-1000|0.00") {
        throw "delivery/clean/capacity positive facts differ: $deliveryFacts"
    }

    $postSetupNegativeCases = @(
        @{
            Name = "delivery photo rejects a non-standard slot"
            Sql = @"
INSERT INTO rec_delivery_photo (
    tenant_id, organization_id, delivery_order_id, position, status,
    created_at, updated_at
) VALUES (
    1, 10, 94, 'MIDDLE_INNER', 'UPLOAD_PENDING',
    '2026-07-25 00:00:16.000', '2026-07-25 00:00:16.000'
);
"@
        },
        @{
            Name = "available photo requires digest and size"
            Sql = @"
UPDATE rec_delivery_photo
SET photo_uid = '11111111-2222-4333-8444-555555555555',
    status = 'AVAILABLE',
    object_url = 'https://cos.example/ecobin/f05.jpg',
    linked_at = '2026-07-25 00:00:16.000',
    updated_at = '2026-07-25 00:00:16.000'
WHERE delivery_order_id = 94 AND position = 'BEFORE_OUTER';
"@
        },
        @{
            Name = "photo URL rejects query credentials"
            Sql = @"
UPDATE rec_delivery_photo
SET photo_uid = '12121212-1212-4212-8212-121212121212',
    status = 'AVAILABLE',
    object_url = 'https://cos.example/ecobin/f05.jpg?secret=test',
    sha256 = UNHEX(REPEAT('61', 32)), size_bytes = 100,
    linked_at = '2026-07-25 00:00:16.000',
    updated_at = '2026-07-25 00:00:16.000'
WHERE delivery_order_id = 94 AND position = 'BEFORE_OUTER';
"@
        },
        @{
            Name = "baseline rejects a negative valid weight"
            Sql = @"
INSERT INTO rec_port_weight_baseline (
    tenant_id, organization_id, port_id, bag_id, version_no, source_type,
    source_bag_event_id, baseline_weight_g, established_at, created_at
) VALUES (
    1, 10, 70, 300, 2, 'INITIAL_BINDING', 96, -1,
    '2026-07-25 00:00:16.000', '2026-07-25 00:00:16.000'
);
"@
        },
        @{
            Name = "completed clean operation requires both close facts"
            Sql = @"
UPDATE rec_clean_operation
SET status = 'COMPLETED', ended_at = '2026-07-25 00:00:16.000',
    end_reason = 'COMPLETED', updated_at = '2026-07-25 00:00:16.000'
WHERE id = 100;
"@
        },
        @{
            Name = "fullness source cannot mix delivery and manual actor"
            Sql = @"
INSERT INTO rec_fullness_detection (
    detection_uid, tenant_id, organization_id, deployment_id, port_id,
    trigger_type, delivery_order_id, initiator_kind, staff_account_id,
    bag_id, baseline_state_snapshot, baseline_id_snapshot,
    baseline_weight_g_snapshot, device_config_version_id,
    port_config_snapshot_id, rule_fingerprint, decision_mode,
    configured_full_weight_g, settle_wait_ms, confirmation_wait_ms,
    status, disposition, lock_version, created_at, updated_at
) VALUES (
    '13131313-1313-4313-8313-131313131313',
    1, 10, 60, 70, 'DELIVERY_COMPLETE', 94, 'STAFF', 20,
    300, 'VALID', 98, 10000, 80, 81, UNHEX(REPEAT('62', 32)),
    'INFRARED_OR_WEIGHT', 50000, 2000, 1000,
    'PENDING_INITIAL_SAMPLE', 'PENDING', 0,
    '2026-07-25 00:00:16.000', '2026-07-25 00:00:16.000'
);
"@
        },
        @{
            Name = "capacity without a baseline cannot invent a percentage"
            Sql = @"
UPDATE rec_port_capacity_state
SET baseline_state = 'INVALID', current_baseline_id = NULL,
    current_baseline_weight_g = NULL, raw_net_weight_g = NULL,
    displayed_fullness_percent = 25.00,
    updated_at = '2026-07-25 00:00:16.000'
WHERE port_id = 70;
"@
        }
    )
    foreach ($case in $postSetupNegativeCases) {
        Expect-MySqlFailure -Database "ecobin_f05_a" `
            -Sql $case.Sql -CaseName $case.Name
    }

    Invoke-MySql -Database "ecobin_f05_a" -Sql @"
UPDATE rec_port_capacity_state
SET latest_stable_total_weight_g = 70000, raw_net_weight_g = 60000,
    displayed_fullness_percent = 120.00, lock_version = 1,
    updated_at = '2026-07-25 00:00:17.000'
WHERE port_id = 70;
"@ | Out-Null
    $overCapacity = Invoke-MySql -Database "ecobin_f05_a" `
        -Sql "SELECT displayed_fullness_percent FROM rec_port_capacity_state WHERE port_id = 70;"
    if ($overCapacity -ne "120.00") {
        throw "fullness percentage above 100 was not preserved"
    }

    $strictReinstallArgs = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "sh", "-lc",
        "mysql -uroot --database=ecobin_f05_a < /tmp/p0-migration/V5__recycling.sql"
    )
    & docker @strictReinstallArgs *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "strict V5 reinstall unexpectedly succeeded"
    }

    [ordered]@{
        mysqlVersion = [string]$version
        imageId = [string]$actualImageId
        migrationFiles = $migrationFiles.Count
        v5Tables = $v5TableCount
        totalTables = [int]$shapeParts[0]
        foreignKeys = [int]$shapeParts[1]
        uniqueConstraints = [int]$shapeParts[2]
        checks = [int]$shapeParts[3]
        nonUniqueIndexes = [int]$shapeParts[4]
        explicitlyIndexedForeignKeys = $explicitForeignKeyIndexCount
        directOrganizationForeignKeyTables = $expectedRecyclingTables.Count
        structureFingerprintSha256 = Get-Sha256 -Value $dumpA
        structuresEqual = $true
        negativeCases = $negativeCases.Count + $postSetupNegativeCases.Count
        caseSensitiveBagCodesPositive = $true
        negativeWeightWithoutInferencePositive = $true
        fourTypedDeliveryPhotoSlotsPositive = $true
        missingOldBagCleanPreparationPositive = $true
        negativeRawCapacityPositive = $true
        fullnessAboveOneHundredPositive = $true
        strictReinstallRejected = $true
    } | ConvertTo-Json
}
finally {
    if ($containerStarted) {
        & docker rm --force $containerName *> $null
    }
}

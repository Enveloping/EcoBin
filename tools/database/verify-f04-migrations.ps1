[CmdletBinding()]
param(
    [string]$MySqlImage = "mysql:8.4",
    [string]$ExpectedImageId =
        "sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$migrationDir = Join-Path $repoRoot "ecobin-bootstrap/src/main/resources/db/p0-migration"
$migrationFiles = @(
    "V1__p0_epoch_and_iam_core.sql",
    "V2__device_inventory_and_configuration.sql",
    "V3__organization_users_and_sessions.sql",
    "V4__device_operations_and_evidence.sql"
)
$expectedTables = @(
    "dev_asset_active_deployment",
    "dev_config_application",
    "dev_config_version",
    "dev_delivery_session",
    "dev_deployment_runtime_state",
    "dev_device_asset",
    "dev_device_command",
    "dev_device_command_event",
    "dev_device_deployment",
    "dev_device_fault_event",
    "dev_device_occupancy",
    "dev_edge_event",
    "dev_physical_result",
    "dev_port",
    "dev_port_config_snapshot",
    "dev_port_runtime_state",
    "iam_organization",
    "iam_organization_miniapp",
    "iam_organization_staff_membership",
    "iam_organization_user",
    "iam_organization_user_capability",
    "iam_organization_user_session",
    "iam_permission_definition",
    "iam_platform_admin",
    "iam_platform_login_session",
    "iam_staff_account",
    "iam_staff_login_session",
    "iam_staff_miniapp_binding",
    "iam_staff_permission_grant",
    "iam_tenant"
)
$requiredConstraints = @(
    "fk_iam_membership_org",
    "fk_iam_membership_staff",
    "fk_iam_grant_org",
    "fk_iam_grant_membership",
    "fk_dev_active_deployment_scope",
    "fk_dev_port_config_version",
    "fk_dev_port_config_port",
    "fk_iam_org_user_registration_deployment",
    "fk_iam_staff_binding_user",
    "fk_iam_staff_session_binding",
    "fk_dev_delivery_session_port",
    "fk_dev_delivery_session_user",
    "fk_dev_delivery_session_device_config",
    "fk_dev_delivery_session_port_config",
    "fk_dev_port_runtime_pending_session",
    "fk_dev_occupancy_active_deployment",
    "fk_dev_occupancy_delivery_session",
    "fk_dev_command_delivery_session",
    "fk_dev_command_config_application",
    "fk_dev_command_event_edge",
    "fk_dev_command_event_command_type",
    "fk_dev_command_event_delivery_ref",
    "fk_dev_fault_first_edge",
    "fk_dev_fault_recovery_edge",
    "fk_dev_result_edge",
    "fk_dev_result_command_type",
    "fk_dev_result_reported_config",
    "fk_dev_result_delivery_frozen_config",
    "fk_dev_result_port",
    "fk_dev_result_delivery_session"
)
$organizationScopedTables = @(
    "iam_organization_miniapp",
    "iam_organization_staff_membership",
    "dev_device_deployment",
    "dev_asset_active_deployment",
    "dev_port",
    "dev_config_version",
    "dev_port_config_snapshot",
    "dev_config_application",
    "dev_deployment_runtime_state",
    "dev_port_runtime_state",
    "iam_organization_user",
    "iam_organization_user_capability",
    "iam_staff_miniapp_binding",
    "iam_organization_user_session",
    "dev_delivery_session",
    "dev_device_fault_event",
    "dev_device_occupancy",
    "dev_device_command",
    "dev_edge_event",
    "dev_device_command_event",
    "dev_physical_result"
)

$containerName = "ecobin-f04-validate-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
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
        if ($statement -notmatch "(?is)\b(?:CREATE|ALTER)\s+TABLE\s+([a-z0-9_]+)") {
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
                ((($_ -replace "``", "").Trim() -split "\s+")[0]).ToLowerInvariant()
            })
            $tables[$tableName].ForeignKeys += [pscustomobject]@{
                Name = $match.Groups[1].Value
                Columns = $columns
            }
        }
        foreach ($match in [regex]::Matches(
            $statement,
            "(?is)(?:PRIMARY\s+KEY|CONSTRAINT\s+[a-z0-9_]+\s+UNIQUE|(?:UNIQUE\s+)?INDEX\s+[a-z0-9_]+)\s*\(([^)]+)\)"
        )) {
            $columns = @($match.Groups[1].Value -split "," | ForEach-Object {
                ((($_ -replace "``", "").Trim() -split "\s+")[0]).ToLowerInvariant()
            })
            $tables[$tableName].Indexes += ,$columns
        }
    }

    $foreignKeyCount = 0
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
                throw "foreign key lacks an explicit DDL left-prefix index: " +
                    "$tableName.$($foreignKey.Name)"
            }
        }
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
            "(?is)CREATE\s+TABLE\s+$([regex]::Escape($tableName))\s*\((.*?)\)\s*ENGINE="
        $tableMatch = [regex]::Match($Sql, $tablePattern)
        if (-not $tableMatch.Success) {
            throw "organization-scoped table DDL not found: $tableName"
        }
        if (-not [regex]::IsMatch(
            $tableMatch.Groups[1].Value,
            "(?is)FOREIGN\s+KEY\s*\(\s*tenant_id\s*,\s*organization_id\s*\)\s*" +
                "REFERENCES\s+iam_organization\s*\(\s*tenant_id\s*,\s*id\s*\)"
        )) {
            throw "organization-scoped table lacks direct organization FK: $tableName"
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
    $tableCount = ([regex]::Matches($allSql, "(?im)^CREATE TABLE\s+")).Count
    if ($tableCount -ne 30) {
        throw "expected 30 CREATE TABLE statements, found $tableCount"
    }
    foreach ($forbidden in @(
        "(?i)IF\s+NOT\s+EXISTS",
        "(?i)FOREIGN_KEY_CHECKS\s*=\s*0",
        "(?im)^\s*INSERT\s+INTO\s+",
        "(?i)dev_delivery_cycle"
    )) {
        if ([regex]::IsMatch($allSql, $forbidden)) {
            throw "forbidden migration content matched: $forbidden"
        }
    }
    $explicitForeignKeyIndexCount = Get-ExplicitForeignKeyCoverage -Sql $allSql
    Assert-DirectOrganizationForeignKeys `
        -Sql $allSql -TableNames $organizationScopedTables

    $actualImageId = (
        Invoke-Docker -Arguments @("image", "inspect", "--format", "{{.Id}}", $MySqlImage)
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
            mysql -uroot --batch --skip-column-names --execute "SELECT 1;" *> $null
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
CREATE DATABASE ecobin_f04_a
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE ecobin_f04_b
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
"@ | Out-Null

    foreach ($database in @("ecobin_f04_a", "ecobin_f04_b")) {
        foreach ($file in $migrationFiles) {
            Invoke-Docker -Arguments @(
                "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
                "sh", "-lc",
                "mysql -uroot --database=$database < /tmp/p0-migration/$file"
            ) | Out-Null
        }
    }

    $shapeSql = @"
SELECT CONCAT_WS(
    '|',
    (SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = DATABASE() AND constraint_type = 'FOREIGN KEY'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = DATABASE() AND constraint_type = 'UNIQUE'),
    (SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = DATABASE() AND constraint_type = 'CHECK'),
    (SELECT COUNT(DISTINCT CONCAT(table_name, ':', index_name))
        FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND non_unique = 1)
);
"@
    $shapeA = Invoke-MySql -Database "ecobin_f04_a" -Sql $shapeSql
    $shapeB = Invoke-MySql -Database "ecobin_f04_b" -Sql $shapeSql
    if ($shapeA -ne "30|78|84|166|79" -or $shapeB -ne $shapeA) {
        throw "unexpected schema counts: A=$shapeA B=$shapeB"
    }
    $shapeParts = @($shapeA -split "\|")

    $tableSql = @"
SELECT table_name
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
ORDER BY table_name;
"@
    $actualTables = @(Invoke-MySql -Database "ecobin_f04_a" -Sql $tableSql)
    if (@(Compare-Object $expectedTables $actualTables).Count -ne 0) {
        throw "installed table list does not match the frozen 30-table manifest"
    }

    $constraintSql = @"
SELECT constraint_name
FROM information_schema.table_constraints
WHERE constraint_schema = DATABASE()
ORDER BY constraint_name;
"@
    $actualConstraints = @(Invoke-MySql -Database "ecobin_f04_a" -Sql $constraintSql)
    foreach ($constraint in $requiredConstraints) {
        if ($constraint -notin $actualConstraints) {
            throw "required constraint missing: $constraint"
        }
    }

    $dumpArgs = @(
        "exec", "-e", "MYSQL_PWD=$rootPassword", $containerName,
        "mysqldump", "-uroot", "--no-data", "--skip-comments",
        "--skip-add-drop-table", "--compact", "--skip-set-charset"
    )
    $dumpA = (
        Invoke-Docker -Arguments ($dumpArgs + @("ecobin_f04_a"))
    ) -join "`n"
    $dumpB = (
        Invoke-Docker -Arguments ($dumpArgs + @("ecobin_f04_b"))
    ) -join "`n"
    if ($dumpA -cne $dumpB) {
        throw "the two independently installed database structures differ"
    }
    $fingerprint = Get-Sha256 -Value $dumpA

    $setupSql = @"
INSERT INTO iam_tenant(
    id, tenant_code, enterprise_name, status, lock_version, created_at, updated_at
) VALUES
    (101, 'tenant-a', 'Tenant A', 'ENABLED', 0, NOW(3), NOW(3)),
    (102, 'tenant-b', 'Tenant B', 'ENABLED', 0, NOW(3), NOW(3));
INSERT INTO iam_organization(
    id, tenant_id, organization_code, organization_name, status,
    lock_version, created_at, updated_at
) VALUES
    (201, 101, 'org-a', 'Org A', 'ENABLED', 0, NOW(3), NOW(3)),
    (202, 101, 'org-b', 'Org B', 'ENABLED', 0, NOW(3), NOW(3)),
    (203, 102, 'org-c', 'Org C', 'ENABLED', 0, NOW(3), NOW(3));
INSERT INTO iam_staff_account(
    id, tenant_id, staff_account_uid, account_kind, login_name, password_hash,
    display_name, enabled, failed_login_count, auth_version,
    password_changed_at, lock_version, created_at, updated_at
) VALUES (
    301, 101, '11111111-1111-4111-8111-111111111111', 'STAFF', 'staff-a',
    'test-only-hash', 'Staff A', 1, 0, 0, NOW(3), 0, NOW(3), NOW(3)
);
INSERT INTO dev_device_asset(
    id, hardware_sn, model_name, expected_port_count, lifecycle_status,
    lock_version, created_at, updated_at
) VALUES
    (401, 'F04-SN-A', 'F04 model', 2, 'IN_USE', 0, NOW(3), NOW(3)),
    (402, 'F04-SN-B', 'F04 model', 2, 'IN_USE', 0, NOW(3), NOW(3));
INSERT INTO dev_device_deployment(
    id, tenant_id, organization_id, asset_id, public_code, lifecycle_status,
    business_enabled, lock_version, created_at, updated_at
) VALUES
    (501, 101, 201, 401, 'Dp_f04_public_code_a',
        'COMMISSIONING', 0, 0, NOW(3), NOW(3)),
    (502, 101, 202, 402, 'Dp_f04_public_code_b',
        'COMMISSIONING', 0, 0, NOW(3), NOW(3));
INSERT INTO iam_organization_miniapp(
    id, tenant_id, organization_id, appid, display_name, login_enabled,
    secret_ref, activated_at, lock_version, configured_at, created_at, updated_at
) VALUES
    (601, 101, 201, 'wx-f04-a', 'Mini A', 1, 'secret://f04/a', NOW(3),
        0, NOW(3), NOW(3), NOW(3)),
    (602, 101, 202, 'wx-f04-b', 'Mini B', 1, 'secret://f04/b', NOW(3),
        0, NOW(3), NOW(3), NOW(3));
INSERT INTO iam_organization_user(
    id, organization_user_uid, tenant_id, organization_id,
    organization_miniapp_id, openid, status, auth_version, lock_version,
    registered_at, registered_via_deployment_id, created_at, updated_at
) VALUES (
    901, '33333333-3333-4333-8333-333333333333',
    101, 201, 601, 'openid-valid', 'ACTIVE', 0, 0,
    NOW(3), 501, NOW(3), NOW(3)
);
INSERT INTO dev_port(
    id, tenant_id, organization_id, deployment_id, port_no, created_at
) VALUES
    (701, 101, 201, 501, 1, NOW(3)),
    (702, 101, 202, 502, 1, NOW(3));
"@
    Invoke-MySql -Database "ecobin_f04_a" -Sql $setupSql | Out-Null

    $negativeCases = @(
        @{
            Name = "staff binding cross-organization"
            Sql = @"
INSERT INTO iam_staff_miniapp_binding(
    binding_uid, tenant_id, organization_id, organization_miniapp_id,
    organization_user_id, staff_account_id, status, bound_at, lock_version,
    created_at
) VALUES (
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    101, 202, 601, 901, 301, 'ACTIVE', NOW(3), 0, NOW(3)
);
"@
        },
        @{
            Name = "registration source cross-organization"
            Sql = @"
INSERT INTO iam_organization_user(
    organization_user_uid, tenant_id, organization_id,
    organization_miniapp_id, openid, status, auth_version, lock_version,
    registered_at, registered_via_deployment_id, created_at, updated_at
) VALUES (
    '22222222-2222-4222-8222-222222222222',
    101, 201, 601, 'openid-a', 'ACTIVE', 0, 0, NOW(3), 502, NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "port runtime cross-organization"
            Sql = @"
INSERT INTO dev_port_runtime_state(
    port_id, tenant_id, organization_id, deployment_id, delivery_door_state,
    delivery_door_actuator_health, delivery_door_contact_state,
    clean_lock_power_state, clean_solenoid_health, clean_door_inferred_state,
    clean_door_state_basis, weight_sensor_health, infrared_value,
    infrared_sensor_health, smoke_state, smoke_sensor_health,
    safety_status, lock_version, created_at, updated_at
) VALUES (
    702, 101, 201, 501, 'CLOSED', 'OK', 'CLOSED',
    'DEENERGIZED', 'OK', 'CLOSED', 'INFERRED_FROM_LOCK_POWER',
    'OK', 'CLEAR', 'OK', 'NORMAL', 'OK', 'SAFE', 0, NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "non-normalized tenant code"
            Sql = @"
INSERT INTO iam_tenant(
    tenant_code, enterprise_name, status, lock_version, created_at, updated_at
) VALUES ('UPPER', 'Invalid', 'ENABLED', 0, NOW(3), NOW(3));
"@
        },
        @{
            Name = "port number out of range"
            Sql = @"
INSERT INTO dev_port(
    tenant_id, organization_id, deployment_id, port_no, created_at
) VALUES (101, 201, 501, 7, NOW(3));
"@
        },
        @{
            Name = "non-v4 public UUID"
            Sql = @"
INSERT INTO iam_platform_admin(
    platform_admin_uid, login_name, password_hash, display_name, enabled,
    failed_login_count, auth_version, password_changed_at, lock_version,
    created_at, updated_at
) VALUES (
    '11111111-1111-3111-8111-111111111111', 'bad-uuid', 'test-only-hash',
    'Bad UUID', 1, 0, 0, NOW(3), 0, NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "invalid E.164 phone"
            Sql = @"
UPDATE iam_organization_user
SET phone_e164 = '+abc', phone_bound_at = NOW(3)
WHERE id = 901;
"@
        },
        @{
            Name = "blank hardware serial"
            Sql = @"
INSERT INTO dev_device_asset(
    hardware_sn, model_name, expected_port_count, lifecycle_status,
    lock_version, created_at, updated_at
) VALUES ('  ', 'Invalid', 1, 'IN_STOCK', 0, NOW(3), NOW(3));
"@
        },
        @{
            Name = "enabled miniapp without activation"
            Sql = @"
INSERT INTO iam_organization_miniapp(
    tenant_id, organization_id, appid, display_name, login_enabled,
    secret_ref, lock_version, configured_at, created_at, updated_at
) VALUES (
    102, 203, 'wx-invalid-activation', 'Invalid', 1, 'secret://invalid',
    0, NOW(3), NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "asset retirement cannot precede creation"
            Sql = @"
INSERT INTO dev_device_asset(
    hardware_sn, model_name, expected_port_count, lifecycle_status,
    retired_at, retirement_reason, lock_version, created_at, updated_at
) VALUES (
    'F04-SN-BAD-RETIREMENT', 'Invalid', 1, 'RETIRED',
    '2026-07-23 00:00:00.000', 'test invalid chronology', 0,
    '2026-07-24 00:00:00.000', '2026-07-24 00:00:00.000'
);
"@
        }
    )
    foreach ($case in $negativeCases) {
        Expect-MySqlFailure -Database "ecobin_f04_a" `
            -Sql $case.Sql -CaseName $case.Name
    }

    $physicalResultSql = @"
INSERT INTO dev_port(
    id, tenant_id, organization_id, deployment_id, port_no, created_at
) VALUES (703, 101, 201, 501, 2, NOW(3));
INSERT INTO dev_config_version(
    id, tenant_id, organization_id, deployment_id, version_no, schema_version,
    device_display_name, edge_heartbeat_interval_ms,
    edge_heartbeat_miss_threshold, mcu_heartbeat_interval_ms,
    mcu_heartbeat_miss_threshold, door_close_retry_limit,
    continue_delivery_wait_ms, negative_weight_threshold_g,
    delivery_auto_close_ms, weight_measurement_timeout_ms,
    clean_solenoid_pulse_ms, smoke_monitoring_enabled, content_sha256,
    mcu_payload_sha256, publication_source, published_at, created_at
) VALUES (
    801, 101, 201, 501, 1, 1, 'F04 device', 10000, 3, 1000, 3, 2,
    30000, 500, 60000, 5000, 1000, 1,
    UNHEX(REPEAT('11', 32)), UNHEX(REPEAT('12', 32)),
    'SYSTEM', NOW(3), NOW(3)
);
INSERT INTO dev_port_config_snapshot(
    id, tenant_id, organization_id, deployment_id, config_version_id, port_id,
    display_name, business_enabled, unit_price_yuan_per_kg, fullness_mode,
    configured_full_weight_g, delivery_settle_delay_ms,
    fullness_settle_wait_ms, fullness_confirmation_wait_ms,
    door_auto_close_timeout_ms, weight_stable_window_ms,
    weight_maximum_fluctuation_g, weight_required_sample_count,
    weight_measurement_timeout_ms, weight_minimum_g, weight_maximum_g,
    calibration_version, infrared_sample_timeout_ms,
    delivery_door_operation_timeout_ms, created_at
) VALUES (
    802, 101, 201, 501, 801, 701, 'Port 1', 1, 1.0000,
    'INFRARED_OR_WEIGHT', 50000, 1000, 2000, 1000, 60000, 500, 50, 5,
    5000, -100000, 100000, 1, 1000, 5000, NOW(3)
);
INSERT INTO dev_config_version(
    id, tenant_id, organization_id, deployment_id, version_no, schema_version,
    device_display_name, edge_heartbeat_interval_ms,
    edge_heartbeat_miss_threshold, mcu_heartbeat_interval_ms,
    mcu_heartbeat_miss_threshold, door_close_retry_limit,
    continue_delivery_wait_ms, negative_weight_threshold_g,
    delivery_auto_close_ms, weight_measurement_timeout_ms,
    clean_solenoid_pulse_ms, smoke_monitoring_enabled, content_sha256,
    mcu_payload_sha256, publication_source, published_at, created_at
) VALUES (
    804, 101, 201, 501, 9007199254740991, 1, 'F04 boundary device',
    4294967295, 2147483647, 4294967295, 2147483647, 2147483647,
    30000, 4294967295, 4294967295, 4294967295, 4294967295, 1,
    UNHEX(REPEAT('13', 32)), UNHEX(REPEAT('14', 32)),
    'SYSTEM', NOW(3), NOW(3)
);
INSERT INTO dev_port_config_snapshot(
    id, tenant_id, organization_id, deployment_id, config_version_id, port_id,
    display_name, business_enabled, unit_price_yuan_per_kg, fullness_mode,
    configured_full_weight_g, delivery_settle_delay_ms,
    fullness_settle_wait_ms, fullness_confirmation_wait_ms,
    door_auto_close_timeout_ms, weight_stable_window_ms,
    weight_maximum_fluctuation_g, weight_required_sample_count,
    weight_measurement_timeout_ms, weight_minimum_g, weight_maximum_g,
    calibration_version, infrared_sample_timeout_ms,
    delivery_door_operation_timeout_ms, created_at
) VALUES (
    804, 101, 201, 501, 804, 703, '12345678901234567890123456789012',
    1, 429496.7295, 'INFRARED_OR_WEIGHT', 4294967295, 4294967295,
    4294967295, 4294967295, 4294967295, 4294967295, 4294967295,
    65535, 4294967295, -2147483648, 2147483647, 4294967295,
    4294967295, 4294967295, NOW(3)
);
INSERT INTO dev_config_application(
    id, application_uid, tenant_id, organization_id, deployment_id,
    config_version_id, status, reported_version_no, reported_content_sha256,
    reported_mcu_payload_sha256, edge_persisted_at, last_failure_at,
    last_failure_code, lock_version, created_at, updated_at
) VALUES (
    803, '88888888-8888-4888-8888-888888888888',
    101, 201, 501, 801, 'FAILED', 1,
    UNHEX(REPEAT('11', 32)), UNHEX(REPEAT('12', 32)), NOW(3), NOW(3),
    'MCU_COMMIT_TIMEOUT', 0, NOW(3), NOW(3)
);
UPDATE dev_config_application
SET status = 'PENDING', edge_persisted_at = NULL, updated_at = NOW(3)
WHERE id = 803;
UPDATE dev_config_application
SET status = 'APPLIED', edge_persisted_at = NOW(3), mcu_synced_at = NOW(3),
    applied_at = NOW(3), updated_at = NOW(3)
WHERE id = 803;
INSERT INTO dev_deployment_runtime_state(
    deployment_id, tenant_id, organization_id, edge_connection_status,
    mcu_link_status, safety_status, aggregate_weight_health, camera_health,
    local_storage_health, clock_sync_health, edge_boot_id,
    edge_software_version, mcu_firmware_version, mcu_boot_id, uart_state,
    uart_protocol_major, uart_protocol_minor, capability_bitmap_hex,
    applied_config_version_no, applied_config_content_sha256,
    applied_mcu_payload_sha256, local_storage_state, clock_state,
    pending_reliable_event_count, lock_version, created_at, updated_at
) VALUES (
    501, 101, 201, 'ONLINE', 'ONLINE', 'SAFE', 'OK', 'OK', 'OK', 'OK',
    7001, 'edge-1.0.0', 'mcu-1.0.0', 101, 'READY', 1, 0,
    '00000000000000ff', 1, UNHEX(REPEAT('11', 32)),
    UNHEX(REPEAT('12', 32)), 'HEALTHY', 'SYNCED', 2, 0, NOW(3), NOW(3)
);
INSERT INTO dev_port_runtime_state(
    port_id, tenant_id, organization_id, deployment_id, delivery_door_state,
    delivery_door_actuator_health, delivery_door_contact_state,
    clean_lock_power_state, clean_solenoid_health, clean_door_inferred_state,
    clean_door_state_basis, weight_sensor_health, infrared_value,
    infrared_sensor_health, smoke_state, smoke_sensor_health, safety_status,
    lock_version, created_at, updated_at
) VALUES
    (
        701, 101, 201, 501, 'CLOSED', 'OK', 'CLOSED', 'DEENERGIZED', 'OK',
        'CLOSED', 'INFERRED_FROM_LOCK_POWER', 'OK', 'CLEAR', 'OK', 'NORMAL',
        'OK', 'SAFE', 0, NOW(3), NOW(3)
    ),
    (
        703, 101, 201, 501, 'OPENING', 'OK', 'UNKNOWN', 'ENERGIZED', 'OK',
        'OPEN', 'INFERRED_FROM_LOCK_POWER', 'OK', 'BLOCKED', 'OK', 'ALARM',
        'OK', 'SAFE', 0, NOW(3), NOW(3)
    );
INSERT INTO dev_delivery_session(
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
    1001, '44444444-4444-4444-8444-444444444444',
    101, 201, 501, 701, 901, 801, 1,
    UNHEX(REPEAT('11', 32)), UNHEX(REPEAT('12', 32)), 802, 2001,
    UNHEX(REPEAT('22', 32)), 3001, 'bag-future-1', 'IN_PROGRESS', 1.0000,
    -1000, 100000, 500, 30000,
    DATE_ADD(NOW(3), INTERVAL 1 MINUTE),
    DATE_ADD(NOW(3), INTERVAL 16 MINUTE),
    0, NOW(3), NOW(3)
);
INSERT INTO dev_device_command(
    id, command_uid, tenant_id, organization_id, deployment_id, command_type,
    delivery_session_id, payload_schema_version, semantic_payload,
    semantic_payload_sha256, physical_state, queued_at, edge_accepted_at,
    physical_started_at, lock_version, created_at, updated_at
) VALUES (
    1051, '99999999-9999-4999-8999-999999999999',
    101, 201, 501, 'START_DELIVERY_SESSION', 1001, 1,
    JSON_OBJECT('sessionUid', '44444444-4444-4444-8444-444444444444'),
    UNHEX(REPEAT('23', 32)), 'PHYSICAL_STARTED',
    NOW(3), NOW(3), NOW(3), 0, NOW(3), NOW(3)
);
INSERT INTO dev_edge_event(
    id, event_uid, tenant_id, organization_id, deployment_id,
    edge_event_sequence, event_type, delivery_class, schema_version,
    target_type, target_stable_key_sha256, device_occurred_at, clock_quality,
    backend_received_at, payload_sha256, canonical_sha256, source_inbox_id,
    created_at
) VALUES (
    1101, '55555555-5555-4555-8555-555555555555',
    101, 201, 501, 1, 'DELIVERY_COMPLETE', 'RELIABLE_FACT', 1,
    'DELIVERY_SESSION', UNHEX(REPEAT('33', 32)), NOW(3), 'SYNCED', NOW(3),
    UNHEX(REPEAT('44', 32)), UNHEX(REPEAT('55', 32)), 9001, NOW(3)
);
INSERT INTO dev_edge_event(
    id, event_uid, tenant_id, organization_id, deployment_id,
    edge_event_sequence, event_type, delivery_class, schema_version,
    target_type, target_stable_key_sha256, device_occurred_at, clock_quality,
    backend_received_at, payload_sha256, canonical_sha256, source_inbox_id,
    created_at
) VALUES (
    1102, '56565656-5656-4565-8565-565656565656',
    101, 201, 501, 2, 'DEVICE_COMMAND_OBSERVED', 'RELIABLE_FACT', 1,
    'DEVICE_COMMAND', UNHEX(REPEAT('34', 32)), NOW(3), 'SYNCED', NOW(3),
    UNHEX(REPEAT('45', 32)), UNHEX(REPEAT('56', 32)), 9002, NOW(3)
);
INSERT INTO dev_physical_result(
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
    1201, 101, 201, 501, 701, 1101, 'DELIVERY_COMPLETE', 1051,
    'START_DELIVERY_SESSION', 1, UNHEX(REPEAT('11', 32)),
    UNHEX(REPEAT('12', 32)), 'DELIVERY', 1001,
    '66666666-6666-4666-8666-666666666666', 'STABLE', 10000, 10000,
    1200, 12, 1, 'OK', NULL, 101, 31,
    '77777777-7777-4777-8777-777777777777', 'TIMEOUT', NULL, 10200,
    5000, 4, 1, 'TIMEOUT', 'WEIGHT_TIMEOUT', 101, 42, NULL,
    'CLOSED', 'OK', 'TERMINAL_WEIGHT_FAILURE', 0, 1, 0, NOW(3)
);
"@
    Invoke-MySql -Database "ecobin_f04_a" -Sql $physicalResultSql | Out-Null

    $postSetupNegativeCases = @(
        @{
            Name = "failed final measurement cannot carry a stable weight"
            Sql = "UPDATE dev_physical_result SET delivery_post_weight_g = 999 WHERE id = 1201;"
        },
        @{
            Name = "measurement UID cannot omit normalized source fields"
            Sql = "UPDATE dev_physical_result SET delivery_post_measurement_status = NULL WHERE id = 1201;"
        },
        @{
            Name = "delivery result requires final closed door"
            Sql = "UPDATE dev_physical_result SET delivery_final_door_state = NULL WHERE id = 1201;"
        },
        @{
            Name = "delivery frozen config rejects wrong digest"
            Sql = @"
UPDATE dev_delivery_session
SET device_config_content_sha256 = UNHEX(REPEAT('ff', 32))
WHERE id = 1001;
"@
        },
        @{
            Name = "configuration APPLIED rejects wrong MCU digest"
            Sql = @"
UPDATE dev_config_application
SET reported_mcu_payload_sha256 = UNHEX(REPEAT('ee', 32))
WHERE id = 803;
"@
        },
        @{
            Name = "physical success requires complete timestamps"
            Sql = @"
INSERT INTO dev_device_command(
    command_uid, tenant_id, organization_id, deployment_id, command_type,
    delivery_session_id, payload_schema_version, semantic_payload,
    semantic_payload_sha256, physical_state, lock_version, created_at, updated_at
) VALUES (
    'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
    101, 201, 501, 'START_DELIVERY_SESSION', 1001, 1, JSON_OBJECT(),
    UNHEX(REPEAT('ab', 32)), 'PHYSICAL_SUCCEEDED', 0, NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "runtime port rejects legacy MOVING state"
            Sql = "UPDATE dev_port_runtime_state SET delivery_door_state = 'MOVING' WHERE port_id = 701;"
        },
        @{
            Name = "runtime projection rejects partial snapshot triple"
            Sql = @"
UPDATE dev_deployment_runtime_state
SET clock_state = NULL
WHERE deployment_id = 501;
"@
        },
        @{
            Name = "F-10 config version exceeds JSON safe integer"
            Sql = @"
INSERT INTO dev_config_version(
    id, tenant_id, organization_id, deployment_id, version_no, schema_version,
    device_display_name, edge_heartbeat_interval_ms,
    edge_heartbeat_miss_threshold, mcu_heartbeat_interval_ms,
    mcu_heartbeat_miss_threshold, door_close_retry_limit,
    continue_delivery_wait_ms, negative_weight_threshold_g,
    delivery_auto_close_ms, weight_measurement_timeout_ms,
    clean_solenoid_pulse_ms, smoke_monitoring_enabled, content_sha256,
    mcu_payload_sha256, publication_source, published_at, created_at
) VALUES (
    805, 101, 201, 501, 9007199254740992, 1, 'Invalid version',
    10000, 3, 1000, 3, 2, 30000, 500, 60000, 5000, 1000, 1,
    UNHEX(REPEAT('15', 32)), UNHEX(REPEAT('16', 32)),
    'SYSTEM', NOW(3), NOW(3)
);
"@
        },
        @{
            Name = "F-10 device u32 exceeds maximum"
            Sql = "UPDATE dev_config_version SET delivery_auto_close_ms = 4294967296 WHERE id = 804;"
        },
        @{
            Name = "F-10 port u32 exceeds maximum"
            Sql = "UPDATE dev_port_config_snapshot SET configured_full_weight_g = 4294967296 WHERE id = 804;"
        },
        @{
            Name = "F-10 port i32 falls below minimum"
            Sql = "UPDATE dev_port_config_snapshot SET weight_minimum_g = -2147483649 WHERE id = 804;"
        },
        @{
            Name = "F-10 unit price ten-thousandths exceeds u32"
            Sql = "UPDATE dev_port_config_snapshot SET unit_price_yuan_per_kg = 429496.7296 WHERE id = 804;"
        },
        @{
            Name = "F-10 port display name exceeds 32 characters"
            Sql = "UPDATE dev_port_config_snapshot SET display_name = '123456789012345678901234567890123' WHERE id = 804;"
        },
        @{
            Name = "F-10 port display name rejects surrounding whitespace"
            Sql = "UPDATE dev_port_config_snapshot SET display_name = '1234567890123456789012345678901 ' WHERE id = 804;"
        },
        @{
            Name = "F-10 command observation rejects non-symbolic error code"
            Sql = @"
INSERT INTO dev_device_command_event(
    tenant_id, organization_id, deployment_id, edge_event_id, edge_event_type,
    command_id, delivery_session_id, observed_command_type, observation_stage,
    mcu_command_uid, error_code, created_at
) VALUES (
    101, 201, 501, 1102, 'DEVICE_COMMAND_OBSERVED',
    1051, 1001, 'START_DELIVERY_SESSION', 'REJECTED',
    NULL, 'bad-error', NOW(3)
);
"@
        }
    )
    foreach ($case in $postSetupNegativeCases) {
        Expect-MySqlFailure -Database "ecobin_f04_a" `
            -Sql $case.Sql -CaseName $case.Name
    }

    $emptyRows = Invoke-MySql -Database "ecobin_f04_b" -Sql @"
SELECT COALESCE(SUM(table_rows), 0)
FROM information_schema.tables
WHERE table_schema = DATABASE();
"@
    if ($emptyRows -ne "0") {
        throw "the untouched validation database is not empty: $emptyRows rows"
    }

    & docker exec -e "MYSQL_PWD=$rootPassword" $containerName sh -lc `
        "mysql -uroot --database=ecobin_f04_a < /tmp/p0-migration/V1__p0_epoch_and_iam_core.sql" `
        *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "strict reinstall into a non-empty/partial schema unexpectedly succeeded"
    }

    [pscustomobject]@{
        mysqlVersion = [string]$version
        imageId = $actualImageId
        migrationFiles = $migrationFiles.Count
        tables = [int]$shapeParts[0]
        foreignKeys = [int]$shapeParts[1]
        uniqueConstraints = [int]$shapeParts[2]
        checks = [int]$shapeParts[3]
        nonUniqueIndexes = [int]$shapeParts[4]
        explicitlyIndexedForeignKeys = $explicitForeignKeyIndexCount
        directOrganizationForeignKeyTables = $organizationScopedTables.Count
        structureFingerprintSha256 = $fingerprint
        structuresEqual = $true
        crossOrganizationNegativeCases = 3
        preSetupNegativeCases = $negativeCases.Count
        postSetupNegativeCases = $postSetupNegativeCases.Count
        deliveryTerminalWeightFailurePositiveCase = $true
        configurationFailureResyncAppliedPositiveCase = $true
        perPortRuntimeProjectionPositiveCase = $true
        f10BoundaryValuesPositiveCase = $true
        strictReinstallRejected = $true
    } | ConvertTo-Json
}
finally {
    if ($containerStarted) {
        & docker rm --force $containerName *> $null
    }
}

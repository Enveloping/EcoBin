[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$catalogPath = Join-Path $PSScriptRoot "../h02-runtime-grants.psd1"
$catalog = Import-PowerShellDataFile -LiteralPath $catalogPath
$provisionPath = Join-Path $PSScriptRoot "../provision-h02-target.ps1"
$provisionSource = Get-Content -LiteralPath $provisionPath -Raw
$f07BootstrapPath = Join-Path $PSScriptRoot "../verify-f07-bootstrap.ps1"
$f07BootstrapSource = Get-Content -LiteralPath $f07BootstrapPath -Raw

if ($catalog.CatalogVersion -ne 35) {
    throw "H-02 runtime grant catalog must be V35 for the V67 target"
}

if ($provisionSource -notmatch 'Get-H02MigrationProvenance' -or
        $provisionSource -notmatch 'migrationManifestSha256' -or
        $provisionSource -notmatch 'migrationSourceCommit') {
    throw "H-02 provisioning must reject dirty migrations and record provenance"
}

if ($provisionSource -notmatch '\$tables\.Count -ne 131' -or
        $provisionSource -notmatch 'Expected 131 domain tables') {
    throw "H-02 provisioning must enforce the V67 131-table shape"
}
if ($provisionSource -notmatch
        'id, asset_uid, device_public_code, hardware_sn' -or
        $provisionSource -notmatch
        'lifecycle_status, created_at, updated_at' -or
        $provisionSource -notmatch
        'GRANT INSERT ON \$database\.dev_device_management_profile' -or
        $provisionSource -notmatch
        'transition_source_event_uid, transitioned_at' -or
        $provisionSource -notmatch
        'GRANT INSERT ON \$database\.dev_device_compatibility_projection' -or
        $provisionSource -notmatch
        'asset_id, architecture_generation, management_state_sequence') {
    throw "V63 triggers are missing their exact definer grants"
}
$releaseControlTriggerColumns = @(
    "release_uid"
    "create_operation_uid"
    "version_name"
    "release_sequence"
    "package_object_key"
    "signature_object_key"
    "package_sha256"
    "package_size"
    "signature_sha256"
    "signature_bytes"
    "signing_key_id"
    "declaration_id"
    "verified_by_platform_admin_id"
    "verified_at"
    "created_by_platform_admin_id"
    "created_at"
)
foreach ($column in $releaseControlTriggerColumns) {
    $expectedGrant =
        '"COLUMN|$DatabaseName|dev_edge_software_release_control|' +
        $column + '|SELECT"'
    if (-not $provisionSource.Contains($expectedGrant)) {
        throw "V64 release-control trigger grant is missing $column"
    }
}
if ($provisionSource -notmatch
        '(?s)GRANT SELECT \(\s*release_uid, create_operation_uid, ' +
        'version_name, release_sequence,.*?' +
        '\) ON \$database\.dev_edge_software_release_control\s*' +
        "TO 'ecobin_trigger_definer'@'%';" -or
    $f07BootstrapSource -notmatch
        '(?s)GRANT SELECT \(\s*release_uid, create_operation_uid, ' +
        'version_name, release_sequence,.*?' +
        '\) ON ``\$database``\.dev_edge_software_release_control\s*' +
        "TO 'ecobin_trigger_definer'@'%';") {
    throw "V64 trigger must receive exact release-control column reads"
}
if ($provisionSource -notmatch (
        '(?s)if \(-not \$skipMigration\) \{.*?' +
        'Invoke-FlywayMigration -Target 67.*?' +
        '\r?\n    \}\r?\n\r?\n' +
        '    # Converge the trigger definer even when.*?' +
        '    Invoke-RootSql -Sql @"\r?\n' +
        'REVOKE IF EXISTS SELECT \(.*?' +
        '\$database\.iam_organization_miniapp.*?' +
        'REVOKE IF EXISTS SELECT \(.*?' +
        'organization_miniapp_id, openid, ' +
        'registered_via_deployment_id.*?' +
        'GRANT TRIGGER ON \$database\.\*\r?\n' +
        "\s+TO 'ecobin_trigger_definer'@'%';\r?\n" +
        'GRANT SELECT \(')) {
    throw "V67 resumed environments must revoke legacy and converge grants"
}
if ($provisionSource -notmatch
        '\$expectedTriggerDefinerGrants = @\(' -or
        $provisionSource -notmatch
        'information_schema\.SCHEMA_PRIVILEGES' -or
        $provisionSource -notmatch
        'information_schema\.TABLE_PRIVILEGES' -or
        $provisionSource -notmatch
        'information_schema\.COLUMN_PRIVILEGES' -or
        $provisionSource -notmatch
        '(?s)Compare-Object.*?-ReferenceObject ' +
        '\$expectedTriggerDefinerGrants') {
    throw "H-02 must reject any unexpected final trigger definer grant"
}
$legacyGrantIndex = $provisionSource.IndexOf(
    "V9 creates the legacy immutable mini-program triggers")
$target36Index = $provisionSource.IndexOf(
    "Invoke-FlywayMigration -Target 36")
$v36GrantIndex = $provisionSource.IndexOf(
    "V36 replaces the trigger shapes")
$target39Index = $provisionSource.IndexOf(
    "Invoke-FlywayMigration -Target 39")
$v39GrantIndex = $provisionSource.IndexOf(
    "V39 installs the current channel/account trigger shapes")
$target67Index = $provisionSource.IndexOf(
    "Invoke-FlywayMigration -Target 67")
if (
    $legacyGrantIndex -lt 0 -or
    $target36Index -le $legacyGrantIndex -or
    $v36GrantIndex -le $target36Index -or
    $target39Index -le $v36GrantIndex -or
    $v39GrantIndex -le $target39Index -or
    $target67Index -le $v39GrantIndex
) {
    throw "H-02 must grant each historical trigger before data backfills"
}
if ($provisionSource -notmatch (
        '(?s)else \{\s*' +
        '# The batch can create and unlock the schema owner.*?' +
        '\$resumeSchemaOwnerUnlocked = \$true\s*' +
        'Invoke-RootSql -Sql @"\s*CREATE DATABASE') -or
    $provisionSource -notmatch
        '\$ownerAccountCount = \[int\]\(Invoke-RootSql') {
    throw "Fresh-install failures must idempotently re-lock the schema owner"
}
if ($f07BootstrapSource -notmatch
        'id, created_at, updated_at' -or
        $f07BootstrapSource -notmatch
        'V63-TRIGGER-PROBE' -or
        $f07BootstrapSource -notmatch
        'deviceAssetManagementTriggerReady\s*=\s*\$true') {
    throw "F-07 must execute the V63 new-asset trigger with final definer grants"
}
if ($provisionSource -notmatch 'Invoke-FlywayMigration -Target 67') {
    throw "H-02 provisioning must migrate through V67"
}
if ($provisionSource -notmatch '\$historyCount -ne 67' -or
        $provisionSource -notmatch
            'Expected sixty-seven successful Flyway migrations') {
    throw "H-02 provisioning must verify all 67 migrations"
}
if ($provisionSource -notmatch
        '\$existingDomainTableCount -eq 131\s+-and\s+' +
        '\$existingHistoryCount -eq 67\s+-and\s+' +
        '\$existingMaxVersion -eq 67' -or
        $provisionSource -notmatch '\$existingMaxVersion -lt 67' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 60\s+-and\s+' +
        '\$existingMaxVersion -eq 60' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 61\s+-and\s+' +
        '\$existingMaxVersion -eq 61' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 62\s+-and\s+' +
        '\$existingMaxVersion -eq 62' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 63\s+-and\s+' +
        '\$existingMaxVersion -eq 63' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 64\s+-and\s+' +
        '\$existingMaxVersion -eq 64' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 65\s+-and\s+' +
        '\$existingMaxVersion -eq 65' -or
        $provisionSource -notmatch
        '\$existingHistoryCount -eq 66\s+-and\s+' +
        '\$existingMaxVersion -eq 66') {
    throw "H-02 migrated resume must recognize V60-V66 and target V67"
}
if ($f07BootstrapSource -notmatch '\$tableCount -ne 132' -or
        $f07BootstrapSource -notmatch
            'correct target must contain 131 domain tables plus Flyway history' -or
        $f07BootstrapSource -notmatch 'targetVersion\s*=\s*67' -or
        $f07BootstrapSource -notmatch 'domainTables\s*=\s*131' -or
        $f07BootstrapSource -notmatch
            'correctV67Ready\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'businessReleaseValidationV65\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'businessUpdateCancellationV66\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'imageBridgeBaselineV67\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'mcuRemoteUpdateCapabilityV60\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'externalRequestIdV61\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'factoryProgressTaskIndexV62\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'ix_ops_task_factory_progress' -or
        $f07BootstrapSource -notmatch
            'bagLabelBatchLimit500\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'clockRecoveryV57UpgradeConverged\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'authorizationNullableStateFactsRejected\s*=\s*\$true' -or
        $f07BootstrapSource -notmatch
            'sealedClockQualityRequired\s*=\s*\$true' -or
        $f07BootstrapSource -match 'correct V56|correctV56Ready') {
    throw (
        "F-07 bootstrap verification must report the V67 shape: " +
        "131 domain tables plus Flyway history"
    )
}
if ($provisionSource -notmatch
        'sha256:9cffaceb9b62d4280247acdb2324b380d2b36208ae34dfe9f0afb62eeaf70f08' -or
        $provisionSource -notmatch '\$RemoteHost\.Length -gt 0') {
    throw "H-02 provisioning must pin the audited Linux production image ID"
}
if ($provisionSource -notmatch '\[switch\]\$AllowExistingBusinessRows') {
    throw "H-02 production resume must explicitly opt in to business rows"
}
if ($provisionSource -notmatch
        '\$AllowExistingBusinessRows -and\s+-not \$ResumeExistingMigratedEnvironment') {
    throw "Business-row opt-in must be limited to migrated resume mode"
}
if ($provisionSource -notmatch
        '\$businessRowCount -ne 0 -and\s+-not \$AllowExistingBusinessRows') {
    throw "H-02 must retain the default empty-target business-row guard"
}
if ($provisionSource -notmatch
        '"dev_runtime_snapshot_policy"' -or
        $provisionSource -notmatch
        '"dev_remote_support_port_slot"') {
    throw "H-02 must exclude seeded policy and port-slot reference rows from business-row checks"
}

foreach ($entry in $catalog.UpdateColumns.GetEnumerator()) {
    $columns = @($entry.Value)
    if ($columns.Count -eq 0) {
        throw "Runtime update grant list is empty for $($entry.Key)"
    }
    if (@($columns | Sort-Object -Unique).Count -ne $columns.Count) {
        throw "Runtime update grant list contains duplicates for $($entry.Key)"
    }
}
$attemptColumns = @($catalog.UpdateColumns.ops_task_attempt)
if ($attemptColumns.Count -ne 13 -or
        $attemptColumns -notcontains "external_request_id") {
    throw (
        "V61 ops_task_attempt UPDATE grants must contain exactly " +
        "the thirteen reviewed result columns"
    )
}

$firmwareGrantShape = @{
    dev_mcu_firmware_release = @(
        "release_status"
        "promoted_by_platform_admin_id"
        "promoted_at"
        "updated_at"
    )
    dev_mcu_firmware_rollout = @(
        "rollout_status"
        "current_wave_no"
        "promoted_by_platform_admin_id"
        "promoted_at"
        "stopped_by_platform_admin_id"
        "stopped_at"
        "stop_reason"
        "lock_version"
        "updated_at"
    )
    dev_mcu_firmware_deployment = @(
        "deployment_status"
        "command_uid"
        "reliable_task_uid"
        "edge_update_uid"
        "target_attempt_count"
        "rollback_attempt_count"
        "installed_firmware_version"
        "installed_firmware_version_code"
        "installed_firmware_identity_hex"
        "error_code"
        "last_event_uid"
        "queued_at"
        "completed_at"
        "lock_version"
        "updated_at"
    )
}
foreach ($entry in $firmwareGrantShape.GetEnumerator()) {
    $actual = @($catalog.UpdateColumns[$entry.Key])
    if (@(Compare-Object @($entry.Value) $actual).Count -ne 0) {
        throw "MCU firmware update grants differ for $($entry.Key)"
    }
}

$softwareCompatibilityGrantShape = @{
    dev_device_management_profile = @(
        "architecture_generation"
        "transition_source_event_uid"
        "transitioned_at"
        "lock_version"
        "updated_at"
    )
    dev_device_compatibility_projection = @(
        "architecture_generation"
        "latest_software_fact_id"
        "source_event_uid"
        "management_state_sequence"
        "compatibility_status"
        "business_admission_status"
        "primary_reason_code"
        "primary_reason_message"
        "reasons_json"
        "capabilities_json"
        "observed_at"
        "received_at"
        "lock_version"
        "updated_at"
    )
}
foreach ($entry in $softwareCompatibilityGrantShape.GetEnumerator()) {
    $actual = @($catalog.UpdateColumns[$entry.Key])
    if (@(Compare-Object @($entry.Value) $actual).Count -ne 0) {
        throw "Device software compatibility UPDATE grants differ for $($entry.Key)"
    }
}
if ($catalog.ReadOnlyTables -contains "dev_edge_software_release" -or
        $catalog.UpdateColumns.ContainsKey("dev_edge_software_release")) {
    throw "V67 runtime must insert but never update immutable release declarations"
}
foreach ($table in @(
    "dev_edge_software_release_sequence",
    "dev_edge_software_release_control",
    "dev_edge_software_rollout",
    "dev_edge_software_deployment"
)) {
    if (-not $catalog.UpdateColumns.ContainsKey($table)) {
        throw "V67 runtime UPDATE grant is missing $table"
    }
}
$releaseControlUpdateColumns = @(
    "release_status"
    "verification_operation_uid"
    "package_sha256"
    "package_size"
    "signature_sha256"
    "signature_bytes"
    "signing_key_id"
    "declaration_id"
    "verification_error_code"
    "verification_error_message"
    "verified_by_platform_admin_id"
    "verified_at"
    "approved_by_platform_admin_id"
    "approved_at"
    "suspended_by_platform_admin_id"
    "suspended_at"
    "suspension_reason"
    "retired_by_platform_admin_id"
    "retired_at"
    "retirement_reason"
    "artifact_uploaded_at"
    "lock_version"
    "updated_at"
)
if (@(Compare-Object $releaseControlUpdateColumns `
            @($catalog.UpdateColumns.dev_edge_software_release_control)).Count -ne 0) {
    throw "V67 business release control UPDATE grants differ"
}
$rolloutUpdateColumns = @(
    "rollout_status"
    "current_wave_no"
    "stopped_by_platform_admin_id"
    "stopped_at"
    "stop_reason"
    "lock_version"
    "updated_at"
)
if (@(Compare-Object $rolloutUpdateColumns `
            @($catalog.UpdateColumns.dev_edge_software_rollout)).Count -ne 0) {
    throw "V67 business rollout UPDATE grants differ"
}
$deploymentUpdateColumns = @(
    "deployment_status"
    "command_uid"
    "reliable_task_uid"
    "edge_update_uid"
    "control_sequence"
    "cancel_command_uid"
    "cancel_reliable_task_uid"
    "cancel_control_sequence"
    "cancellation_status"
    "cancel_reason"
    "cancel_requested_by_platform_admin_id"
    "cancel_requested_at"
    "cancel_result_at"
    "stage_sequence"
    "business_admission_state"
    "download_attempt_count"
    "target_attempt_count"
    "rollback_attempt_count"
    "installed_release_uid"
    "installed_version_name"
    "installed_release_sequence"
    "installed_package_sha256"
    "database_restored"
    "error_code"
    "last_event_uid"
    "queued_at"
    "completed_at"
    "lock_version"
    "updated_at"
)
if (@(Compare-Object $deploymentUpdateColumns `
            @($catalog.UpdateColumns.dev_edge_software_deployment)).Count -ne 0) {
    throw "V67 business deployment UPDATE grants differ"
}
if ($catalog.ReadOnlyTables -contains
            "dev_edge_software_deployment_progress" -or
        $catalog.SlotTables -contains
            "dev_edge_software_deployment_progress" -or
        $catalog.UpdateColumns.ContainsKey(
            "dev_edge_software_deployment_progress") -or
        $catalog.PendingUpdateTables -contains
            "dev_edge_software_deployment_progress") {
    throw "Business deployment progress must retain SELECT/INSERT-only grants"
}
if ($catalog.ReadOnlyTables -contains
            "dev_edge_software_deployment_cancel_result" -or
        $catalog.SlotTables -contains
            "dev_edge_software_deployment_cancel_result" -or
        $catalog.UpdateColumns.ContainsKey(
            "dev_edge_software_deployment_cancel_result") -or
        $catalog.PendingUpdateTables -contains
            "dev_edge_software_deployment_cancel_result") {
    throw "Business cancellation results must retain SELECT/INSERT-only grants"
}
if ($catalog.ReadOnlyTables -contains "dev_device_software_fact" -or
        $catalog.SlotTables -contains "dev_device_software_fact" -or
        $catalog.UpdateColumns.ContainsKey("dev_device_software_fact") -or
        $catalog.PendingUpdateTables -contains "dev_device_software_fact") {
    throw "Device software facts must retain default SELECT/INSERT-only grants"
}
$assetFirmwareColumns = @(
    "mcu_firmware_version_code"
    "mcu_firmware_identity_hex"
    "mcu_fixed_frame_revision"
)
foreach ($column in $assetFirmwareColumns) {
    if (@($catalog.UpdateColumns.dev_device_asset) -notcontains $column) {
        throw "MCU firmware asset update grant is missing $column"
    }
}

$removedDeviceTables = @(
    "dev_device_deployment"
    "dev_asset_tenant_allocation"
    "dev_asset_active_deployment"
    "dev_asset_active_tenant_allocation"
    "dev_deployment_runtime_state"
)
$requiredDeleteTables = @(
    "dev_device_occupancy"
    "rec_bag_current_occupancy"
    "rec_port_clean_restart_interlock"
    "fund_active_withdrawal"
    "rec_bag_label_batch"
)
if (@(Compare-Object $requiredDeleteTables @($catalog.SlotTables)).Count -ne 0) {
    throw "V46 runtime DELETE grants do not match the reviewed catalog"
}
$catalogTables = @(
    $catalog.ReadOnlyTables
    $catalog.SlotTables
    $catalog.UpdateColumns.Keys
    $catalog.PendingUpdateTables
)
foreach ($removedTable in $removedDeviceTables) {
    if ($catalogTables -contains $removedTable) {
        throw "V36 removed table remains in runtime grants: $removedTable"
    }
}

if ($catalog.UpdateColumns.ContainsKey("iam_organization_miniapp")) {
    throw "V39 removed iam_organization_miniapp remains in runtime grants"
}
$channelColumns = @($catalog.UpdateColumns.iam_miniapp_channel)
if ($channelColumns -contains "entry_base_url") {
    throw "V41 removed iam_miniapp_channel.entry_base_url remains in runtime grants"
}
$bindingColumns = @(
    $catalog.UpdateColumns.iam_organization_miniapp_binding
)
if (@(Compare-Object @("lock_version") $bindingColumns).Count -ne 0) {
    throw "V39 organization-channel binding locking grant is not minimal"
}
$subjectColumns = @($catalog.UpdateColumns.iam_wechat_subject)
if (@(Compare-Object @("lock_version") $subjectColumns).Count -ne 0) {
    throw "V39 WeChat subject locking grant is not minimal"
}
$organizationUserColumns = @(
    $catalog.UpdateColumns.iam_organization_user
)
if ($organizationUserColumns -notcontains "last_login_at") {
    throw "V49 organization-user last-login runtime grant is missing"
}

$platformAdminColumns = @(
    $catalog.UpdateColumns.iam_platform_admin
)
if ($platformAdminColumns -notcontains "deleted_at") {
    throw "V50 platform-administrator logical-delete runtime grant is missing"
}
if ($platformAdminColumns -contains "admin_kind") {
    throw "V50 platform-administrator kind must remain immutable at runtime"
}

$factoryBagColumns = @(
    $catalog.UpdateColumns.dev_factory_installed_bag
)
$factoryBagRequiredColumns = @(
    "bag_code"
    "installation_source"
    "installed_by_factory_operator_id"
    "label_item_id"
    "tare_status"
    "last_failure_code"
    "installed_at"
    "updated_at"
)
if (@(
        Compare-Object $factoryBagRequiredColumns $factoryBagColumns
    ).Count -ne 0) {
    throw (
        "Factory-installed bag runtime UPDATE grants must be limited to " +
        "factory label correction and the automatic tare projection"
    )
}

$v52UpdateGrants = @{
    dev_device_enrollment_challenge = @("status", "consumed_at")
    dev_device_enrollment = @(
        "status", "asset_id", "onenet_device_id", "encrypted_response",
        "response_nonce", "response_sha256", "failure_code",
        "attempt_count", "next_attempt_at", "completed_at", "updated_at"
    )
    iam_platform_admin_maintenance_ssh_key = @(
        "revoked_at", "revoked_reason", "lock_version", "updated_at"
    )
    iam_factory_operator = @(
        "display_name", "enabled", "auth_version", "lock_version",
        "updated_at"
    )
    iam_factory_operator_binding_intent = @(
        "status", "consumed_at", "consumed_wechat_subject_id"
    )
    iam_factory_operator_miniapp_binding = @(
        "status", "revoked_at", "revocation_reason", "lock_version",
        "updated_at"
    )
    iam_factory_operator_miniapp_session = @(
        "revoked_at", "revocation_reason"
    )
    rec_bag_label_claim = @("released_at", "release_reason")
    dev_remote_support_port_slot = @("lock_version")
    dev_remote_support_session = @(
        "state", "close_operation_uid", "close_request_sha256",
        "close_command_uid", "device_reported_state", "server_lease_state",
        "failure_code", "failure_detail", "certificate_serial",
        "certificate_text", "certificate_sha256", "certificate_issued_at",
        "opened_at", "close_requested_at", "closed_at", "lease_released_at",
        "lock_version", "updated_at"
    )
}
foreach ($entry in $v52UpdateGrants.GetEnumerator()) {
    $actual = @($catalog.UpdateColumns[$entry.Key])
    if (@(Compare-Object @($entry.Value) $actual).Count -ne 0) {
        throw "V52 runtime UPDATE grant mismatch for $($entry.Key)"
    }
}

$v52AssetColumns = @($catalog.UpdateColumns.dev_device_asset)
foreach ($requiredColumn in @(
        "registration_source",
        "factory_bag_revision",
        "factory_bag_set_sha256"
    )) {
    if ($v52AssetColumns -notcontains $requiredColumn) {
        throw "V52 device-asset runtime UPDATE grant is missing $requiredColumn"
    }
}
if ($catalog.UpdateColumns.ContainsKey("dev_port")) {
    throw (
        "dev_port topology is immutable; callers must lock a mutable " +
        "device or capacity root instead of widening runtime UPDATE grants"
    )
}

$cleanOperationRequiredColumns = @(
    "pre_unlock_weight_status"
    "pre_unlock_weight_g"
    "pre_unlock_weight_fault_code"
    "status"
    "edge_saved_confirmed"
    "first_unlock_may_have_executed"
    "clean_lock_deenergized_confirmed"
    "cleaner_physical_close_confirmed"
    "edge_saved_at"
    "first_possible_unlock_at"
    "solenoid_powered_off_at"
    "cleaner_confirmed_closed_at"
    "execution_deadline_at"
    "pre_unlock_end_requested_at"
    "recovery_requested_at"
    "reopen_count"
    "recovery_count"
    "completion_record_id"
    "ended_at"
    "end_reason"
    "lock_version"
    "updated_at"
)
$cleanOperationColumns = @(
    $catalog.UpdateColumns.rec_clean_operation
)
if (@(
        Compare-Object `
            $cleanOperationRequiredColumns `
            $cleanOperationColumns
    ).Count -ne 0) {
    throw (
        "rec_clean_operation runtime UPDATE grants must include the " +
        "complete reviewed state-machine projection"
    )
}

$assetRequiredColumns = @(
    "registration_source"
    "factory_bag_revision"
    "factory_bag_set_sha256"
    "mcu_firmware_version_code"
    "mcu_firmware_identity_hex"
    "mcu_fixed_frame_revision"
    "mcu_remote_update_capable"
    "installation_display_name"
    "installation_address"
    "installation_latitude"
    "installation_longitude"
    "installation_profile_version"
    "installation_updated_by_organization_user_id"
    "installation_updated_at"
    "tenant_id"
    "tenant_assigned_at"
    "organization_id"
    "organization_assigned_at"
    "acceptance_status"
    "acceptance_generation"
    "accepted_at"
    "acceptance_evidence_sha256"
    "last_acceptance_evaluated_at"
    "acceptance_failure_json"
    "lifecycle_status"
    "disabled_at"
    "disable_reason"
    "retired_at"
    "retirement_reason"
    "control_version"
    "updated_at"
)
$assetColumns = @($catalog.UpdateColumns.dev_device_asset)
if (@(Compare-Object $assetRequiredColumns $assetColumns).Count -ne 0) {
    throw "dev_device_asset runtime UPDATE grants do not match V60"
}

$factorySealAuthorizationRequiredColumns = @(
    "authorization_status"
    "acknowledged_at"
    "cancelled_at"
    "cancellation_reason"
    "completion_event_uid"
    "completion_payload_sha256"
    "image_release_id"
    "image_release_sha256"
    "factory_report_sha256"
    "authorization_binding_sha256"
    "operator_confirmation_uid"
    "completion_clock_quality"
    "sealed_at"
    "cleanup_completed_at"
    "completion_received_at"
    "updated_at"
)
$factorySealAuthorizationColumns = @(
    $catalog.UpdateColumns.dev_factory_seal_authorization
)
if (@(
        Compare-Object `
            $factorySealAuthorizationRequiredColumns `
            $factorySealAuthorizationColumns
    ).Count -ne 0) {
    throw (
        "dev_factory_seal_authorization runtime UPDATE grants must expose " +
        "only the reviewed acknowledgement, cancellation, and terminal " +
        "completion projection"
    )
}
if ($catalog.ReadOnlyTables -contains "dev_factory_seal_authorization" -or
        $catalog.SlotTables -contains "dev_factory_seal_authorization") {
    throw (
        "dev_factory_seal_authorization requires INSERT and narrow UPDATE, " +
        "but must never receive DELETE"
    )
}
$rolloutRequiredColumns = @(
    "rollout_uid"
    "base_url_sha256"
    "rollout_status"
    "next_asset_id"
    "started_at"
    "completed_at"
    "updated_at"
)
$rolloutColumns = @(
    $catalog.UpdateColumns.dev_device_entry_url_rollout
)
if (@(Compare-Object $rolloutRequiredColumns $rolloutColumns).Count -ne 0) {
    throw "dev_device_entry_url_rollout grants do not match V42"
}
$removedQrColumns = @(
    "miniapp_qr_status"
    "miniapp_qr_object_key"
    "miniapp_qr_generated_at"
)
foreach ($removedQrColumn in $removedQrColumns) {
    if ($assetColumns -contains $removedQrColumn) {
        throw "V39 removed QR column remains in runtime grants: $removedQrColumn"
    }
}

if (-not $catalog.UpdateColumns.ContainsKey("dev_device_runtime_state")) {
    throw "V36 dev_device_runtime_state runtime UPDATE grants are missing"
}

$runtimePolicyRequiredColumns = @(
    "policy_version"
    "fallback_interval_ms"
    "rollout_uid"
    "rollout_status"
    "next_asset_id"
    "target_asset_count"
    "processed_asset_count"
    "published_asset_count"
    "publication_source"
    "updated_by_platform_admin_id"
    "change_reason"
    "started_at"
    "completed_at"
    "lock_version"
    "updated_at"
)
$runtimePolicyColumns = @(
    $catalog.UpdateColumns.dev_runtime_snapshot_policy
)
if (@(
        Compare-Object `
            $runtimePolicyRequiredColumns `
            $runtimePolicyColumns
    ).Count -ne 0) {
    throw "V46 runtime snapshot policy UPDATE grants are not minimal"
}

$opsAlertColumns = @($catalog.UpdateColumns.ops_alert)
if ($opsAlertColumns -notcontains "source_key") {
    throw "ops_alert.source_key is required by the reliable-task alert projection"
}

$governanceIdempotencyColumns = @(
    $catalog.UpdateColumns.ops_governance_idempotency
)
$governanceIdempotencyRequiredColumns = @(
    "status"
    "result_resource_uid"
    "result_state"
    "result_version"
    "completed_at"
    "updated_at"
)
if (@(
        Compare-Object `
            $governanceIdempotencyRequiredColumns `
            $governanceIdempotencyColumns
    ).Count -ne 0) {
    throw (
        "Governance idempotency runtime UPDATE grants must keep request " +
        "identity immutable and expose only completion projection columns"
    )
}

if ($catalog.UpdateColumns.ContainsKey(
        "rec_organization_delivery_config")) {
    throw (
        "rec_organization_delivery_config is immutable and must not receive " +
        "runtime UPDATE grants"
    )
}

$merchantBindingAllowedColumns = @(
    "status"
    "disabled_at"
    "lock_version"
    "updated_at"
)
$merchantBindingColumns = @(
    $catalog.UpdateColumns.fund_miniapp_merchant_binding
)
$unexpectedMerchantBindingColumns = @(
    $merchantBindingColumns |
        Where-Object { $merchantBindingAllowedColumns -notcontains $_ }
)
$missingMerchantBindingColumns = @(
    $merchantBindingAllowedColumns |
        Where-Object { $merchantBindingColumns -notcontains $_ }
)
if ($missingMerchantBindingColumns.Count -ne 0 -or
        $unexpectedMerchantBindingColumns.Count -ne 0) {
    throw (
        "fund_miniapp_merchant_binding runtime UPDATE grants must exactly " +
        "match the frozen projection columns; missing=" +
        ($missingMerchantBindingColumns -join ", ") + "; unexpected=" +
        ($unexpectedMerchantBindingColumns -join ", ")
    )
}

$transferAuthorizationAllowedColumns = @(
    "authorization_id"
    "local_state"
    "channel_state"
    "package_info"
    "package_expires_at"
    "last_api_error_code"
    "close_reason"
    "state_conflict"
    "submitted_at"
    "channel_created_at"
    "confirmation_deadline_at"
    "authorized_at"
    "closed_at"
    "channel_updated_at"
    "lock_version"
    "updated_at"
)
$transferAuthorizationColumns = @(
    $catalog.UpdateColumns.fund_wechat_transfer_authorization
)
$unexpectedTransferAuthorizationColumns = @(
    $transferAuthorizationColumns |
        Where-Object {
            $transferAuthorizationAllowedColumns -notcontains $_
        }
)
$missingTransferAuthorizationColumns = @(
    $transferAuthorizationAllowedColumns |
        Where-Object { $transferAuthorizationColumns -notcontains $_ }
)
if ($missingTransferAuthorizationColumns.Count -ne 0 -or
        $unexpectedTransferAuthorizationColumns.Count -ne 0) {
    throw (
        "fund_wechat_transfer_authorization runtime UPDATE grants must " +
        "exclude immutable request and recipient snapshots; missing=" +
        ($missingTransferAuthorizationColumns -join ", ") +
        "; unexpected=" +
        ($unexpectedTransferAuthorizationColumns -join ", ")
    )
}

Write-Output "h02-runtime-grants-contract=PASS"

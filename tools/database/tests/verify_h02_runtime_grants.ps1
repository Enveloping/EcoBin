[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$catalogPath = Join-Path $PSScriptRoot "../h02-runtime-grants.psd1"
$catalog = Import-PowerShellDataFile -LiteralPath $catalogPath
$provisionPath = Join-Path $PSScriptRoot "../provision-h02-target.ps1"
$provisionSource = Get-Content -LiteralPath $provisionPath -Raw

if ($catalog.CatalogVersion -ne 28) {
    throw "H-02 runtime grant catalog must be V28 for MCU firmware rollout"
}

if ($provisionSource -notmatch 'Get-H02MigrationProvenance' -or
        $provisionSource -notmatch 'migrationManifestSha256' -or
        $provisionSource -notmatch 'migrationSourceCommit') {
    throw "H-02 provisioning must reject dirty migrations and record provenance"
}

if ($provisionSource -notmatch '\$tables\.Count -ne 118' -or
        $provisionSource -notmatch 'Expected 118 domain tables') {
    throw "H-02 provisioning must enforce the V55 118-table shape"
}
if ($provisionSource -notmatch 'Invoke-FlywayMigration -Target 55') {
    throw "H-02 provisioning must migrate through V55"
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
    throw "dev_device_asset runtime UPDATE grants do not match V55"
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

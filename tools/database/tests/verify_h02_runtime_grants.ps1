[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$catalogPath = Join-Path $PSScriptRoot "../h02-runtime-grants.psd1"
$catalog = Import-PowerShellDataFile -LiteralPath $catalogPath
$provisionPath = Join-Path $PSScriptRoot "../provision-h02-target.ps1"
$provisionSource = Get-Content -LiteralPath $provisionPath -Raw

if ($provisionSource -notmatch '\$tables\.Count -ne 98' -or
        $provisionSource -notmatch 'Expected 98 domain tables') {
    throw "H-02 provisioning must enforce the V45 98-table shape"
}
if ($provisionSource -match 'Expected 99 domain tables') {
    throw "H-02 provisioning still enforces the removed V35 table count"
}
if ($provisionSource -notmatch 'Invoke-FlywayMigration -Target 45') {
    throw "H-02 provisioning must migrate through V45"
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

foreach ($entry in $catalog.UpdateColumns.GetEnumerator()) {
    $columns = @($entry.Value)
    if ($columns.Count -eq 0) {
        throw "Runtime update grant list is empty for $($entry.Key)"
    }
    if (@($columns | Sort-Object -Unique).Count -ne $columns.Count) {
        throw "Runtime update grant list contains duplicates for $($entry.Key)"
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
    throw "V45 runtime DELETE grants do not match the reviewed catalog"
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

$assetRequiredColumns = @(
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
    throw "dev_device_asset runtime UPDATE grants do not match V41"
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

$opsAlertColumns = @($catalog.UpdateColumns.ops_alert)
if ($opsAlertColumns -notcontains "source_key") {
    throw "ops_alert.source_key is required by the reliable-task alert projection"
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

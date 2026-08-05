[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$catalogPath = Join-Path $PSScriptRoot "../h02-runtime-grants.psd1"
$catalog = Import-PowerShellDataFile -LiteralPath $catalogPath

foreach ($entry in $catalog.UpdateColumns.GetEnumerator()) {
    $columns = @($entry.Value)
    if ($columns.Count -eq 0) {
        throw "Runtime update grant list is empty for $($entry.Key)"
    }
    if (@($columns | Sort-Object -Unique).Count -ne $columns.Count) {
        throw "Runtime update grant list contains duplicates for $($entry.Key)"
    }
}

$opsAlertColumns = @($catalog.UpdateColumns.ops_alert)
if ($opsAlertColumns -notcontains "source_key") {
    throw "ops_alert.source_key is required by the reliable-task alert projection"
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

Write-Output "h02-runtime-grants-contract=PASS"

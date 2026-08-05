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

Write-Output "h02-runtime-grants-contract=PASS"

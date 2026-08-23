[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $PSCommandPath
$toolRoot = Split-Path -Parent $scriptDirectory
$scripts = @(
    (Join-Path $toolRoot 'New-EcobinOrangePiImage.ps1'),
    (Join-Path $toolRoot 'flash-and-verify.ps1'),
    (Join-Path $toolRoot 'trusted-flash-entry.ps1'),
    $PSCommandPath
)
foreach ($script in $scripts) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $script,
        [ref]$tokens,
        [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        throw "PowerShell parse failure in ${script}: $($errors[0].Message)"
    }
}

& python (Join-Path $toolRoot 'lib\validate_inputs.py') --allow-unlocked
if ($LASTEXITCODE -ne 0) {
    throw 'Checked-in lock metadata validation failed.'
}
$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    & python -m unittest discover -v -s $scriptDirectory -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) {
        throw 'Orange Pi image tooling unit tests failed.'
    }
}
finally {
    Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
}
Write-Output 'ecobin-orangepi-image-tests=PASS'

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "Hello",
        "State",
        "Configure",
        "DoorCycle",
        "SafeClose",
        "Full"
    )]
    [string]$Action = "State",

    [ValidateRange(0, 9007199254740991)]
    [long]$EdgeBootId = 0,

    [ValidateRange(0, 9007199254740991)]
    [long]$ConfigVersion = 0,

    [ValidateRange(1, 6)]
    [int]$PortCount = 1,

    [ValidateRange(30000, 45000)]
    [int]$DoorTravelWaitMs = 30000,

    [ValidateRange(1, 300)]
    [int]$BootRecoveryTimeoutSeconds = 45,

    [ValidatePattern("^(0[xX][0-9a-fA-F]+|[0-9]+)$")]
    [string]$RequiredCapabilities = "0x300",

    [ValidatePattern("^/dev/[a-zA-Z0-9._/-]+$")]
    [string]$SerialPort = "/dev/ttyS5",

    [ValidatePattern("^/[a-zA-Z0-9._/-]+$")]
    [string]$RemoteDirectory = "/root/ecobin-door-hil-20260726",

    [ValidatePattern("^[a-zA-Z0-9_.@:-]+$")]
    [string]$JumpHost = "ubuntu@115.159.67.35",

    [ValidatePattern("^[a-zA-Z0-9_.@:-]+$")]
    [string]$TargetHost = "root@127.0.0.1",

    [ValidateRange(1, 65535)]
    [int]$TargetPort = 22023,

    [string]$SshExecutable = "ssh",

    [switch]$RepeatConfiguration,
    [switch]$ConfirmPhysicalAction,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$maximumSafeInteger = [long]9007199254740991
$nowMilliseconds = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()

if ($EdgeBootId -eq 0) {
    $EdgeBootId = (
        $nowMilliseconds * 1000L + (Get-Random -Minimum 1 -Maximum 1000)
    )
}
if ($ConfigVersion -eq 0) {
    $ConfigVersion = $nowMilliseconds
}
if (
    $EdgeBootId -lt 1 -or
    $EdgeBootId -gt $maximumSafeInteger -or
    $ConfigVersion -lt 1 -or
    $ConfigVersion -gt $maximumSafeInteger
) {
    throw "edge boot ID and config version must be in 1..$maximumSafeInteger"
}

$physicalOpenActions = @("DoorCycle", "Full")
if (
    $Action -in $physicalOpenActions -and
    -not $ConfirmPhysicalAction
) {
    throw (
        "$Action physically opens the delivery door. " +
        "Re-run with -ConfirmPhysicalAction after checking the site."
    )
}

$commonProbeArguments = @(
    "--port", $SerialPort,
    "--edge-boot-id", $EdgeBootId.ToString(),
    "--port-count", $PortCount.ToString(),
    "--required-capabilities", $RequiredCapabilities
)
$probeArguments = [System.Collections.Generic.List[string]]::new()
$probeArguments.AddRange([string[]]$commonProbeArguments)
$remoteTimeoutSeconds = 25

switch ($Action) {
    "Hello" {
        $remoteTimeoutSeconds = 20
    }
    "State" {
        $probeArguments.Add("--query-state")
    }
    "Configure" {
        $probeArguments.Add("--config-version")
        $probeArguments.Add($ConfigVersion.ToString())
        $probeArguments.Add("--apply-sample-configuration")
        if ($RepeatConfiguration) {
            $probeArguments.Add("--repeat-sample-configuration")
        }
        $probeArguments.Add("--query-state")
        $remoteTimeoutSeconds = 50
    }
    "DoorCycle" {
        $probeArguments.Add("--config-version")
        $probeArguments.Add($ConfigVersion.ToString())
        $probeArguments.Add("--door-travel-wait-ms")
        $probeArguments.Add($DoorTravelWaitMs.ToString())
        $probeArguments.Add("--boot-recovery-timeout-s")
        $probeArguments.Add($BootRecoveryTimeoutSeconds.ToString())
        $probeArguments.Add("--run-door-cycle")
        $probeArguments.Add("--query-state")
        $remoteTimeoutSeconds = 120
    }
    "SafeClose" {
        Write-Warning (
            "Issuing SAFE_CLOSE to all delivery doors; " +
            "mechanical door position remains NOT_OBSERVABLE."
        )
        $probeArguments.Add("--safe-close-only")
        $probeArguments.Add("--query-state")
        $remoteTimeoutSeconds = 70
    }
    "Full" {
        $probeArguments.Add("--config-version")
        $probeArguments.Add($ConfigVersion.ToString())
        $probeArguments.Add("--door-travel-wait-ms")
        $probeArguments.Add($DoorTravelWaitMs.ToString())
        $probeArguments.Add("--boot-recovery-timeout-s")
        $probeArguments.Add($BootRecoveryTimeoutSeconds.ToString())
        $probeArguments.Add("--repeat-sample-configuration")
        $probeArguments.Add("--run-door-cycle")
        $probeArguments.Add("--query-state")
        $remoteTimeoutSeconds = 150
    }
}

function ConvertTo-ShellToken {
    param([Parameter(Mandatory)][string]$Value)

    if (
        $Value -eq "&&" -or
        $Value -match "^[a-zA-Z0-9_./:@+=-]+$"
    ) {
        return $Value
    }
    $escaped = $Value.Replace("'", "'`"`"'")
    return "'$escaped'"
}

$remoteTokens = [System.Collections.Generic.List[string]]::new()
$remoteTokens.Add("cd")
$remoteTokens.Add($RemoteDirectory)
$remoteTokens.Add("&&")
$remoteTokens.Add("timeout")
$remoteTokens.Add("--signal=INT")
$remoteTokens.Add("${remoteTimeoutSeconds}s")
$remoteTokens.Add("python3")
$remoteTokens.Add("uart_hil_probe.py")
$remoteTokens.AddRange([string[]]$probeArguments)
$remoteCommand = (
    $remoteTokens |
        ForEach-Object { ConvertTo-ShellToken $_ }
) -join " "

$knownHostsFile = if ($env:OS -eq "Windows_NT") {
    "NUL"
} else {
    "/dev/null"
}
$sshArguments = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ConnectionAttempts=1",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=$knownHostsFile",
    "-o", "LogLevel=ERROR",
    "-J", $JumpHost,
    "-p", $TargetPort.ToString(),
    $TargetHost,
    $remoteCommand
)

Write-Host (
    "EcoBin Orange Pi UART HIL: action={0} edgeBootId={1} configVersion={2}" `
        -f $Action, $EdgeBootId, $ConfigVersion
)

if ($DryRun) {
    $displayArguments = (
        $sshArguments |
            ForEach-Object { ConvertTo-ShellToken $_ }
    ) -join " "
    Write-Output "$SshExecutable $displayArguments"
    exit 0
}

& $SshExecutable @sshArguments
$sshExitCode = $LASTEXITCODE
if ($sshExitCode -ne 0) {
    throw "Orange Pi UART HIL failed with SSH exit code $sshExitCode"
}

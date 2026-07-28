[CmdletBinding()]
param(
    [ValidateSet("Fake", "Real")]
    [string]$Mode = "Fake",

    [switch]$SkipBuild,

    [string]$JavaHome
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\.."))
$rootPom = Join-Path $repositoryRoot "pom.xml"
$localSecretsPath = Join-Path `
    $repositoryRoot ".ecobin\application-local-secrets.yml"

if (-not (Test-Path -LiteralPath $rootPom -PathType Leaf)) {
    throw "EcoBin repository root was not found: $repositoryRoot"
}
if (-not (Test-Path -LiteralPath $localSecretsPath -PathType Leaf)) {
    throw @"
Local YAML configuration was not found:
$localSecretsPath
Run .\tools\development\migrate-dotenv-to-local-yaml.ps1 once, or copy
tools\development\application-local-secrets.example.yml to that path.
"@
}

$profile = "local-$($Mode.ToLowerInvariant())"

function Test-Java21Home {
    param([string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $false
    }
    $javaExecutable = Join-Path $Candidate "bin\java.exe"
    if (-not (Test-Path -LiteralPath $javaExecutable -PathType Leaf)) {
        return $false
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $versionLine = (
            & $javaExecutable -version 2>&1 |
                Select-Object -First 1
        ).ToString()
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return $versionLine -match 'version "21(?:\.|")'
}

function Find-Java21Home {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($JavaHome)) {
        $candidates.Add($JavaHome)
    } else {
        foreach ($environmentCandidate in @(
                $env:JAVA_HOME_21_X64,
                $env:JAVA_HOME)) {
            if (-not [string]::IsNullOrWhiteSpace($environmentCandidate)) {
                $candidates.Add($environmentCandidate)
            }
        }

        $javaCommand = Get-Command java -ErrorAction SilentlyContinue
        if ($null -ne $javaCommand) {
            $currentHome = Split-Path (
                Split-Path $javaCommand.Source -Parent) -Parent
            $candidates.Add($currentHome)

            $previousErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $javaHomeSetting = & $javaCommand.Source `
                        -XshowSettings:properties -version 2>&1 |
                    Where-Object {
                        $_.ToString() -match '^\s*java\.home\s*='
                    } |
                    Select-Object -First 1
            } finally {
                $ErrorActionPreference = $previousErrorActionPreference
            }
            $javaHomeText = if ($null -eq $javaHomeSetting) {
                ""
            } else {
                $javaHomeSetting.ToString()
            }
            if ($javaHomeText -match '=\s*(.+?)\s*$') {
                $effectiveHome = $matches[1]
                $candidates.Add($effectiveHome)
                $effectiveParent = Split-Path $effectiveHome -Parent
                Get-ChildItem -LiteralPath $effectiveParent -Directory `
                        -ErrorAction SilentlyContinue |
                    Where-Object {
                        $_.Name -match '(?i)(jdk|java).?21'
                    } |
                    ForEach-Object { $candidates.Add($_.FullName) }
            }
        }

        foreach ($standardParent in @(
                (Join-Path $env:USERPROFILE ".jdks"),
                (Join-Path $env:ProgramFiles "Java"),
                (Join-Path $env:ProgramFiles "Eclipse Adoptium"))) {
            if (Test-Path -LiteralPath $standardParent -PathType Container) {
                Get-ChildItem -LiteralPath $standardParent -Directory `
                        -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '(?i)(jdk|java|temurin).?21' } |
                    ForEach-Object { $candidates.Add($_.FullName) }
            }
        }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Java21Home $resolved) {
            return $resolved
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($JavaHome)) {
        throw "-JavaHome is not a Java 21 JDK: $JavaHome"
    }
    throw "Java 21 was not found. Pass -JavaHome <jdk-21-directory> once."
}

$java21Home = Find-Java21Home
$originalJavaHome = $env:JAVA_HOME
$originalPath = $env:PATH

Push-Location $repositoryRoot
try {
    $env:JAVA_HOME = $java21Home
    $env:PATH = "$(Join-Path $java21Home 'bin');$originalPath"
    Write-Host "[EcoBin] Java 21: $java21Home"

    if (-not $SkipBuild) {
        Write-Host "[EcoBin] Installing the latest multi-module build..."
        & .\mvnw.cmd -q install -DskipTests
        if ($LASTEXITCODE -ne 0) {
            throw "EcoBin multi-module build failed with exit code $LASTEXITCODE"
        }
    }

    Write-Host "[EcoBin] Starting backend with profile '$profile'."
    if ($Mode -eq "Fake") {
        Write-Host "[EcoBin] Real OneNet/COS/WeChat settings are masked."
    } else {
        Write-Host "[EcoBin] Real services are enabled; default-admin bootstrap is disabled."
    }

    & .\mvnw.cmd -pl ecobin-bootstrap spring-boot:run `
        "-Dspring-boot.run.profiles=$profile" `
        "-Dspring-boot.run.workingDirectory=$repositoryRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "EcoBin backend exited with code $LASTEXITCODE"
    }
} finally {
    $env:JAVA_HOME = $originalJavaHome
    $env:PATH = $originalPath
    Pop-Location
}

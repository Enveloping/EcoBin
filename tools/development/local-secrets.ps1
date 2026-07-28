function Read-EcoBinLocalSecrets {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Local YAML configuration was not found: $Path"
    }

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($rawLine)) {
            continue
        }
        if ($rawLine -match '^\s' -or $rawLine.TrimStart().StartsWith('#')) {
            continue
        }

        $separator = $rawLine.IndexOf(':')
        if ($separator -le 0) {
            throw "Unsupported local YAML line. Keep every setting at the top level."
        }

        $key = $rawLine.Substring(0, $separator).Trim()
        if ($key -notmatch '^[A-Za-z][A-Za-z0-9_-]*$') {
            throw "Unsupported local YAML key: $key"
        }
        if ($values.ContainsKey($key)) {
            throw "Duplicate local YAML key: $key"
        }

        $value = $rawLine.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and
            $value.StartsWith("'") -and
            $value.EndsWith("'")) {
            $value = $value.Substring(1, $value.Length - 2).
                Replace("''", "'")
        } elseif ($value.Length -ge 2 -and
            $value.StartsWith('"') -and
            $value.EndsWith('"')) {
            try {
                $value = [string]($value | ConvertFrom-Json)
            } catch {
                throw "Invalid double-quoted YAML value for key: $key"
            }
        } else {
            $commentStart = $value.IndexOf(' #')
            if ($commentStart -ge 0) {
                $value = $value.Substring(0, $commentStart).TrimEnd()
            }
            if ($value -in @('null', 'Null', 'NULL', '~')) {
                $value = ''
            }
        }
        $values[$key] = $value
    }

    return $values
}

function Get-EcoBinRequiredLocalSecret {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$Secrets,

        [Parameter(Mandatory)]
        [string]$Name
    )

    $value = [string]$Secrets[$Name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Local YAML configuration is missing required key: $Name"
    }
    return $value
}

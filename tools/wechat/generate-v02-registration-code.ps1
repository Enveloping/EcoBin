[CmdletBinding()]
param(
    [string]$DeploymentCode = 'Dp_SGMqV11JX26yI5dQp7DvUA',

    [ValidateSet('develop', 'trial', 'release')]
    [string]$EnvVersion = 'develop',

    [ValidateRange(280, 1280)]
    [int]$Width = 430,

    [string]$OutputBaseName = 'v02-trusted-registration-code',

    [switch]$CheckPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Convert-WechatError {
    param(
        [Parameter(Mandatory)][byte[]]$Bytes,
        [Parameter(Mandatory)][string]$Operation
    )

    try {
        $text = [Text.Encoding]::UTF8.GetString($Bytes)
        $errorBody = $text | ConvertFrom-Json
        $errorCodeProperty = $errorBody.PSObject.Properties['errcode']
        $errorMessageProperty = $errorBody.PSObject.Properties['errmsg']
        $errorCode = if ($null -ne $errorCodeProperty) {
            [string]$errorCodeProperty.Value
        } else {
            'UNKNOWN'
        }
        $errorMessage = if (
            $null -ne $errorMessageProperty -and
            $errorMessageProperty.Value
        ) {
            [string]$errorMessageProperty.Value
        } else {
            '微信接口未返回错误说明'
        }
        return "$Operation 失败：errcode=$errorCode, errmsg=$errorMessage"
    } catch {
        return "$Operation 失败：微信接口返回了无法识别的响应"
    }
}

function Invoke-JsonPost {
    param(
        [Parameter(Mandatory)][Net.Http.HttpClient]$Client,
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][hashtable]$Payload,
        [Parameter(Mandatory)][string]$Operation
    )

    $json = $Payload | ConvertTo-Json -Depth 5 -Compress
    $content = [Net.Http.StringContent]::new(
        $json,
        [Text.Encoding]::UTF8,
        'application/json'
    )
    try {
        $response = $Client.PostAsync($Uri, $content).
            GetAwaiter().GetResult()
        try {
            $bytes = $response.Content.ReadAsByteArrayAsync().
                GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                $status = [int]$response.StatusCode
                $detail = Convert-WechatError `
                    -Bytes $bytes `
                    -Operation $Operation
                throw "$detail（HTTP $status）"
            }
            return [pscustomobject]@{
                Bytes = $bytes
                ContentType = [string]$response.Content.Headers.ContentType
            }
        } finally {
            $response.Dispose()
        }
    } finally {
        $content.Dispose()
    }
}

if ($DeploymentCode -notmatch '^Dp_[A-Za-z0-9_-]{6,61}$') {
    throw 'DeploymentCode 不符合 EcoBin 公开部署码格式'
}
if ($DeploymentCode.Length -gt 32) {
    throw 'DeploymentCode 超过微信小程序码 scene 的 32 字符限制'
}
if (
    [IO.Path]::GetFileName($OutputBaseName) -ne $OutputBaseName -or
    [string]::IsNullOrWhiteSpace($OutputBaseName) -or
    $OutputBaseName.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0
) {
    throw 'OutputBaseName 只能是脚本同目录下的合法文件基础名'
}

$repositoryRoot = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..')
)
$localSecretsPath = Join-Path `
    $repositoryRoot '.ecobin\application-local-secrets.yml'
$readerPath = Join-Path `
    $repositoryRoot 'tools\development\local-secrets.ps1'
. $readerPath

$localSecrets = Read-EcoBinLocalSecrets -Path $localSecretsPath
$appId = [string]$localSecrets['wechatAppid']
$appSecret = [string]$localSecrets['wechatSecret']
if ([string]::IsNullOrWhiteSpace($appId)) {
    throw '本地 secrets YAML 中缺少 wechatAppid'
}
if ([string]::IsNullOrWhiteSpace($appSecret)) {
    throw '本地 secrets YAML 中缺少 wechatSecret'
}

$handler = [Net.Http.HttpClientHandler]::new()
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(30)

try {
    $tokenResponse = Invoke-JsonPost `
        -Client $client `
        -Uri 'https://api.weixin.qq.com/cgi-bin/stable_token' `
        -Payload @{
            grant_type = 'client_credential'
            appid = $appId
            secret = $appSecret
            force_refresh = $false
        } `
        -Operation '获取微信 access_token'

    try {
        $tokenBody = [Text.Encoding]::UTF8.GetString(
            $tokenResponse.Bytes
        ) | ConvertFrom-Json
    } catch {
        throw '获取微信 access_token 失败：响应不是合法 JSON'
    }
    $tokenErrorCodeProperty =
        $tokenBody.PSObject.Properties['errcode']
    $tokenErrorMessageProperty =
        $tokenBody.PSObject.Properties['errmsg']
    if (
        $null -ne $tokenErrorCodeProperty -and
        [int]$tokenErrorCodeProperty.Value -ne 0
    ) {
        $message = if (
            $null -ne $tokenErrorMessageProperty -and
            $tokenErrorMessageProperty.Value
        ) {
            [string]$tokenErrorMessageProperty.Value
        } else {
            '微信接口未返回错误说明'
        }
        throw "获取微信 access_token 失败：errcode=$(
            [int]$tokenErrorCodeProperty.Value
        ), errmsg=$message"
    }
    $accessTokenProperty =
        $tokenBody.PSObject.Properties['access_token']
    $accessToken = if ($null -ne $accessTokenProperty) {
        [string]$accessTokenProperty.Value
    } else {
        ''
    }
    if ([string]::IsNullOrWhiteSpace($accessToken)) {
        throw '获取微信 access_token 失败：响应中没有 access_token'
    }

    $escapedToken = [Uri]::EscapeDataString($accessToken)
    $codeResponse = Invoke-JsonPost `
        -Client $client `
        -Uri "https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token=$escapedToken" `
        -Payload @{
            scene = $DeploymentCode
            page = 'pages/login/login'
            check_path = [bool]$CheckPath
            env_version = $EnvVersion
            width = $Width
        } `
        -Operation '生成微信小程序码'

    $bytes = $codeResponse.Bytes
    $looksLikeJson = $codeResponse.ContentType -match 'json' -or
        ($bytes.Length -gt 0 -and $bytes[0] -eq [byte][char]'{')
    if ($looksLikeJson) {
        throw (Convert-WechatError `
            -Bytes $bytes `
            -Operation '生成微信小程序码')
    }

    $isPng = $bytes.Length -ge 8 -and
        $bytes[0] -eq 0x89 -and
        $bytes[1] -eq 0x50 -and
        $bytes[2] -eq 0x4E -and
        $bytes[3] -eq 0x47
    $isJpeg = $bytes.Length -ge 3 -and
        $bytes[0] -eq 0xFF -and
        $bytes[1] -eq 0xD8 -and
        $bytes[2] -eq 0xFF
    if (-not $isPng -and -not $isJpeg) {
        throw '生成微信小程序码失败：返回内容不是 PNG 或 JPEG 图片'
    }

    $extension = if ($isPng) { '.png' } else { '.jpg' }
    $outputPath = Join-Path $PSScriptRoot (
        $OutputBaseName + $extension
    )
    $temporaryPath = "$outputPath.$PID.tmp"
    try {
        [IO.File]::WriteAllBytes($temporaryPath, $bytes)
        [IO.File]::Move($temporaryPath, $outputPath, $true)
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath
        }
    }

    Write-Host "小程序码已生成：$outputPath"
    Write-Host "页面：pages/login/login"
    Write-Host "scene：$DeploymentCode"
    Write-Host "版本：$EnvVersion"
} finally {
    $client.Dispose()
    $handler.Dispose()
}

[CmdletBinding()]
param(
    [string]$PluginRoot = "",
    [string]$ConfigPath = "",
    [string]$PythonExe = "python",
    [string]$EnvFile = "",
    [string]$Since = "",
    [int]$MaxResults = 0,
    [ValidateSet("journal", "preprint", "conference")]
    [string]$DocumentType = "journal",
    [switch]$NoState,
    [switch]$EnableEmail,
    [switch]$EnableWeChat,
    [switch]$EnableIEEE,
    [switch]$EnableElsevier
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-PluginPath {
    param([string]$PathValue, [string]$BasePath)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return $expanded
    }
    return (Join-Path $BasePath $expanded)
}

function Ensure-Property {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Value
    )
    if ($null -eq $Object.PSObject.Properties[$Name]) {
        Add-Member -InputObject $Object -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Quote-NativeArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
    $PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $PluginRoot = (Resolve-Path $PluginRoot).Path
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $PluginRoot "assets\power_system_radar_config.json"
}
$ConfigPath = (Resolve-Path $ConfigPath).Path
$configSlug = ([System.IO.Path]::GetFileNameWithoutExtension($ConfigPath) -replace '[^A-Za-z0-9_-]', '_')

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $PluginRoot "radar.env.ps1"
} elseif (-not [System.IO.Path]::IsPathRooted($EnvFile)) {
    # 定时任务的当前目录并不可靠；所有相对环境文件统一相对插件根目录解析。
    $EnvFile = Join-Path $PluginRoot $EnvFile
}
if (Test-Path -LiteralPath $EnvFile) {
    $EnvFile = (Resolve-Path $EnvFile).Path
}

if (Test-Path -LiteralPath $EnvFile) {
    . $EnvFile
}

if ($PythonExe -eq "python" -and $null -eq (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $codexPython) {
        $PythonExe = $codexPython
    }
}

$configText = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8)
$config = $configText | ConvertFrom-Json

if ($null -ne $config.manual_exports -and $null -ne $config.manual_exports.paths) {
    foreach ($manualPath in $config.manual_exports.paths) {
        $resolvedManualPath = Resolve-PluginPath -PathValue ([string]$manualPath) -BasePath $PluginRoot
        if ($null -ne $resolvedManualPath) {
            New-Item -ItemType Directory -Force -Path $resolvedManualPath | Out-Null
        }
    }
}

$runtimeConfigPath = $ConfigPath
if ($EnableEmail -or $EnableWeChat -or $EnableIEEE -or $EnableElsevier) {
    Ensure-Property -Object $config -Name "notifications" -Value ([pscustomobject]@{})
    Ensure-Property -Object $config.notifications -Name "email" -Value ([pscustomobject]@{ enabled = $false })
    Ensure-Property -Object $config.notifications -Name "wechat" -Value ([pscustomobject]@{ enabled = $false })
    Ensure-Property -Object $config.notifications.email -Name "enabled" -Value $false
    Ensure-Property -Object $config.notifications.wechat -Name "enabled" -Value $false

    if ($EnableEmail) {
        $config.notifications.email.enabled = $true
    }
    if ($EnableWeChat) {
        $config.notifications.wechat.enabled = $true
    }
    if ($EnableIEEE) {
        if ([string]::IsNullOrWhiteSpace($env:IEEE_XPLORE_API_KEY)) {
            Write-Warning "EnableIEEE was requested, but IEEE_XPLORE_API_KEY is not set in the environment file."
        }
        foreach ($source in $config.sources) {
            if ($source.type -eq "ieee_xplore_api") {
                $source.enabled = $true
            }
        }
    }
    if ($EnableElsevier) {
        if ([string]::IsNullOrWhiteSpace($env:ELSEVIER_API_KEY)) {
            Write-Warning "EnableElsevier was requested, but ELSEVIER_API_KEY is not set in the environment file."
        }
        foreach ($source in $config.sources) {
            if ($source.type -eq "elsevier_scopus_api") {
                $source.enabled = $true
            }
        }
    }

    $runtimeDir = Join-Path $PluginRoot "work\power-system-radar"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    # Basic 与 Full 可能由两个任务同时启动，运行时配置必须按配置文件隔离。
    $runtimeConfigPath = Join-Path $runtimeDir ("runtime_config_{0}_{1}.json" -f $configSlug, $DocumentType)
    $json = $config | ConvertTo-Json -Depth 100
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($runtimeConfigPath, $json, $utf8NoBom)
}

$logDir = Join-Path $PluginRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$logPath = Join-Path $logDir ("radar_{0}_{1}_{2}.log" -f $configSlug, $DocumentType, $stamp)

$radarScript = Join-Path $PluginRoot "scripts\power_system_radar.py"
$arguments = @(
    $radarScript,
    "--config", $runtimeConfigPath,
    "--document-type", $DocumentType,
    "--run"
)

if (-not [string]::IsNullOrWhiteSpace($Since)) {
    $arguments += @("--since", $Since)
}
if ($MaxResults -gt 0) {
    $arguments += @("--max-results", [string]$MaxResults)
}
if ($NoState) {
    $arguments += "--no-state"
}

Push-Location $PluginRoot
try {
    "[$(Get-Date -Format s)] Starting Power System Academic Radar" | Tee-Object -FilePath $logPath
    "PluginRoot: $PluginRoot" | Tee-Object -FilePath $logPath -Append
    "ConfigPath: $runtimeConfigPath" | Tee-Object -FilePath $logPath -Append
    "PythonExe: $PythonExe" | Tee-Object -FilePath $logPath -Append

    $nativeArgumentList = ($arguments | ForEach-Object { Quote-NativeArgument ([string]$_) }) -join " "
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $PythonExe
    $startInfo.Arguments = $nativeArgumentList
    $startInfo.WorkingDirectory = $PluginRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    $exitCode = $process.ExitCode
    if (-not [string]::IsNullOrWhiteSpace($stdout)) {
        $stdout.TrimEnd() | Tee-Object -FilePath $logPath -Append
    }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        $stderr.TrimEnd() | Tee-Object -FilePath $logPath -Append
    }

    if ($exitCode -ne 0) {
        throw "Radar run failed with exit code $exitCode. See log: $logPath"
    }

    "[$(Get-Date -Format s)] Completed" | Tee-Object -FilePath $logPath -Append
}
finally {
    Pop-Location
}

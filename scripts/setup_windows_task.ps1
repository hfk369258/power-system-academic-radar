[CmdletBinding()]
param(
    [string]$TaskName = "PowerSystemAcademicRadar",
    [string]$TaskPath = "\Codex\",
    [string]$DailyTime = "08:30",
    [ValidateSet("Daily", "Weekly")]
    [string]$Frequency = "Daily",
    [ValidateSet("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]
    [string]$DayOfWeek = "Monday",
    [ValidateSet("journal", "preprint", "conference")]
    [string]$DocumentType = "journal",
    [string]$PluginRoot = "",
    [string]$RunScript = "",
    [string]$ConfigPath = "",
    [string]$PythonExe = "python",
    [string]$EnvFile = "",
    [switch]$EnableEmail,
    [switch]$EnableWeChat,
    [switch]$EnableIEEE,
    [switch]$EnableElsevier,
    [switch]$Disable,
    [switch]$Remove,
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Quote-TaskArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
    $PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $PluginRoot = (Resolve-Path $PluginRoot).Path
}

if ($Remove) {
    # 删除任务（方案删除/旧版混合任务迁移时使用）
    $existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    }
    Write-Host "Removed scheduled task: $TaskPath$TaskName"
    return
}

if ($Disable) {
    # 真正的「停用」：保留任务定义，仅禁用触发，与 UI「停止任务」文案一致
    $existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Disable-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Out-Null
    }
    Write-Host "Disabled scheduled task: $TaskPath$TaskName"
    return
}

if ([string]::IsNullOrWhiteSpace($RunScript)) {
    $RunScript = Join-Path $PluginRoot "scripts\run_radar.ps1"
}
$RunScript = (Resolve-Path $RunScript).Path

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $PluginRoot "assets\power_system_radar_config.json"
}
$ConfigPath = (Resolve-Path $ConfigPath).Path

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $PluginRoot "radar.env.ps1"
} elseif (-not [System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile = Join-Path $PluginRoot $EnvFile
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Environment file not found: $EnvFile"
}
$EnvFile = (Resolve-Path $EnvFile).Path

$time = [datetime]::ParseExact($DailyTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)

$taskArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", (Quote-TaskArgument $RunScript),
    "-PluginRoot", (Quote-TaskArgument $PluginRoot),
    "-ConfigPath", (Quote-TaskArgument $ConfigPath),
    "-PythonExe", (Quote-TaskArgument $PythonExe),
    "-EnvFile", (Quote-TaskArgument $EnvFile),
    "-DocumentType", $DocumentType
)

if ($EnableEmail) {
    $taskArgs += "-EnableEmail"
}
if ($EnableWeChat) {
    $taskArgs += "-EnableWeChat"
}
if ($EnableIEEE) {
    $taskArgs += "-EnableIEEE"
}
if ($EnableElsevier) {
    $taskArgs += "-EnableElsevier"
}

if ($DryRun) {
    Write-Host "Dry run only. No scheduled task was registered."
    Write-Host "Task: $TaskPath$TaskName"
    Write-Host "Frequency: $Frequency"
    Write-Host "Time: $DailyTime"
    if ($Frequency -eq "Weekly") {
        Write-Host "Day of week: $DayOfWeek"
    }
    Write-Host "Action: powershell.exe $($taskArgs -join ' ')"
    Write-Host "Working directory: $PluginRoot"
    return
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($taskArgs -join " ") `
    -WorkingDirectory $PluginRoot

if ($Frequency -eq "Weekly") {
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $DayOfWeek -At $time
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
}
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

$description = "Runs Power System Academic Radar for the $DocumentType category."

$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $Force) {
    throw "Scheduled task $TaskPath$TaskName already exists. Re-run with -Force to replace it."
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force:$Force | Out-Null

Write-Host "Registered scheduled task: $TaskPath$TaskName"
Write-Host "Frequency: $Frequency"
Write-Host "Time: $DailyTime"
if ($Frequency -eq "Weekly") {
    Write-Host "Day of week: $DayOfWeek"
}
Write-Host "Run script: $RunScript"
Write-Host "Config: $ConfigPath"
Write-Host "Env file: $EnvFile"

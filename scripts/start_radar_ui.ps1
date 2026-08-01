[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $env:TEMP "power-system-academic-radar-ui.pid"

# Stop only the previous UI process recorded by this plugin. A stale PID file
# is harmless; process-name checking prevents terminating an unrelated process.
if (Test-Path -LiteralPath $PidFile) {
    $oldPidText = (Get-Content -Raw -LiteralPath $PidFile -ErrorAction SilentlyContinue).Trim()
    if ($oldPidText -match '^\d+$') {
        $oldProcess = Get-Process -Id ([int]$oldPidText) -ErrorAction SilentlyContinue
        if ($null -ne $oldProcess -and $oldProcess.ProcessName -like "python*") {
            Stop-Process -Id $oldProcess.Id -Force
            Start-Sleep -Milliseconds 350
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Test-LocalPortFree {
    param([int]$CandidatePort)
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $CandidatePort)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

$attempts = 0
while (-not (Test-LocalPortFree -CandidatePort $Port)) {
    $Port++
    $attempts++
    if ($attempts -ge 20) {
        $message = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5om+5LiN5Yiw5Y+v55So55qE5pys5Zyw56uv5Y+j77yM6K+35YWz6Zet5pen5o6n5Yi25Y+w56qX5Y+j5ZCO6YeN6K+V44CC"))
        throw $message
    }
}

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    if (Test-Path -LiteralPath $bundledPython) {
        $PythonExe = $bundledPython
    } elseif ($null -ne (Get-Command "python" -ErrorAction SilentlyContinue)) {
        $PythonExe = "python"
    } else {
        $message = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5pyq5om+5YiwIFB5dGhvbu+8jOivt+WuieijhSBQeXRob24g5oiW6YCa6L+HIC1QeXRob25FeGUg5oyH5a6a6Kej6YeK5Zmo44CC"))
        throw $message
    }
}

$arguments = @(
    (Join-Path $PluginRoot "scripts\radar_config_ui.py"),
    "--port", [string]$Port,
    "--pid-file", $PidFile
)
if ($NoBrowser) {
    $arguments += "--no-browser"
}

Write-Host "Starting Academic Radar UI: http://127.0.0.1:$Port/"
Write-Host "Press Ctrl+C or close this window to stop the UI."
& $PythonExe @arguments

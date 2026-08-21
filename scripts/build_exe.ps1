# Build the Power System Academic Radar desktop UI exe (Windows).
# Output: dist\power-system-radar-ui\power-system-radar-ui.exe -- double-click to run.
# User data (profiles/, radar.env.ps1, outputs/) lives next to the exe.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

Write-Host "==> Installing build dependencies (PyInstaller + pywebview; first time only)..."
python -m pip install --upgrade pyinstaller pywebview
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Preserve user profile data (profiles/) across rebuilds; PyInstaller --clean wipes dist.
$ExistingProfiles = Join-Path $Root "dist\power-system-radar-ui\profiles"
$ProfileBackup = Join-Path $env:TEMP "radar-ui-profiles-backup"
if (Test-Path -LiteralPath $ExistingProfiles) {
    if (Test-Path -LiteralPath $ProfileBackup) { Remove-Item -Recurse -Force $ProfileBackup }
    Copy-Item -Recurse -Force $ExistingProfiles $ProfileBackup
    Write-Host "Existing profiles/ backed up for restore."
}

Write-Host "==> PyInstaller onedir build (no console window)..."
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name power-system-radar-ui `
    --hidden-import webview.platforms.edgechromium `
    scripts\radar_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Assembling runtime layout (scripts/ and assets/ next to the exe)..."
$Target = Join-Path $Root "dist\power-system-radar-ui"
Copy-Item -Recurse -Force (Join-Path $Root "scripts") (Join-Path $Target "scripts")
Copy-Item -Recurse -Force (Join-Path $Root "assets") (Join-Path $Target "assets")
Copy-Item -Force (Join-Path $Root "radar.env.example.ps1") (Join-Path $Target "radar.env.example.ps1")
Copy-Item -Force (Join-Path $Root ".gitignore") (Join-Path $Target ".gitignore")

if (Test-Path -LiteralPath $ProfileBackup) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Target "profiles") | Out-Null
    Copy-Item -Recurse -Force (Join-Path $ProfileBackup "*") (Join-Path $Target "profiles")
    Remove-Item -Recurse -Force $ProfileBackup
    Write-Host "profiles/ restored."
}

Write-Host ""
Write-Host "Build complete: $Target\power-system-radar-ui.exe"
Write-Host "Double-click the exe to open the config console. Closing its window stops the UI only; scheduled radar runs are independent."

# ---------------------------------------------------------------------------
# Release artifacts: power-system-radar-ui_v<version>.zip (for in-app self-update
# and GitHub Release) plus an optional Inno Setup installer.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==> Reading APP_VERSION from scripts/radar_update.py..."
$Version = (python -c "import sys; sys.path.insert(0, 'scripts'); import radar_update; print(radar_update.APP_VERSION)").Trim()
if (-not $Version) { throw "Failed to read APP_VERSION" }
Write-Host "    version = $Version"

Write-Host "==> Assembling release package..."
$ReleaseStage = Join-Path $Root "dist\_release\power-system-radar-ui"
if (Test-Path -LiteralPath (Join-Path $Root "dist\_release")) {
    Remove-Item -Recurse -Force (Join-Path $Root "dist\_release")
}
New-Item -ItemType Directory -Force -Path $ReleaseStage | Out-Null
Copy-Item -Path (Join-Path $Target "*") -Destination $ReleaseStage -Recurse -Force
# Cache dirs and local user data must never enter the release package.
foreach ($name in @("__pycache__", ".pytest_cache", "profiles", "work", "logs")) {
    Get-ChildItem $ReleaseStage -Recurse -Force -Directory -Filter $name -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

$ZipName = "power-system-radar-ui_v$Version.zip"
$ZipPath = Join-Path $Root "dist\$ZipName"
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $ReleaseStage -DestinationPath $ZipPath
Write-Host "Release zip created: $ZipPath"

# Optional: build the Inno Setup installer (requires Inno Setup 6 / ISCC.exe).
$Issc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$IsscPath = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
$IsccExe = if ($Issc) { $Issc.Source } elseif ($IsscPath) { $IsscPath } else { $null }
if ($IsccExe) {
    Write-Host "==> Building installer (Inno Setup)..."
    & $IsccExe "/DAppVersion=$Version" (Join-Path $Root "installer\power-system-radar-ui.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed" }
} else {
    Write-Warning "Inno Setup 6 (ISCC.exe) not found; skipping installer build. Install Inno Setup and re-run to produce the setup exe."
}

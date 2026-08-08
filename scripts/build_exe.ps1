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

Write-Host "==> PyInstaller onedir build..."
python -m PyInstaller --noconfirm --clean --onedir --name power-system-radar-ui `
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
# tools/fetch_webview2.ps1
# Downloads the Microsoft Edge WebView2 Evergreen Bootstrapper into the
# repo root so installer.iss can bundle it.  Idempotent -- skips download
# if the file already exists.
#
# Why this lives in tools/ rather than build.bat: build.bat is the
# PyInstaller build, not the installer build.  This script is invoked
# manually OR via build.bat right before ISCC.  Either way, run from
# the repo root.

$ErrorActionPreference = "Stop"

# Microsoft's Evergreen Bootstrapper URL -- small (~2 MB), idempotent.
# This downloads-and-runs the latest WebView2 runtime at install time on
# the friend's PC.  Stable URL -- Microsoft has hosted it since 2020.
$url    = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$target = Join-Path $PSScriptRoot "..\MicrosoftEdgeWebview2Setup.exe"
$target = [System.IO.Path]::GetFullPath($target)

if (Test-Path $target) {
    $sizeMB = [math]::Round((Get-Item $target).Length / 1MB, 2)
    Write-Host "[fetch_webview2] Already present: $target ($sizeMB MB)"
    Write-Host "[fetch_webview2] Delete it to force a fresh download."
    exit 0
}

Write-Host "[fetch_webview2] Downloading WebView2 bootstrapper..."
Write-Host "[fetch_webview2] URL: $url"
Write-Host "[fetch_webview2] Dst: $target"

# Use Invoke-WebRequest with UseBasicParsing -- works on PS 5.1 + 7
try {
    Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing -ErrorAction Stop
} catch {
    Write-Error "[fetch_webview2] Download failed: $_"
    exit 1
}

if (-not (Test-Path $target)) {
    Write-Error "[fetch_webview2] Download produced no file -- aborting."
    exit 1
}

$sizeMB = [math]::Round((Get-Item $target).Length / 1MB, 2)
Write-Host "[fetch_webview2] OK -- $sizeMB MB at $target"
Write-Host "[fetch_webview2] Re-run 'ISCC installer.iss' to bundle it."

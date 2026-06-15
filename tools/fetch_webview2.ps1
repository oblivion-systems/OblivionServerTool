# tools/fetch_webview2.ps1
# Downloads the Microsoft Edge WebView2 Evergreen Bootstrapper into the
# repo root so installer.iss can bundle it.  Idempotent -- skips download
# if the file already exists AND is plausibly intact.
#
# Why this lives in tools/ rather than build.bat: build.bat calls this
# as step [1.6/3].  It can also be invoked manually before ISCC.
# Either way, run from the repo root.

$ErrorActionPreference = "Stop"

# Microsoft Evergreen Bootstrapper URL -- ~1.6 MB, idempotent at install.
$url    = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$target = Join-Path $PSScriptRoot "..\MicrosoftEdgeWebview2Setup.exe"
$target = [System.IO.Path]::GetFullPath($target)

# v0.16.8 (review fix #3) -- minimum size guard.  Real bootstrapper is
# ~1.6 MB; treat anything under 1 MB as a truncated download (CDN edge
# drop, network hiccup) and re-fetch rather than ship a corrupt exe.
$MIN_SIZE_BYTES = 1MB

if (Test-Path $target) {
    $sizeBytes = (Get-Item $target).Length
    $sizeMB    = [math]::Round($sizeBytes / 1MB, 2)
    if ($sizeBytes -lt $MIN_SIZE_BYTES) {
        Write-Host "[fetch_webview2] Existing file is suspiciously small ($sizeMB MB) -- deleting and re-fetching."
        Remove-Item $target -Force
    } else {
        Write-Host "[fetch_webview2] Already present: $target ($sizeMB MB)"
        Write-Host "[fetch_webview2] Delete it to force a fresh download."
        exit 0
    }
}

Write-Host "[fetch_webview2] Downloading WebView2 bootstrapper..."
Write-Host "[fetch_webview2] URL: $url"
Write-Host "[fetch_webview2] Dst: $target"

# v0.16.8 (review fix #3) -- explicit timeout + max redirections.
# UseBasicParsing works on PS 5.1 + 7.
try {
    Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing `
        -MaximumRedirection 5 -TimeoutSec 60 -ErrorAction Stop
} catch {
    Write-Error "[fetch_webview2] Download failed: $_"
    if (Test-Path $target) { Remove-Item $target -Force -ErrorAction SilentlyContinue }
    exit 1
}

if (-not (Test-Path $target)) {
    Write-Error "[fetch_webview2] Download produced no file -- aborting."
    exit 1
}

$sizeBytes = (Get-Item $target).Length
$sizeMB    = [math]::Round($sizeBytes / 1MB, 2)

# v0.16.8 (review fix #3) -- size sanity check on the freshly-downloaded
# file.  A 0-byte file from a truncated download would otherwise be
# bundled into the installer and fail at the friend's install time with
# ERROR_BAD_EXE_FORMAT, surfacing a mid-install dialog they can't recover
# from.  Better to fail loudly here.
if ($sizeBytes -lt $MIN_SIZE_BYTES) {
    Remove-Item $target -Force -ErrorAction SilentlyContinue
    Write-Error "[fetch_webview2] Download truncated ($sizeMB MB, need >= 1 MB) -- aborting."
    exit 1
}

Write-Host "[fetch_webview2] OK -- $sizeMB MB at $target"
Write-Host "[fetch_webview2] Re-run 'ISCC installer.iss' to bundle it."

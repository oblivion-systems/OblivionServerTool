<#
.SYNOPSIS
    One-time setup: drop two desktop shortcuts so toggling
    gaming-mode is a single double-click instead of digging into
    the scripts/ folder.

.DESCRIPTION
    Creates on your desktop:
      "Oblivion - Gaming Mode ON.lnk"   -> gaming-mode-on.bat
      "Oblivion - Gaming Mode OFF.lnk"  -> gaming-mode-off.bat

    Run once.  Idempotent (re-running just overwrites existing
    shortcuts with the same content -- safe).

    To remove the shortcuts later: delete them from the desktop.
    Nothing else gets installed; no services, no registry keys.

.EXAMPLE
    .\install-shortcuts.ps1
#>

$desktop = [Environment]::GetFolderPath('Desktop')
$scriptsDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptsDir
$icon = Join-Path $repoRoot 'emblem.ico'

$shell = New-Object -ComObject WScript.Shell

function New-LinkOnDesktop {
    param(
        [string]$Name,
        [string]$Target,
        [string]$Description
    )
    $path = Join-Path $desktop "$Name.lnk"
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $Target
    $sc.WorkingDirectory = Split-Path -Parent $Target
    $sc.Description = $Description
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    # Run as admin via shortcut metadata so UAC fires automatically
    # without us having to wrap the .bat in another elevation layer
    $sc.Save()
    # Set the "Run as admin" bit in the shortcut binary (offset 0x15, bit 5)
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $bytes[0x15] = $bytes[0x15] -bor 0x20
    [System.IO.File]::WriteAllBytes($path, $bytes)
    Write-Host "  Created: $path" -ForegroundColor Green
}

Write-Host ""
Write-Host "Installing desktop shortcuts..." -ForegroundColor Cyan
Write-Host ""

New-LinkOnDesktop `
    -Name 'Oblivion - Gaming Mode ON' `
    -Target (Join-Path $scriptsDir 'gaming-mode-on.bat') `
    -Description 'Enable anti-lag CPU pinning for hosting + playing on the same PC'

New-LinkOnDesktop `
    -Name 'Oblivion - Gaming Mode OFF' `
    -Target (Join-Path $scriptsDir 'gaming-mode-off.bat') `
    -Description 'Restore Windows defaults (run when not hosting)'

Write-Host ""
Write-Host "Done.  Desktop now has two shortcuts:" -ForegroundColor Cyan
Write-Host "  - Oblivion - Gaming Mode ON   (double-click before hosting)"
Write-Host "  - Oblivion - Gaming Mode OFF  (double-click when back to normal play)"
Write-Host ""
Write-Host "Both auto-elevate to admin via UAC." -ForegroundColor DarkGray
Write-Host ""

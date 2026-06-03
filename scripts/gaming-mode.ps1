<#
.SYNOPSIS
    Toggle Windows + CS2 process tweaks for "I'm hosting the server
    AND playing on this PC" alt-tab-friendly sessions.

.DESCRIPTION
    GAMING mode applies five tweaks that together eliminate the
    alt-tab -> lag spike pattern when running cs2.exe (game client) +
    cs2.exe -dedicated (server) on the same machine:

      1. Power Plan -> Ultimate Performance (or High Perf fallback) --
         stops the CPU from down-clocking on foreground change
      2. Game Mode -> Off -- Game Mode tries to give CS2 "all" the
         resources, which paradoxically causes shuffling on alt-tab
      3. Game DVR -> Off -- drops the background recording overhead
      4. cs2.exe -dedicated -> priority High + pinned to first N cores
         (so it can't be demoted to background priority on alt-tab)
      5. cs2.exe (client) -> pinned to the REMAINING cores (priority
         left alone for VAC/anti-cheat friendliness)

    Result: Windows can't shuffle resources around on alt-tab because
    everything is already explicitly pinned.  No CPU contention
    between client + server.  No power-state changes.  No lag spike.

    DEFAULT mode undoes all of the above.

.PARAMETER Mode
    Gaming   -- enable anti-lag tweaks (default when no arg)
    Default  -- restore normal Windows behaviour
    Status   -- show current state without changing anything

.PARAMETER ServerCores
    Override the auto-computed split.  Default: 4 cores for server,
    rest for client.  On a 4-core CPU this would leave only 0 cores
    for the client, so the script bails and tells you to do it
    manually.

.EXAMPLE
    .\gaming-mode.ps1              # default = Gaming mode
    .\gaming-mode.ps1 -Mode Status
    .\gaming-mode.ps1 -Mode Default
    .\gaming-mode.ps1 -ServerCores 6   # give the server 6 cores
#>

[CmdletBinding()]
param(
    [ValidateSet('Gaming', 'Default', 'Status')]
    [string]$Mode = 'Gaming',
    [int]$ServerCores = 0    # 0 = auto
)

# -- Constants --------------------------------------------------------------
$PLAN_BALANCED  = '381b4222-f694-41f0-9685-ff5bb260df2e'
$PLAN_HIGHPERF  = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
$PLAN_ULTIMATE  = 'e9a42b02-d5df-448d-aa00-03f14749eb61'

$GAME_BAR_REG = 'HKCU:\Software\Microsoft\GameBar'
$GAME_DVR_REG = 'HKCU:\System\GameConfigStore'

# -- Helpers ----------------------------------------------------------------
function Get-CurrentPowerPlan {
    $line = powercfg /getactivescheme 2>$null | Out-String
    if ($line -match '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') {
        return $matches[1]
    }
    return 'unknown'
}

function Get-GameModeStatus {
    $val = Get-ItemProperty -Path $GAME_BAR_REG -Name 'AllowAutoGameMode' -ErrorAction SilentlyContinue
    if ($val -and $val.AllowAutoGameMode -eq 0) { return 'Off' } else { return 'On (default)' }
}

function Get-GameDVRStatus {
    $val = Get-ItemProperty -Path $GAME_DVR_REG -Name 'GameDVR_Enabled' -ErrorAction SilentlyContinue
    if ($val -and $val.GameDVR_Enabled -eq 0) { return 'Off' } else { return 'On' }
}

function Get-CS2Server {
    Get-CimInstance Win32_Process -Filter "Name='cs2.exe'" 2>$null |
        Where-Object { $_.CommandLine -match '-dedicated' }
}

function Get-CS2Client {
    Get-CimInstance Win32_Process -Filter "Name='cs2.exe'" 2>$null |
        Where-Object { $_.CommandLine -notmatch '-dedicated' }
}

function Get-Oblivion {
    Get-CimInstance Win32_Process -Filter "Name='OblivionServerTool.exe'" 2>$null
}

function Set-PowerPlan-Internal {
    param([string]$Guid)
    powercfg /setactive $Guid 2>$null | Out-Null
}

function Set-GameMode {
    param([bool]$Enable)
    $val = if ($Enable) { 1 } else { 0 }
    if (-not (Test-Path $GAME_BAR_REG)) {
        New-Item -Path $GAME_BAR_REG -Force | Out-Null
    }
    Set-ItemProperty -Path $GAME_BAR_REG -Name 'AllowAutoGameMode' -Value $val -Force
    Set-ItemProperty -Path $GAME_BAR_REG -Name 'AutoGameModeEnabled' -Value $val -Force -ErrorAction SilentlyContinue
}

function Set-GameDVR {
    param([bool]$Enable)
    $val = if ($Enable) { 1 } else { 0 }
    if (-not (Test-Path $GAME_DVR_REG)) {
        New-Item -Path $GAME_DVR_REG -Force | Out-Null
    }
    Set-ItemProperty -Path $GAME_DVR_REG -Name 'GameDVR_Enabled' -Value $val -Force
}

function Set-Process-Priority {
    param($CimProc, [string]$Priority)
    if (-not $CimProc) { return }
    try {
        $p = Get-Process -Id $CimProc.ProcessId -ErrorAction Stop
        $p.PriorityClass = $Priority
    } catch {
        Write-Host "  (failed to set $Priority on PID $($CimProc.ProcessId): $($_.Exception.Message))" -ForegroundColor Yellow
    }
}

function Set-Process-Affinity {
    param($CimProc, [int64]$Mask)
    if (-not $CimProc) { return }
    try {
        $p = Get-Process -Id $CimProc.ProcessId -ErrorAction Stop
        $p.ProcessorAffinity = [IntPtr]$Mask
    } catch {
        Write-Host "  (failed to set affinity 0x$('{0:X}' -f $Mask) on PID $($CimProc.ProcessId): $($_.Exception.Message))" -ForegroundColor Yellow
    }
}

function Format-Affinity {
    param([int64]$Mask, [int]$LogicalCores)
    $cores = @()
    for ($i = 0; $i -lt $LogicalCores; $i++) {
        if (($Mask -band (1 -shl $i)) -ne 0) { $cores += $i }
    }
    if ($cores.Count -eq $LogicalCores) { return "all" }
    return ($cores -join ',')
}

# -- Status display ---------------------------------------------------------
function Show-Status {
    Write-Host ""
    Write-Host "=== Current state ===" -ForegroundColor Cyan
    $logical = [System.Environment]::ProcessorCount
    $plan = Get-CurrentPowerPlan
    $planName = switch ($plan) {
        $PLAN_BALANCED { 'Balanced' }
        $PLAN_HIGHPERF { 'High Performance' }
        $PLAN_ULTIMATE { 'Ultimate Performance' }
        default { "Other ($plan)" }
    }
    Write-Host ("  Power Plan      : {0}" -f $planName)
    Write-Host ("  Game Mode       : {0}" -f (Get-GameModeStatus))
    Write-Host ("  Game DVR        : {0}" -f (Get-GameDVRStatus))
    Write-Host ("  Logical cores   : {0}" -f $logical)

    $server = Get-CS2Server
    if ($server) {
        $p = Get-Process -Id $server.ProcessId -ErrorAction SilentlyContinue
        if ($p) {
            $affStr = Format-Affinity $p.ProcessorAffinity $logical
            Write-Host ("  Server  PID {0,-6}: priority={1}, cores={2}" -f $server.ProcessId, $p.PriorityClass, $affStr)
        }
    } else {
        Write-Host "  Server          : not running"
    }

    $client = Get-CS2Client
    if ($client) {
        $p = Get-Process -Id $client.ProcessId -ErrorAction SilentlyContinue
        if ($p) {
            $affStr = Format-Affinity $p.ProcessorAffinity $logical
            Write-Host ("  Client  PID {0,-6}: priority={1}, cores={2}" -f $client.ProcessId, $p.PriorityClass, $affStr)
        }
    } else {
        Write-Host "  Client          : not running"
    }

    $obl = Get-Oblivion
    if ($obl) {
        foreach ($o in $obl) {
            $p = Get-Process -Id $o.ProcessId -ErrorAction SilentlyContinue
            if ($p) {
                $affStr = Format-Affinity $p.ProcessorAffinity $logical
                Write-Host ("  Oblivion PID {0,-5}: priority={1}, cores={2}" -f $o.ProcessId, $p.PriorityClass, $affStr)
            }
        }
    }
}

# -- Apply: gaming ----------------------------------------------------------
function Apply-Gaming {
    Write-Host ""
    Write-Host "=== Applying GAMING mode (anti-lag tweaks) ===" -ForegroundColor Green

    # 1. Power Plan
    $plans = powercfg /list 2>$null | Out-String
    if ($plans -match $PLAN_ULTIMATE) {
        Set-PowerPlan-Internal $PLAN_ULTIMATE
        Write-Host "  [OK]  Power Plan -> Ultimate Performance"
    } else {
        Set-PowerPlan-Internal $PLAN_HIGHPERF
        Write-Host "  [OK]  Power Plan -> High Performance (Ultimate not available -- run as admin to enable)"
    }

    # 2. Game Mode
    Set-GameMode -Enable $false
    Write-Host "  [OK]  Game Mode -> Off"

    # 3. Game DVR
    Set-GameDVR -Enable $false
    Write-Host "  [OK]  Game DVR -> Off"

    # 4 + 5. CPU affinity
    $logical = [System.Environment]::ProcessorCount
    if ($ServerCores -gt 0) {
        $sCores = $ServerCores
    } else {
        # Auto: server gets 4 cores (good for CS2 server at 128-tick),
        # or 1/4 of total if you have more than 16 logical cores.
        $sCores = [Math]::Max(4, [Math]::Min(8, [Math]::Floor($logical / 4)))
    }
    if ($sCores -ge $logical) {
        Write-Host "  [WARN] $sCores cores for server but only $logical available -- can't split.  Skipping affinity." -ForegroundColor Yellow
    } else {
        $serverMask  = [int64]((1 -shl $sCores) - 1)
        $allMask     = [int64]((1 -shl $logical) - 1)
        $clientMask  = $allMask -band (-bnot $serverMask)
        Write-Host ("  [OK]  Affinity split: server cores 0..{0}, client cores {1}..{2}" -f ($sCores-1), $sCores, ($logical-1))

        $server = Get-CS2Server
        if ($server) {
            Set-Process-Priority $server 'High'
            Set-Process-Affinity $server $serverMask
            Write-Host ("  [OK]  Server  PID {0}: priority=High, pinned" -f $server.ProcessId)
        } else {
            Write-Host "  [..]  Server not running yet -- re-run this script after Oblivion starts the server"
        }

        $client = Get-CS2Client
        if ($client) {
            # Don't touch priority -- anti-cheat is touchy.  Just affinity.
            Set-Process-Affinity $client $clientMask
            Write-Host ("  [OK]  Client  PID {0}: pinned to client cores (priority untouched -- VAC-friendly)" -f $client.ProcessId)
        } else {
            Write-Host "  [..]  Client not running yet -- re-run this script after launching CS2"
        }

        # Oblivion + OS get core 0 to themselves (well, share with the server cores
        # but Oblivion is lightweight)
        $obl = Get-Oblivion
        if ($obl) {
            foreach ($o in $obl) {
                Set-Process-Affinity $o 1
            }
            Write-Host ("  [OK]  Oblivion: pinned to core 0 only (low-impact admin panel)")
        }
    }

    Write-Host ""
    Write-Host "Tip: if you START a process AFTER running this script (CS2 client / server / Oblivion)," -ForegroundColor Cyan
    Write-Host "      re-run the script to apply the pinning to the new process." -ForegroundColor Cyan
}

# -- Apply: default ---------------------------------------------------------
function Apply-Default {
    Write-Host ""
    Write-Host "=== Restoring DEFAULT mode (affinity + Game Mode/DVR only) ===" -ForegroundColor Yellow

    # v0.11.14: do NOT revert power plan to Balanced.  Operator wants
    # High Performance (or Ultimate) at all times -- Balanced costs
    # responsiveness for general use too, not just hosting.  Power plan
    # stays at whatever ON left it.  Only Game Mode / DVR / affinity
    # get reset by this script.
    $plan = Get-CurrentPowerPlan
    $planName = switch ($plan) {
        $PLAN_BALANCED { 'Balanced' }
        $PLAN_HIGHPERF { 'High Performance' }
        $PLAN_ULTIMATE { 'Ultimate Performance' }
        default { "Other ($plan)" }
    }
    Write-Host "  [SKIP] Power Plan unchanged (currently: $planName) -- per operator preference"

    Set-GameMode -Enable $true
    Write-Host "  [OK]  Game Mode -> On"

    Set-GameDVR -Enable $true
    Write-Host "  [OK]  Game DVR -> On"

    $logical = [System.Environment]::ProcessorCount
    $allMask = [int64]((1 -shl $logical) - 1)

    foreach ($CimProc in @(Get-CS2Server) + @(Get-CS2Client) + @(Get-Oblivion)) {
        if ($CimProc) {
            Set-Process-Priority $CimProc 'Normal'
            Set-Process-Affinity $CimProc $allMask
        }
    }
    Write-Host "  [OK]  All process affinities -> all cores; priorities -> Normal"
}

# -- Main -------------------------------------------------------------------
switch ($Mode) {
    'Gaming'  { Apply-Gaming;  Show-Status }
    'Default' { Apply-Default; Show-Status }
    'Status'  { Show-Status }
}
Write-Host ""

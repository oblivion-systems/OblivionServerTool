@echo off
REM ============================================================================
REM  gaming-mode-on.bat — enable anti-lag tweaks for "server + client on one PC"
REM
REM  Auto-elevates to admin (some powercfg calls + Ultimate Performance need it).
REM  See gaming-mode.ps1 for the full list of what this changes.
REM ============================================================================

REM Self-elevate if not running as admin
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting admin elevation...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gaming-mode.ps1" -Mode Gaming
pause

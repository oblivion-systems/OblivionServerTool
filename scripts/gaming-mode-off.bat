@echo off
REM ============================================================================
REM  gaming-mode-off.bat — restore Windows defaults (after a session)
REM
REM  Use this when you're done playing on the host PC.  Returns power plan to
REM  Balanced, re-enables Game Mode + DVR, and clears all affinity pins so
REM  Windows is back to its usual self for general computer use.
REM ============================================================================

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting admin elevation...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gaming-mode.ps1" -Mode Default
pause

@echo off
REM ============================================================================
REM  gaming-mode-status.bat — show current state without changing anything
REM ============================================================================

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gaming-mode.ps1" -Mode Status
pause

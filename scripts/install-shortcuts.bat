@echo off
REM ============================================================================
REM  install-shortcuts.bat - one-time setup
REM
REM  Creates two desktop shortcuts so toggling gaming-mode is a single
REM  double-click from the desktop instead of digging into scripts/.
REM
REM  Run this ONCE.  Idempotent (safe to re-run; just overwrites existing
REM  shortcuts).
REM ============================================================================

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-shortcuts.ps1"
pause

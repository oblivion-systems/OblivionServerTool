@echo off
REM ==========================================================================
REM  pull-latest.bat - fetch the latest OblivionServerTool.exe release
REM  and drop it into dist\ ready to launch.
REM
REM  Why this exists: the repo is private, so a plain curl/wget can't reach
REM  the release asset.  This script piggybacks on your existing `gh` CLI
REM  auth (the same one that publishes the releases) to download the .exe
REM  without exposing any tokens in the script itself.
REM
REM  What it does:
REM    1. Verifies gh CLI is installed + authenticated
REM    2. Refuses to overwrite if OblivionServerTool.exe is currently running
REM       (so you can save state cleanly first)
REM    3. Backs up your current exe to dist\OblivionServerTool.exe.bak
REM    4. Downloads the latest release's .exe to dist\
REM    5. Offers to launch it
REM
REM  NOTE: This file is ASCII-only.  cmd.exe on the default Windows code page
REM  (1252) silently aborts on multi-byte UTF-8 sequences in comments, which
REM  is why earlier attempts with fancy unicode dashes flashed and died.
REM ==========================================================================
setlocal

REM Run from the script's own directory so dist\ resolves correctly even
REM when invoked via right-click -> Run or from a shortcut.
pushd "%~dp0"

set "REPO=jacquesvniekerk-eng/OblivionServerTool"
set "EXE_NAME=OblivionServerTool.exe"
set "DEST_DIR=dist"
set "DEST=%DEST_DIR%\%EXE_NAME%"
set "BACKUP=%DEST%.bak"

echo.
echo === Oblivion Server Tool - pull latest release ===
echo Repo : %REPO%
echo Dest : %DEST%
echo CWD  : %CD%
echo.

REM --- 1. gh CLI present? -----------------------------------------------
where gh >nul 2>&1
if errorlevel 1 (
  echo [X] gh CLI not found on PATH.
  echo     Install from https://cli.github.com/ or run:
  echo         winget install --id GitHub.cli
  goto :fail
)

REM --- 2. gh authenticated? ---------------------------------------------
gh auth status >nul 2>&1
if errorlevel 1 (
  echo [X] gh CLI not authenticated.  Run: gh auth login
  echo     Pick GitHub.com, HTTPS, login with web browser.
  goto :fail
)

REM --- 3. App not running? ----------------------------------------------
REM   Refuse to overwrite a running .exe -- Windows would lock the file and
REM   the user would lose any unsaved state.  Operator closes from the tray
REM   first, then re-runs.
tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
if not errorlevel 1 (
  echo [!] %EXE_NAME% is currently running.
  echo     Close it from the system tray or app window first
  echo     (so it saves state cleanly), then re-run this script.
  goto :fail
)

REM --- 4. Ensure dest dir exists ----------------------------------------
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"

REM --- 5. Show the release we'll pull -----------------------------------
echo [i] Querying latest release...
set "TAG="
for /f "usebackq delims=" %%v in (`gh release view --repo %REPO% --json tagName -q ".tagName" 2^>nul`) do set "TAG=%%v"
if "%TAG%"=="" (
  echo [X] Couldn't read latest release.  Possible causes:
  echo     - no releases published yet
  echo     - your gh token lacks read access to %REPO%
  goto :fail
)
echo [i] Latest release tag: %TAG%

REM --- 6. Back up existing exe (if any) ---------------------------------
if exist "%DEST%" (
  echo [i] Backing up existing exe to %BACKUP%
  copy /Y "%DEST%" "%BACKUP%" >nul
  if errorlevel 1 (
    echo [X] Backup failed.  Aborting before overwriting %DEST%.
    goto :fail
  )
)

REM --- 7. Download via gh -----------------------------------------------
echo [i] Downloading %EXE_NAME% from release %TAG%...
gh release download %TAG% --repo %REPO% --pattern %EXE_NAME% --dir %DEST_DIR% --clobber
if errorlevel 1 (
  echo [X] Download failed.  If you backed up your old exe it's still at:
  echo         %BACKUP%
  goto :fail
)

REM --- 8. Report what we got --------------------------------------------
for %%I in ("%DEST%") do (
  echo [+] Downloaded %%~zI bytes  /  %%~tI
)

REM --- 9. Offer to launch -----------------------------------------------
echo.
choice /M "Launch the new version now"
if errorlevel 2 goto :done
echo [i] Launching %DEST%...
start "" "%DEST%"

:done
echo.
echo Done.  If anything goes wrong, the previous version is at:
echo     %BACKUP%
echo.
popd
pause
endlocal
exit /b 0

:fail
echo.
popd
pause
endlocal
exit /b 1
